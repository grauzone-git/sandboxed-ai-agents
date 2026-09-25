// Package cli implements the standalone host controller.
package cli

import (
	"errors"
	"fmt"
	"os"
	"os/exec"

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
		fmt.Fprintln(os.Stdout, "Usage: sandboxed-agents NAME COMMAND [PARAMETERS]\n       sandboxed-agents version|list\n       sandboxed-agents build [additional Podman build arguments]\n\nCommand names cannot be used as sandbox names.\nThis preview implements version, build, list, up, start, stop, restart, remove, shell, check, and check-full.\n       sandboxed-agents NAME up [WORKSPACE [PORT]] --agents LIST [--tools LIST] [--cpus N] [--memory SIZE] [--ssh-port PORT]\n       sandboxed-agents NAME start|stop|restart|shell|check|check-full\n       sandboxed-agents NAME remove [--volumes]\nOther commands are not available in this preview.\nSANDBOX_CONTROLLER selects the owner group (default: default).")
		return nil
	case "version":
		fmt.Fprintf(os.Stdout, "sandboxed-agents version %s\ncommit %s\nassets %s\n", Version, Commit, sandboxassets.Hash())
		return nil
	case "up", "start", "stop", "restart", "remove", "shell", "check", "check-full":
		return lifecycle(command, name, parameters)
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
		return fmt.Errorf("%s is not available in this preview", command)
	}
}
