package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

func fakeCapabilityPodman(args []string) bool {
	source := os.Getenv("SANDBOX_CAPABILITY_PROFILE")
	if source == "" {
		return false
	}
	if args[0] == "info" && args[len(args)-1] == "{{json .Host.Security}}" {
		json.NewEncoder(os.Stdout).Encode(map[string]string{"seccompProfilePath": source})
		return true
	}
	if args[0] == "image" && args[1] == "inspect" {
		if os.Getenv("SANDBOX_CAPABILITY_INSPECT_FAIL") == "1" {
			os.Exit(29)
		}
		fmt.Println(strings.Repeat("a", 64))
		return true
	}
	if args[0] == "build" && slices.Contains(args, "--pull=never") {
		context := args[len(args)-1]
		for _, name := range []string{"Containerfile.podman", "nested-podman.cjs", "nested-storage.conf"} {
			if _, err := os.Stat(filepath.Join(context, name)); err != nil {
				panic(err)
			}
		}
		if os.Getenv("SANDBOX_CAPABILITY_BUILD_FAIL") == "1" {
			os.Exit(31)
		}
		fmt.Println(strings.Repeat("b", 64))
		return true
	}
	return false
}

func TestPodmanCapabilityUsesDerivedImageAndRestrictedStateProfile(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("machine seccomp staging is implemented in #43")
	}
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
	source := filepath.Join(t.TempDir(), "host-seccomp.json")
	if err := os.WriteFile(source, []byte(`{"defaultAction":"SCMP_ACT_ERRNO","defaultErrnoRet":1,"syscalls":[{"names":["setns","read"],"action":"SCMP_ACT_ALLOW"},{"names":["sethostname","quotactl"],"action":"SCMP_ACT_ERRNO","errnoRet":1},{"names":["setdomainname"],"action":"SCMP_ACT_ERRNO"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE="+source)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %s", err, output)
	}
	var run, build []string
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "run" {
			run = call
		}
		if call[0] == "build" {
			build = call
		}
	}
	for _, want := range []string{"--device=/dev/fuse", "--device=/dev/net/tun", "--security-opt=label=disable", "--security-opt=apparmor=unconfined", "--security-opt=unmask=ALL", "/run/user/1000:rw,nosuid,nodev,noexec,mode=0700", "io.sandboxed-agents.capabilities=podman", strings.Repeat("b", 64)} {
		if !slices.Contains(run, want) {
			t.Fatalf("run missing %q: %v", want, run)
		}
	}
	if slices.Contains(run, "--privileged") || slices.Contains(run, "--security-opt=no-new-privileges") {
		t.Fatalf("invalid nested security: %v", run)
	}
	for _, arg := range run {
		if strings.HasPrefix(arg, "--cap-add") || arg == "--security-opt=seccomp=unconfined" {
			t.Fatalf("nested runtime lost syscall/capability restrictions: %v", run)
		}
	}
	if !slices.Contains(build, "BASE_IMAGE="+strings.Repeat("a", 64)) {
		t.Fatalf("build did not pin base image: %v", build)
	}
	if _, err := os.Stat(build[len(build)-1]); !os.IsNotExist(err) {
		t.Fatalf("derived context remains: %v", err)
	}
	profile := ""
	for _, arg := range run {
		if strings.HasPrefix(arg, "--security-opt=seccomp=") {
			profile = strings.TrimPrefix(arg, "--security-opt=seccomp=")
		}
	}
	if !strings.Contains(profile, string(filepath.Separator)+"sandboxed-agents"+string(filepath.Separator)) || !strings.Contains(profile, "0.1.0-dev") {
		t.Fatalf("profile outside versioned state: %s", profile)
	}
	data, err := os.ReadFile(profile)
	if err != nil {
		t.Fatal(err)
	}
	var policy map[string]any
	if err := json.Unmarshal(data, &policy); err != nil {
		t.Fatal(err)
	}
	expected := `{"defaultAction":"SCMP_ACT_ERRNO","defaultErrnoRet":1,"syscalls":[{"names":["read"],"action":"SCMP_ACT_ALLOW"},{"names":["quotactl"],"action":"SCMP_ACT_ERRNO","errnoRet":1},{"names":["sethostname","setdomainname","setns"],"action":"SCMP_ACT_ALLOW"}]}`
	var want map[string]any
	json.Unmarshal([]byte(expected), &want)
	actual, _ := json.Marshal(policy)
	wanted, _ := json.Marshal(want)
	if string(actual) != string(wanted) {
		t.Fatalf("policy changed unrelated restrictions:\n%s\nwant %s", actual, wanted)
	}
}

func TestPodmanCapabilityRejectsPermissiveHostPolicyBeforeProvisioning(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("machine seccomp staging is implemented in #43")
	}
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
	source := filepath.Join(t.TempDir(), "seccomp.json")
	if err := os.WriteFile(source, []byte(`{"defaultAction":"SCMP_ACT_ALLOW","syscalls":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE="+source)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "deny-by-default") {
		t.Fatalf("unsafe host policy accepted: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "build" || call[0] == "run" || (call[0] == "volume" && call[1] == "create") {
			t.Fatalf("provisioned with unsafe profile: %v", call)
		}
	}
}

func TestPodmanCapabilityRefreshesStoredPolicyWhenContentDiffers(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("machine seccomp staging is implemented in #43")
	}
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
	source := filepath.Join(t.TempDir(), "seccomp.json")
	if err := os.WriteFile(source, []byte(`{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE="+source)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	profile := ""
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "run" {
			for _, arg := range call {
				if strings.HasPrefix(arg, "--security-opt=seccomp=") {
					profile = strings.TrimPrefix(arg, "--security-opt=seccomp=")
				}
			}
		}
	}
	original, err := os.ReadFile(profile)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(profile, []byte(`{"defaultAction":"SCMP_ACT_ALLOW"}`), 0600); err != nil {
		t.Fatal(err)
	}
	again := exec.Command(command.Path, command.Args[1:]...)
	again.Env = command.Env
	again.Dir = command.Dir
	output, err = again.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	restored, err := os.ReadFile(profile)
	if err != nil {
		t.Fatal(err)
	}
	if string(restored) != string(original) {
		t.Fatalf("stored policy not refreshed: %s", restored)
	}
	info, err := os.Stat(profile)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0600 {
		t.Fatalf("policy mode %o", info.Mode().Perm())
	}
}

func TestPodmanCapabilityFailureLeavesVolumesAndWorkspaceUntouched(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("machine seccomp staging is implemented in #43")
	}
	for _, test := range []struct {
		env  string
		code int
	}{{"SANDBOX_CAPABILITY_INSPECT_FAIL=1", 29}, {"SANDBOX_CAPABILITY_BUILD_FAIL=1", 31}} {
		t.Run(test.env, func(t *testing.T) {
			workspace := filepath.Join(t.TempDir(), "new-workspace")
			command, log := lifecycleCommand(t, "agent01", "up", workspace, "--agents", "codex", "--capabilities", "podman")
			source := filepath.Join(t.TempDir(), "seccomp.json")
			if err := os.WriteFile(source, []byte(`{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}`), 0600); err != nil {
				t.Fatal(err)
			}
			command.Env = append(command.Env, "SANDBOX_CAPABILITY_PROFILE="+source, "SANDBOX_IMAGE=localhost/custom:v2", test.env)
			output, err := command.CombinedOutput()
			exit, ok := err.(*exec.ExitError)
			if !ok || exit.ExitCode() != test.code {
				t.Fatalf("wrong failure: %v %s", err, output)
			}
			inspected := false
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "run" || (call[0] == "volume" && call[1] == "create") {
					t.Fatalf("provisioned after capability failure: %v", call)
				}
				if call[0] == "image" && call[1] == "inspect" {
					inspected = true
					if call[len(call)-1] != "localhost/custom:v2" {
						t.Fatalf("ignored configured base image: %v", call)
					}
				}
				if call[0] == "build" {
					if _, err := os.Stat(call[len(call)-1]); !os.IsNotExist(err) {
						t.Fatalf("failed build left context: %v", err)
					}
				}
			}
			if !inspected {
				t.Fatal("base image was not inspected")
			}
			if _, err := os.Stat(workspace); !os.IsNotExist(err) {
				t.Fatalf("workspace created after capability failure: %v", err)
			}
		})
	}
}

func TestCheckAcceptsOnlyEphemeralNestedRuntimeMount(t *testing.T) {
	for _, kind := range []string{"tmpfs", "bind", "volume"} {
		t.Run(kind, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", "check")
			mounts := `[{"Type":"volume","Name":"agent01-home","Destination":"/home/agent"},{"Type":"volume","Name":"agent01-sshd","Destination":"/var/lib/agent-sshd"},{"Type":"volume","Name":"agent01-workspace","Destination":"/workspace"},{"Type":"` + kind + `","Source":"/host/runtime","Name":"runtime","Destination":"/run/user/1000"}]`
			command.Env = append(command.Env, "SANDBOX_MOUNTS="+mounts)
			output, err := command.CombinedOutput()
			if kind == "tmpfs" {
				if err != nil {
					t.Fatalf("ephemeral runtime rejected: %v %s", err, output)
				}
			} else {
				if err == nil {
					t.Fatalf("persistent runtime accepted: %s", output)
				}
				for _, call := range lifecycleCalls(t, log) {
					if call[0] == "exec" {
						t.Fatalf("smoke ran with persistent runtime: %v", call)
					}
				}
			}
		})
	}
}

func TestCapabilitySelectionIsExplicitAndValidatedBeforePodman(t *testing.T) {
	for _, value := range []string{"", "docker", "podman,none", "podman,podman", "podman,", "all"} {
		t.Run(value, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", value)
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), "capabilities") {
				t.Fatalf("bad capability accepted: %v %s", err, output)
			}
			if calls := lifecycleCalls(t, log); len(calls) > 0 {
				t.Fatalf("invalid capability reached Podman: %v", calls)
			}
		})
	}
	command, log := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "none")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "build" || (call[0] == "image" && call[1] == "inspect") {
			t.Fatalf("unrequested capability preparation: %v", call)
		}
		if call[0] == "run" && (!slices.Contains(call, "--security-opt=no-new-privileges") || !slices.Contains(call, "io.sandboxed-agents.capabilities=none")) {
			t.Fatalf("default isolation not retained: %v", call)
		}
	}
}

func TestSeccompCacheRejectsRedirectsAndRepairsPermissions(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux state permissions")
	}
	for _, kind := range []string{"permissions", "file-link", "directory-link"} {
		t.Run(kind, func(t *testing.T) {
			command, _ := lifecycleCommand(t, "agent01", "up", "--agents", "codex", "--capabilities", "podman")
			root := t.TempDir()
			state := filepath.Join(root, "sandboxed-agents")
			directory := filepath.Join(state, "seccomp", "0.1.0-dev")
			profile := filepath.Join(directory, "nested-podman.json")
			source := filepath.Join(t.TempDir(), "source.json")
			if err := os.WriteFile(source, []byte(`{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}`), 0600); err != nil {
				t.Fatal(err)
			}
			command.Env = append(command.Env, "XDG_STATE_HOME="+root, "SANDBOX_CAPABILITY_PROFILE="+source)
			if output, err := command.CombinedOutput(); err != nil {
				t.Fatalf("%v %s", err, output)
			}
			switch kind {
			case "permissions":
				for _, path := range []string{state, filepath.Dir(directory), directory, profile} {
					if err := os.Chmod(path, 0777); err != nil {
						t.Fatal(err)
					}
				}
			case "file-link":
				content, err := os.ReadFile(profile)
				if err != nil {
					t.Fatal(err)
				}
				outside := filepath.Join(t.TempDir(), "outside.json")
				if err := os.WriteFile(outside, content, 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Remove(profile); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(outside, profile); err != nil {
					t.Fatal(err)
				}
			case "directory-link":
				outside := filepath.Join(t.TempDir(), "outside")
				if err := os.Rename(directory, outside); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(outside, directory); err != nil {
					t.Fatal(err)
				}
			}
			again := exec.Command(command.Path, command.Args[1:]...)
			again.Env = command.Env
			again.Dir = command.Dir
			output, err := again.CombinedOutput()
			if kind != "permissions" {
				if err == nil {
					t.Fatalf("redirected state accepted: %s", output)
				}
				return
			}
			if err != nil {
				t.Fatalf("%v %s", err, output)
			}
			for _, path := range []string{state, filepath.Dir(directory), directory, profile} {
				info, err := os.Stat(path)
				if err != nil {
					t.Fatal(err)
				}
				want := os.FileMode(0700)
				if path == profile {
					want = 0600
				}
				if info.Mode().Perm() != want {
					t.Errorf("%s mode %o, want %o", path, info.Mode().Perm(), want)
				}
			}
		})
	}
}
