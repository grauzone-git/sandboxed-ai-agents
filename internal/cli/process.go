package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const ownerLabel = "io.sandboxed-agents.project"
const versionLabel = "io.sandboxed-agents.version"

func owner() string {
	if value := os.Getenv("SANDBOX_CONTROLLER"); value != "" {
		return value
	}
	return "default"
}

func imageName() string {
	if value := os.Getenv("SANDBOX_IMAGE"); value != "" {
		return value
	}
	return "localhost/agent-sandbox:dev"
}

func podman(args ...string) error {
	cmd := platformPodmanCommand(args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	return cmd.Run()
}

func capturePodman(quiet bool, args ...string) ([]byte, error) {
	cmd := platformPodmanCommand(args...)
	var output bytes.Buffer
	cmd.Stdout = &output
	if !quiet {
		cmd.Stderr = os.Stderr
	}
	err := cmd.Run()
	return output.Bytes(), err
}

func requirePodman() error {
	if _, err := exec.LookPath("podman"); err != nil {
		return fmt.Errorf("%s", podmanPrerequisites)
	}
	return platformPodmanPreflight()
}

func validateController() error {
	value := os.Getenv("SANDBOX_CONTROLLER")
	drive := len(value) >= 2 && value[1] == ':' && ((value[0] >= 'a' && value[0] <= 'z') || (value[0] >= 'A' && value[0] <= 'Z'))
	if value == "." || value == ".." || drive || strings.ContainsAny(value, "/\\\x00\r\n") {
		return fmt.Errorf("SANDBOX_CONTROLLER must be a group name, not a path")
	}
	return nil
}

func checkoutOwner(value string) bool {
	return filepath.IsAbs(value) && !strings.ContainsAny(value, "\x00\r\n")
}

func readVolumeOwner(volume string) (string, error) {
	data, err := capturePodman(false, "volume", "inspect", "--format", `{{index .Labels "`+ownerLabel+`"}}`, volume)
	return strings.TrimSpace(string(data)), err
}

// Only an authoritative, currently owned container can authorize its attached
// checkout volumes. A label alone never authorizes an unrelated retained volume.
func checkVolumeOwner(volume, actual, expected string, container *updateContainer) error {
	if container != nil && container.Config.Labels[ownerLabel] != expected {
		return fmt.Errorf("container is not owned by this configuration")
	}
	if actual == expected {
		return nil
	}
	if container != nil && expected == owner() {
		source := container.Config.Labels[adoptedFromLabel]
		if checkoutOwner(source) && actual == source {
			for _, mount := range container.Mounts {
				if mount.Type == "volume" && mount.Name == volume {
					return nil
				}
			}
		}
	}
	return fmt.Errorf("volume %s belongs to another configuration", volume)
}

func requireContainerVolumeOwned(volume, expected string, container *updateContainer) error {
	actual, err := readVolumeOwner(volume)
	if err != nil {
		return err
	}
	return checkVolumeOwner(volume, actual, expected, container)
}

func requireAttachedVolumeOwned(name, volume string) error {
	actual, err := readVolumeOwner(volume)
	if err != nil {
		return err
	}
	if actual == owner() {
		return nil
	}
	// Ordinary foreign volumes remain forbidden without needing a mount
	// snapshot. The full snapshot below is the authority for an exemption.
	metadata, err := capturePodman(true, "inspect", "--format", "{{json .Config.Labels}}", name)
	var labels map[string]string
	if err != nil || json.Unmarshal(metadata, &labels) != nil || labels[adoptedFromLabel] == "" {
		return checkVolumeOwner(volume, actual, owner(), nil)
	}
	container, err := inspectUpdateContainer(name, owner())
	if err != nil {
		return fmt.Errorf("volume %s belongs to another configuration: %w", volume, err)
	}
	return checkVolumeOwner(volume, actual, owner(), &container)
}
