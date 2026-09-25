//go:build windows

package main

import (
	"encoding/json"
	"golang.org/x/sys/windows"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestWindowsLifecyclePinsSelectedWSLMachine(t *testing.T) {
	command, _ := lifecycleCommand(t, "agent01", "up", "--agents", "codex")
	log := filepath.Join(t.TempDir(), "runtime.jsonl")
	command.Env = append(command.Env, "SANDBOX_WINDOWS_RUNTIME_LOG="+log)
	command.Env = append(command.Env, "CONTAINER_CONNECTION=work-machine")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "system" || call[0] == "machine" {
			continue
		}
		if len(call) < 3 || call[0] != "--connection" || call[1] != "work-machine" {
			t.Fatalf("unselected engine call: %v", call)
		}
	}
}

func TestWindowsRuntimeRejectsMisconfiguredMachinesWithoutProvisioning(t *testing.T) {
	for _, fault := range []string{"missing", "rootful", "remote", "stopped", "hyperv", "endpoint", "old-client", "old-server", "rootless", "arch", "cgroups"} {
		t.Run(fault, func(t *testing.T) {
			command, _ := lifecycleCommand(t, "agent01", "up", "--agents", "codex")
			log := filepath.Join(t.TempDir(), "runtime.jsonl")
			command.Env = append(command.Env, "SANDBOX_WINDOWS_RUNTIME_LOG="+log)
			command.Env = append(command.Env, "SANDBOX_FAKE_MACHINE_FAULT="+fault)
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("misconfigured %s accepted: %s", fault, output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if slices.Contains(call, "run") || slices.Contains(call, "create") || slices.Contains(call, "start") {
					t.Fatalf("runtime preflight provisioned resource: %v", call)
				}
			}
		})
	}
}

func TestWindowsWorkspaceRejectsAliasesOfProtectedPaths(t *testing.T) {
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex")
	var home string
	for _, entry := range command.Env {
		if strings.HasPrefix(entry, "USERPROFILE=") {
			home = strings.TrimPrefix(entry, "USERPROFILE=")
		}
	}
	protected := filepath.Join(home, ".ssh")
	if err := os.MkdirAll(protected, 0700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(t.TempDir(), "alias")
	if output, err := exec.Command("cmd", "/c", "mklink", "/J", alias, protected).CombinedOutput(); err != nil {
		t.Fatalf("junction: %v %s", err, output)
	}
	paths := []string{alias, strings.ToUpper(protected), `\\localhost\C$\Windows`, `\\?\` + protected}
	pointer, err := windows.UTF16PtrFromString(protected)
	if err != nil {
		t.Fatal(err)
	}
	buffer := make([]uint16, 32768)
	count, err := windows.GetShortPathName(pointer, &buffer[0], uint32(len(buffer)))
	if err != nil {
		t.Fatal(err)
	}
	paths = append(paths, windows.UTF16ToString(buffer[:count]))
	for _, path := range paths {
		candidate := exec.Command(command.Path, "agent01", "up", path, "--agents", "codex")
		candidate.Env = command.Env
		candidate.Dir = command.Dir
		output, err := candidate.CombinedOutput()
		if err == nil {
			t.Fatalf("protected alias accepted %s: %s", path, output)
		}
	}
	if calls := lifecycleCalls(t, log); len(calls) != 0 {
		t.Fatalf("alias rejection reached Podman: %v", calls)
	}
}

func TestWindowsNestedPodmanStagesEnginePolicyInsideMachine(t *testing.T) {
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
	policyLog := filepath.Join(t.TempDir(), "guest-policy.json")
	command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE=guest", "SANDBOX_GUEST_PROFILE_LOG="+policyLog)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	policy, err := os.ReadFile(policyLog)
	if err != nil {
		t.Fatal(err)
	}
	var stored map[string]any
	if err := json.Unmarshal(policy, &stored); err != nil {
		t.Fatal(err)
	}
	if stored["defaultAction"] != "SCMP_ACT_ERRNO" || !strings.Contains(string(policy), "setns") {
		t.Fatalf("wrong guest policy: %s", policy)
	}
	found := false
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "run" {
			for _, arg := range call {
				if strings.HasPrefix(arg, "--security-opt=seccomp=") {
					found = true
					if !strings.HasPrefix(arg, "--security-opt=seccomp=/home/user/.local/share/sandboxed-agents/seccomp/") {
						t.Fatalf("host policy path sent to guest: %s", arg)
					}
				}
				if strings.Contains(arg, ":/workspace") && !strings.HasPrefix(arg, "agent01-workspace:") {
					t.Fatalf("unexpected policy bind: %v", call)
				}
			}
		}
	}
	if !found {
		t.Fatal("guest seccomp not selected")
	}
}

func TestWindowsNestedPodmanRequiresGuestDevicesAndSubordinateIDs(t *testing.T) {
	for _, fault := range []string{"devices", "subids"} {
		t.Run(fault, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
			command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE=guest", "SANDBOX_FAKE_MACHINE_FAULT="+fault)
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("unsafe guest accepted: %s", output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "build" || call[0] == "run" || (call[0] == "volume" && call[1] == "create") {
					t.Fatalf("provisioned before guest guards: %v", call)
				}
			}
		})
	}
}

func TestWindowsConnectionAliasSelectsMatchingMachineEndpoint(t *testing.T) {
	command, _ := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
	log := filepath.Join(t.TempDir(), "runtime.jsonl")
	command.Env = append(command.Env, "CONTAINER_CONNECTION=renamed-connection", "SANDBOX_WINDOWS_RUNTIME_LOG="+log, "SANDBOX_CAPABILITY_PROFILE=guest")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("connection alias rejected: %v %s", err, output)
	}
	guest := false
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "--connection" && call[1] != "renamed-connection" {
			t.Fatalf("engine call lost connection alias: %v", call)
		}
		if len(call) > 2 && call[0] == "machine" && call[1] == "ssh" {
			guest = true
			if call[2] != "work-machine" {
				t.Fatalf("guest call targeted alias instead of machine: %v", call)
			}
		}
	}
	if !guest {
		t.Fatal("nested setup did not reach selected guest")
	}
}
