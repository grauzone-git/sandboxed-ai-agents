package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

type adoptionSSHMigration struct {
	checkReady func(string) error
	commit     func(context.Context) error
}

func prepareAdoptionSSH(name, source, port string) (*adoptionSSHMigration, error) {
	files, err := sshPaths(name)
	if err != nil {
		return nil, err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	legacy := filepath.Join(home, ".ssh", "sanboxed-agents", name)
	if err := noSSHLinks(legacy); err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(legacy)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if len(entries) == 0 {
		return nil, nil
	}
	if err := files.validate(); err != nil {
		return nil, err
	}
	checkDestination := func() error {
		for _, path := range []string{files.key, files.public, files.known, files.entry, files.owner} {
			if _, err := os.Lstat(path); !os.IsNotExist(err) {
				return fmt.Errorf("adoption destination already exists: %s", path)
			}
		}
		return nil
	}
	if err := checkDestination(); err != nil {
		return nil, err
	}
	legacyLock := filepath.Join(filepath.Dir(legacy), ".config.lock")
	if _, err := os.Lstat(legacyLock); !os.IsNotExist(err) {
		return nil, fmt.Errorf("SSH config update is locked or unavailable: %s", legacyLock)
	}
	originals := map[string][]byte{}
	for _, base := range []string{"id_ed25519", "id_ed25519.pub", "known_hosts", name + ".conf", "owner.json"} {
		path := filepath.Join(legacy, base)
		if err := noSSHLinks(path); err != nil {
			return nil, err
		}
		info, err := os.Lstat(path)
		if os.IsNotExist(err) && base == "owner.json" {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("incomplete checkout SSH configuration: %w", err)
		}
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("checkout SSH state must use regular files")
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		originals[base] = data
	}
	if metadata, ok := originals["owner.json"]; ok {
		var identity struct {
			Project string `json:"project"`
			Name    string `json:"name"`
		}
		if json.Unmarshal(metadata, &identity) != nil || identity.Project != source || identity.Name != name {
			return nil, fmt.Errorf("checkout SSH state belongs to another checkout")
		}
	}
	pin := strings.Fields(string(originals["known_hosts"]))
	if len(pin) != 3 || pin[0] != "[127.0.0.1]:"+port || pin[1] != "ssh-ed25519" {
		return nil, fmt.Errorf("checkout SSH host pin must contain one Ed25519 key for 127.0.0.1 on the sandbox's port %s", port)
	}
	if !validEd25519Blob(pin[2]) {
		return nil, fmt.Errorf("checkout SSH host pin contains an invalid Ed25519 key")
	}
	keygen := exec.Command("ssh-keygen", "-y", "-P", "", "-f", filepath.Join(legacy, "id_ed25519"))
	keygen.Stdin = strings.NewReader("")
	public, err := keygen.Output()
	if err != nil {
		return nil, fmt.Errorf("cannot validate checkout SSH key: %w", err)
	}
	fields := strings.Fields(string(originals["id_ed25519.pub"]))
	derived := strings.Fields(string(public))
	if len(fields) < 2 || len(derived) < 2 || fields[0] != "ssh-ed25519" || fields[0] != derived[0] || fields[1] != derived[1] {
		return nil, fmt.Errorf("checkout SSH public and private keys do not match")
	}
	migration := &adoptionSSHMigration{checkReady: func(id string) error {
		host, err := sandboxSSHHostKey(id)
		if err != nil {
			return err
		}
		if host[1] != pin[2] {
			return fmt.Errorf("checkout SSH host pin does not match the replacement sandbox")
		}
		authorized, err := capturePodman(false, "exec", "--user", "0", id, "/bin/cat", "/var/lib/agent-sshd/authorized_keys")
		if err != nil {
			return err
		}
		for _, line := range strings.Split(string(authorized), "\n") {
			key := strings.Fields(line)
			if len(key) >= 2 && key[0] == fields[0] && key[1] == fields[1] {
				return nil
			}
		}
		return fmt.Errorf("checkout SSH client key is not authorized by the replacement sandbox")
	}}
	migration.commit = func(ctx context.Context) (result error) {
		check := func() error {
			if ctx.Err() != nil {
				return updateInterrupted(ctx.Err())
			}
			return nil
		}
		if err := check(); err != nil {
			return err
		}
		if err := files.validate(); err != nil {
			return err
		}
		release, err := files.lock()
		if err != nil {
			return err
		}
		defer release()
		releaseLegacy, err := lockSSHDirectory(legacyLock)
		if err != nil {
			return err
		}
		defer releaseLegacy()
		if err := checkDestination(); err != nil {
			return err
		}
		for base, original := range originals {
			path := filepath.Join(legacy, base)
			if err := unchangedLegacySSH(path, original); err != nil {
				return err
			}
		}
		originalConfig, err := os.ReadFile(files.hostConfig)
		configExisted := err == nil
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err := securePrivatePath(filepath.Dir(files.hostConfig)); err != nil {
			return err
		}
		created := map[string][]byte{}
		deleted := map[string]bool{}
		defer func() {
			if result == nil {
				return
			}
			var recovery error
			for base := range deleted {
				original := originals[base]
				path := filepath.Join(legacy, base)
				if err := noSSHLinks(path); err != nil {
					recovery = errors.Join(recovery, err)
					continue
				}
				current, err := os.ReadFile(path)
				if err == nil && bytes.Equal(current, original) {
					continue
				}
				if !os.IsNotExist(err) {
					recovery = errors.Join(recovery, fmt.Errorf("checkout SSH state changed; existing file retained: %s", path))
					continue
				}
				if err := writeSSHFile(path, original); err != nil {
					recovery = errors.Join(recovery, err)
				}
			}
			// Keep replacement copies if restoring a deleted legacy file failed.
			if recovery == nil {
				for path, expected := range created {
					current, err := os.ReadFile(path)
					if os.IsNotExist(err) {
						continue
					}
					if err != nil || !bytes.Equal(current, expected) {
						recovery = errors.Join(recovery, fmt.Errorf("new SSH state changed; existing file retained: %s", path))
						continue
					}
					if err := os.Remove(path); err != nil {
						recovery = errors.Join(recovery, err)
					}
				}
				os.Remove(files.directory)
			}
			if recovery != nil {
				result = fmt.Errorf("%w; SSH restoration failed: %v", result, recovery)
			}
		}()
		if err := os.MkdirAll(files.directory, 0700); err != nil {
			return err
		}
		if err := securePrivatePath(files.directory); err != nil {
			return err
		}
		ownerData, _ := json.Marshal(struct{ Controller, Name string }{owner(), name})

		for _, item := range []struct {
			path string
			data []byte
		}{{files.owner, append(ownerData, '\n')}, {files.key, originals["id_ed25519"]}, {files.public, originals["id_ed25519.pub"]}, {files.known, originals["known_hosts"]}, {files.entry, files.hostEntry(port)}} {
			if err := check(); err != nil {
				return err
			}
			if err := writeSSHFile(item.path, item.data); err != nil {
				return err
			}
			created[item.path] = item.data
		}
		current, err := files.includeContent(originalConfig, true)
		if err != nil {
			return err
		}
		oldEntry := filepath.ToSlash(filepath.Join(legacy, name+".conf"))
		var remaining strings.Builder
		for _, line := range strings.SplitAfter(string(current), "\n") {
			remaining.WriteString(removeLegacySSHInclude(line, oldEntry))
		}
		for base, original := range originals {
			path := filepath.Join(legacy, base)
			if err := unchangedLegacySSH(path, original); err != nil {
				return err
			}
			if err := check(); err != nil {
				return err
			}
			if err := os.Remove(path); err != nil {
				return err
			}
			deleted[base] = true
		}
		// Commit the config last. A failed comparison leaves concurrent edits
		// intact, and rollback only restores the legacy files removed above.
		if err := replaceSSHConfig(files.hostConfig, []byte(remaining.String()), originalConfig, configExisted, check); err != nil {
			return err
		}
		os.Remove(legacy)
		return nil
	}
	return migration, nil
}

// Only the directive is case insensitive on Linux. Include paths retain their
// filesystem spelling, and unrelated lines are copied without normalization.
func removeLegacySSHInclude(line, legacy string) string {
	ending := ""
	body := line
	if strings.HasSuffix(body, "\r\n") {
		ending = "\r\n"
		body = strings.TrimSuffix(body, ending)
	} else if strings.HasSuffix(body, "\n") {
		ending = "\n"
		body = strings.TrimSuffix(body, ending)
	}
	space := func(c byte) bool { return c == ' ' || c == '\t' || c == '\r' }
	i := 0
	for i < len(body) && space(body[i]) {
		i++
	}
	indent := body[:i]
	start := i
	for i < len(body) && !space(body[i]) && body[i] != '=' {
		i++
	}
	if !strings.EqualFold(body[start:i], "Include") {
		return line
	}
	for i < len(body) && space(body[i]) {
		i++
	}
	if i < len(body) && body[i] == '=' {
		i++
		for i < len(body) && space(body[i]) {
			i++
		}
	}
	prefix := body[:i]
	kept := []string{}
	removed := false
	comment := ""
	for i < len(body) {
		for i < len(body) && space(body[i]) {
			i++
		}
		if i == len(body) {
			break
		}
		if body[i] == '#' {
			comment = body[i:]
			break
		}
		tokenStart := i
		var value strings.Builder
		quote := byte(0)
		for i < len(body) {
			c := body[i]
			if quote == 0 && space(c) {
				break
			}
			if c == '"' || c == '\'' {
				if quote == 0 {
					quote = c
					i++
					continue
				}
				if quote == c {
					quote = 0
					i++
					continue
				}
			}
			if c == '\\' && i+1 < len(body) && (body[i+1] == '"' || body[i+1] == '\'' || body[i+1] == '\\' || space(body[i+1])) {
				i++
				c = body[i]
			}
			value.WriteByte(c)
			i++
		}
		if quote != 0 {
			return line
		}
		same := value.String() == legacy
		if runtime.GOOS == "windows" {
			same = strings.EqualFold(filepath.ToSlash(value.String()), legacy)
		}
		if same {
			removed = true
		} else {
			kept = append(kept, body[tokenStart:i])
		}
	}
	if !removed {
		return line
	}
	if len(kept) == 0 {
		if comment != "" {
			return indent + comment + ending
		}
		return ""
	}
	result := prefix + strings.Join(kept, " ")
	if comment != "" {
		result += " " + comment
	}
	return result + ending
}

func unchangedLegacySSH(path string, original []byte) error {
	if err := noSSHLinks(path); err != nil {
		return err
	}
	current, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(current, original) {
		return fmt.Errorf("checkout SSH state changed during adoption; retry")
	}
	return nil
}
