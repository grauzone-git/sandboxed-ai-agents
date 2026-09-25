package cli

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"
)

type sshFiles struct{ name, root, directory, entry, hostConfig, key, public, known, owner string }

func sshPaths(name string) (sshFiles, error) {
	state, err := stateDir()
	if err != nil {
		return sshFiles{}, err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return sshFiles{}, err
	}
	root := filepath.Join(state, "ssh")
	directory := filepath.Join(root, name)
	files := sshFiles{name: name, root: root, directory: directory, entry: filepath.Join(root, name+".conf"), hostConfig: filepath.Join(home, ".ssh", "config"), key: filepath.Join(directory, "id_ed25519"), public: filepath.Join(directory, "id_ed25519.pub"), known: filepath.Join(directory, "known_hosts"), owner: filepath.Join(directory, "owner.json")}
	for _, path := range []string{root, files.hostConfig} {
		if strings.ContainsAny(filepath.ToSlash(path), "\r\n\"%$*?[]") {
			return files, fmt.Errorf("SSH paths must not contain quotes, line breaks, or expansion characters")
		}
	}
	return files, nil
}

func noSSHLinks(path string) error {
	for current := filepath.Clean(path); ; current = filepath.Dir(current) {
		info, err := os.Lstat(current)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err == nil && pathRedirected(info) {
			return fmt.Errorf("SSH setup refuses symlink or reparse point: %s", current)
		}
		if filepath.Dir(current) == current {
			break
		}
	}
	return nil
}

func (files sshFiles) validate() error {
	for _, path := range []string{files.root, files.directory, files.entry, files.hostConfig, files.key, files.public, files.known, files.owner} {
		if err := noSSHLinks(path); err != nil {
			return err
		}
		info, err := os.Lstat(path)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err == nil && path != files.root && path != files.directory && !info.Mode().IsRegular() {
			return fmt.Errorf("unexpected SSH file type: %s", path)
		}
	}
	expected, _ := json.Marshal(struct{ Controller, Name string }{owner(), files.name})
	data, err := os.ReadFile(files.owner)
	if err == nil {
		if string(data) != string(expected)+"\n" {
			return fmt.Errorf("SSH state belongs to another controller")
		}
	} else if !os.IsNotExist(err) {
		return err
	} else {
		entries, err := os.ReadDir(files.directory)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if len(entries) > 0 {
			return fmt.Errorf("existing SSH state has no ownership record; move it aside explicitly")
		}
		if _, err := os.Lstat(files.entry); err == nil {
			return fmt.Errorf("existing SSH config has no ownership record; move it aside explicitly")
		}
	}
	data, err = os.ReadFile(files.hostConfig)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if !utf8.Valid(data) {
		return fmt.Errorf("SSH config must use UTF-8; existing config was retained")
	}
	return nil
}

func validateSSHSetup(name string) error {
	if _, err := exec.LookPath("ssh-keygen"); err != nil {
		return fmt.Errorf("install the OpenSSH client (ssh-keygen) before requesting SSH setup")
	}
	files, err := sshPaths(name)
	if err != nil {
		return err
	}
	return files.validate()
}

func (files sshFiles) lock() (func(), error) {
	if err := files.validate(); err != nil {
		return nil, err
	}
	state, err := stateDir()
	if err != nil {
		return nil, err
	}
	if err := privateStateDirectory(state, files.root); err != nil {
		return nil, err
	}
	if err := securePrivatePath(files.root); err != nil {
		return nil, err
	}
	return lockSSHDirectory(filepath.Join(files.root, ".config-lock"))
}

func lockSSHDirectory(lock string) (func(), error) {
	if err := noSSHLinks(lock); err != nil {
		return nil, err
	}
	if err := os.Mkdir(lock, 0700); err != nil {
		return nil, fmt.Errorf("SSH config update is locked or unavailable: %s: %w", lock, err)
	}
	return func() { os.Remove(lock) }, nil
}

func writeSSHFile(path string, data []byte) error {
	return writeSSHFileChecked(path, data, nil)
}

func writeSSHFileChecked(path string, data []byte, check func() error) error {
	if err := noSSHLinks(path); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".sandbox-ssh-")
	if err != nil {
		return err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	if err = securePrivatePath(temporary); err != nil {
		file.Close()
		return err
	}
	if _, err = file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if check != nil {
		if err := check(); err != nil {
			return err
		}
	}
	return os.Rename(temporary, path)
}

func (files sshFiles) include(install bool) error {
	if err := noSSHLinks(files.hostConfig); err != nil {
		return err
	}
	original, err := os.ReadFile(files.hostConfig)
	existed := err == nil
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	updatedBytes, err := files.includeContent(original, install)
	if err != nil {
		return err
	}
	updated := string(updatedBytes)
	if updated == string(original) {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(files.hostConfig), 0700); err != nil {
		return err
	}
	if err := securePrivatePath(filepath.Dir(files.hostConfig)); err != nil {
		return err
	}
	return replaceSSHConfig(files.hostConfig, []byte(updated), original, existed, nil)
}

func setupSSH(name string) error {
	if err := validateSSHSetup(name); err != nil {
		return err
	}
	if err := requireOwned(name); err != nil {
		return err
	}
	files, err := sshPaths(name)
	if err != nil {
		return err
	}
	release, err := files.lock()
	if err != nil {
		return err
	}
	defer release()
	mapping, err := capturePodman(false, "port", name, "2222/tcp")
	if err != nil {
		return err
	}
	value := strings.TrimSpace(string(mapping))
	if !strings.HasPrefix(value, "127.0.0.1:") {
		return fmt.Errorf("expected one loopback-only SSH port mapping")
	}
	port := strings.TrimPrefix(value, "127.0.0.1:")
	number, err := strconv.Atoi(port)
	if err != nil || number < 1024 || number > 65535 {
		return fmt.Errorf("unexpected SSH port mapping")
	}
	fields, err := sandboxSSHHostKey(name)
	if err != nil {
		return err
	}

	if err := os.MkdirAll(files.directory, 0700); err != nil {
		return err
	}
	if err := securePrivatePath(files.directory); err != nil {
		return err
	}
	identity, _ := json.Marshal(struct{ Controller, Name string }{owner(), files.name})
	if err := writeSSHFile(files.owner, append(identity, '\n')); err != nil {
		return err
	}
	if _, err := os.Stat(files.key); os.IsNotExist(err) {
		command := exec.Command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name+" sandbox access", "-f", files.key)
		command.Stderr = os.Stderr
		if err := command.Run(); err != nil {
			return err
		}
	} else if err != nil {
		return err
	}
	if err := securePrivatePath(files.key); err != nil {
		return err
	}
	public, err := exec.Command("ssh-keygen", "-y", "-P", "", "-f", files.key).Output()
	if err != nil {
		return err
	}
	if err := writeSSHFile(files.public, public); err != nil {
		return err
	}
	authorize := platformPodmanCommand("exec", "-i", "--user", "0", name, "/bin/sh", "-c", "/bin/cat > /var/lib/agent-sshd/authorized_keys && /bin/chmod 0644 /var/lib/agent-sshd/authorized_keys")
	authorize.Stdin = bytes.NewReader(public)
	authorize.Stdout, authorize.Stderr = os.Stdout, os.Stderr
	if err := authorize.Run(); err != nil {
		return err
	}
	known := fmt.Sprintf("[127.0.0.1]:%s %s %s\n", port, fields[0], fields[1])
	if err := writeSSHFile(files.known, []byte(known)); err != nil {
		return err
	}

	if err := writeSSHFile(files.entry, files.hostEntry(port)); err != nil {
		return err
	}
	if err := files.include(true); err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "SSH configured: ssh %s\n", name)
	return nil
}

func sshConfigCommand(name string, args []string) error {
	if len(args) > 1 || (len(args) == 1 && args[0] != "--install") {
		return fmt.Errorf("usage: sandboxed-agents NAME ssh-config [--install]")
	}
	if len(args) == 1 {
		files, err := sshPaths(name)
		if err != nil {
			return err
		}
		if err := files.validate(); err != nil {
			return err
		}
		complete := true
		for _, path := range []string{files.entry, files.key, files.known, files.public} {
			if _, err := os.Stat(path); err != nil {
				complete = false
			}
		}
		if complete {
			release, err := files.lock()
			if err != nil {
				return err
			}
			defer release()
			for _, path := range []string{files.directory, files.entry, files.key, files.known, files.public, files.owner} {
				if err := securePrivatePath(path); err != nil {
					return err
				}
			}
			return files.include(true)
		}
		if err := requirePodman(); err != nil {
			return err
		}
		return setupSSH(name)
	}
	files, err := sshPaths(name)
	if err != nil {
		return err
	}
	if err := files.validate(); err != nil {
		return err
	}
	data, err := os.ReadFile(files.entry)
	if err != nil {
		return fmt.Errorf("run sandboxed-agents %s ssh-config --install while the sandbox is running", name)
	}
	_, err = os.Stdout.Write(data)
	return err
}

func fingerprint(name string) error {
	files, err := sshPaths(name)
	if err != nil {
		return err
	}
	if err := files.validate(); err != nil {
		return err
	}
	if _, err := os.Stat(files.known); err != nil {
		return fmt.Errorf("run sandboxed-agents %s ssh-config --install first", name)
	}
	command := exec.Command("ssh-keygen", "-lf", files.known)
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	return command.Run()
}

func checkSSH(name string) error {
	files, err := sshPaths(name)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(files.key); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return err
	}
	if err := files.validate(); err != nil {
		return err
	}
	if _, err := os.Stat(files.entry); err != nil {
		return fmt.Errorf("SSH setup is incomplete; run sandboxed-agents %s ssh-config --install", name)
	}
	command := exec.Command("ssh", "-F", files.entry, "-o", "BatchMode=yes", name, "true")
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return command.Run()
}

// beginSSHRemoval holds the configuration lock through container deletion, so
// invalid local state is discovered while SSH access can still be retained.
func beginSSHRemoval(name string) (func() error, func(), error) {
	files, err := sshPaths(name)
	if err != nil {
		return nil, nil, err
	}
	if err := files.validate(); err != nil {
		return nil, nil, err
	}
	_, dirErr := os.Lstat(files.directory)
	_, entryErr := os.Lstat(files.entry)
	if os.IsNotExist(dirErr) && os.IsNotExist(entryErr) {
		return func() error { return nil }, func() {}, nil
	}
	release, err := files.lock()
	if err != nil {
		return nil, nil, err
	}
	cleanup := func() error {
		if err := files.validate(); err != nil {
			return err
		}
		for _, path := range []string{files.entry, files.key, files.public, files.known, files.owner} {
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				return err
			}
		}
		// Unknown user files are retained; only known managed files are deleted.
		if entries, err := os.ReadDir(files.directory); err == nil && len(entries) == 0 {
			if err := os.Remove(files.directory); err != nil {
				return err
			}
		}
		entries, err := os.ReadDir(files.root)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".conf") {
				return nil
			}
		}
		return files.include(false)
	}
	return cleanup, release, nil
}

func (files sshFiles) includeContent(original []byte, install bool) ([]byte, error) {
	if !utf8.Valid(original) {
		return nil, fmt.Errorf("SSH config must use UTF-8")
	}
	include := `Include "` + filepath.ToSlash(filepath.Join(files.root, "*.conf")) + `"`
	content := string(original)
	bom := ""
	if strings.HasPrefix(content, "\ufeff") {
		bom = "\ufeff"
		content = strings.TrimPrefix(content, bom)
	}
	var remaining strings.Builder
	for _, line := range strings.SplitAfter(content, "\n") {
		if strings.TrimSpace(line) != include {
			remaining.WriteString(line)
		}
	}
	updated := remaining.String()
	if install {
		updated = include + "\n" + updated
	}
	updated = bom + updated
	return []byte(updated), nil
}

func replaceSSHConfig(path string, data, expected []byte, existed bool, check func() error) error {
	return writeSSHFileChecked(path, data, func() error {
		if err := noSSHLinks(path); err != nil {
			return err
		}
		current, err := os.ReadFile(path)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if (err == nil) != existed || !bytes.Equal(current, expected) {
			return fmt.Errorf("SSH config changed; retry. Existing config was retained")
		}
		if check != nil {
			return check()
		}
		return nil
	})
}

func validEd25519Blob(encoded string) bool {
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	return err == nil && len(decoded) == 51 && binary.BigEndian.Uint32(decoded[:4]) == 11 && string(decoded[4:15]) == "ssh-ed25519" && binary.BigEndian.Uint32(decoded[15:19]) == 32
}

func (files sshFiles) hostEntry(port string) []byte {
	return []byte(fmt.Sprintf("Host %s\n    HostName 127.0.0.1\n    Port %s\n    User agent\n    IdentityFile \"%s\"\n    IdentitiesOnly yes\n    IdentityAgent none\n    ForwardAgent no\n    ForwardX11 no\n    UserKnownHostsFile \"%s\"\n    StrictHostKeyChecking yes\n    ServerAliveInterval 30\n", files.name, port, filepath.ToSlash(files.key), filepath.ToSlash(files.known)))
}

func sandboxSSHHostKey(name string) ([]string, error) {
	data, err := capturePodman(false, "exec", "--user", "0", name, "/bin/cat", "/var/lib/agent-sshd/ssh_host_ed25519_key.pub")
	if err != nil {
		return nil, err
	}
	fields := strings.Fields(string(data))
	if len(fields) < 2 || fields[0] != "ssh-ed25519" {
		return nil, fmt.Errorf("sandbox did not return an Ed25519 host key")
	}
	if !validEd25519Blob(fields[1]) {
		return nil, fmt.Errorf("invalid Ed25519 SSH host key")
	}

	return fields, nil
}
