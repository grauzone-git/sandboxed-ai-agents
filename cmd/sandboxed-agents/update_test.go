package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"
)

const oldUpdateID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const newUpdateID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

func updateFixture() map[string]any {
	return map[string]any{"Name": "agent01", "Id": oldUpdateID, "Config": map[string]any{"Labels": map[string]string{"io.sandboxed-agents.project": "default", "io.sandboxed-agents.capabilities": "none", "io.sandboxed-agents.version": "0.0.1"}}, "State": map[string]any{"Running": true, "Status": "running"}, "Mounts": []map[string]any{{"Type": "volume", "Name": "agent01-home", "Destination": "/home/agent", "RW": true}, {"Type": "volume", "Name": "agent01-sshd", "Destination": "/var/lib/agent-sshd", "RW": true}, {"Type": "volume", "Name": "agent01-workspace", "Destination": "/workspace", "RW": true}}, "HostConfig": map[string]any{"PortBindings": map[string]any{"2222/tcp": []map[string]string{{"HostIp": "127.0.0.1", "HostPort": "2223"}}}, "Memory": int64(6442450944), "NanoCpus": int64(2500000000), "PidsLimit": 1024, "ShmSize": int64(536870912)}}
}
func fakeUpdatePodman(args []string) bool {
	filename := os.Getenv("SANDBOX_UPDATE_STATE")
	if filename == "" {
		return false
	}
	writeFile := func(path string, data []byte) {
		if err := os.WriteFile(path, data, 0600); err != nil {
			panic(err)
		}
	}
	exists := func(path string) bool {
		_, err := os.Stat(path)
		if err != nil && !os.IsNotExist(err) {
			panic(err)
		}
		return err == nil
	}
	respond := func(value any) {
		if err := json.NewEncoder(os.Stdout).Encode(value); err != nil {
			panic(err)
		}
	}
	data, err := os.ReadFile(filename)
	if err != nil {
		panic(err)
	}
	var state map[string]map[string]any
	if err := json.Unmarshal(data, &state); err != nil {
		panic(err)
	}
	save := func() {
		data, err := json.Marshal(state)
		if err != nil {
			panic(err)
		}
		writeFile(filename, data)
	}
	key := func(id string) string {
		for name, info := range state {
			if name == id || info["Id"] == id {
				return name
			}
		}
		return ""
	}
	step := args[0]
	if step == "exec" {
		step = "ready"
		if strings.Contains(strings.Join(args, " "), "sandbox-agents boot") {
			step = "agents-boot"
		}
		if strings.Contains(strings.Join(args, " "), "sandbox-tools boot") {
			step = "tools-boot"
		}
	}
	if step == "ready" {
		fmt.Fprintln(os.Stderr, "readiness attempt not ready")
	}
	if step == os.Getenv("SANDBOX_UPDATE_BLOCK") {
		time.Sleep(time.Minute)
	}
	if step == "ready" && os.Getenv("SANDBOX_UPDATE_READY_EXIT") != "" {
		if !exists(filename + ".ready-retried") {
			writeFile(filename+".ready-retried", []byte("1"))
			code, err := strconv.Atoi(os.Getenv("SANDBOX_UPDATE_READY_EXIT"))
			if err != nil {
				panic(err)
			}
			os.Exit(code)
		}
	}
	if step == os.Getenv("SANDBOX_UPDATE_FAIL") {
		if step == "ready" {
			os.Exit(27)
		}
		if !exists(filename + ".failed") {
			writeFile(filename+".failed", []byte("1"))
			os.Exit(27)
		}
	}
	switch args[0] {
	case "info":
		if args[len(args)-1] == "{{json .Host.Security}}" {
			respond(map[string]string{"seccompProfilePath": os.Getenv("SANDBOX_CAPABILITY_PROFILE")})
		} else {
			fmt.Print("true")
		}
	case "ps":
		for name := range state {
			fmt.Println(name)
		}
	case "container":
		name := key(args[len(args)-1])
		if name == "" {
			os.Exit(1)
		}
		respond([]any{state[name]})
	case "volume":
		fmt.Print("default")
	case "image":
		fmt.Print(strings.Repeat("c", 64))
	case "build":
		if os.Getenv("SANDBOX_UPDATE_CHANGED") == "1" {
			state["agent01"]["HostConfig"].(map[string]any)["Memory"] = 1
			save()
		}
		if strings.Contains(strings.Join(args, " "), "--quiet") {
			fmt.Print(strings.Repeat("d", 64))
		}
		return true
	case "stop", "start":
		name := key(args[1])
		if name == "" {
			os.Exit(1)
		}
		running := args[0] == "start"
		status := "exited"
		if running {
			status = "running"
		}
		state[name]["State"] = map[string]any{"Running": running, "Status": status}
		save()
	case "rename":
		name := key(args[1])
		if name == "" {
			os.Exit(1)
		}
		if state[args[2]] != nil {
			os.Exit(29)
		}
		info := state[name]
		delete(state, name)
		info["Name"] = args[2]
		state[args[2]] = info
		save()
		if os.Getenv("SANDBOX_UPDATE_BLOCK") == "rename-partial" && args[2] != "agent01" {
			writeFile(filename+".renamed", []byte("1"))
			time.Sleep(time.Minute)
		}
		if os.Getenv("SANDBOX_UPDATE_FAIL") == "rename-partial" {
			if !exists(filename + ".rename-failed") {
				writeFile(filename+".rename-failed", []byte("1"))
				os.Exit(27)
			}
		}
	case "create":
		var name, cidfile string
		for i, arg := range args {
			if arg == "--name" {
				name = args[i+1]
			}
			if arg == "--cidfile" {
				cidfile = args[i+1]
			}
		}
		info := namedUpdateFixture(name)
		id := newUpdateID
		if name != "agent01" {
			id = fmt.Sprintf("%x", sha256.Sum256([]byte("new "+name)))
		}
		info["Id"] = id
		labels := info["Config"].(map[string]any)["Labels"].(map[string]string)
		for i, arg := range args {
			if arg == "--label" {
				key, value, _ := strings.Cut(args[i+1], "=")
				labels[key] = value
			}
		}
		info["State"] = map[string]any{"Running": false, "Status": "created"}
		state[name] = info
		save()
		if os.Getenv("SANDBOX_UPDATE_BLOCK") == "create-no-cid" {
			writeFile(filename+".created", []byte("1"))
			time.Sleep(time.Minute)
		}
		if os.Getenv("SANDBOX_UPDATE_FAIL") == "create-no-cid" {
			os.Exit(27)
		}
		if os.Getenv("SANDBOX_UPDATE_FAIL") == "create-foreign" {
			delete(labels, "io.sandboxed-agents.update-transaction")
			info["Config"] = map[string]any{"Labels": labels}
			save()
			os.Exit(27)
		}
		if os.Getenv("SANDBOX_UPDATE_FAIL") == "create-old-cid" {
			id = oldUpdateID
		}
		writeFile(cidfile, []byte(id))
		if os.Getenv("SANDBOX_UPDATE_FAIL") == "create-partial" {
			os.Exit(27)
		}
	case "rm":
		name := key(args[len(args)-1])
		if name == "" {
			os.Exit(1)
		}
		delete(state, name)
		save()
	case "exec":
		return true
	default:
		fmt.Fprintln(os.Stderr, "unexpected update fake", args)
		os.Exit(28)
	}
	return true
}
func TestUpdatePreservesStorageSettingsAndBootsSelections(t *testing.T) {
	command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	calls := lifecycleCalls(t, log)
	var create []string
	agents, tools := false, false
	for _, call := range calls {
		if call[0] == "create" {
			create = call
		}
		joined := strings.Join(call, " ")
		if strings.Contains(joined, "sandbox-agents boot") {
			agents = true
		}
		if strings.Contains(joined, "sandbox-tools boot") {
			tools = true
		}
		if call[0] == "volume" && call[1] != "inspect" {
			t.Fatalf("update touched volumes: %v", call)
		}
		if call[0] == "build" {
			t.Fatal("--no-build rebuilt")
		}
	}
	for _, want := range []string{"--memory=6442450944", "--cpus=2.5", "--pids-limit=1024", "--shm-size=536870912", "127.0.0.1:2223:2222", "agent01-home:/home/agent", "agent01-workspace:/workspace", strings.Repeat("c", 64)} {
		found := false
		for _, arg := range create {
			if arg == want {
				found = true
			}
		}
		if !found {
			t.Fatalf("create missing %s: %v", want, create)
		}
	}
	if !agents || !tools {
		t.Fatalf("saved selections not booted: %v", calls)
	}
	final := readUpdateState(t, state)
	if len(final) != 1 || final["agent01"]["Id"] != newUpdateID {
		t.Fatalf("wrong resulting containers: %v", final)
	}
}

func TestUpdateRollsBackEachFailedTransactionStep(t *testing.T) {
	for _, step := range []string{"stop", "rename", "rename-partial", "create", "create-partial", "create-no-cid", "create-old-cid", "start", "ready", "agents-boot", "tools-boot"} {
		t.Run(step, func(t *testing.T) {
			command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
			command.Env = append(command.Env, "SANDBOX_UPDATE_FAIL="+step)
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("expected %s failure: %s", step, output)
			}
			if step == "ready" && (strings.Count(string(output), "readiness attempt not ready") != 1 || !strings.Contains(string(output), "within 15 seconds") || strings.Contains(string(output), "interrupted")) {
				t.Fatalf("readiness timeout repeated stderr or looked like cancellation: %s", output)
			}
			assertOriginalUpdateRestored(t, state, true)
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "volume" && call[1] != "inspect" {
					t.Fatalf("rollback touched volume: %v", call)
				}
				if call[0] == "rm" && call[len(call)-1] != newUpdateID {
					t.Fatalf("rollback deleted original container: %v", call)
				}
			}
		})
	}
}
func TestUpdateBackupRemovalFailureKeepsHealthyReplacement(t *testing.T) {
	command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
	command.Env = append(command.Env, "SANDBOX_UPDATE_FAIL=rm")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "was updated") {
		t.Fatalf("missing cleanup failure: %v %s", err, output)
	}
	final := readUpdateState(t, state)
	if len(final) != 2 || final["agent01"]["Id"] != newUpdateID {
		t.Fatalf("healthy replacement rolled back: %v", final)
	}
	var backupName string
	for name, info := range final {
		if info["Id"] == oldUpdateID {
			backupName = name
		}
	}
	if !regexp.MustCompile(`^agent01-update-backup-[0-9a-f]{32}$`).MatchString(backupName) {
		t.Fatalf("backup suffix is not explicit random hex: %s", backupName)
	}
	transaction := final["agent01"]["Config"].(map[string]any)["Labels"].(map[string]any)["io.sandboxed-agents.update-transaction"]
	if transaction != strings.TrimPrefix(backupName, "agent01-update-backup-") {
		t.Fatalf("backup name and transaction token differ: %v", final)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "rm" && call[len(call)-1] == newUpdateID {
			t.Fatal("healthy replacement removed")
		}
	}
}
func TestUpdateAllValidatesEverySandboxBeforeBuilding(t *testing.T) {
	foreign := updateFixture()
	foreign["Name"] = "agent02"
	foreign["Config"] = map[string]any{"Labels": map[string]string{"io.sandboxed-agents.project": "foreign"}}
	command, log, _ := updateTestCommand(t, map[string]any{"agent01": updateFixture(), "agent02": foreign}, "update", "--all")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "not owned") {
		t.Fatalf("foreign update: %v %s", err, output)
	}
	assertNoUpdateMutation(t, log, "", "build", "stop", "rename")
}

func TestListMarksOlderVersionsWithoutUpdating(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "sandboxed-agents")
	if runtime.GOOS == "windows" {
		binary += ".exe"
	}
	build := exec.Command(filepath.Join(runtime.GOROOT(), "bin", "go"), "build", "-ldflags=-X main.version=4.2.0-beta.2", "-o", binary, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build explicit version: %v %s", err, output)
	}
	for _, test := range []struct {
		version  string
		outdated bool
	}{
		{"4.1.9", true}, {"4.2.0-beta.1", true}, {"4.2.0-beta.2", false}, {"4.2.0", false}, {"9.0.0", false}, {"", false}, {"invalid", false},
	} {
		t.Run(test.version, func(t *testing.T) {
			fixture := updateFixture()
			fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.version"] = test.version
			command, log, _ := updateTestCommand(t, map[string]any{"agent01": fixture}, "list")
			command.Path, command.Args[0] = binary, binary
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			rows := strings.Split(strings.TrimSpace(string(output)), "\n")
			if len(rows) != 2 {
				t.Fatalf("unexpected listing: %s", output)
			}
			columns := regexp.MustCompile(`\s{2,}`).Split(strings.TrimSpace(rows[1]), -1)
			want := "running"
			if test.outdated {
				want += " (outdated)"
			}
			if len(columns) < 2 || columns[1] != want {
				t.Fatalf("wrong state cell for %s: %s", test.version, output)
			}
			assertNoUpdateMutation(t, log, "", "build", "stop", "rename")
		})
	}
}

func TestUpdatePreservesStoppedStateSSHAndCleansTransactionFiles(t *testing.T) {
	fixture := updateFixture()
	fixture["State"] = map[string]any{"Running": false, "Status": "exited"}
	command, log, state := updateTestCommand(t, map[string]any{"agent01": fixture}, "agent01", "update", "--no-build")
	root := t.TempDir()
	key := filepath.Join(root, "sandboxed-agents", "ssh", "agent01", "id_ed25519")
	if err := os.MkdirAll(filepath.Dir(key), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(key, []byte("retained private key fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "XDG_STATE_HOME="+root, "LOCALAPPDATA="+root)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	final := readUpdateState(t, state)
	if final["agent01"]["State"].(map[string]any)["Running"] != false {
		t.Fatal("update started previously stopped sandbox")
	}
	data, err := os.ReadFile(key)
	if err != nil || string(data) != "retained private key fixture" {
		t.Fatal("update modified SSH key")
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "create" {
			for i, arg := range call {
				if arg == "--cidfile" {
					if _, err := os.Stat(filepath.Dir(call[i+1])); !os.IsNotExist(err) {
						t.Fatal("transaction state leaked")
					}
				}
			}
		}
		if strings.Contains(strings.Join(call, " "), " init") {
			t.Fatal("update replaced selections")
		}
	}
}
func TestUpdateBuildFailureDoesNotStopContainer(t *testing.T) {
	command, log, _ := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update")
	command.Env = append(command.Env, "SANDBOX_UPDATE_FAIL=build")
	if output, err := command.CombinedOutput(); err == nil {
		t.Fatalf("expected build failure: %s", output)
	}
	assertNoUpdateMutation(t, log, "", "stop", "rename", "create")
}

func TestUpdatePreservesNestedCapability(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux capability profile fixture; Windows guest staging is covered separately")
	}
	fixture := updateFixture()
	fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.capabilities"] = "podman"
	command, log, _ := updateTestCommand(t, map[string]any{"agent01": fixture}, "agent01", "update", "--no-build")
	profile := filepath.Join(t.TempDir(), "source.json")
	if err := os.WriteFile(profile, []byte(`{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE="+profile)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	var create []string
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "create" {
			create = call
		}
	}
	for _, want := range []string{"io.sandboxed-agents.capabilities=podman", "--device=/dev/fuse", "--device=/dev/net/tun", "/run/user/1000:rw,nosuid,nodev,noexec,mode=0700", strings.Repeat("d", 64)} {
		if !slices.Contains(create, want) {
			t.Fatalf("capability lost %s: %v", want, create)
		}
	}
}

func namedUpdateFixture(name string) map[string]any {
	fixture := updateFixture()
	fixture["Name"] = name
	if name != "agent01" {
		fixture["Id"] = fmt.Sprintf("%x", sha256.Sum256([]byte("old "+name)))
	}
	for _, mount := range fixture["Mounts"].([]map[string]any) {
		mount["Name"] = strings.Replace(mount["Name"].(string), "agent01", name, 1)
	}
	return fixture
}

func writeUpdateState(t *testing.T, path string, fixtures map[string]any) {
	t.Helper()
	data, err := json.Marshal(fixtures)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func readUpdateState(t *testing.T, path string) map[string]map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var state map[string]map[string]any
	if err := json.Unmarshal(data, &state); err != nil {
		t.Fatal(err)
	}
	return state
}

func assertNoUpdateMutation(t *testing.T, log, target string, verbs ...string) {
	t.Helper()
	for _, call := range lifecycleCalls(t, log) {
		if target != "" && !slices.Contains(call, target) {
			continue
		}
		if slices.Contains(verbs, call[0]) {
			t.Fatalf("unexpected mutation: %v", call)
		}
	}
}

func assertOriginalUpdateRestored(t *testing.T, state string, running bool) {
	t.Helper()
	final := readUpdateState(t, state)
	status, ok := final["agent01"]["State"].(map[string]any)
	if len(final) != 1 || final["agent01"]["Id"] != oldUpdateID || !ok || status["Running"] != running {
		t.Fatalf("original container was not restored with Running=%v: %v", running, final)
	}
}

func updateTestCommand(t *testing.T, fixtures map[string]any, args ...string) (*exec.Cmd, string, string) {
	t.Helper()
	command, log := lifecycleCommand(t, args...)
	state := filepath.Join(t.TempDir(), "containers.json")
	writeUpdateState(t, state, fixtures)
	command.Env = append(command.Env, "SANDBOX_UPDATE_STATE="+state)
	return command, log, state
}

func TestUpdateRetriesTransientReadinessFailures(t *testing.T) {
	for _, code := range []string{"1", "125", "126", "255"} {
		t.Run(code, func(t *testing.T) {
			command, log, _ := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
			command.Env = append(command.Env, "SANDBOX_UPDATE_READY_EXIT="+code)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			if strings.Contains(string(output), "readiness attempt not ready") {
				t.Fatalf("transient stderr leaked: %s", output)
			}
			attempts := 0
			for _, call := range lifecycleCalls(t, log) {
				if slices.Contains(call, "/bin/sh") {
					attempts++
				}
			}
			if attempts != 2 {
				t.Fatalf("expected readiness retry: %v", lifecycleCalls(t, log))
			}
		})
	}
}

func TestUpdateAllSkipsBackupsButUpdatesSimilarlyNamedSandboxes(t *testing.T) {
	for _, suffix := range []string{"12345", "abc123def456", "0123456789abcdef0123456789abcdef"} {
		t.Run(suffix, func(t *testing.T) {
			backup := updateFixture()
			backup["Name"] = "agent01-update-backup-" + suffix
			backup["Id"] = strings.Repeat("e", 64)
			backup["State"] = map[string]any{"Running": false, "Status": "exited"}
			ordinary := "agent02-update-backup-67890"
			command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture(), backup["Name"].(string): backup, ordinary: namedUpdateFixture(ordinary)}, "update", "--all")
			output, err := command.CombinedOutput()
			if err != nil || !strings.Contains(string(output), "Skipping update backup agent01-update-backup-"+suffix) {
				t.Fatalf("%v %s", err, output)
			}
			builds, creates := 0, 0
			initialInspections := map[string]int{}
			for _, call := range lifecycleCalls(t, log) {
				if builds == 0 && call[0] == "container" && call[1] == "inspect" {
					initialInspections[call[len(call)-1]]++
				}
				if call[0] == "build" {
					builds++
				}
				if call[0] == "create" {
					creates++
				}
			}
			assertNoUpdateMutation(t, log, strings.Repeat("e", 64), "stop", "rm")
			for _, name := range []string{"agent01", backup["Name"].(string), ordinary} {
				if initialInspections[name] != 1 {
					t.Fatalf("initial snapshot inspected %s %d times", name, initialInspections[name])
				}
			}
			if builds != 1 || creates != 2 {
				t.Fatalf("wrong batch: %v", lifecycleCalls(t, log))
			}
			final := readUpdateState(t, state)
			if len(final) != 3 || final[ordinary]["Id"] == namedUpdateFixture(ordinary)["Id"] {
				t.Fatalf("ordinary sandbox skipped: %v", final)
			}
		})
	}
}

func TestUpdateAllStopsAfterFirstReplacementFailure(t *testing.T) {
	command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture(), "agent02": namedUpdateFixture("agent02")}, "update", "--all", "--no-build")
	command.Env = append(command.Env, "SANDBOX_UPDATE_FAIL=tools-boot")
	if output, err := command.CombinedOutput(); err == nil {
		t.Fatalf("expected failure: %s", output)
	}
	assertNoUpdateMutation(t, log, namedUpdateFixture("agent02")["Id"].(string), "stop", "rename", "create")
	final := readUpdateState(t, state)
	if len(final) != 2 || final["agent01"]["Id"] != oldUpdateID || final["agent02"]["Id"] != namedUpdateFixture("agent02")["Id"] {
		t.Fatalf("wrong batch rollback: %v", final)
	}
}

func TestUpdateRollsBackStoppedSandboxWithoutStartingIt(t *testing.T) {
	for _, step := range []string{"tools-boot", "stop"} {
		t.Run(step, func(t *testing.T) {
			fixture := updateFixture()
			fixture["State"] = map[string]any{"Running": false, "Status": "exited"}
			command, log, state := updateTestCommand(t, map[string]any{"agent01": fixture}, "agent01", "update", "--no-build")
			command.Env = append(command.Env, "SANDBOX_UPDATE_FAIL="+step)
			if output, err := command.CombinedOutput(); err == nil {
				t.Fatalf("expected failure: %s", output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "volume" && call[1] != "inspect" {
					t.Fatalf("rollback touched volume: %v", call)
				}
				if call[0] == "start" && call[1] == oldUpdateID {
					t.Fatalf("started stopped original: %v", call)
				}
			}
			assertOriginalUpdateRestored(t, state, false)
		})
	}
}

func TestUpdateRefusesChangedSnapshotBeforeStopping(t *testing.T) {
	command, log, _ := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update")
	command.Env = append(command.Env, "SANDBOX_UPDATE_CHANGED=1")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "changed during update") {
		t.Fatalf("%v %s", err, output)
	}
	assertNoUpdateMutation(t, log, "", "stop", "rename", "create")
}

func TestUpdateDoesNotRemoveUnidentifiedNameCollision(t *testing.T) {
	command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
	command.Env = append(command.Env, "SANDBOX_UPDATE_FAIL=create-foreign")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "backup agent01-update-backup-") {
		t.Fatalf("%v %s", err, output)
	}
	assertNoUpdateMutation(t, log, "", "rm")
	final := readUpdateState(t, state)
	if len(final) != 2 {
		t.Fatalf("lost backup or colliding container: %v", final)
	}
}

func TestUpdateCapabilityOverride(t *testing.T) {
	for _, selected := range []string{"none", "podman"} {
		t.Run(selected, func(t *testing.T) {
			fixture := updateFixture()
			initial := "podman"
			if selected == "podman" {
				initial = "none"
			}
			fixture["Config"].(map[string]any)["Labels"].(map[string]string)["io.sandboxed-agents.capabilities"] = initial
			command, log, _ := updateTestCommand(t, map[string]any{"agent01": fixture}, "agent01", "update", "--no-build", "--capabilities", selected)
			profile := filepath.Join(t.TempDir(), "source.json")
			if err := os.WriteFile(profile, []byte(`{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}`), 0600); err != nil {
				t.Fatal(err)
			}
			command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE="+profile)
			if output, err := command.CombinedOutput(); err != nil {
				t.Fatalf("%v %s", err, output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "create" && (!slices.Contains(call, "io.sandboxed-agents.capabilities="+selected) || slices.Contains(call, "--device=/dev/fuse") != (selected == "podman")) {
					t.Fatalf("override lost: %v", call)
				}
			}
		})
	}
}

func TestUpdatePreservesBindWorkspace(t *testing.T) {
	workspace := filepath.Join(t.TempDir(), "workspace with spaces")
	if err := os.Mkdir(workspace, 0700); err != nil {
		t.Fatal(err)
	}
	source := workspace
	if runtime.GOOS == "windows" {
		source = "/mnt/" + strings.ToLower(workspace[:1]) + filepath.ToSlash(workspace[2:])
	}
	fixture := updateFixture()
	fixture["Mounts"].([]map[string]any)[2] = map[string]any{"Type": "bind", "Source": source, "Destination": "/workspace", "RW": true}
	command, log, _ := updateTestCommand(t, map[string]any{"agent01": fixture}, "agent01", "update", "--no-build")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "create" {
			found := false
			for _, arg := range call {
				if strings.HasSuffix(arg, ":/workspace:Z") {
					info, err := os.Stat(strings.TrimSuffix(arg, ":/workspace:Z"))
					want, statErr := os.Stat(workspace)
					if statErr != nil {
						t.Fatal(statErr)
					}
					found = err == nil && os.SameFile(info, want)
				}
			}
			if !found {
				t.Fatalf("workspace not restored as guarded local bind: %v", call)
			}
		}
	}
}

func TestUpdateReadinessTimeoutStopsHungAttemptAndReportsLastError(t *testing.T) {
	command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
	command.Env = append(command.Env, "SANDBOX_UPDATE_BLOCK=ready")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "within 15 seconds") || strings.Contains(string(output), "interrupted") || strings.Count(string(output), "readiness attempt not ready") != 1 {
		t.Fatalf("hung readiness timeout: %v %s", err, output)
	}
	attempts := 0
	for _, call := range lifecycleCalls(t, log) {
		if slices.Contains(call, "/bin/sh") {
			attempts++
		}
	}
	if attempts != 1 {
		t.Fatalf("expected one blocked readiness attempt: %v", lifecycleCalls(t, log))
	}
	assertOriginalUpdateRestored(t, state, true)
}
