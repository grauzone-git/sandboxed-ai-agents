package cli

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
)

var namedCommands = strings.Fields("up start stop restart remove shell ssh-config check check-full fingerprint agents tools run tool service forward update adopt copilot claude codex hermes opencode deepseek t3")
var unnamedCommands = []string{"version", "build", "list", "update", "adopt"}
var namePattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]*$`)

func isHelp(s string) bool { return s == "help" || s == "--help" || s == "-h" }

func parse(args []string) (command, name string, parameters []string, err error) {
	if len(args) == 0 || isHelp(args[0]) {
		return "help", "", nil, nil
	}
	first, rest := args[0], args[1:]
	reserved := slices.Contains(namedCommands, first) || slices.Contains(unnamedCommands, first) || first == "azdo"
	if reserved && len(rest) > 0 && slices.Contains(namedCommands, rest[0]) {
		return "", "", nil, fmt.Errorf("%q is a command name and cannot be used as a sandbox name", first)
	}
	if slices.Contains(unnamedCommands, first) {
		if (first == "update" || first == "adopt") && !slices.Contains(rest, "--all") && !slices.Contains(rest, "--help") && !slices.Contains(rest, "-h") {
			return "", "", nil, fmt.Errorf("use sandboxed-agents NAME %s, or sandboxed-agents %s --all", first, first)
		}
		if (first == "list" || first == "version") && len(rest) > 0 {
			return "", "", nil, fmt.Errorf("Usage: sandboxed-agents %s", first)
		}
		return first, "", rest, nil
	}
	if first == "azdo" {
		return "", "", nil, fmt.Errorf("the azdo --pat-env command was removed; use sandboxed-agents NAME tools setup azdo --persist")
	}
	if slices.Contains(namedCommands, first) {
		return "", "", nil, fmt.Errorf("commands take the sandbox name first: sandboxed-agents NAME %s", first)
	}
	if !namePattern.MatchString(first) {
		return "", "", nil, fmt.Errorf("use an alphanumeric container name (plus _, ., -)")
	}
	if len(rest) == 0 {
		return "", "", nil, fmt.Errorf("use sandboxed-agents NAME COMMAND; run sandboxed-agents --help")
	}
	command = rest[0]
	if isHelp(command) {
		return "help", "", nil, nil
	}
	if slices.Contains([]string{"build", "list", "version"}, command) {
		return "", "", nil, fmt.Errorf("sandboxed-agents %s does not take a sandbox name", command)
	}
	if !slices.Contains(namedCommands, command) {
		return "", "", nil, fmt.Errorf("Unknown command: %s. Run sandboxed-agents --help", command)
	}
	if (command == "update" || command == "adopt") && slices.Contains(rest[1:], "--all") {
		return "", "", nil, fmt.Errorf("use sandboxed-agents %s --all without a sandbox name", command)
	}
	return command, first, rest[1:], nil
}
