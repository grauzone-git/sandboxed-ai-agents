package cli

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"
)

type azureOptions struct {
	interactive, tenantOnly     bool
	cloud, tenant, subscription string
}

var azureTenantPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9.-]*$`)
var azureAuthorizationPattern = regexp.MustCompile(`^/[a-zA-Z0-9.-]+/oauth2/(v2\.0/)?authorize$`)

var azureClouds = map[string]string{"AzureCloud": "login.microsoftonline.com", "AzureChinaCloud": "login.chinacloudapi.cn"}

func parseAzure(args []string) (azureOptions, error) {
	options := azureOptions{}
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		flag := args[i]
		if seen[flag] {
			return options, fmt.Errorf("supply each Azure setup option only once")
		}
		seen[flag] = true
		switch flag {
		case "--interactive":
			options.interactive = true
		case "--tenant-only":
			options.tenantOnly = true
		case "--cloud", "--tenant", "--subscription":
			i++
			if i == len(args) || args[i] == "" || strings.HasPrefix(args[i], "-") || strings.ContainsFunc(args[i], unicode.IsControl) {
				return options, fmt.Errorf("supply a nonempty value for each Azure setup option")
			}
			switch flag {
			case "--cloud":
				options.cloud = args[i]
			case "--tenant":
				options.tenant = args[i]
			case "--subscription":
				options.subscription = args[i]
			}
		default:
			return options, fmt.Errorf("use Azure setup [--interactive] [--cloud CLOUD] [--tenant TENANT] [--subscription SUBSCRIPTION | --tenant-only]")
		}
	}
	if options.cloud != "" && azureClouds[options.cloud] == "" {
		return options, fmt.Errorf("unsupported Azure cloud; choose AzureCloud or AzureChinaCloud")
	}
	if options.tenant != "" && !azureTenantPattern.MatchString(options.tenant) {
		return options, fmt.Errorf("use an Azure tenant ID or domain name")
	}
	if options.tenantOnly && options.subscription != "" {
		return options, fmt.Errorf("use Azure --subscription or --tenant-only, not both")
	}
	return options, nil
}

const azureUnconfirmed = "Azure setup stopped after the new sign-in was committed, so the sandbox may already use it. Check with az account show in the sandbox before retrying setup."

type azureUnconfirmedError struct{ cause error }

func (e azureUnconfirmedError) Error() string {
	return azureUnconfirmed + " " + e.cause.Error()
}
func (e azureUnconfirmedError) Unwrap() error { return e.cause }

type azureInterrupted struct{ cause error }

func (azureInterrupted) Error() string {
	return "Azure setup cancelled. Retry explicit setup when ready."
}
func (azureInterrupted) ExitCode() int   { return 130 }
func (e azureInterrupted) Unwrap() error { return e.cause }

func azureCallback(value, cloud string) (int, error) {
	invalid := fmt.Errorf("Azure returned an unexpected authorization endpoint or callback. Update the image and retry explicit setup")
	endpoint, err := url.Parse(value)
	if err != nil {
		return 0, invalid
	}
	query, err := url.ParseQuery(endpoint.RawQuery)
	if err != nil {
		return 0, invalid
	}
	if azureClouds[cloud] == "" || endpoint.Scheme != "https" || endpoint.Host != azureClouds[cloud] || endpoint.User != nil || endpoint.Fragment != "" || !azureAuthorizationPattern.MatchString(endpoint.EscapedPath()) || query.Get("response_type") != "code" || query.Get("state") == "" {
		return 0, invalid
	}
	for _, values := range query {
		if len(values) != 1 {
			return 0, invalid
		}
	}
	redirect, err := url.Parse(query.Get("redirect_uri"))
	if err != nil {
		return 0, invalid
	}
	port, err := strconv.Atoi(redirect.Port())
	if err != nil || port < 1024 || port > 65535 || redirect.Scheme != "http" || (redirect.Hostname() != "localhost" && redirect.Hostname() != "127.0.0.1") || redirect.User != nil || redirect.RawQuery != "" || redirect.Fragment != "" || (redirect.Path != "" && redirect.Path != "/") {
		return 0, invalid
	}
	return port, nil
}

type azureProcess struct {
	command *exec.Cmd
	input   io.WriteCloser
	events  chan string
	done    chan struct{}
	err     error
}

func startAzureProcess(ctx context.Context, command *exec.Cmd) (*azureProcess, error) {
	command.Stdin, command.Stdout, command.Stderr = nil, nil, io.Discard
	input, err := command.StdinPipe()
	if err != nil {
		return nil, err
	}
	output, err := command.StdoutPipe()
	if err != nil {
		input.Close()
		return nil, err
	}
	if err := command.Start(); err != nil {
		input.Close()
		output.Close()
		return nil, err
	}
	child := &azureProcess{command: command, input: input, events: make(chan string, 16), done: make(chan struct{})}
	go func() {
		scanner := bufio.NewScanner(output)
		scanner.Buffer(make([]byte, 4096), 65536)
		for scanner.Scan() {
			select {
			case child.events <- scanner.Text():
			case <-ctx.Done():
				output.Close()
				child.err = command.Wait()
				close(child.events)
				close(child.done)
				return
			}
		}
		close(child.events)
		child.err = command.Wait()
		close(child.done)
	}()
	return child, nil
}
func (child *azureProcess) stop() {
	if child == nil {
		return
	}
	child.input.Close()
	select {
	case <-child.done:
		return
	case <-time.After(100 * time.Millisecond):
	}
	if runtime.GOOS == "windows" {
		child.command.Process.Kill()
	} else {
		child.command.Process.Signal(syscall.SIGTERM)
	}
	select {
	case <-child.done:
		return
	case <-time.After(3 * time.Second):
		child.command.Process.Kill()
	}
	select {
	case <-child.done:
	case <-time.After(3 * time.Second):
	}
}
func (child *azureProcess) answer(value any) error {
	if err := json.NewEncoder(child.input).Encode(value); err != nil {
		return fmt.Errorf("Azure setup transport closed unexpectedly: %w", err)
	}
	return nil
}

func verifyAzureForward(ctx context.Context, port int, child *azureProcess) error {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	invalid := fmt.Errorf("Azure callback forwarding failed. Check managed SSH access and local port availability, then retry --interactive")
	select {
	case marker, ok := <-child.events:
		if !ok || marker != "SANDBOX_AZURE_FORWARD" {
			return invalid
		}
	case <-ctx.Done():
		return invalid
	case <-child.done:
		return invalid
	}
	transport := &http.Transport{Proxy: nil}
	defer transport.CloseIdleConnections()
	client := http.Client{Transport: transport, Timeout: 500 * time.Millisecond, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	for {
		select {
		case <-ctx.Done():
			return invalid
		case <-child.done:
			return invalid
		default:
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodHead, fmt.Sprintf("http://127.0.0.1:%d/", port), nil)
		if err != nil {
			return invalid
		}
		response, err := client.Do(request)
		if err == nil {
			response.Body.Close()
			if response.StatusCode == 501 {
				select {
				case <-child.done:
					return invalid
				default:
					return nil
				}
			}
		}
		select {
		case <-ctx.Done():
			return invalid
		case <-time.After(100 * time.Millisecond):
		}
	}
}

func openAzureBrowser(ctx context.Context, authorization string) (func(), error) {
	token := make([]byte, 24)
	if _, err := rand.Read(token); err != nil {
		return nil, err
	}
	route := "/" + hex.EncodeToString(token)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("Azure browser redirect could not bind a local port")
	}
	server := &http.Server{ReadHeaderTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second, MaxHeaderBytes: 4096, ErrorLog: log.New(io.Discard, "", 0), Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.RequestURI != route {
			w.WriteHeader(404)
			return
		}
		w.Header().Set("Location", authorization)
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.WriteHeader(302)
	})}
	go server.Serve(listener)
	stopRedirect := func() { server.Close() }
	local := "http://" + listener.Addr().String() + route
	launch, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var command *exec.Cmd
	if runtime.GOOS == "windows" {
		command = exec.CommandContext(launch, "rundll32.exe", "url.dll,FileProtocolHandler", local)
	} else {
		command = exec.CommandContext(launch, "xdg-open", local)
	}
	command.Stdin, command.Stdout, command.Stderr = nil, io.Discard, io.Discard
	if err := command.Run(); err != nil {
		stopRedirect()
		return nil, fmt.Errorf("host browser launch failed; configure your default browser and retry --interactive")
	}
	return stopRedirect, nil
}

func azurePrompt(ctx context.Context, scanner *bufio.Scanner, kind string) (string, error) {
	if kind != "tenant" && kind != "subscription" {
		return "", fmt.Errorf("unexpected Azure setup prompt")
	}
	fmt.Fprintf(os.Stdout, "Azure %s ID or name: ", kind)
	result := make(chan string, 1)
	go func() {
		if scanner.Scan() {
			result <- strings.TrimSpace(scanner.Text())
		} else {
			result <- ""
		}
	}()
	select {
	case answer := <-result:
		if answer == "" {
			return "", fmt.Errorf("Azure setup cancelled; supply a tenant and subscription or --tenant-only and retry")
		}
		return answer, nil
	case <-ctx.Done():
		return "", fmt.Errorf("Azure setup timed out or was cancelled")
	}
}

func azureInteractive(name string, args []string, options azureOptions) (result error) {
	signalContext, stopSignals := signal.NotifyContext(context.Background(), azureSignals()...)
	defer stopSignals()
	ctx, cancel := context.WithTimeout(signalContext, 600*time.Second)
	defer cancel()
	remote := []string{"/opt/az/bin/python3", "-B", "/usr/local/lib/sandbox-agents/azure_setup.py", "--host-protocol"}
	remote = append(remote, args...)
	for i, arg := range remote {
		remote[i] = shellQuote(arg)
	}
	command, err := managedSSH(name, "-T", name, strings.Join(remote, " "))
	if err != nil {
		return err
	}
	login, err := startAzureProcess(ctx, command)
	if err != nil {
		return fmt.Errorf("Azure setup could not start; check OpenSSH and managed SSH access")
	}
	var forward *azureProcess
	var closeBrowser func()
	committed := false
	input := bufio.NewScanner(os.Stdin)
	input.Buffer(make([]byte, 4096), 65536)
	defer func() {
		if closeBrowser != nil {
			closeBrowser()
		}
		cancel()
		login.stop()
		forward.stop()
		if result == nil {
			return
		}
		if signalContext.Err() != nil {
			result = azureInterrupted{cause: result}
		} else if ctx.Err() == context.DeadlineExceeded {
			result = fmt.Errorf("Azure setup timed out; retry explicit setup: %w", result)
		}
		if committed {
			result = azureUnconfirmedError{cause: result}
		}
	}()
	for {
		var forwardDone <-chan struct{}
		if forward != nil {
			forwardDone = forward.done
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-forwardDone:
			return fmt.Errorf("Azure callback forwarding stopped; check SSH access and retry --interactive")
		case line, ok := <-login.events:
			if !ok {
				return fmt.Errorf("sandbox Azure setup ended before completion; check SSH access, update the image, and retry")
			}
			var event struct{ Event, Value, Cloud string }
			if json.Unmarshal([]byte(line), &event) != nil {
				return fmt.Errorf("unexpected Azure setup response; update the image and retry")
			}
			switch event.Event {
			case "browser":
				if forward != nil || committed {
					return fmt.Errorf("unexpected second Azure browser request")
				}
				if options.cloud != "" && event.Cloud != options.cloud {
					return fmt.Errorf("sandbox returned a different Azure cloud; update the image and retry")
				}
				port, err := azureCallback(event.Value, event.Cloud)
				if err != nil {
					return err
				}
				address := fmt.Sprintf("127.0.0.1:%d", port)
				if err := requireForwardPort(address); err != nil {
					return fmt.Errorf("Azure callback port is unavailable; retry --interactive")
				}
				command, err := managedSSH(name, "-o", "ExitOnForwardFailure=yes", "-T", "-L", address+":"+address, name, "printf 'SANDBOX_AZURE_FORWARD\\n'; exec cat")
				if err != nil {
					return err
				}
				forward, err = startAzureProcess(ctx, command)
				if err != nil {
					return fmt.Errorf("Azure callback forwarding failed; check managed SSH access")
				}
				if err := verifyAzureForward(ctx, port, forward); err != nil {
					return err
				}
				closeBrowser, err = openAzureBrowser(ctx, event.Value)
				if err != nil {
					return err
				}
				fmt.Fprintln(os.Stdout, "Complete Azure sign-in in your host browser. Keep this command running.")
			case "prompt":
				if committed {
					return fmt.Errorf("unexpected Azure prompt after commit")
				}
				answer, err := azurePrompt(ctx, input, event.Value)
				if err != nil {
					return err
				}
				if err := login.answer(map[string]string{"answer": answer}); err != nil {
					return err
				}
			case "ready":
				if committed {
					return fmt.Errorf("unexpected second Azure commit request")
				}
				committed = true
				if err := login.answer(map[string]bool{"commit": true}); err != nil {
					return err
				}
			case "complete":
				if !committed {
					return fmt.Errorf("unexpected Azure completion before commit")
				}
				select {
				case <-login.done:
					if login.err != nil {
						return fmt.Errorf("Azure setup did not exit cleanly; check sandbox account context before retrying: %w", login.err)
					}
				case <-ctx.Done():
					return ctx.Err()
				case <-time.After(5 * time.Second):
					return fmt.Errorf("Azure setup did not exit after completion: %w", context.DeadlineExceeded)
				}
				fmt.Fprintln(os.Stdout, "Azure setup completed. The callback tunnel is closing.")
				return nil
			case "error":
				messages := map[string]string{"interaction": "Azure interaction required; retry explicit setup", "permission": "Azure permission denied; check tenant, subscription, and roles", "network": "Azure network request failed; check connectivity and retry", "cancelled": "Azure setup was cancelled or timed out in the sandbox; retry explicit setup", "busy": "Another Azure setup is running in this sandbox; wait for it to finish"}
				if message := messages[event.Value]; message != "" {
					return fmt.Errorf("%s", message)
				}
				return fmt.Errorf("Azure setup failed; check your selection and retry setup")
			default:
				return fmt.Errorf("unexpected Azure setup response; update the image and retry")
			}
		}
	}
}
