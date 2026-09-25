package cli

import (
	"fmt"
	"sort"
)

var runManagers = map[string]string{"run": "agents", "tool": "tools"}

var sessionManagers = map[string]string{
	"copilot": "agents", "claude": "agents", "codex": "agents",
	"hermes": "agents", "opencode": "agents", "deepseek": "agents", "t3": "tools",
}

func sessionNames() []string {
	names := make([]string, 0, len(sessionManagers))
	for name := range sessionManagers {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func managementArguments(command string, parameters []string) ([]string, bool, error) {
	interactive := false
	operation := "list"
	if len(parameters) > 0 {
		operation = parameters[0]
	}
	arguments := []string{operation}
	switch operation {
	case "list", "check":
		if len(parameters) > 1 {
			return nil, false, fmt.Errorf("unexpected %s %s arguments", command, operation)
		}
	case "set", "enable", "disable", "update":
		if len(parameters) != 2 {
			return nil, false, fmt.Errorf("use sandboxed-agents NAME %s %s LIST", command, operation)
		}
		selected := parameters[1]
		// The manager interprets update all as the currently enabled selection.
		if operation != "update" || selected != "all" {
			var err error
			selected, err = selection(selected, command)
			if err != nil {
				return nil, false, err
			}
		}
		arguments = append(arguments, selected)
	case "login":
		if len(parameters) != 2 {
			return nil, false, fmt.Errorf("use sandboxed-agents NAME %s login TARGET", command)
		}
		target := parameters[1]
		// GitHub login uses the image's built-in CLI, not a catalog-managed tool.
		supported := target == "github"
		if command == "agents" {
			catalog, err := readCatalog(command)
			if err != nil {
				return nil, false, err
			}
			supported = len(catalog[target].Login) > 0
		}
		if !supported {
			return nil, false, fmt.Errorf("managed %s login is not supported for %q", command, target)
		}
		arguments = append(arguments, target)
		interactive = true
	default:
		return nil, false, fmt.Errorf("unknown %s operation: %s", command, operation)
	}
	return arguments, interactive, nil
}

func dispatch(command, name string, parameters []string) error {
	kind := command
	interactive := true
	var arguments []string
	switch command {
	case "agents", "tools":
		var err error
		arguments, interactive, err = managementArguments(command, parameters)
		if err != nil {
			return err
		}
	case "run", "tool":
		kind = runManagers[command]
		if len(parameters) == 0 {
			return fmt.Errorf("use sandboxed-agents NAME %s TARGET [arguments...]", command)
		}
		if err := validateManagerTarget(kind, parameters[0]); err != nil {
			return err
		}
		arguments = append([]string{"run"}, parameters...)
	default:
		kind = sessionManagers[command]
		if len(parameters) != 0 {
			return fmt.Errorf("session %s takes no arguments; use run or tool for command arguments", command)
		}
		if err := validateManagerTarget(kind, command); err != nil {
			return err
		}
		arguments = []string{"session", command}
	}
	if err := requirePodman(); err != nil {
		return err
	}
	if err := requireOwned(name); err != nil {
		return err
	}
	return podman(managerCommand(name, kind, interactive, arguments...)...)
}

func validateManagerTarget(kind, target string) error {
	catalog, err := readCatalog(kind)
	if err != nil {
		return err
	}
	if _, exists := catalog[target]; !exists {
		return fmt.Errorf("unknown %s target: %q", kind, target)
	}
	return nil
}
