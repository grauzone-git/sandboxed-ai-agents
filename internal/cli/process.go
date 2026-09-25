package cli

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
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
	cmd := exec.Command("podman", args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	return cmd.Run()
}

func capturePodman(quiet bool, args ...string) ([]byte, error) {
	cmd := exec.Command("podman", args...)
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
		return fmt.Errorf("Podman is not installed/on PATH. Install Podman 5+, then retry")
	}
	output, err := capturePodman(false, "info", "--format", "{{.Host.Security.Rootless}}")
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(output)) != "true" {
		return fmt.Errorf("Podman must be rootless")
	}
	return nil
}
