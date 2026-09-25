package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"
)

func azureAuthorization(host, port string, overrides url.Values) string {
	query := url.Values{"redirect_uri": {"http://localhost:" + port}, "response_type": {"code"}, "state": {"dummy-state"}}
	for key, values := range overrides {
		query[key] = values
	}
	return "https://" + host + "/tenant/oauth2/v2.0/authorize?" + query.Encode()
}

func TestAzureSetupRejectsInvalidOptionsBeforePodman(t *testing.T) {
	for _, args := range [][]string{{"--cloud", "OtherCloud"}, {"--tenant", "bad tenant"}, {"--subscription", "secret\nvalue"}, {"--interactive", "--interactive"}, {"--tenant-only", "--subscription", "demo"}, {"--cloud"}} {
		command, log := lifecycleCommand(t, append([]string{"agent01", "tools", "setup", "azure"}, args...)...)
		output, err := command.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "Azure") {
			t.Fatalf("expected Azure validation: %v %s", err, output)
		}
		if calls := lifecycleCalls(t, log); len(calls) > 0 {
			t.Fatalf("validation reached Podman: %v", calls)
		}
	}
}

func azureRecord(args []string) {
	file, err := os.OpenFile(filepath.Join(os.Getenv("SANDBOX_AZURE_ROOT"), "calls.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		panic(err)
	}
	defer file.Close()
	json.NewEncoder(file).Encode(args)
}
func fakeAzureSSH() bool {
	root := os.Getenv("SANDBOX_AZURE_ROOT")
	if root == "" {
		return false
	}
	azureRecord(append([]string{"ssh"}, os.Args[1:]...))
	mode := os.Getenv("SANDBOX_AZURE_MODE")
	if slices.Contains(os.Args, "-L") {
		if mode == "forward-fail" {
			os.Exit(5)
		}
		bind := os.Args[slices.Index(os.Args, "-L")+1]
		parts := strings.Split(bind, ":")
		listener, err := net.Listen("tcp4", "127.0.0.1:"+parts[1])
		if err != nil {
			panic(err)
		}
		server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			os.WriteFile(filepath.Join(root, "forward-verified"), nil, 0600)
			w.WriteHeader(501)
		})}
		go server.Serve(listener)
		if mode != "no-marker" {
			fmt.Println("SANDBOX_AZURE_FORWARD")
		}
		io.Copy(io.Discard, os.Stdin)
		server.Close()
		os.WriteFile(filepath.Join(root, "forward-closed"), nil, 0600)
		return true
	}
	defer os.WriteFile(filepath.Join(root, "login-closed"), nil, 0600)
	emit := func(value any) { json.NewEncoder(os.Stdout).Encode(value) }
	if mode == "bad-prompt" {
		emit(map[string]string{"event": "prompt", "value": "never-print-this-token"})
		io.Copy(io.Discard, os.Stdin)
		return true
	}
	if mode == "bad-event" {
		emit(map[string]string{"event": "token", "value": "never-print-this-token"})
		io.Copy(io.Discard, os.Stdin)
		return true
	}
	if mode == "prompts" {
		scanner := bufio.NewScanner(os.Stdin)
		for _, kind := range []string{"tenant", "subscription"} {
			emit(map[string]string{"event": "prompt", "value": kind})
			if !scanner.Scan() {
				return true
			}
			os.WriteFile(filepath.Join(root, kind+"-answer"), []byte(scanner.Text()), 0600)
		}
	}
	cloud := os.Getenv("SANDBOX_AZURE_CLOUD")
	if cloud == "" {
		cloud = "AzureCloud"
	}
	emit(map[string]string{"event": "browser", "cloud": cloud, "value": os.Getenv("SANDBOX_AZURE_URL")})
	if mode == "browser-idle" {
		io.Copy(io.Discard, os.Stdin)
		return true
	}
	deadline := time.Now().Add(20 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(root, "browser-opened")); err == nil {
			break
		}
		if time.Now().After(deadline) {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	if mode == "second-browser" {
		emit(map[string]string{"event": "browser", "cloud": "AzureCloud", "value": os.Getenv("SANDBOX_AZURE_URL")})
		io.Copy(io.Discard, os.Stdin)
		return true
	}
	if mode == "hang" {
		io.Copy(io.Discard, os.Stdin)
		return true
	}
	emit(map[string]string{"event": "ready"})
	scanner := bufio.NewScanner(os.Stdin)
	if !scanner.Scan() {
		return true
	}
	if mode == "hang-after-commit" {
		os.WriteFile(filepath.Join(root, "committed"), nil, 0600)
		io.Copy(io.Discard, os.Stdin)
		return true
	}
	if mode == "error-after-commit" {
		emit(map[string]string{"event": "error", "value": "network"})
		return true
	}
	if mode == "commit-eof" {
		return true
	}
	emit(map[string]string{"event": "complete"})
	if mode == "complete-exit" {
		os.Exit(29)
	}
	return true
}
func fakeAzureBrowser() {
	azureRecord(append([]string{"browser"}, os.Args[1:]...))
	root := os.Getenv("SANDBOX_AZURE_ROOT")
	if os.Getenv("SANDBOX_AZURE_MODE") == "browser-fail" {
		fmt.Fprintln(os.Stderr, "never-print-this-token")
		os.Exit(13)
	}
	if _, err := os.Stat(filepath.Join(root, "forward-verified")); err != nil {
		panic("browser opened before forward verification")
	}
	local := os.Args[len(os.Args)-1]
	if strings.Contains(local, "dummy-state") || strings.Contains(local, "oauth2") {
		panic("OAuth URL leaked to browser argv")
	}
	client := http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Get(local)
	if err != nil {
		panic(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 302 || response.Header.Get("Location") != os.Getenv("SANDBOX_AZURE_URL") || response.Header.Get("Cache-Control") != "no-store" || response.Header.Get("Referrer-Policy") != "no-referrer" {
		panic("invalid private browser redirect")
	}
	os.WriteFile(filepath.Join(root, "browser-opened"), nil, 0600)
}
func azureCommand(t *testing.T, mode string) (*exec.Cmd, string) {
	t.Helper()
	install, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	output, err := install.CombinedOutput()
	if err != nil {
		t.Fatalf("SSH fixture: %v %s", err, output)
	}
	command := cloneSSHCommand(install, "agent01", "tools", "setup", "azure", "--interactive", "--tenant", "tenant", "--tenant-only", "--cloud", "AzureCloud")
	port := freeLoopbackPort(t)
	authority := "login.microsoftonline.com"
	authorization := azureAuthorization(authority, port, nil)
	command.Env = append(command.Env, "SANDBOX_AZURE_ROOT="+root, "SANDBOX_AZURE_MODE="+mode, "SANDBOX_AZURE_URL="+authorization)
	var search string
	for _, entry := range command.Env {
		if strings.HasPrefix(entry, "PATH=") {
			search = strings.TrimPrefix(entry, "PATH=")
		}
	}
	bin := strings.Split(search, string(os.PathListSeparator))[0]
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	opener := "xdg-open"
	if runtime.GOOS == "windows" {
		opener = "rundll32.exe"
	}
	if err := os.WriteFile(filepath.Join(bin, opener), data, 0700); err != nil {
		t.Fatal(err)
	}
	return command, root
}
func TestAzureRejectsUnexpectedAuthorityWithoutBrowserOrTokenOutput(t *testing.T) {
	command, root := azureCommand(t, "browser-idle")
	command.Env = append(command.Env, "SANDBOX_AZURE_URL="+azureAuthorization("evil.example", "45678", nil))
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "authorization endpoint") {
		t.Fatalf("unexpected authority accepted: %v %s", err, output)
	}
	if strings.Contains(string(output), "dummy-state") || strings.Contains(string(output), "evil.example") {
		t.Fatalf("authorization details leaked: %s", output)
	}
	data, err := os.ReadFile(filepath.Join(root, "calls.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "browser") || strings.Contains(string(data), "-L") {
		t.Fatalf("unsafe callback reached forward/browser: %s", data)
	}
}

func TestAzureBrowserUsesVerifiedTunnelAndPrivateRedirectThenCleansUp(t *testing.T) {
	command, root := azureCommand(t, "success")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v %s", err, output)
	}
	if !strings.Contains(string(output), "Azure setup completed") {
		t.Fatalf("no completion: %s", output)
	}
	for _, name := range []string{"browser-opened", "forward-closed", "login-closed"} {
		if _, err := os.Stat(filepath.Join(root, name)); err != nil {
			t.Fatalf("missing %s: %v", name, err)
		}
	}
	data, err := os.ReadFile(filepath.Join(root, "calls.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"dummy-state", "oauth2", "login.microsoftonline.com"} {
		if strings.Contains(string(data), secret) || strings.Contains(string(output), secret) {
			t.Fatalf("OAuth detail leaked: %s %s", data, output)
		}
	}
	var local string
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var call []string
		json.Unmarshal([]byte(line), &call)
		if call[0] == "browser" {
			local = call[len(call)-1]
		}
	}
	if local == "" {
		t.Fatal("browser not invoked")
	}
	client := http.Client{Timeout: time.Second}
	if response, err := client.Get(local); err == nil {
		response.Body.Close()
		t.Fatal("private redirect remained open after completion")
	}
}

func TestAzurePromptsPreserveMultipleInputLines(t *testing.T) {
	command, root := azureCommand(t, "prompts")
	command.Stdin = strings.NewReader("tenant-name\nsubscription name\n")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("prompt input lost: %v %s", err, output)
	}
	for _, item := range []struct{ kind, answer string }{{"tenant", "tenant-name"}, {"subscription", "subscription name"}} {
		data, err := os.ReadFile(filepath.Join(root, item.kind+"-answer"))
		if err != nil {
			t.Fatal(err)
		}
		var value map[string]string
		if err := json.Unmarshal(data, &value); err != nil {
			t.Fatal(err)
		}
		if value["answer"] != item.answer {
			t.Fatalf("wrong prompt answer: %s", data)
		}
	}
}

func TestAzureClosedProtocolAndTransportFailuresStayRedacted(t *testing.T) {
	for _, item := range []struct{ mode, message string }{{"bad-event", "unexpected Azure"}, {"bad-prompt", "unexpected Azure"}, {"second-browser", "second Azure browser"}, {"forward-fail", "forwarding failed"}, {"browser-fail", "browser launch failed"}, {"commit-eof", "may already use it"}} {
		t.Run(item.mode, func(t *testing.T) {
			command, root := azureCommand(t, item.mode)
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), item.message) {
				t.Fatalf("wrong failure: %v %s", err, output)
			}
			for _, secret := range []string{"never-print-this-token", "dummy-state", "oauth2"} {
				if strings.Contains(string(output), secret) {
					t.Fatalf("secret leaked: %s", output)
				}
			}
			if item.mode == "forward-fail" {
				if _, err := os.Stat(filepath.Join(root, "browser-opened")); !os.IsNotExist(err) {
					t.Fatal("browser opened before tunnel was ready")
				}
			}
		})
	}
}

func TestAzureCancellationClosesChildrenAndPreservesCommitWarning(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows test runner cannot send console Ctrl-C to a detached CLI")
	}
	for _, cancellation := range []os.Signal{os.Interrupt, syscall.SIGHUP} {
		for _, mode := range []string{"hang", "hang-after-commit"} {
			t.Run(mode+"-"+cancellation.String(), func(t *testing.T) {
				command, root := azureCommand(t, mode)
				var output bytes.Buffer
				command.Stdout = &output
				command.Stderr = &output
				if err := command.Start(); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { command.Process.Kill() })
				marker := "browser-opened"
				if mode == "hang-after-commit" {
					marker = "committed"
				}
				deadline := time.Now().Add(10 * time.Second)
				for {
					if _, err := os.Stat(filepath.Join(root, marker)); err == nil {
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("fake Azure setup did not reach cancellation point")
					}
					time.Sleep(10 * time.Millisecond)
				}
				if err := command.Process.Signal(cancellation); err != nil {
					t.Fatal(err)
				}
				err := command.Wait()
				exit, ok := err.(*exec.ExitError)
				if !ok || exit.ExitCode() != 130 {
					t.Fatalf("cancellation exit: %v %s", err, output.String())
				}
				if mode == "hang-after-commit" && !strings.Contains(output.String(), "may already use it") {
					t.Fatalf("missing commit warning: %s", output.String())
				}
				for _, name := range []string{"login-closed", "forward-closed"} {
					if _, err := os.Stat(filepath.Join(root, name)); err != nil {
						t.Fatalf("child remained active %s: %v", name, err)
					}
				}
			})
		}
	}
}

func TestAzureChinaCloudUsesOnlyItsSelectedAuthority(t *testing.T) {
	command, _ := azureCommand(t, "success")
	command.Args[len(command.Args)-1] = "AzureChinaCloud"
	for i, entry := range command.Env {
		if strings.HasPrefix(entry, "SANDBOX_AZURE_URL=") {
			command.Env[i] = strings.Replace(entry, "login.microsoftonline.com", "login.chinacloudapi.cn", 1)
		}
	}
	command.Env = append(command.Env, "SANDBOX_AZURE_CLOUD=AzureChinaCloud")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("China cloud rejected: %v %s", err, output)
	}
}

func TestAzureListenerWithoutSSHReadinessNeverOpensBrowser(t *testing.T) {
	command, root := azureCommand(t, "no-marker")
	start := time.Now()
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "forwarding failed") {
		t.Fatalf("missing readiness accepted: %v %s", err, output)
	}
	if time.Since(start) > 25*time.Second {
		t.Fatal("forward readiness did not time out")
	}
	if _, err := os.Stat(filepath.Join(root, "browser-opened")); !os.IsNotExist(err) {
		t.Fatal("browser opened without SSH readiness marker")
	}
	if _, err := os.Stat(filepath.Join(root, "forward-closed")); err != nil {
		t.Fatalf("tunnel remained active: %v", err)
	}
}

func TestAzureCallbackValidationRejectsUnsafeRedirectsAndDuplicateFields(t *testing.T) {
	for _, item := range []struct{ key, value string }{{"redirect_uri", "https://localhost:45678"}, {"redirect_uri", "http://evil.example:45678"}, {"redirect_uri", "http://localhost:22"}, {"redirect_uri", "http://user@localhost:45678"}, {"response_type", "token"}, {"state", ""}, {"duplicate", ""}} {
		t.Run(item.key+item.value, func(t *testing.T) {
			command, _ := azureCommand(t, "browser-idle")
			overrides := url.Values{item.key: {item.value}}
			if item.key == "duplicate" {
				overrides = url.Values{"redirect_uri": {"http://localhost:45678", "http://localhost:56789"}}
			}
			command.Env = append(command.Env, "SANDBOX_AZURE_URL="+azureAuthorization("login.microsoftonline.com", "45678", overrides))
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), "authorization endpoint") {
				t.Fatalf("unsafe callback accepted: %v %s", err, output)
			}
		})
	}
}

func TestAzureInteractiveRequiresSSHOptIn(t *testing.T) {
	command, log := lifecycleCommand(t, "agent01", "tools", "setup", "azure", "--interactive")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "sandboxed-agents agent01 ssh-config --install") {
		t.Fatalf("missing SSH setup not diagnosed: %v %s", err, output)
	}
	for _, call := range lifecycleCalls(t, log) {
		if call[0] == "exec" {
			t.Fatalf("started Azure without host SSH setup: %v", call)
		}
	}
}

func TestAzureErrorAfterCommitReportsUnconfirmedSession(t *testing.T) {
	command, _ := azureCommand(t, "error-after-commit")
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "may already use it") {
		t.Fatalf("commit ambiguity hidden: %v %s", err, output)
	}
}

func TestAzureRejectsWildcardOccupiedCallbackBeforeTunnelOrBrowser(t *testing.T) {
	command, root := azureCommand(t, "browser-idle")
	listener, err := net.Listen("tcp4", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, port, _ := net.SplitHostPort(listener.Addr().String())
	command.Env = append(command.Env, "SANDBOX_AZURE_URL="+azureAuthorization("login.microsoftonline.com", port, nil))
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "callback port is unavailable") {
		t.Fatalf("occupied wildcard callback accepted: %v %s", err, output)
	}
	data, err := os.ReadFile(filepath.Join(root, "calls.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var call []string
		if err := json.Unmarshal([]byte(line), &call); err != nil {
			t.Fatal(err)
		}
		if call[0] == "browser" || slices.Contains(call, "-L") {
			t.Fatalf("occupied callback reached browser or tunnel: %v", call)
		}
	}
}

func TestAzureUnconfirmedCompletionPreservesProcessFailure(t *testing.T) {
	command, _ := azureCommand(t, "complete-exit")
	output, err := command.CombinedOutput()
	if err == nil || command.ProcessState.ExitCode() != 29 {
		t.Fatalf("completion failure lost: %v %s", err, output)
	}
	for _, message := range []string{"may already use it", "did not exit cleanly", "exit status 29"} {
		if !strings.Contains(string(output), message) {
			t.Fatalf("completion cause missing %q: %s", message, output)
		}
	}
}
