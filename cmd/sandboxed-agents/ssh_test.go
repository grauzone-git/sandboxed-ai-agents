package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func fakeSSHPodman(args []string) bool {
	if os.Getenv("SANDBOX_SSH_TEST") != "1" {
		return false
	}
	if args[0] == "port" {
		fmt.Println("127.0.0.1:2222")
		return true
	}
	if args[0] == "exec" && args[len(args)-1] == "/var/lib/agent-sshd/ssh_host_ed25519_key.pub" {
		fmt.Print(os.Getenv("SANDBOX_TEST_HOST_KEY"))
		return true
	}
	return false
}

func sshCommand(t *testing.T, args ...string) (*exec.Cmd, string, string) {
	t.Helper()
	command, log := lifecycleCommand(t, args...)
	root := t.TempDir()
	home := filepath.Join(root, "home")
	state := filepath.Join(root, "state")
	if err := os.MkdirAll(home, 0700); err != nil {
		t.Fatal(err)
	}
	hostkey := filepath.Join(root, "host-key")
	keygen, err := exec.LookPath("ssh-keygen")
	if err != nil {
		t.Fatal("SSH test needs OpenSSH ssh-keygen")
	}
	output, err := exec.Command(keygen, "-q", "-t", "ed25519", "-N", "", "-f", hostkey).CombinedOutput()
	if err != nil {
		t.Fatalf("host fixture key: %v %s", err, output)
	}
	public, err := os.ReadFile(hostkey + ".pub")
	if err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	binary, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	var path string
	for _, entry := range command.Env {
		if strings.HasPrefix(entry, "PATH=") {
			path = strings.TrimPrefix(entry, "PATH=")
		}
	}
	bin := strings.Split(path, string(os.PathListSeparator))[0]
	filename := "ssh"
	if strings.HasSuffix(executable, ".exe") {
		filename += ".exe"
	}
	if err := os.WriteFile(filepath.Join(bin, filename), binary, 0700); err != nil {
		t.Fatal(err)
	}
	command.Env = append(command.Env, "SANDBOX_SSH_TEST=1", "SANDBOX_TEST_HOST_KEY="+string(public), "HOME="+home, "USERPROFILE="+home, "XDG_STATE_HOME="+state, "LOCALAPPDATA="+state)
	return command, log, root
}

func TestSSHInstallPinsHostKeyAndPreservesExistingConfig(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	home := filepath.Join(root, "home")
	sshdir := filepath.Join(home, ".ssh")
	os.Mkdir(sshdir, 0700)
	original := "Host personal\n    HostName personal.example\n"
	config := filepath.Join(sshdir, "config")
	os.WriteFile(config, []byte(original), 0600)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	state := filepath.Join(root, "state", "sandboxed-agents", "ssh")
	got, err := os.ReadFile(config)
	if err != nil {
		t.Fatal(err)
	}
	include := `Include "` + filepath.ToSlash(filepath.Join(state, "*.conf")) + `"`
	if string(got) != include+"\n"+original {
		t.Fatalf("config changed unexpectedly: %q", got)
	}
	known, err := os.ReadFile(filepath.Join(state, "agent01", "known_hosts"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(known), "[127.0.0.1]:2222 ssh-ed25519 ") {
		t.Fatalf("host not pinned: %s", known)
	}
	entry, err := os.ReadFile(filepath.Join(state, "agent01.conf"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"Host agent01", "StrictHostKeyChecking yes", "IdentityAgent none", "ForwardAgent no", "ForwardX11 no"} {
		if !strings.Contains(string(entry), want) {
			t.Fatalf("config missing %s: %s", want, entry)
		}
	}
	key := filepath.Join(state, "agent01", "id_ed25519")
	before, err := os.ReadFile(key)
	if err != nil {
		t.Fatal(err)
	}
	again := exec.Command(command.Path, command.Args[1:]...)
	again.Env = command.Env
	again.Dir = command.Dir
	output, err = again.CombinedOutput()
	if err != nil {
		t.Fatalf("reinstall: %v %s", err, output)
	}
	after, _ := os.ReadFile(key)
	if string(before) != string(after) {
		t.Fatal("reinstall replaced private key")
	}
	got, _ = os.ReadFile(config)
	if strings.Count(string(got), include) != 1 {
		t.Fatalf("duplicate Include: %s", got)
	}
}

func cloneSSHCommand(command *exec.Cmd, args ...string) *exec.Cmd {
	next := exec.Command(command.Path, args...)
	next.Env = command.Env
	next.Dir = command.Dir
	return next
}
func TestSSHRemovalRetainsOtherSandboxAndUnrelatedFiles(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	second := cloneSSHCommand(command, "agent02", "ssh-config", "--install")
	if output, err := second.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	state := filepath.Join(root, "state", "sandboxed-agents", "ssh")
	config := filepath.Join(root, "home", ".ssh", "config")
	unrelated := "Host personal\n    HostName example.test\n"
	file, err := os.OpenFile(config, os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	file.WriteString(unrelated)
	file.Close()
	note := filepath.Join(state, "agent01", "notes.txt")
	os.WriteFile(note, []byte("keep"), 0600)
	firstRemoval := cloneSSHCommand(command, "agent01", "remove")
	if output, err := firstRemoval.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	if _, err := os.Stat(filepath.Join(state, "agent01.conf")); !os.IsNotExist(err) {
		t.Fatal("removed sandbox config retained")
	}
	if _, err := os.Stat(note); err != nil {
		t.Fatal("unrelated state file removed")
	}
	content, _ := os.ReadFile(config)
	if !strings.Contains(string(content), "Include") {
		t.Fatal("removed shared Include too early")
	}
	secondRemoval := cloneSSHCommand(command, "agent02", "remove")
	if output, err := secondRemoval.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	content, _ = os.ReadFile(config)
	if string(content) != unrelated {
		t.Fatalf("unrelated config changed: %q", content)
	}
}

func TestUpSSHIsExplicitAndValidatedBeforeProvisioning(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "up", "--agents", "codex", "--ssh-config")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	if _, err := os.Stat(filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01", "id_ed25519")); err != nil {
		t.Fatal(err)
	}
}
func TestSSHStateSymlinkBlocksRemovalBeforeStoppingContainer(t *testing.T) {
	command, log, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	state := filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01")
	target := filepath.Join(root, "saved-ssh")
	if err := os.Rename(state, target); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, state); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	os.Remove(log)
	removal := cloneSSHCommand(command, "agent01", "remove")
	output, err := removal.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "symlink") {
		t.Fatalf("unsafe removal: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "stop" || call[0] == "rm" {
			t.Fatalf("container touched before state guard: %v", call)
		}
	}
	if _, err := os.Stat(filepath.Join(target, "id_ed25519")); err != nil {
		t.Fatal("private key removed through symlink")
	}
}
func TestFailedContainerRemovalKeepsSSHAccess(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	removal := cloneSSHCommand(command, "agent01", "remove")
	removal.Env = append(removal.Env, "SANDBOX_LIFECYCLE_FAIL=rm")
	if output, err := removal.CombinedOutput(); err == nil {
		t.Fatalf("expected failed removal: %s", output)
	}
	if _, err := os.Stat(filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01.conf")); err != nil {
		t.Fatal("removed SSH after failed container rm")
	}
}

func fakeSSH() {
	file, err := os.OpenFile(os.Getenv("SANDBOX_LIFECYCLE_LOG"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		panic(err)
	}
	json.NewEncoder(file).Encode(append([]string{"ssh"}, os.Args[1:]...))
	file.Close()
	mode := os.Getenv("SANDBOX_SERVICE_SSH_FAIL")
	if (mode == "service" && strings.Contains(strings.Join(os.Args[1:], " "), "sandbox-tools service")) || (mode == "tunnel" && strings.Contains(strings.Join(os.Args[1:], " "), "ExitOnForwardFailure=yes")) {
		os.Exit(29)
	}
}
func TestCheckUsesSSHOnlyAfterOptInAndFingerprintWorks(t *testing.T) {
	command, log, _ := sshCommand(t, "agent01", "check")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "ssh" {
			t.Fatal("plain check used SSH")
		}
	}
	install := cloneSSHCommand(command, "agent01", "ssh-config", "--install")
	if output, err := install.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	check := cloneSSHCommand(command, "agent01", "check")
	if output, err := check.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	found := false
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "ssh" {
			found = true
			if !strings.Contains(strings.Join(call, " "), "BatchMode=yes agent01 true") {
				t.Fatalf("wrong SSH check: %v", call)
			}
		}
	}
	if !found {
		t.Fatal("configured check did not verify SSH access")
	}
	fingerprint := cloneSSHCommand(command, "agent01", "fingerprint")
	output, err := fingerprint.CombinedOutput()
	if err != nil || !strings.Contains(string(output), "SHA256:") {
		t.Fatalf("fingerprint: %v %s", err, output)
	}
}

func TestSSHInstallPreservesUTF8BOMAndRejectsForeignOwner(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	config := filepath.Join(root, "home", ".ssh", "config")
	os.MkdirAll(filepath.Dir(config), 0700)
	original := "\ufeffHost personal\r\n    HostName personal.example\r\n"
	os.WriteFile(config, []byte(original), 0600)
	command.Env = append(command.Env, "SANDBOX_CONTAINER_OWNER=foreign")
	if output, err := command.CombinedOutput(); err == nil {
		t.Fatalf("foreign install allowed: %s", output)
	}
	got, _ := os.ReadFile(config)
	if string(got) != original {
		t.Fatal("foreign install edited config")
	}
	install := cloneSSHCommand(command, "agent01", "ssh-config", "--install")
	install.Env = append(install.Env, "SANDBOX_CONTAINER_OWNER=default")
	if output, err := install.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	got, _ = os.ReadFile(config)
	if !strings.HasPrefix(string(got), "\ufeffInclude ") || !strings.HasSuffix(string(got), strings.TrimPrefix(original, "\ufeff")) {
		t.Fatalf("BOM/unrelated content changed: %q", got)
	}
}
func TestSSHFailedAuthorizationPreservesHostConfiguration(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	command.Env = append(command.Env, "SANDBOX_LIFECYCLE_FAIL=exec")
	config := filepath.Join(root, "home", ".ssh", "config")
	os.MkdirAll(filepath.Dir(config), 0700)
	original := "Host personal\n    HostName example.test\n"
	os.WriteFile(config, []byte(original), 0600)
	if output, err := command.CombinedOutput(); err == nil {
		t.Fatalf("expected authorization failure: %s", output)
	}
	got, _ := os.ReadFile(config)
	if string(got) != original {
		t.Fatal("failed authorization edited host config")
	}
}

func TestExistingSSHConfigCanBeReinstalledOffline(t *testing.T) {
	command, log, _ := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	os.Remove(log)
	offline := cloneSSHCommand(command, "agent01", "ssh-config", "--install")
	offline.Env = append(offline.Env, "PATH="+t.TempDir())
	if output, err := offline.CombinedOutput(); err != nil {
		t.Fatalf("offline reinstall: %v %s", err, output)
	}
	if len(lifecycleCalls(t, log)) > 0 {
		t.Fatal("existing configuration contacted Podman")
	}
}
func TestStartCanExplicitlyConfigureSSH(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "start", "--ssh-config")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	if _, err := os.Stat(filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01.conf")); err != nil {
		t.Fatal(err)
	}
}

func TestSSHGeneratedConfigurationIsAcceptedByOpenSSH(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	config := filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01.conf")
	// -G expands the configuration without connecting to a host.
	output, err := exec.Command("ssh", "-G", "-F", config, "agent01").CombinedOutput()
	if err != nil {
		t.Fatalf("OpenSSH rejected generated config: %v %s", err, output)
	}
	for _, want := range []string{"hostname 127.0.0.1", "user agent", "port 2222", "stricthostkeychecking true"} {
		if !strings.Contains(string(output), want) {
			t.Fatalf("missing %s: %s", want, output)
		}
	}
}

func TestSSHPrivateFilesAreOwnerOnlyOnUnix(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows ACL is covered by the native ACL test")
	}
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	state := filepath.Join(root, "state", "sandboxed-agents", "ssh")
	for _, path := range []string{filepath.Join(state, "agent01", "id_ed25519"), filepath.Join(state, "agent01", "known_hosts"), filepath.Join(state, "agent01.conf"), filepath.Join(root, "home", ".ssh", "config")} {
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0600 {
			t.Fatalf("mode %o for %s", info.Mode().Perm(), path)
		}
	}
}

func TestSSHInstallPreservesGlobalDefaultsForUnrelatedHosts(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	config := filepath.Join(root, "home", ".ssh", "config")
	os.MkdirAll(filepath.Dir(config), 0700)
	original := "Compression yes\nHost personal\n    HostName example.test\n"
	os.WriteFile(config, []byte(original), 0600)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	output, err := exec.Command("ssh", "-G", "-F", config, "unrelated.example").CombinedOutput()
	if err != nil || !strings.Contains(string(output), "compression yes") {
		t.Fatalf("global defaults changed: %v %s", err, output)
	}
}
