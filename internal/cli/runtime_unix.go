//go:build !windows

package cli

import (
	"fmt"
	"os/exec"
	"strings"
)

const podmanPrerequisites = "Podman is not installed/on PATH. Install Podman 5+, then retry"

func platformPodmanCommand(args ...string) *exec.Cmd { return exec.Command("podman", args...) }
func platformPodmanPreflight() error {
	output, err := capturePodman(false, "info", "--format", "{{.Host.Security.Rootless}}")
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(output)) != "true" {
		return fmt.Errorf("Podman must be rootless")
	}
	return nil
}

func platformNestedSeccomp() (string, error) { return prepareLocalNestedSeccomp() }
