package main

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func fakeLifecyclePodman() {
	args := os.Args[1:]
	f, err := os.OpenFile(os.Getenv("SANDBOX_LIFECYCLE_LOG"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		panic(err)
	}
	json.NewEncoder(f).Encode(args)
	f.Close()
	if fakeSSHPodman(args) {
		return
	}
	if fakeCapabilityPodman(args) {
		return
	}
	switch args[0] {
	case "info":
		fmt.Print("true")
	case "container":
		os.Exit(1)
	case "image":
		return
	case "volume":
		if args[1] == "exists" {
			if os.Getenv("SANDBOX_VOLUME_OWNER") == "" {
				os.Exit(1)
			}
			return
		}
		if args[1] == "inspect" {
			fmt.Print(os.Getenv("SANDBOX_VOLUME_OWNER"))
		}
	case "inspect":
		if strings.Contains(strings.Join(args, " "), "json .Mounts") {
			if value := os.Getenv("SANDBOX_MOUNTS"); value != "" {
				fmt.Print(value)
			} else {
				fmt.Print(`[{"Type":"volume","Name":"agent01-home","Destination":"/home/agent"},{"Type":"volume","Name":"agent01-sshd","Destination":"/var/lib/agent-sshd"},{"Type":"volume","Name":"agent01-workspace","Destination":"/workspace"}]`)
			}
		} else {
			fmt.Print(os.Getenv("SANDBOX_CONTAINER_OWNER"))
		}
	}
	if os.Getenv("SANDBOX_LIFECYCLE_FAIL") == args[0] {
		os.Exit(27)
	}
}

func lifecycleCommand(t *testing.T, args ...string) (*exec.Cmd, string) {
	t.Helper()
	if len(args) > 1 && args[1] == "up" && !slices.Contains(args, "--ssh-port") {
		listener, err := net.Listen("tcp4", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		port := fmt.Sprint(listener.Addr().(*net.TCPAddr).Port)
		listener.Close()
		args = append(append([]string{}, args...), "--ssh-port", port)
	}
	command := cliCommand(t, args...)
	bin := t.TempDir()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	name := "podman"
	if strings.HasSuffix(executable, ".exe") {
		name += ".exe"
	}
	if err := os.WriteFile(filepath.Join(bin, name), data, 0700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(t.TempDir(), "calls.jsonl")
	command.Env = append(command.Env, "PATH="+bin+string(os.PathListSeparator)+os.Getenv("PATH"), "SANDBOX_LIFECYCLE_LOG="+log, "SANDBOX_CONTROLLER=default", "SANDBOX_CONTAINER_OWNER=default", "SANDBOX_VOLUME_OWNER=")
	return command, log
}
func lifecycleCalls(t *testing.T, path string) [][]string {
	t.Helper()
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var calls [][]string
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var call []string
		if err := json.Unmarshal([]byte(line), &call); err != nil {
			t.Fatal(err)
		}
		calls = append(calls, call)
	}
	return calls
}
func TestUpRequiresExplicitAgentsBeforePodman(t *testing.T) {
	for _, args := range [][]string{{"agent01", "up"}, {"agent01", "up", "--agents", "none"}, {"agent01", "up", "--agents", ""}} {
		command, log := lifecycleCommand(t, args...)
		output, err := command.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "--agents") {
			t.Fatalf("expected explicit agents error: %v %s", err, output)
		}
		if calls := lifecycleCalls(t, log); len(calls) != 0 {
			t.Fatalf("validation contacted podman: %v", calls)
		}
	}
}

func TestUpCreatesOwnedNamedVolumesAndInitializesExplicitSelections(t *testing.T) {
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex@1.2.3", "--tools", "t3", "--cpus", "2.5", "--memory", "6g", "--ssh-port", "2223")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %s", err, output)
	}
	calls := lifecycleCalls(t, log)
	var run []string
	creates := 0
	agents, tools := false, false
	for _, call := range calls {
		joined := strings.Join(call, " ")
		if call[0] == "run" {
			run = call
		}
		if strings.HasPrefix(joined, "volume create ") {
			creates++
			if !strings.Contains(joined, "io.sandboxed-agents.project=default") {
				t.Fatalf("unowned volume: %v", call)
			}
		}
		if strings.Contains(joined, "sandbox-agents init codex@1.2.3") {
			agents = true
		}
		if strings.Contains(joined, "sandbox-tools init t3") {
			tools = true
		}
	}
	if creates != 3 || !agents || !tools {
		t.Fatalf("missing creation/initialization: %v", calls)
	}
	for _, want := range []string{"--cpus=2.5", "--memory=6g", "--pids-limit=2048", "--security-opt=no-new-privileges", "--network=pasta:--no-map-gw", "127.0.0.1:2223:2222", "agent01-workspace:/workspace", "agent01-home:/home/agent", "agent01-sshd:/var/lib/agent-sshd", "io.sandboxed-agents.project=default", "io.sandboxed-agents.version=0.1.0-dev"} {
		found := false
		for _, arg := range run {
			if arg == want {
				found = true
			}
		}
		if !found {
			t.Fatalf("run missing %q: %v", want, run)
		}
	}
	for _, key := range []string{"HOME=", "XDG_STATE_HOME=", "LOCALAPPDATA="} {
		root := ""
		for _, entry := range command.Env {
			if strings.HasPrefix(entry, key) {
				root = strings.TrimPrefix(entry, key)
			}
		}
		entries, _ := os.ReadDir(root)
		if len(entries) > 0 {
			t.Fatalf("plain up wrote host state %s: %v", root, entries)
		}
	}
}

func TestLifecycleRejectsForeignOwnersBeforeMutation(t *testing.T) {
	for _, action := range []string{"start", "stop", "restart", "remove", "shell", "check"} {
		t.Run(action, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", action)
			command.Env = append(command.Env, "SANDBOX_CONTAINER_OWNER=/other/checkout")
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), "not owned") {
				t.Fatalf("expected ownership error: %v %s", err, output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] != "info" && call[0] != "inspect" {
					t.Fatalf("foreign resource touched: %v", call)
				}
			}
		})
	}
}
func TestLifecycleOperationsUsePodmanWithoutSSH(t *testing.T) {
	for _, action := range []string{"start", "stop", "restart", "shell"} {
		t.Run(action, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", action)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			calls := lifecycleCalls(t, log)
			last := strings.Join(calls[len(calls)-1], " ")
			want := action + " agent01"
			if action == "shell" {
				want = "exec -i --user 1000:1000 --workdir /workspace agent01 /bin/bash -l"
			}
			if last != want {
				t.Fatalf("got %q want %q", last, want)
			}
		})
	}
}

func TestRemoveRetainsVolumesUnlessExplicitlyRequested(t *testing.T) {
	for _, removeVolumes := range []bool{false, true} {
		t.Run(fmt.Sprint(removeVolumes), func(t *testing.T) {
			args := []string{"agent01", "remove"}
			if removeVolumes {
				args = append(args, "--volumes")
			}
			command, log := lifecycleCommand(t, args...)
			command.Env = append(command.Env, "SANDBOX_VOLUME_OWNER=default")
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			removed := 0
			calls := lifecycleCalls(t, log)
			stopped, containerRemoved := false, false
			for _, call := range calls {
				if call[0] == "stop" {
					stopped = true
				}
				if call[0] == "rm" {
					if !stopped {
						t.Fatal("remove before stop")
					}
					containerRemoved = true
				}
				if len(call) > 1 && call[0] == "volume" && call[1] == "rm" {
					removed++
					if !containerRemoved {
						t.Fatal("volume removal before container removal")
					}
					if len(call) != 3 {
						t.Fatalf("unsafe volume removal: %v", call)
					}
				}
			}
			if !containerRemoved || (removeVolumes && removed != 3) || (!removeVolumes && removed != 0) {
				t.Fatalf("incorrect removal: %v", calls)
			}
			if !removeVolumes && !strings.Contains(string(output), "retained") {
				t.Fatalf("no retention message: %s", output)
			}
		})
	}
}
func TestForeignRetainedVolumesBlockCreateAndRemoveBeforeMutation(t *testing.T) {
	for _, args := range [][]string{{"agent01", "up", "--agents", "codex"}, {"agent01", "remove", "--volumes"}} {
		command, log := lifecycleCommand(t, args...)
		command.Env = append(command.Env, "SANDBOX_VOLUME_OWNER=foreign")
		output, err := command.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "another configuration") {
			t.Fatalf("expected ownership error: %v %s", err, output)
		}
		for _, call := range lifecycleCalls(t, log) {
			if call[0] == "run" || call[0] == "stop" || call[0] == "rm" || (call[0] == "volume" && (call[1] == "rm" || call[1] == "create")) {
				t.Fatalf("mutation before validation: %v", call)
			}
		}
	}
}

func TestCheckUsesExecAndRejectsUnexpectedMounts(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		t.Run(fmt.Sprint(invalid), func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", "check")
			if invalid {
				command.Env = append(command.Env, `SANDBOX_MOUNTS=[{"Type":"bind","Source":"/","Destination":"/home/agent"}]`)
			}
			output, err := command.CombinedOutput()
			calls := lifecycleCalls(t, log)
			if invalid {
				if err == nil || !strings.Contains(string(output), "mount") {
					t.Fatalf("expected mount refusal: %v %s", err, output)
				}
				for _, call := range calls {
					if call[0] == "exec" {
						t.Fatalf("smoke test ran with unsafe mounts: %v", calls)
					}
				}
			} else {
				if err != nil {
					t.Fatalf("%v %s", err, output)
				}
				if got := strings.Join(calls[len(calls)-1], " "); got != "exec --user 1000:1000 --workdir /workspace agent01 /usr/local/bin/agent-smoke" {
					t.Fatalf("check did not use exec: %v", calls)
				}
			}
		})
	}
}

func TestCreateWaitsForEntrypointBeforeInitializingAgents(t *testing.T) {
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	ready := false
	for _, call := range lifecycleCalls(t, log) {
		if strings.Join(call, " ") == "exec --user 0 agent01 /bin/test -f /run/agent-booted" {
			ready = true
		}
		if strings.Contains(strings.Join(call, " "), "sandbox-agents init") && !ready {
			t.Fatal("agent init raced container entrypoint")
		}
	}
	if !ready {
		t.Fatal("entrypoint readiness never checked")
	}
}
func TestLifecyclePropagatesPodmanFailureWithoutLaterMutation(t *testing.T) {
	for _, test := range []struct {
		args      []string
		fail      string
		forbidden string
	}{
		{[]string{"agent01", "up", "--agents", "codex"}, "run", "exec"},
		{[]string{"agent01", "remove", "--volumes"}, "rm", "volume rm"},
		{[]string{"agent01", "start"}, "start", ""},
	} {
		command, log := lifecycleCommand(t, test.args...)
		command.Env = append(command.Env, "SANDBOX_LIFECYCLE_FAIL="+test.fail, "SANDBOX_VOLUME_OWNER=default")
		output, err := command.CombinedOutput()
		exit, ok := err.(*exec.ExitError)
		if !ok || exit.ExitCode() != 27 {
			t.Fatalf("want Podman exit27, got %v %s", err, output)
		}
		if test.forbidden != "" {
			for _, call := range lifecycleCalls(t, log) {
				if strings.HasPrefix(strings.Join(call, " "), test.forbidden) {
					t.Fatalf("mutation after failure: %v", call)
				}
			}
		}
	}
}
func TestInvalidCreateOptionsFailBeforePodman(t *testing.T) {
	for _, options := range [][]string{{"--agents", "wat"}, {"--agents", "codex,codex"}, {"--agents", "codex@bad version"}, {"--agents", "codex", "--tools", "hermes-dashboard"}, {"--agents", "codex", "--cpus", "0"}, {"--agents", "codex", "--memory", "-1"}, {"--agents", "codex", "--ssh-port", "22"}, {"--agents", "codex", "--agents", "claude"}, {"--agents", "codex", "--tools", ""}} {
		command, log := lifecycleCommand(t, append([]string{"agent01", "up"}, options...)...)
		output, err := command.CombinedOutput()
		if err == nil {
			t.Fatalf("invalid options accepted: %v %s", options, output)
		}
		if calls := lifecycleCalls(t, log); len(calls) > 0 {
			t.Fatalf("validation contacted Podman: %v", calls)
		}
	}
}

func TestCreateRejectsOccupiedSSHPortBeforeMutation(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	port := fmt.Sprint(listener.Addr().(*net.TCPAddr).Port)
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--ssh-port", port)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "port") {
		t.Fatalf("expected unavailable port: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "run" || (call[0] == "volume" && call[1] == "create") {
			t.Fatalf("mutation before port check: %v", call)
		}
	}
}
