package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func adoptionState(t *testing.T, containers map[string]any) string {
	t.Helper()
	state := filepath.Join(t.TempDir(), "containers.json")
	data, err := json.Marshal(containers)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(state, data, 0600); err != nil {
		t.Fatal(err)
	}
	return state
}

func TestAdoptionAndListSkipOnlyBackupsWithOriginalVolumeIdentities(t *testing.T) {
	for _, operation := range []string{"list", "adopt"} {
		t.Run(operation, func(t *testing.T) {
			source := filepath.Join(t.TempDir(), "checkout")
			containers := map[string]any{}
			backupName := "agent01-update-backup-abcdef123456"
			ordinaryName := "project-update-backup-abcdef123456"
			for _, name := range []string{"agent01", backupName, ordinaryName} {
				info := updateFixture()
				info["Name"] = name
				info["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
				if name == ordinaryName {
					info["Id"] = strings.Repeat("c", 64)
					for _, mount := range info["Mounts"].([]map[string]any) {
						mount["Name"] = strings.Replace(mount["Name"].(string), "agent01", name, 1)
					}
				}
				if name == backupName {
					info["Id"] = strings.Repeat("d", 64)
					info["State"] = map[string]any{"Running": false, "Status": "exited"}
				}
				containers[name] = info
			}
			state := adoptionState(t, containers)
			args := []string{"list"}
			if operation == "adopt" {
				args = []string{"adopt", "--all", "--from", source}
			}
			command, log := lifecycleCommand(t, args...)
			command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			if operation == "list" {
				if strings.Contains(string(output), backupName+" adopt") || !strings.Contains(string(output), ordinaryName+" adopt") {
					t.Fatalf("incorrect backup classification: %s", output)
				}
				for _, call := range lifecycleCalls(t, log) {
					if call[0] != "info" && call[0] != "ps" && call[0] != "container" {
						t.Fatalf("list mutated state: %v", call)
					}
				}
			} else {
				if strings.Count(string(output), "Adopted ") != 2 {
					t.Fatalf("incorrect adoption count: %s", output)
				}
				data, _ := os.ReadFile(state)
				var final map[string]map[string]any
				if err := json.Unmarshal(data, &final); err != nil {
					t.Fatal(err)
				}
				retained, _ := json.Marshal(final[backupName])
				original, _ := json.Marshal(containers[backupName])
				if string(retained) != string(original) {
					t.Fatalf("backup was changed: %s", retained)
				}
			}
		})
	}
}

func TestAdoptionRejectsUntrustedOrUnattachedVolumeExemptions(t *testing.T) {
	for _, scenario := range []string{"unattached", "checkout-metadata", "relative-metadata"} {
		t.Run(scenario, func(t *testing.T) {
			source := filepath.Join(t.TempDir(), "checkout")
			info := updateFixture()
			labels := info["Config"].(map[string]any)["Labels"].(map[string]string)
			labels["io.sandboxed-agents.adopted-from"] = source
			args := []string{"agent01", "remove", "--volumes"}
			volumeOwner := source
			switch scenario {
			case "unattached":
				info["Mounts"].([]map[string]any)[2] = map[string]any{"Type": "bind", "Source": t.TempDir(), "Destination": "/workspace"}
			case "checkout-metadata":
				labels["io.sandboxed-agents.project"] = source
				labels["io.sandboxed-agents.adopted-from"] = source + "-third-party"
				volumeOwner = source + "-third-party"
				args = []string{"agent01", "adopt", "--from", source}
			case "relative-metadata":
				labels["io.sandboxed-agents.adopted-from"] = "other-group"
				volumeOwner = "other-group"
				args = []string{"agent01", "update", "--no-build"}
			}
			state := adoptionState(t, map[string]any{"agent01": info})
			before, _ := os.ReadFile(state)
			command, log := lifecycleCommand(t, args...)
			command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+volumeOwner)
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("untrusted volume exemption accepted: %s", output)
			}
			after, _ := os.ReadFile(state)
			if string(before) != string(after) {
				t.Fatalf("container changed: %s", after)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "stop" || call[0] == "rm" || call[0] == "rename" || call[0] == "build" || call[0] == "create" || (call[0] == "volume" && call[1] != "inspect" && call[1] != "exists") {
					t.Fatalf("untrusted owner mutated resources: %v", call)
				}
			}
		})
	}
}

func TestControllerGroupsCannotBePaths(t *testing.T) {
	for _, group := range []string{"/srv/project", `C:\project`, `C:project`, `\\server\share`, "team/project", `team\project`, ".", "..", "line\nbreak"} {
		command, log := lifecycleCommand(t, "list")
		command.Env = append(command.Env, "SANDBOX_CONTROLLER="+group)
		output, err := command.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "SANDBOX_CONTROLLER") {
			t.Fatalf("path group accepted: %q %v %s", group, err, output)
		}
		if calls := lifecycleCalls(t, log); len(calls) != 0 {
			t.Fatalf("invalid group reached Podman: %v", calls)
		}
	}
	for _, group := range []string{"", "default", "team blue", "team.v2", "team-2", "_team", "team:blue", "team@work"} {
		command, _ := lifecycleCommand(t, "list")
		state := adoptionState(t, map[string]any{})
		command.Env = append(command.Env, "SANDBOX_CONTROLLER="+group, "SANDBOX_UPDATE_STATE="+state)
		if output, err := command.CombinedOutput(); err != nil {
			t.Fatalf("non-path group rejected: %q %v %s", group, err, output)
		}
	}
}

func TestListRetainsCurrentControllerUpdateBackups(t *testing.T) {
	name := "agent01-update-backup-abcdef123456"
	info := updateFixture()
	info["Name"] = name
	info["State"] = map[string]any{"Running": false, "Status": "exited"}
	state := adoptionState(t, map[string]any{name: info})
	before, err := os.ReadFile(state)
	if err != nil {
		t.Fatal(err)
	}
	command, log := lifecycleCommand(t, "list")
	command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	if !strings.Contains(string(output), name) || !strings.Contains(string(output), "stopped") || strings.Contains(string(output), "adopt --from") {
		t.Fatalf("owned recovery backup not listed correctly: %s", output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] != "info" && call[0] != "ps" && !(len(call) > 1 && call[0] == "container" && call[1] == "inspect") {
			t.Fatalf("list changed resources: %v", call)
		}
	}
	after, err := os.ReadFile(state)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatalf("list changed container state: %s", after)
	}
}
