package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAdoptionRefusesMismatchedCheckoutBeforeMutation(t *testing.T) {
	source := filepath.Join(t.TempDir(), "checkout")
	command, log := lifecycleCommand(t, "agent01", "adopt", "--from", source)
	fixture := updateFixture()
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source + "-different"
	state := filepath.Join(t.TempDir(), "containers.json")
	data, _ := json.Marshal(map[string]any{"agent01": fixture})
	if err := os.WriteFile(state, data, 0600); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "owned") {
		t.Fatalf("expected source ownership refusal: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		switch call[0] {
		case "stop", "rename", "create", "run", "rm", "build":
			t.Fatalf("mutated mismatched checkout: %v", call)
		}
	}
}

func TestAdoptionChangesOnlyContainerOwnershipAndKeepsVolumes(t *testing.T) {
	source := filepath.Join(t.TempDir(), "checkout with spaces")
	command, log := lifecycleCommand(t, "agent01", "adopt", "--from", source)
	fixture := updateFixture()
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
	state := filepath.Join(t.TempDir(), "containers.json")
	data, _ := json.Marshal(map[string]any{"agent01": fixture})
	os.WriteFile(state, data, 0600)
	command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	data, _ = os.ReadFile(state)
	var final map[string]map[string]any
	json.Unmarshal(data, &final)
	if len(final) != 1 || final["agent01"]["Id"] != newUpdateID {
		t.Fatalf("unexpected containers: %s", data)
	}
	labels := final["agent01"]["Config"].(map[string]any)["Labels"].(map[string]any)
	if labels["io.sandboxed-agents.project"] != "default" || labels["io.sandboxed-agents.adopted-from"] != source {
		t.Fatalf("ownership labels: %v", labels)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "volume" && call[1] != "inspect" {
			t.Fatalf("changed volume: %v", call)
		}
	}
}

func TestAdoptionFailureRestoresCheckoutContainer(t *testing.T) {
	source := filepath.Join(t.TempDir(), "checkout")
	command, log := lifecycleCommand(t, "agent01", "adopt", "--from", source)
	fixture := updateFixture()
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
	state := filepath.Join(t.TempDir(), "containers.json")
	data, _ := json.Marshal(map[string]any{"agent01": fixture})
	os.WriteFile(state, data, 0600)
	command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source, "SANDBOX_UPDATE_FAIL=tools-boot")
	if output, err := command.CombinedOutput(); err == nil {
		t.Fatalf("expected rollback: %s", output)
	}
	data, _ = os.ReadFile(state)
	var final map[string]map[string]any
	json.Unmarshal(data, &final)
	if len(final) != 1 || final["agent01"]["Id"] != oldUpdateID {
		t.Fatalf("rollback lost original: %s", data)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "volume" && call[1] != "inspect" {
			t.Fatalf("changed volume: %v", call)
		}
	}
}

func TestAdoptionMovesExistingSSHKeysAndPreservesUnrelatedFiles(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	source := filepath.Join(t.TempDir(), "checkout")
	managed := filepath.Join(root, "state", "sandboxed-agents", "ssh")
	legacy := filepath.Join(root, "home", ".ssh", "sanboxed-agents", "agent01")
	if err := os.MkdirAll(legacy, 0700); err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{"id_ed25519", "id_ed25519.pub", "known_hosts"} {
		if err := os.Rename(filepath.Join(managed, "agent01", base), filepath.Join(legacy, base)); err != nil {
			t.Fatal(err)
		}
	}
	key, _ := os.ReadFile(filepath.Join(legacy, "id_ed25519"))
	os.Remove(filepath.Join(managed, "agent01", "owner.json"))
	os.Remove(filepath.Join(managed, "agent01"))
	os.Remove(filepath.Join(managed, "agent01.conf"))
	oldEntry := filepath.Join(legacy, "agent01.conf")
	os.WriteFile(oldEntry, []byte("Host agent01\n HostName 127.0.0.1\n"), 0600)
	os.WriteFile(filepath.Join(legacy, "personal-note"), []byte("keep"), 0600)
	config := filepath.Join(root, "home", ".ssh", "config")
	os.WriteFile(config, []byte("Include \""+filepath.ToSlash(oldEntry)+"\"\nHost personal\n HostName personal.example\n"), 0600)
	state := filepath.Join(t.TempDir(), "containers.json")
	fixture := updateFixture()
	fixture["HostConfig"].(map[string]any)["PortBindings"].(map[string]any)["2222/tcp"].([]map[string]string)[0]["HostPort"] = "2222"
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
	data, _ := json.Marshal(map[string]any{"agent01": fixture})
	os.WriteFile(state, data, 0600)
	adoption := cloneSSHCommand(command, "agent01", "adopt", "--from", source)
	public, err := os.ReadFile(filepath.Join(legacy, "id_ed25519.pub"))
	if err != nil {
		t.Fatal(err)
	}
	adoption.Env = append(adoption.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source, "SANDBOX_TEST_AUTHORIZED_KEYS="+string(public))
	if output, err := adoption.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	saved, err := os.ReadFile(filepath.Join(managed, "agent01", "id_ed25519"))
	if err != nil || string(saved) != string(key) {
		t.Fatalf("key not preserved: %v", err)
	}
	for _, base := range []string{"id_ed25519", "id_ed25519.pub", "known_hosts", "agent01.conf"} {
		if _, err := os.Stat(filepath.Join(legacy, base)); !os.IsNotExist(err) {
			t.Fatalf("legacy file retained: %s", base)
		}
	}
	if data, err := os.ReadFile(filepath.Join(legacy, "personal-note")); err != nil || string(data) != "keep" {
		t.Fatal("unrelated file lost")
	}
	data, _ = os.ReadFile(config)
	if strings.Contains(string(data), filepath.ToSlash(oldEntry)) || !strings.Contains(string(data), "personal.example") || strings.Count(string(data), "Include") != 1 {
		t.Fatalf("wrong Include migration: %s", data)
	}
}

func TestListShowsCheckoutAdoptionWithoutExecutingInForeignContainer(t *testing.T) {
	command, log := lifecycleCommand(t, "list")
	source := filepath.Join(t.TempDir(), "checkout")
	fixture := updateFixture()
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
	state := filepath.Join(t.TempDir(), "containers.json")
	data, _ := json.Marshal(map[string]any{"agent01": fixture})
	os.WriteFile(state, data, 0600)
	command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state)
	output, err := command.CombinedOutput()
	if err != nil || !strings.Contains(string(output), "agent01 adopt --from") {
		t.Fatalf("missing adoption guidance: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] != "info" && call[0] != "ps" && call[0] != "container" {
			t.Fatalf("not read-only: %v", call)
		}
	}
}

func TestAdoptedContainerVolumeRemovalUsesOnlyItsLabelRecord(t *testing.T) {
	for _, matching := range []bool{true, false} {
		source := filepath.Join(t.TempDir(), "checkout")
		command, log := lifecycleCommand(t, "agent01", "remove", "--volumes")
		fixture := updateFixture()
		fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.adopted-from"] = source
		state := filepath.Join(t.TempDir(), "containers.json")
		data, _ := json.Marshal(map[string]any{"agent01": fixture})
		os.WriteFile(state, data, 0600)
		volumeOwner := source
		if !matching {
			volumeOwner += "-other"
		}
		command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+volumeOwner)
		output, err := command.CombinedOutput()
		if matching && err != nil {
			t.Fatalf("adopted volume refused: %v %s", err, output)
		}
		if !matching && err == nil {
			t.Fatalf("foreign volume accepted: %s", output)
		}
		deleted := 0
		for _, call := range lifecycleCalls(t, log) {
			if !matching && (call[0] == "stop" || call[0] == "rm" || (call[0] == "volume" && call[1] == "rm")) {
				t.Fatalf("foreign owner mutated: %v", call)
			}
			if call[0] == "volume" && call[1] == "rm" {
				deleted++
			}
		}
		if matching && deleted != 3 {
			t.Fatalf("did not delete allowed volumes: %v", lifecycleCalls(t, log))
		}
	}
}

func TestAdoptionAllPreflightsEveryOwnerAndNeverAdoptsImplicitly(t *testing.T) {
	source := filepath.Join(t.TempDir(), "checkout")
	for _, args := range [][]string{{"adopt", "--all", "--from", source}, {"agent01", "start"}} {
		command, log := lifecycleCommand(t, args...)
		first := updateFixture()
		first["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
		other := updateFixture()
		other["Name"] = "agent02"
		other["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source + "-other"
		state := filepath.Join(t.TempDir(), "containers.json")
		data, _ := json.Marshal(map[string]any{"agent01": first, "agent02": other})
		os.WriteFile(state, data, 0600)
		command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source)
		if output, err := command.CombinedOutput(); err == nil {
			t.Fatalf("foreign owner accepted: %s", output)
		}
		for _, call := range lifecycleCalls(t, log) {
			switch call[0] {
			case "stop", "start", "rename", "rm", "create", "build":
				t.Fatalf("changed foreign resources: %v", call)
			}
		}
		after, _ := os.ReadFile(state)
		if string(after) != string(data) {
			t.Fatal("container state changed")
		}
	}
}

func TestAdoptionAllSelectsOnlyExactCheckoutAndReportsCompletion(t *testing.T) {
	for _, empty := range []bool{false, true} {
		t.Run(map[bool]string{false: "selected", true: "empty"}[empty], func(t *testing.T) {
			source := filepath.Join(t.TempDir(), "checkout")
			command, log := lifecycleCommand(t, "adopt", "--all", "--from", source)
			fixtures := map[string]any{}
			for i, name := range []string{"agent01", "agent02", "foreign-checkout", "foreign-group"} {
				fixture := updateFixture()
				fixture["Name"] = name
				fixture["Id"] = strings.Repeat(string(rune('d'+i)), 64)
				for _, mount := range fixture["Mounts"].([]map[string]any) {
					mount["Name"] = strings.Replace(mount["Name"].(string), "agent01", name, 1)
				}
				labels := fixture["Config"].(map[string]any)["Labels"].(map[string]string)
				labels["io.sandboxed-agents.project"] = source
				if name == "foreign-checkout" {
					labels["io.sandboxed-agents.project"] = source + "-other"
				}
				if name == "foreign-group" {
					labels["io.sandboxed-agents.project"] = "other-group"
				}
				fixtures[name] = fixture
			}
			state := filepath.Join(t.TempDir(), "containers.json")
			before, _ := json.Marshal(fixtures)
			if err := os.WriteFile(state, before, 0600); err != nil {
				t.Fatal(err)
			}
			names := "agent02\nagent01\nagent01\n"
			if empty {
				names = ""
			}
			command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source, "SANDBOX_ADOPTION_PS_NAMES="+names)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			data, err := os.ReadFile(state)
			if err != nil {
				t.Fatal(err)
			}
			var final map[string]map[string]any
			if err := json.Unmarshal(data, &final); err != nil {
				t.Fatal(err)
			}
			if len(final) != 4 {
				t.Fatalf("unexpected containers: %s", data)
			}
			if empty {
				if string(data) != string(before) || !strings.Contains(string(output), "No sandboxes") {
					t.Fatalf("empty adoption changed state: %s %s", data, output)
				}
			} else {
				if strings.Count(string(output), "Adopted ") != 2 {
					t.Fatalf("wrong completion output: %s", output)
				}
				for _, name := range []string{"agent01", "agent02"} {
					labels := final[name]["Config"].(map[string]any)["Labels"].(map[string]any)
					if labels["io.sandboxed-agents.project"] != "default" || labels["io.sandboxed-agents.adopted-from"] != source {
						t.Fatalf("wrong adopted labels: %v", labels)
					}
				}
			}
			for _, name := range []string{"foreign-checkout", "foreign-group"} {
				original, _ := json.Marshal(fixtures[name])
				retained, _ := json.Marshal(final[name])
				if string(original) != string(retained) {
					t.Fatalf("changed %s", name)
				}
			}
			filtered, builds := false, 0
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "ps" && strings.Contains(strings.Join(call, " "), "label=io.sandboxed-agents.project="+source) {
					filtered = true
				}
				if call[0] == "build" {
					builds++
				}
				if call[0] == "volume" && call[1] != "inspect" {
					t.Fatalf("changed volumes: %v", call)
				}
			}
			if !filtered || (empty && builds != 0) || (!empty && builds != 1) {
				t.Fatalf("wrong selection/build calls: %v", lifecycleCalls(t, log))
			}
		})
	}
}
