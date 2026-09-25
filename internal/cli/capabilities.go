package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

const capabilitiesLabel = "io.sandboxed-agents.capabilities"

var imageIDPattern = regexp.MustCompile(`^(sha256:)?[a-f0-9]{64}$`)

func validateCapabilities(value string) error {
	if value != "none" && value != "podman" {
		return fmt.Errorf("capabilities must be podman or none; duplicates are not allowed")
	}
	return nil
}

func prepareCapabilities(image, capability string) (string, []string, error) {
	if err := validateCapabilities(capability); err != nil {
		return "", nil, err
	}
	if capability == "none" {
		return image, []string{"--security-opt=no-new-privileges"}, nil
	}
	base, err := capturePodman(false, "image", "inspect", "--format", "{{.Id}}", image)
	if err != nil {
		return "", nil, err
	}
	identity := strings.TrimSpace(string(base))
	if !imageIDPattern.MatchString(identity) {
		return "", nil, fmt.Errorf("Podman returned an invalid base image ID")
	}
	profile, err := platformNestedSeccomp()
	if err != nil {
		return "", nil, err
	}
	var derived string
	err = withBuildContext(func(context string) error {
		output, err := capturePodman(false, "build", "--quiet", "--pull=never", "--build-arg", "BASE_IMAGE="+identity, "-f", filepath.Join(context, "Containerfile.podman"), "--label", versionLabel+"="+Version, context)
		if err != nil {
			return err
		}
		lines := strings.Split(strings.TrimSpace(string(output)), "\n")
		derived = lines[len(lines)-1]
		if !imageIDPattern.MatchString(derived) {
			return fmt.Errorf("Podman returned an invalid capability image ID")
		}
		return nil
	})
	if err != nil {
		return "", nil, err
	}
	return derived, []string{"--device=/dev/fuse", "--device=/dev/net/tun", "--security-opt=label=disable", "--security-opt=apparmor=unconfined", "--security-opt=unmask=ALL", "--security-opt=seccomp=" + profile, "--tmpfs", "/run/user/1000:rw,nosuid,nodev,noexec,mode=0700"}, nil
}

func prepareLocalNestedSeccomp() (string, error) {
	data, err := capturePodman(false, "info", "--format", "{{json .Host.Security}}")
	if err != nil {
		return "", err
	}
	var security struct {
		SeccompProfilePath string `json:"seccompProfilePath"`
	}
	if json.Unmarshal(data, &security) != nil || !filepath.IsAbs(security.SeccompProfilePath) {
		return "", fmt.Errorf("Podman must report an absolute host seccomp profile path for nested containers")
	}
	data, err = os.ReadFile(security.SeccompProfilePath)
	if err != nil {
		return "", fmt.Errorf("read host seccomp profile: %w", err)
	}
	content, err := nestedSeccompContent(data)
	if err != nil {
		return "", err
	}
	return storeNestedSeccomp(content)
}

func nestedSeccompContent(data []byte) ([]byte, error) {
	var profile map[string]json.RawMessage
	if json.Unmarshal(data, &profile) != nil || profile == nil {
		return nil, fmt.Errorf("invalid host seccomp profile")
	}
	var defaultAction string
	if json.Unmarshal(profile["defaultAction"], &defaultAction) != nil || !slices.Contains([]string{"SCMP_ACT_ERRNO", "SCMP_ACT_KILL", "SCMP_ACT_KILL_PROCESS", "SCMP_ACT_KILL_THREAD", "SCMP_ACT_TRAP"}, defaultAction) {
		return nil, fmt.Errorf("nested Podman requires a deny-by-default host seccomp profile")
	}
	var rules []map[string]json.RawMessage
	if json.Unmarshal(profile["syscalls"], &rules) != nil || rules == nil {
		return nil, fmt.Errorf("invalid host seccomp syscall rules")
	}
	nested := []string{"sethostname", "setdomainname", "setns"}
	retained := make([]map[string]json.RawMessage, 0, len(rules)+1)
	for _, rule := range rules {
		var names []string
		if json.Unmarshal(rule["names"], &names) != nil || names == nil {
			return nil, fmt.Errorf("invalid host seccomp syscall names")
		}
		filtered := make([]string, 0, len(names))
		for _, name := range names {
			if !slices.Contains(nested, name) {
				filtered = append(filtered, name)
			}
		}
		if len(filtered) > 0 {
			rule["names"], _ = json.Marshal(filtered)
			retained = append(retained, rule)
		}
	}
	names, _ := json.Marshal(nested)
	// Remove conditional denials before adding ALLOW: an ERRNO rule would win.
	retained = append(retained, map[string]json.RawMessage{"names": names, "action": json.RawMessage(`"SCMP_ACT_ALLOW"`)})
	profile["syscalls"], _ = json.Marshal(retained)
	content, err := json.MarshalIndent(profile, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(content, '\n'), nil
}

func storeNestedSeccomp(content []byte) (string, error) {
	state, err := stateDir()
	if err != nil {
		return "", err
	}
	if Version == "" || filepath.Base(Version) != Version || Version == "." || Version == ".." {
		return "", fmt.Errorf("invalid version for seccomp state path")
	}
	directory := filepath.Join(state, "seccomp", Version)
	if err := privateStateDirectory(state, directory); err != nil {
		return "", err
	}
	target := filepath.Join(directory, "nested-podman.json")
	if info, err := os.Lstat(target); err == nil {
		if !info.Mode().IsRegular() || pathRedirected(info) {
			return "", fmt.Errorf("seccomp state must be a regular file: %s", target)
		}
		if err := securePrivatePath(target); err != nil {
			return "", err
		}
	} else if !os.IsNotExist(err) {
		return "", err
	}
	if previous, err := os.ReadFile(target); err == nil && bytes.Equal(previous, content) {
		return target, nil
	}
	temporary, err := os.CreateTemp(directory, ".nested-seccomp-")
	if err != nil {
		return "", err
	}
	defer os.Remove(temporary.Name())
	if err := securePrivatePath(temporary.Name()); err != nil {
		temporary.Close()
		return "", err
	}
	if _, err := temporary.Write(content); err != nil {
		temporary.Close()
		return "", err
	}
	if err := temporary.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(temporary.Name(), target); err != nil {
		return "", err
	}
	return target, nil
}
