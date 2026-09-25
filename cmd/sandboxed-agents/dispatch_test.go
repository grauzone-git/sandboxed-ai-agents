package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestAgentAndToolListsUseOwnedContainerManager(t *testing.T) {
	for _, kind := range []string{"agents", "tools"} {
		for _, operation := range [][]string{nil, {"list"}, {"check"}} {
			args := append([]string{"agent01", kind}, operation...)
			command, log := lifecycleCommand(t, args...)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v: %s", err, output)
			}
			verb := "list"
			if len(operation) > 0 {
				verb = operation[0]
			}
			calls := lifecycleCalls(t, log)
			want := []string{"exec", "--user", "1000:1000", "--workdir", "/workspace", "agent01", "/usr/local/bin/sandbox-" + kind, verb}
			if !reflect.DeepEqual(calls[len(calls)-1], want) {
				t.Fatalf("manager command: %v; want %v", calls, want)
			}
		}
	}
}

func TestAgentAndToolMutationsValidateAndPreserveUpdateAll(t *testing.T) {
	for _, test := range []struct{ kind, operation, input, expected string }{
		{"agents", "set", " codex@1.2.3 , claude ", "codex@1.2.3,claude"},
		{"tools", "enable", "t3@latest", "t3@latest"},
		{"agents", "disable", "hermes", "hermes"},
		{"tools", "set", "none", "none"},
		{"agents", "update", "all", "all"},
		{"tools", "update", "all", "all"},
	} {
		command, log := lifecycleCommand(t, "agent01", test.kind, test.operation, test.input)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s", err, output)
		}
		calls := lifecycleCalls(t, log)
		want := []string{"exec", "--user", "1000:1000", "--workdir", "/workspace", "agent01", "/usr/local/bin/sandbox-" + test.kind, test.operation, test.expected}
		if !reflect.DeepEqual(calls[len(calls)-1], want) {
			t.Fatalf("manager mutation: %v; want %v", calls, want)
		}
	}
}

func TestManagedLoginsUseInteractivePodmanWithoutSSH(t *testing.T) {
	for _, test := range []struct{ kind, target string }{
		{"agents", "codex"}, {"agents", "claude"}, {"agents", "copilot"},
		{"agents", "opencode"}, {"agents", "hermes"}, {"tools", "github"},
	} {
		command, log := lifecycleCommand(t, "agent01", test.kind, "login", test.target)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s", err, output)
		}
		calls := lifecycleCalls(t, log)
		want := []string{"exec", "-i", "--user", "1000:1000", "--workdir", "/workspace", "agent01", "/usr/local/bin/sandbox-" + test.kind, "login", test.target}
		if !reflect.DeepEqual(calls[len(calls)-1], want) {
			t.Fatalf("login transport: %v; want %v", calls, want)
		}
	}
}

func TestRunAndToolPreserveArgumentsAsDistinctValues(t *testing.T) {
	for _, test := range []struct{ command, kind, target string }{
		{"run", "agents", "codex"}, {"tool", "tools", "t3"},
	} {
		extra := []string{"--prompt", "text with spaces", `quote"value`, "$(printf should-remain-literal)", "--", ""}
		args := append([]string{"agent01", test.command, test.target}, extra...)
		command, log := lifecycleCommand(t, args...)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s", err, output)
		}
		calls := lifecycleCalls(t, log)
		want := append([]string{"exec", "-i", "--user", "1000:1000", "--workdir", "/workspace", "agent01", "/usr/local/bin/sandbox-" + test.kind, "run", test.target}, extra...)
		if !reflect.DeepEqual(calls[len(calls)-1], want) {
			t.Fatalf("run arguments: %v; want %v", calls, want)
		}
	}
}

func TestSessionShortcutsUseContainerManagers(t *testing.T) {
	for _, target := range []string{"codex", "claude", "copilot", "opencode", "hermes", "deepseek", "t3"} {
		command, log := lifecycleCommand(t, "agent01", target)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s", err, output)
		}
		kind := "agents"
		if target == "t3" {
			kind = "tools"
		}
		calls := lifecycleCalls(t, log)
		want := []string{"exec", "-i", "--user", "1000:1000", "--workdir", "/workspace", "agent01", "/usr/local/bin/sandbox-" + kind, "session", target}
		if !reflect.DeepEqual(calls[len(calls)-1], want) {
			t.Fatalf("session transport: %v; want %v", calls, want)
		}
	}
}

func TestManagerValidationRejectsUnknownTargetsBeforePodman(t *testing.T) {
	for _, args := range [][]string{
		{"agents", "list", "extra"}, {"tools", "check", "extra"},
		{"agents", "set"}, {"agents", "set", "codex", "extra"},
		{"agents", "enable", "unknown"}, {"agents", "disable", "codex,codex"},
		{"agents", "update", "codex@bad version"}, {"tools", "set", "codex"},
		{"tools", "enable", "hermes-dashboard@latest"}, {"agents", "erase", "all"},
		{"agents", "login"}, {"agents", "login", "codex", "--extra"},
		{"agents", "login", "codex@latest"}, {"agents", "login", "deepseek"},
		{"tools", "login", "gh"}, {"tools", "login", "claude"},
		{"run"}, {"tool"}, {"run", "unknown"}, {"tool", "codex"},
		{"run", "codex@latest"}, {"codex", "extra"}, {"t3", "extra"},
	} {
		command, log := lifecycleCommand(t, append([]string{"agent01"}, args...)...)
		output, err := command.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "Error:") {
			t.Fatalf("invalid arguments accepted %v: %v %s", args, err, output)
		}
		if calls := lifecycleCalls(t, log); len(calls) != 0 {
			t.Fatalf("validation reached Podman: %v", calls)
		}
	}
}

func TestManagerCommandsRefuseForeignOwnersAndPropagateFailures(t *testing.T) {
	for _, args := range [][]string{
		{"agents", "list"}, {"tools", "set", "none"}, {"agents", "login", "codex"},
		{"run", "codex", "--version"}, {"tool", "t3", "--help"}, {"hermes"},
	} {
		for _, foreign := range []bool{false, true} {
			command, log := lifecycleCommand(t, append([]string{"agent01"}, args...)...)
			if foreign {
				command.Env = append(command.Env, "SANDBOX_CONTAINER_OWNER=other-group")
			} else {
				command.Env = append(command.Env, "SANDBOX_LIFECYCLE_FAIL=exec")
			}
			output, err := command.CombinedOutput()
			if foreign {
				if err == nil || !strings.Contains(string(output), "not owned") {
					t.Fatalf("foreign manager access %v: %v %s", args, err, output)
				}
				for _, call := range lifecycleCalls(t, log) {
					if call[0] == "exec" {
						t.Fatalf("foreign manager executed: %v", call)
					}
				}
			} else if exit, ok := err.(*exec.ExitError); !ok || exit.ExitCode() != 27 {
				t.Fatalf("manager exit not propagated %v: %v %s", args, err, output)
			}
			for _, directory := range []string{".ssh", "state", "local"} {
				if _, err := os.Stat(filepath.Join(command.Dir, directory)); !os.IsNotExist(err) {
					t.Fatalf("manager command wrote host state %s: %v", directory, err)
				}
			}
		}
	}
}
