package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBindWorkspaceWithSpaces(t *testing.T) {
	workspace := filepath.Join(t.TempDir(), "project with spaces")
	command, log := lifecycleCommand(t, "agent01", "up", workspace, "--agents", "codex")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %s", err, output)
	}
	workspaceInfo, err := os.Stat(workspace)
	if err != nil || !workspaceInfo.IsDir() {
		t.Fatalf("workspace missing: %v", err)
	}
	found := false
	for _, call := range lifecycleCalls(t, log) {
		for _, arg := range call {
			if source, ok := strings.CutSuffix(arg, ":/workspace:Z"); ok {
				sourceInfo, err := os.Stat(source)
				if err != nil {
					t.Fatalf("bind source missing: %v", err)
				}
				found = os.SameFile(workspaceInfo, sourceInfo)
			}
		}
		if call[0] == "volume" && call[1] == "create" && call[len(call)-1] == "agent01-workspace" {
			t.Fatalf("bind created workspace volume: %v", call)
		}
	}
	if !found {
		t.Fatalf("missing bind: %s", output)
	}
}

func TestProtectedWorkspaceRefusedBeforeMutation(t *testing.T) {
	for _, kind := range []string{"state", "ssh", "executable", "build"} {
		t.Run(kind, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", "up", "placeholder", "--agents", "codex")
			var workspace string
			switch kind {
			case "state":
				state := filepath.Join(t.TempDir(), "state")
				command.Env = append(command.Env, "XDG_STATE_HOME="+state, "LOCALAPPDATA="+state)
				workspace = state
			case "ssh":
				home := t.TempDir()
				command.Env = append(command.Env, "HOME="+home, "USERPROFILE="+home)
				workspace = filepath.Join(home, ".ssh", "new")
			case "executable":
				executable, err := os.Executable()
				if err != nil {
					t.Fatal(err)
				}
				workspace = filepath.Dir(executable)
			case "build":
				root, err := os.MkdirTemp("", "sandboxed-agents-build-")
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { os.RemoveAll(root) })
				workspace = root
			}
			command.Args[3] = workspace
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("protected bind accepted: %s", output)
			}
			if !strings.Contains(strings.ToLower(string(output)), "workspace") {
				t.Fatalf("not workspace diagnostic: %s", output)
			}
			if kind == "executable" && !strings.Contains(strings.ToLower(string(output)), "global install") {
				t.Fatalf("missing global install advice: %s", output)
			}
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "run" || call[0] == "volume" && call[1] == "create" {
					t.Fatalf("protected bind mutated storage: %v", call)
				}
			}
		})
	}
}

func TestProjectLocalLauncherSymlinkCannotExposeWorkspace(t *testing.T) {
	workspace := t.TempDir()
	command, log := lifecycleCommand(t, "agent01", "up", workspace, "--agents", "codex")
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(workspace, "sandboxed-agents")
	if strings.HasSuffix(executable, ".exe") {
		link += ".exe"
	}
	if err = os.Symlink(executable, link); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	command.Path = link
	command.Args[0] = link
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "global install") {
		t.Fatalf("local launcher accepted: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "run" {
			t.Fatalf("created unsafe bind: %v", call)
		}
	}
}

func TestWorkspaceSymlinkIntoStateIsRejected(t *testing.T) {
	state := t.TempDir()
	protected := filepath.Join(state, "sandboxed-agents")
	if err := os.Mkdir(protected, 0700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(t.TempDir(), "state-link")
	if err := os.Symlink(protected, link); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	command, log := lifecycleCommand(t, "agent01", "up", filepath.Join(link, "new-workspace"), "--agents", "codex")
	command.Env = append(command.Env, "XDG_STATE_HOME="+state, "LOCALAPPDATA="+state)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "protected path") {
		t.Fatalf("unsafe state alias: %v %s", err, output)
	}
	if _, err := os.Stat(filepath.Join(protected, "new-workspace")); !os.IsNotExist(err) {
		t.Fatalf("created protected directory: %v", err)
	}
	if len(lifecycleCalls(t, log)) != 0 {
		t.Fatal("unsafe bind contacted Podman")
	}
}

func TestCheckAllowsSafeWorkspaceBindOnly(t *testing.T) {
	for _, destination := range []string{"/workspace", "/home/agent"} {
		t.Run(destination, func(t *testing.T) {
			workspace := t.TempDir()
			mounts := []map[string]string{
				{"Type": "volume", "Name": "agent01-home", "Destination": "/home/agent"},
				{"Type": "volume", "Name": "agent01-sshd", "Destination": "/var/lib/agent-sshd"},
				{"Type": "volume", "Name": "agent01-workspace", "Destination": "/workspace"},
			}
			for _, mount := range mounts {
				if mount["Destination"] == destination {
					mount["Type"] = "bind"
					mount["Source"] = workspace
					delete(mount, "Name")
				}
			}
			data, err := json.Marshal(mounts)
			if err != nil {
				t.Fatal(err)
			}
			command, _ := lifecycleCommand(t, "agent01", "check")
			command.Env = append(command.Env, "SANDBOX_MOUNTS="+string(data))
			output, err := command.CombinedOutput()
			if (err == nil) != (destination == "/workspace") {
				t.Fatalf("bind policy: %v %s", err, output)
			}
		})
	}
}

func TestWorkspaceCannotExposeFutureBuildContexts(t *testing.T) {
	temporary := t.TempDir()
	command, log := lifecycleCommand(t, "agent01", "up", temporary, "--agents", "codex")
	command.Env = append(command.Env, "TMPDIR="+temporary, "TEMP="+temporary, "TMP="+temporary)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "build") {
		t.Fatalf("future build parent accepted: %v %s", err, output)
	}
	if len(lifecycleCalls(t, log)) != 0 {
		t.Fatal("unsafe build parent contacted Podman")
	}
}
