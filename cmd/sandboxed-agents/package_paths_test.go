package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPackageLaunchPathCannotBecomeWorkspace(t *testing.T) {
	for _, test := range []struct {
		name string
		link bool
	}{{"shim", false}, {"symlink", true}} {
		t.Run(test.name, func(t *testing.T) {
			directory := t.TempDir()
			launch := filepath.Join(directory, "sandboxed-agents")
			if test.link {
				executable, err := os.Executable()
				if err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(executable, launch); err != nil {
					t.Skipf("symlink unavailable: %v", err)
				}
			} else if err := os.WriteFile(launch, []byte("package shim"), 0600); err != nil {
				t.Fatal(err)
			}
			command, log := lifecycleCommand(t, "agent01", "up", directory, "--agents", "codex")
			paths, _ := json.Marshal([]string{launch})
			command.Env = append(command.Env, "SANDBOX_LAUNCH_PATHS="+string(paths))
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), "global install") {
				t.Fatalf("package launch path exposed: %v %s", err, output)
			}
			if calls := lifecycleCalls(t, log); len(calls) != 0 {
				t.Fatalf("unsafe workspace reached Podman: %v", calls)
			}
		})
	}
}

func TestPackageLaunchPathsCannotOverrideBinaryProtection(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	command, log := lifecycleCommand(t, "agent01", "up", filepath.Dir(executable), "--agents", "codex")
	command.Env = append(command.Env, "SANDBOX_LAUNCH_PATHS=[]")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(strings.ToLower(string(output)), "workspace") {
		t.Fatalf("binary path exposed: %v %s", err, output)
	}
	if calls := lifecycleCalls(t, log); len(calls) != 0 {
		t.Fatalf("unsafe workspace reached Podman: %v", calls)
	}
}

func TestPackageLaunchPathsRejectInvalidInputBeforePodman(t *testing.T) {
	for _, problem := range []string{"invalid-json", "relative", "newline", "null", "tab"} {
		t.Run(problem, func(t *testing.T) {
			workspace := t.TempDir()
			launch := filepath.Join(t.TempDir(), "sandboxed-agents")
			switch problem {
			case "relative":
				launch = "relative/sandboxed-agents"
			case "newline":
				launch += "\n"
			case "null":
				launch += "\x00"
			case "tab":
				launch += "\t"
			}
			encoded, err := json.Marshal([]string{launch})
			if err != nil {
				t.Fatal(err)
			}
			message := "package launch paths must be absolute filenames"
			if problem == "invalid-json" {
				encoded = []byte("[")
				message = "invalid package launch paths"
			}
			command, log := lifecycleCommand(t, "agent01", "up", workspace, "--agents", "codex")
			command.Env = append(command.Env, "SANDBOX_LAUNCH_PATHS="+string(encoded))
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), message) {
				t.Fatalf("invalid launch path accepted: %v %s", err, output)
			}
			if calls := lifecycleCalls(t, log); len(calls) != 0 {
				t.Fatalf("invalid launch path reached Podman: %v", calls)
			}
		})
	}
}
