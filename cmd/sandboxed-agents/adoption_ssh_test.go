package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

type adoptionSSHFixture struct {
	command                                           *exec.Cmd
	log, root, legacy, managed, config, state, source string
}

func newAdoptionSSHFixture(t *testing.T) adoptionSSHFixture {
	t.Helper()
	command, log, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("SSH fixture: %v %s", err, output)
	}
	legacy := filepath.Join(root, "home", ".ssh", "sanboxed-agents", "agent01")
	managed := filepath.Join(root, "state", "sandboxed-agents", "ssh")
	if err := os.MkdirAll(legacy, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"id_ed25519", "id_ed25519.pub", "known_hosts"} {
		if err := os.Rename(filepath.Join(managed, "agent01", name), filepath.Join(legacy, name)); err != nil {
			t.Fatal(err)
		}
	}
	os.Remove(filepath.Join(managed, "agent01", "owner.json"))
	os.Remove(filepath.Join(managed, "agent01"))
	if err := os.Rename(filepath.Join(managed, "agent01.conf"), filepath.Join(legacy, "agent01.conf")); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "checkout")
	fixture := updateFixture()
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.project"] = source
	fixture["HostConfig"].(map[string]any)["PortBindings"].(map[string]any)["2222/tcp"] = []map[string]string{{"HostIp": "127.0.0.1", "HostPort": "2222"}}
	state := filepath.Join(t.TempDir(), "containers.json")
	data, _ := json.Marshal(map[string]any{"agent01": fixture})
	if err := os.WriteFile(state, data, 0600); err != nil {
		t.Fatal(err)
	}
	adoption := cloneSSHCommand(command, "agent01", "adopt", "--from", source)
	adoption.Env = append(adoption.Env, "SANDBOX_UPDATE_STATE="+state, "SANDBOX_UPDATE_VOLUME_OWNER="+source)
	public, err := os.ReadFile(filepath.Join(legacy, "id_ed25519.pub"))
	if err != nil {
		t.Fatal(err)
	}
	adoption.Env = append(adoption.Env, "SANDBOX_TEST_AUTHORIZED_KEYS="+string(public))
	config := filepath.Join(root, "home", ".ssh", "config")
	if err := os.WriteFile(config, []byte("Include \""+filepath.ToSlash(filepath.Join(legacy, "agent01.conf"))+"\"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	os.Remove(log)
	return adoptionSSHFixture{adoption, log, root, legacy, managed, config, state, source}
}

func TestAdoptionSSHRemovesOnlyExactLegacyIncludeTokens(t *testing.T) {
	for _, syntax := range []string{"quoted", "equals", "comment", "multiple", "case-sensitive"} {
		t.Run(syntax, func(t *testing.T) {
			fixture := newAdoptionSSHFixture(t)
			old := filepath.ToSlash(filepath.Join(fixture.legacy, "agent01.conf"))
			line := "Include \"" + old + "\"\n"
			retained := "Host personal\r\n    HostName personal.example\r\n"
			switch syntax {
			case "equals":
				line = "  iNcLuDe = \"" + old + "\"\r\n"
			case "comment":
				line = "Include \"" + old + "\" # keep this comment\n"
				retained = "# keep this comment\n" + retained
			case "multiple":
				line = "Include \"" + old + "\" \"/some/other.conf\"\n"
				retained = "Include \"/some/other.conf\"\n" + retained
			case "case-sensitive":
				if runtime.GOOS == "windows" {
					t.Skip("Windows paths are case insensitive")
				}
				line += "Include \"" + strings.ToUpper(old) + "\"\n"
				retained = "Include \"" + strings.ToUpper(old) + "\"\n" + retained
			}
			user := line + "Host personal\r\n    HostName personal.example\r\n"
			os.WriteFile(fixture.config, []byte(user), 0600)
			output, err := fixture.command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			data, err := os.ReadFile(fixture.config)
			if err != nil {
				t.Fatal(err)
			}
			newInclude := `Include "` + filepath.ToSlash(filepath.Join(fixture.managed, "*.conf")) + `"` + "\n"
			if string(data) != newInclude+retained {
				t.Fatalf("unrelated Include content changed: got %q want %q", data, newInclude+retained)
			}
		})
	}
}

func fakeAdoptionSSHMutation(step string) bool {
	if step == "rm" && os.Getenv("SANDBOX_ADOPTION_INTERRUPT") == "cleanup" {
		interruptAdoptionParent()
		os.Exit(27)
	}
	if path := os.Getenv("SANDBOX_ADOPTION_LATE_SYMLINK"); path != "" && step == "tools-boot" {
		data, err := os.ReadFile(path)
		if err != nil {
			panic(err)
		}
		if err := os.WriteFile(path+".retained", data, 0600); err != nil {
			panic(err)
		}
		if err := os.Remove(path); err != nil {
			panic(err)
		}
		if err := os.Symlink(path+".retained", path); err != nil {
			panic(err)
		}
		return true
	}
	if path := os.Getenv("SANDBOX_ADOPTION_CONCURRENT_CONFIG"); path != "" && step == "tools-boot" {
		current, err := os.ReadFile(path)
		if err != nil {
			panic(err)
		}
		if err := os.WriteFile(path, append(current, []byte("Host concurrent\n    HostName retained.example\n")...), 0600); err != nil {
			panic(err)
		}
		if lock := os.Getenv("SANDBOX_ADOPTION_LEGACY_LOCK"); lock != "" {
			if err := os.Mkdir(lock, 0700); err != nil && !os.IsExist(err) {
				panic(err)
			}
		}
		if directory := os.Getenv("SANDBOX_ADOPTION_READONLY_SOURCE"); directory != "" {
			if err := os.Chmod(directory, 0500); err != nil {
				panic(err)
			}
		}
		return true
	}

	if path := os.Getenv("SANDBOX_ADOPTION_LATE_DESTINATION"); path != "" && step == "tools-boot" {
		if err := os.MkdirAll(path, 0700); err != nil {
			panic(err)
		}
		if err := os.WriteFile(filepath.Join(path, "owner.json"), []byte("{\"Controller\":\"default\",\"Name\":\"agent01\"}\n"), 0600); err != nil {
			panic(err)
		}
		if err := os.WriteFile(filepath.Join(path, "id_ed25519"), []byte("concurrent identity"), 0600); err != nil {
			panic(err)
		}
		return true
	}
	path := os.Getenv("SANDBOX_ADOPTION_READONLY_SOURCE")
	if path == "" || step != "tools-boot" {
		return false
	}
	if err := os.Chmod(path, 0500); err != nil {
		panic(err)
	}
	return true
}
func TestAdoptionSSHMigrationFailureRestoresKeysConfigAndContainer(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("requires Unix directory permissions")
	}
	fixture := newAdoptionSSHFixture(t)
	originalConfig, _ := os.ReadFile(fixture.config)
	key, _ := os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
	fixture.command.Env = append(fixture.command.Env, "SANDBOX_ADOPTION_READONLY_SOURCE="+fixture.legacy)
	t.Cleanup(func() { os.Chmod(fixture.legacy, 0700) })
	output, err := fixture.command.CombinedOutput()
	if err == nil {
		t.Fatalf("expected migration failure: %s", output)
	}
	if strings.Contains(string(output), "SSH restoration failed") {
		t.Fatalf("rollback failed to recognize retained originals: %s", output)
	}
	current, _ := os.ReadFile(fixture.config)
	if string(current) != string(originalConfig) {
		t.Fatalf("host config lost on rollback: %q", current)
	}
	current, err = os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
	if err != nil || string(current) != string(key) {
		t.Fatal("legacy identity lost during rollback")
	}
	if _, err := os.Stat(filepath.Join(fixture.managed, "agent01", "id_ed25519")); !os.IsNotExist(err) {
		t.Fatal("replacement keys leaked after rollback")
	}
	data, _ := os.ReadFile(fixture.state)
	var final map[string]map[string]any
	json.Unmarshal(data, &final)
	if len(final) != 1 || final["agent01"]["Id"] != oldUpdateID || final["agent01"]["State"].(map[string]any)["Running"] != true {
		t.Fatalf("migration failure did not restore original container: %s", data)
	}
}

func TestAdoptionSSHPreflightRejectsInvalidStateBeforeMutation(t *testing.T) {
	for _, problem := range []string{"destination", "private-key", "public-key", "pin-key", "pin-port", "pin-type", "pin-empty", "metadata-owner", "metadata-json", "redirected-file", "redirected-directory"} {
		t.Run(problem, func(t *testing.T) {
			fixture := newAdoptionSSHFixture(t)
			write := func(path, value string) {
				t.Helper()
				if err := os.WriteFile(path, []byte(value), 0600); err != nil {
					t.Fatal(err)
				}
			}
			switch problem {
			case "destination":
				write(filepath.Join(fixture.managed, "agent01.conf"), "personal content")
			case "private-key":
				write(filepath.Join(fixture.legacy, "id_ed25519"), "invalid private key")
			case "public-key":
				write(filepath.Join(fixture.legacy, "id_ed25519.pub"), "ssh-ed25519 different-key")
			case "pin-key":
				write(filepath.Join(fixture.legacy, "known_hosts"), "[127.0.0.1]:2222 ssh-ed25519 YQ==\n")
			case "pin-port", "pin-type":
				pin, err := os.ReadFile(filepath.Join(fixture.legacy, "known_hosts"))
				if err != nil {
					t.Fatal(err)
				}
				old, replacement := ":2222", ":2223"
				if problem == "pin-type" {
					old, replacement = "ssh-ed25519", "ssh-rsa"
				}
				write(filepath.Join(fixture.legacy, "known_hosts"), strings.ReplaceAll(string(pin), old, replacement))
			case "pin-empty":
				write(filepath.Join(fixture.legacy, "known_hosts"), "")
			case "metadata-owner":
				data, _ := json.Marshal(map[string]string{"project": fixture.source + "-other", "name": "agent01"})
				write(filepath.Join(fixture.legacy, "owner.json"), string(data))
			case "metadata-json":
				write(filepath.Join(fixture.legacy, "owner.json"), "{")
			case "redirected-file", "redirected-directory":
				path := filepath.Join(fixture.legacy, "id_ed25519")
				if problem == "redirected-directory" {
					path = fixture.legacy
				}
				target := path + "-original"
				if err := os.Rename(path, target); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(target, path); err != nil {
					t.Skipf("symlink unavailable: %v", err)
				}
			}
			state, _ := os.ReadFile(fixture.state)
			config, _ := os.ReadFile(fixture.config)
			output, err := fixture.command.CombinedOutput()
			if err == nil {
				t.Fatalf("invalid SSH state adopted: %s", output)
			}
			for _, call := range lifecycleCalls(t, fixture.log) {
				switch call[0] {
				case "stop", "start", "rename", "create", "run", "rm", "build", "exec":
					t.Fatalf("mutated before SSH validation: %v", call)
				}
			}
			after, _ := os.ReadFile(fixture.state)
			if string(after) != string(state) {
				t.Fatal("container state changed")
			}
			after, _ = os.ReadFile(fixture.config)
			if string(after) != string(config) {
				t.Fatal("host config changed")
			}
		})
	}
}

func TestAdoptionWithoutSSHDoesNotOptIn(t *testing.T) {
	fixture := newAdoptionSSHFixture(t)
	sshdir := filepath.Join(fixture.root, "home", ".ssh")
	if err := os.RemoveAll(sshdir); err != nil {
		t.Fatal(err)
	}
	if err := os.RemoveAll(filepath.Join(fixture.root, "state")); err != nil {
		t.Fatal(err)
	}
	output, err := fixture.command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	if _, err := os.Lstat(sshdir); !os.IsNotExist(err) {
		t.Fatalf("adoption opted into SSH: %v", err)
	}
	if _, err := os.Lstat(fixture.managed); !os.IsNotExist(err) {
		t.Fatalf("adoption generated SSH state: %v", err)
	}
}

func TestAdoptionSSHRetainsDestinationCreatedAfterPreflight(t *testing.T) {
	fixture := newAdoptionSSHFixture(t)
	destination := filepath.Join(fixture.managed, "agent01")
	fixture.command.Env = append(fixture.command.Env, "SANDBOX_ADOPTION_LATE_DESTINATION="+destination)
	key, _ := os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
	config, _ := os.ReadFile(fixture.config)
	output, err := fixture.command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "destination already exists") {
		t.Fatalf("late collision accepted: %v %s", err, output)
	}
	current, err := os.ReadFile(filepath.Join(destination, "id_ed25519"))
	if err != nil || string(current) != "concurrent identity" {
		t.Fatalf("concurrent identity overwritten: %v %q", err, current)
	}
	current, err = os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
	if err != nil || string(current) != string(key) {
		t.Fatal("legacy key changed")
	}
	current, err = os.ReadFile(fixture.config)
	if err != nil || string(current) != string(config) {
		t.Fatal("host config changed")
	}
	data, _ := os.ReadFile(fixture.state)
	var final map[string]map[string]any
	json.Unmarshal(data, &final)
	if len(final) != 1 || final["agent01"]["Id"] != oldUpdateID {
		t.Fatalf("container not restored: %s", data)
	}
}

func TestAdoptionSSHCoordinatesLegacyConfigLock(t *testing.T) {
	for _, late := range []bool{false, true} {
		fixture := newAdoptionSSHFixture(t)
		lock := filepath.Join(filepath.Dir(fixture.legacy), ".config.lock")
		originalConfig, _ := os.ReadFile(fixture.config)
		if late {
			fixture.command.Env = append(fixture.command.Env, "SANDBOX_ADOPTION_CONCURRENT_CONFIG="+fixture.config, "SANDBOX_ADOPTION_LEGACY_LOCK="+lock)
		} else if err := os.Mkdir(lock, 0700); err != nil {
			t.Fatal(err)
		}
		output, err := fixture.command.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "locked") {
			t.Fatalf("legacy writer was not coordinated: %v %s", err, output)
		}
		expected := string(originalConfig)
		if late {
			expected += "Host concurrent\n    HostName retained.example\n"
		}
		current, _ := os.ReadFile(fixture.config)
		if string(current) != expected {
			t.Fatalf("concurrent SSH config lost: %q", current)
		}
		if _, err := os.Stat(lock); err != nil {
			t.Fatalf("another writer's lock was removed: %v", err)
		}
		if _, err := os.Stat(filepath.Join(fixture.legacy, "id_ed25519")); err != nil {
			t.Fatal("legacy identity lost")
		}
		state, _ := os.ReadFile(fixture.state)
		var final map[string]map[string]any
		if err := json.Unmarshal(state, &final); err != nil {
			t.Fatal(err)
		}
		if len(final) != 1 || final["agent01"]["Id"] != oldUpdateID {
			t.Fatalf("container not retained/restored: %s", state)
		}
		if !late {
			for _, call := range lifecycleCalls(t, fixture.log) {
				if call[0] == "stop" || call[0] == "create" || call[0] == "build" || call[0] == "rm" {
					t.Fatalf("mutated during existing legacy lock: %v", call)
				}
			}
		}
	}
}

func TestAdoptionSSHFailedMigrationPreservesConcurrentConfig(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("requires Unix directory permissions")
	}
	fixture := newAdoptionSSHFixture(t)
	original, _ := os.ReadFile(fixture.config)
	fixture.command.Env = append(fixture.command.Env, "SANDBOX_ADOPTION_CONCURRENT_CONFIG="+fixture.config, "SANDBOX_ADOPTION_READONLY_SOURCE="+fixture.legacy)
	t.Cleanup(func() { os.Chmod(fixture.legacy, 0700) })
	if output, err := fixture.command.CombinedOutput(); err == nil {
		t.Fatalf("expected migration rollback: %s", output)
	}
	current, _ := os.ReadFile(fixture.config)
	expected := string(original) + "Host concurrent\n    HostName retained.example\n"
	if string(current) != expected {
		t.Fatalf("concurrent config lost during rollback: %q", current)
	}
}

func TestAdoptionSSHEncryptedKeyFailsBeforeMutation(t *testing.T) {
	fixture := newAdoptionSSHFixture(t)
	key := filepath.Join(fixture.legacy, "id_ed25519")
	if output, err := exec.Command("ssh-keygen", "-q", "-p", "-P", "", "-N", "test-passphrase", "-f", key).CombinedOutput(); err != nil {
		t.Fatalf("encrypt fixture: %v %s", err, output)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, fixture.command.Path, fixture.command.Args[1:]...)
	command.Env, command.Dir = fixture.command.Env, fixture.command.Dir
	output, err := command.CombinedOutput()
	if err == nil || ctx.Err() != nil || !strings.Contains(string(output), "cannot validate checkout SSH key") {
		t.Fatalf("encrypted key did not fail promptly: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, fixture.log) {
		if call[0] == "stop" || call[0] == "create" || call[0] == "build" || call[0] == "rm" {
			t.Fatalf("mutated with encrypted legacy key: %v", call)
		}
	}
}

func TestAdoptionSSHChecksReplacementIdentityBeforeMigration(t *testing.T) {
	for _, scenario := range []string{"matching", "matching-stopped", "stale-host-pin", "unauthorized-client", "late-symlink"} {
		t.Run(scenario, func(t *testing.T) {
			fixture := newAdoptionSSHFixture(t)
			originalConfig, err := os.ReadFile(fixture.config)
			if err != nil {
				t.Fatal(err)
			}
			originalKey, err := os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
			if err != nil {
				t.Fatal(err)
			}
			switch scenario {
			case "matching-stopped":
				state := readUpdateState(t, fixture.state)
				state["agent01"]["State"] = map[string]any{"Running": false, "Status": "exited"}
				data, err := json.Marshal(state)
				if err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(fixture.state, data, 0600); err != nil {
					t.Fatal(err)
				}
			case "stale-host-pin":
				other := filepath.Join(t.TempDir(), "host-key")
				if output, err := exec.Command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", other).CombinedOutput(); err != nil {
					t.Fatalf("%v %s", err, output)
				}
				public, err := os.ReadFile(other + ".pub")
				if err != nil {
					t.Fatal(err)
				}
				fixture.command.Env = append(fixture.command.Env, "SANDBOX_TEST_HOST_KEY="+string(public))
			case "unauthorized-client":
				fixture.command.Env = append(fixture.command.Env, "SANDBOX_TEST_AUTHORIZED_KEYS=# no matching client key\n")
			case "late-symlink":
				probe := filepath.Join(t.TempDir(), "link")
				if err := os.Symlink(fixture.config, probe); err != nil {
					t.Skipf("symlinks unavailable: %v", err)
				}
				fixture.command.Env = append(fixture.command.Env, "SANDBOX_ADOPTION_LATE_SYMLINK="+filepath.Join(fixture.legacy, "id_ed25519"))
			}
			output, err := fixture.command.CombinedOutput()
			if strings.HasPrefix(scenario, "matching") {
				if err != nil {
					t.Fatalf("matching identity rejected: %v %s", err, output)
				}
				checks := 0
				for _, call := range lifecycleCalls(t, fixture.log) {
					if call[0] == "exec" && strings.HasPrefix(call[len(call)-1], "/var/lib/agent-sshd/") {
						if len(call) != 6 || call[1] != "--user" || call[2] != "0" || call[3] != strings.Repeat("b", 64) || call[4] != "/bin/cat" {
							t.Fatalf("SSH check did not read replacement by ID: %v", call)
						}
						checks++
					}
					if scenario == "matching-stopped" && call[0] == "stop" && call[1] == strings.Repeat("b", 64) && checks != 2 {
						t.Fatalf("replacement stopped before SSH checks: %v", call)
					}
				}
				if checks != 2 {
					t.Fatalf("SSH identity was not checked: %d", checks)
				}
				if scenario == "matching-stopped" && readUpdateState(t, fixture.state)["agent01"]["State"].(map[string]any)["Running"] != false {
					t.Fatal("originally stopped sandbox left running")
				}
				return
			}
			if err == nil {
				t.Fatalf("unsafe SSH migration accepted: %s", output)
			}
			want := map[string]string{"stale-host-pin": "host pin does not match", "unauthorized-client": "client key is not authorized", "late-symlink": "symlink"}[scenario]
			if !strings.Contains(strings.ToLower(string(output)), want) {
				t.Fatalf("unexpected refusal: %s", output)
			}
			config, readErr := os.ReadFile(fixture.config)
			if readErr != nil || string(config) != string(originalConfig) {
				t.Fatalf("legacy config changed: %v %s", readErr, config)
			}
			key, readErr := os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
			if readErr != nil || string(key) != string(originalKey) {
				t.Fatalf("legacy key changed: %v", readErr)
			}
			data, err := os.ReadFile(fixture.state)
			if err != nil {
				t.Fatal(err)
			}
			var containers map[string]map[string]any
			if err := json.Unmarshal(data, &containers); err != nil {
				t.Fatal(err)
			}
			if len(containers) != 1 || containers["agent01"]["Id"] != strings.Repeat("a", 64) {
				t.Fatalf("original container not restored: %s", data)
			}
			if _, err := os.Lstat(filepath.Join(fixture.managed, "agent01", "id_ed25519")); !os.IsNotExist(err) {
				t.Fatalf("replacement key written before validation: %v", err)
			}
		})
	}
}

func interruptAdoptionParent() {
	parent, err := os.FindProcess(os.Getppid())
	if err != nil {
		panic(err)
	}
	if err := parent.Signal(os.Interrupt); err != nil {
		panic(err)
	}
	// Let the parent's signal context receive the interrupt before this fake returns.
	time.Sleep(100 * time.Millisecond)
}

func TestAdoptionInterruptionRespectsSSHCommit(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("parent-targeted Unix interrupt test")
	}
	for _, phase := range []string{"verification", "cleanup"} {
		t.Run(phase, func(t *testing.T) {
			fixture := newAdoptionSSHFixture(t)
			fixture.command.Env = append(fixture.command.Env, "SANDBOX_ADOPTION_INTERRUPT="+phase)
			originalConfig, err := os.ReadFile(fixture.config)
			if err != nil {
				t.Fatal(err)
			}
			originalKey, err := os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
			if err != nil {
				t.Fatal(err)
			}
			output, err := fixture.command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), "update interrupted") {
				t.Fatalf("missing interruption: %v %s", err, output)
			}
			state := readUpdateState(t, fixture.state)
			if phase == "verification" {
				if len(state) != 1 || state["agent01"]["Id"] != oldUpdateID {
					t.Fatalf("original container not restored: %v", state)
				}
				config, err := os.ReadFile(fixture.config)
				if err != nil || string(config) != string(originalConfig) {
					t.Fatalf("legacy config changed: %v %s", err, config)
				}
				key, err := os.ReadFile(filepath.Join(fixture.legacy, "id_ed25519"))
				if err != nil || string(key) != string(originalKey) {
					t.Fatalf("legacy key changed: %v", err)
				}
				if _, err := os.Lstat(filepath.Join(fixture.managed, "agent01", "id_ed25519")); !os.IsNotExist(err) {
					t.Fatalf("SSH migration happened despite cancellation: %v", err)
				}
			} else {
				if len(state) != 2 || state["agent01"]["Id"] != strings.Repeat("b", 64) {
					t.Fatalf("committed container rolled back: %v", state)
				}
				if !strings.Contains(string(output), "stopped backup") {
					t.Fatalf("missing retained backup diagnostic: %s", output)
				}
				key, err := os.ReadFile(filepath.Join(fixture.managed, "agent01", "id_ed25519"))
				if err != nil || string(key) != string(originalKey) {
					t.Fatalf("committed SSH identity lost: %v", err)
				}
				config, err := os.ReadFile(fixture.config)
				if err != nil || strings.Contains(string(config), filepath.ToSlash(fixture.legacy)) || !strings.Contains(string(config), filepath.ToSlash(fixture.managed)) {
					t.Fatalf("SSH config commit lost: %v %s", err, config)
				}
			}
		})
	}
}
