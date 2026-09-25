// Package cli implements the standalone host controller.
package cli

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"

	sandboxassets "github.com/grauzone-git/sandboxed-ai-agents"
)

var Version = "0.1.0-dev"
var Commit = "unknown"

func Run(args []string) int {
	if err := run(args); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		var exit *exec.ExitError
		if errors.As(err, &exit) && exit.ExitCode() > 0 {
			return exit.ExitCode()
		}
		return 1
	}
	return 0
}

func run(args []string) error {
	command, name, parameters, err := parse(args)
	if err != nil {
		return err
	}
	switch command {
	case "help":
		fmt.Fprintln(os.Stdout, "Usage: sandboxed-agents NAME COMMAND [PARAMETERS]\n       sandboxed-agents version|list\n       sandboxed-agents build [additional Podman build arguments]\n\nCommand names cannot be used as sandbox names.\nThis preview implements version, build, list, up, start, stop, restart, remove, shell, check, check-full, ssh-config, fingerprint, agent/tool management, sessions, and update.\n       sandboxed-agents NAME up [WORKSPACE [PORT]] --agents LIST [--tools LIST] [--cpus N] [--memory SIZE] [--ssh-port PORT] [--capabilities podman|none] [--ssh-config]\n       sandboxed-agents NAME start|restart [--ssh-config]\n       sandboxed-agents NAME stop|shell|check|check-full|fingerprint\n       sandboxed-agents NAME ssh-config [--install]\n       sandboxed-agents NAME remove [--volumes]\n       sandboxed-agents NAME update [--no-build] [--capabilities podman|none]\n       sandboxed-agents update --all [--no-build] [--capabilities podman|none]\n       sandboxed-agents NAME agents|tools [list|check|set|enable|disable|update LIST]\n       sandboxed-agents NAME agents login codex|claude|copilot|opencode|hermes\n       sandboxed-agents NAME tools login github\n       sandboxed-agents NAME run AGENT [arguments...]\n       sandboxed-agents NAME tool TOOL [arguments...]\n       sandboxed-agents NAME "+strings.Join(sessionNames(), "|")+"\nOther commands are not available in this preview.\nSANDBOX_CONTROLLER selects the owner group (default: default).")
		return nil
	case "version":
		fmt.Fprintf(os.Stdout, "sandboxed-agents version %s\ncommit %s\nassets %s\n", Version, Commit, sandboxassets.Hash())
		return nil
	case "up", "start", "stop", "restart", "remove", "shell", "check", "check-full":
		return lifecycle(command, name, parameters)
	case "agents", "tools", "run", "tool":
		return dispatch(command, name, parameters)
	case "update":
		return update(name, parameters)
	case "ssh-config":
		return sshConfigCommand(name, parameters)
	case "fingerprint":
		if len(parameters) != 0 {
			return fmt.Errorf("usage: sandboxed-agents NAME fingerprint")
		}
		return fingerprint(name)
	case "list":
		if err := requirePodman(); err != nil {
			return err
		}
		return list()
	case "build":
		if err := requirePodman(); err != nil {
			return err
		}
		return build(parameters)
	default:
		if _, exists := sessionManagers[command]; exists {
			return dispatch(command, name, parameters)
		}
		return fmt.Errorf("%s is not available in this preview", command)
	}
}
