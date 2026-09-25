package main

import (
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func installedSSHCommand(t *testing.T, args ...string) (*exec.Cmd, string, string) {
	t.Helper()
	command, log, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("SSH fixture: %v %s", err, output)
	}
	if err := os.Remove(log); err != nil {
		t.Fatal(err)
	}
	if len(args) == 0 {
		args = []string{"agent01", "ssh-config", "--install"}
	}
	return cloneSSHCommand(command, args...), log, root
}

func freeLoopbackPort(t *testing.T) string {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, port, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	return port
}

func TestForwardRequiresSSHBeforeStartingService(t *testing.T) {
	command, _, _ := sshCommand(t, "agent01", "forward", "t3")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "ssh-config --install") {
		t.Fatalf("missing opt-in diagnostic: %v %s", err, output)
	}
}

func TestServicesAndForwardUsePinnedSSH(t *testing.T) {
	for _, id := range []string{"t3", "hermes", "deepseek", "tokentracker"} {
		t.Run(id, func(t *testing.T) {
			command, log, _ := installedSSHCommand(t)
			port := freeLoopbackPort(t)
			forward := cloneSSHCommand(command, "agent01", "forward", id, port)
			if output, err := forward.CombinedOutput(); err != nil {
				t.Fatalf("%v %s", err, output)
			}
			calls := lifecycleCalls(t, log)
			var ssh [][]string
			for _, call := range calls {
				if call[0] == "ssh" {
					ssh = append(ssh, call)
				}
			}
			if len(ssh) != 2 {
				t.Fatalf("expected service and tunnel: %v", calls)
			}
			if !strings.Contains(strings.Join(ssh[0], " "), "service") || !strings.Contains(strings.Join(ssh[0], " "), "start") {
				t.Fatalf("service not started: %v", ssh[0])
			}
			for _, call := range ssh {
				if !strings.Contains(strings.Join(call, " "), "StrictHostKeyChecking=yes") {
					t.Fatalf("unpinned: %v", call)
				}
			}
			if !strings.Contains(strings.Join(ssh[1], " "), "127.0.0.1:"+port+":127.0.0.1:") {
				t.Fatalf("not loopback: %v", ssh[1])
			}
		})
	}
}

func TestToolSetupPreservesArgumentsWithoutSSH(t *testing.T) {
	for _, args := range [][]string{{"t3"}, {"azdo"}, {"azdo", "--persist"}, {"azdo", "--clear"}} {
		command, log := lifecycleCommand(t, append([]string{"agent01", "tools", "setup"}, args...)...)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%v %s", err, output)
		}
		found := false
		for _, call := range lifecycleCalls(t, log) {
			if call[0] == "ssh" {
				t.Fatal("setup unexpectedly required SSH")
			}
			if call[0] == "exec" && strings.Contains(strings.Join(call, " "), "/usr/local/bin/sandbox-tools setup "+strings.Join(args, " ")) {
				want := append([]string{"exec", "-i", "--user", "1000:1000", "--workdir", "/workspace", "agent01", "/usr/local/bin/sandbox-tools", "setup"}, args...)
				if !reflect.DeepEqual(call, want) {
					t.Fatalf("incorrect setup transport: %v; want %v", call, want)
				}
				found = true
			}
		}
		if !found {
			t.Fatalf("setup missing: %v", lifecycleCalls(t, log))
		}
	}
}

func TestInvalidServiceAndSetupInputsDoNotContactPodman(t *testing.T) {
	for _, args := range [][]string{{"service", "evil;command"}, {"service", "t3", "unknown"}, {"forward", "t3", "80"}, {"tools", "setup", "azdo", "--persist", "--clear"}, {"tools", "setup", "t3", "unexpected"}} {
		command, log := lifecycleCommand(t, append([]string{"agent01"}, args...)...)
		if output, err := command.CombinedOutput(); err == nil {
			t.Fatalf("accepted invalid: %s", output)
		}
		if _, err := os.Stat(log); !os.IsNotExist(err) {
			t.Fatalf("invalid input contacted engine: %v", err)
		}
	}
}

func TestServiceOperationsPreserveRemoteStatus(t *testing.T) {
	for _, operation := range []string{"", "status", "start", "stop", "restart", "logs"} {
		t.Run(operation, func(t *testing.T) {
			command, log, _ := installedSSHCommand(t)
			args := []string{"agent01", "service", "hermes"}
			if operation != "" {
				args = append(args, operation)
			} else {
				operation = "status"
			}
			service := cloneSSHCommand(command, args...)
			service.Env = append(service.Env, "SANDBOX_SERVICE_SSH_FAIL=service")
			output, err := service.CombinedOutput()
			if err == nil || service.ProcessState.ExitCode() != 29 {
				t.Fatalf("remote failure lost: %v %s", err, output)
			}
			calls := lifecycleCalls(t, log)
			last := calls[len(calls)-1]
			if last[0] != "ssh" || last[len(last)-1] != "/usr/local/bin/sandbox-tools service 'hermes-dashboard' '"+operation+"'" {
				t.Fatalf("wrong remote operation: %v", calls)
			}
		})
	}
}

func TestForwardFailurePropagation(t *testing.T) {
	for _, mode := range []string{"service", "tunnel"} {
		t.Run(mode, func(t *testing.T) {
			command, log, _ := installedSSHCommand(t)
			port := freeLoopbackPort(t)
			forward := cloneSSHCommand(command, "agent01", "forward", "tokentracker", port)
			forward.Env = append(forward.Env, "SANDBOX_SERVICE_SSH_FAIL="+mode)
			output, err := forward.CombinedOutput()
			if err == nil || forward.ProcessState.ExitCode() != 29 {
				t.Fatalf("failure lost: %v %s", err, output)
			}
			sshCalls := 0
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "ssh" {
					sshCalls++
				}
			}
			want := 1
			if mode == "tunnel" {
				want = 2
			}
			if sshCalls != want {
				t.Fatalf("unexpected SSH calls after failure: %v", lifecycleCalls(t, log))
			}
		})
	}
}

func TestServicesAndSetupRejectForeignOwnership(t *testing.T) {
	for _, args := range [][]string{{"service", "t3"}, {"forward", "t3"}, {"tools", "setup", "t3"}, {"tools", "setup", "azdo", "--clear"}} {
		command, log, _ := installedSSHCommand(t)
		foreign := cloneSSHCommand(command, append([]string{"agent01"}, args...)...)
		foreign.Env = append(foreign.Env, "SANDBOX_CONTAINER_OWNER=other")
		if output, err := foreign.CombinedOutput(); err == nil {
			t.Fatalf("foreign resource accepted: %s", output)
		}
		for _, call := range lifecycleCalls(t, log) {
			if call[0] == "ssh" || call[0] == "exec" {
				t.Fatalf("foreign resource contacted: %v", call)
			}
		}
	}
}

func TestToolSetupPropagatesManagerFailure(t *testing.T) {
	for _, target := range []string{"t3", "azdo", "azure"} {
		command, _ := lifecycleCommand(t, "agent01", "tools", "setup", target)
		command.Env = append(command.Env, "SANDBOX_LIFECYCLE_FAIL=exec")
		output, err := command.CombinedOutput()
		if err == nil || command.ProcessState.ExitCode() != 27 {
			t.Fatalf("setup failure lost: %v %s", err, output)
		}
	}
}

func TestServiceRequiresExplicitSSHSetup(t *testing.T) {
	command, log, home := sshCommand(t, "agent01", "service", "tokentracker", "start")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "ssh-config --install") {
		t.Fatalf("missing opt-in diagnostic: %v %s", err, output)
	}
	if calls := lifecycleCalls(t, log); len(calls) != 0 {
		t.Fatalf("missing SSH setup contacted engine or SSH: %v", calls)
	}
	if entries, err := os.ReadDir(filepath.Join(home, "home")); err != nil || len(entries) != 0 {
		t.Fatalf("missing opt-in changed home: %v %v", entries, err)
	}
}

func TestForwardRejectsOccupiedPortBeforeService(t *testing.T) {
	for _, address := range []string{"127.0.0.1:0", "0.0.0.0:0"} {
		t.Run(address, func(t *testing.T) {
			command, log, _ := installedSSHCommand(t)
			listener, err := net.Listen("tcp4", address)
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			_, port, err := net.SplitHostPort(listener.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			forward := cloneSSHCommand(command, "agent01", "forward", "t3", port)
			output, err := forward.CombinedOutput()
			if err == nil || !strings.Contains(string(output), "unavailable") {
				t.Fatalf("occupied port accepted: %v %s", err, output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "ssh" || call[0] == "exec" {
					t.Fatalf("started with occupied port: %v", call)
				}
			}
		})
	}
}
