package cli

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"

	sandboxassets "github.com/grauzone-git/sandboxed-ai-agents"
)

type createOptions struct {
	agents, tools, port, cpus, memory, pidsLimit, shmSize, workspace, capabilities string
	bind, sshConfig                                                                bool
}

func lifecycle(command, name string, args []string) error {
	if command == "up" {
		options, err := parseCreate(args)
		if err != nil {
			return err
		}
		if options.sshConfig {
			if err := validateSSHSetup(name); err != nil {
				return err
			}
		}
		if err := requirePodman(); err != nil {
			return err
		}
		return createSandbox(name, options)
	}
	setupSSHRequested := (command == "start" || command == "restart") && len(args) == 1 && args[0] == "--ssh-config"
	if setupSSHRequested {
		if err := validateSSHSetup(name); err != nil {
			return err
		}
	}
	if len(args) > 0 && !setupSSHRequested && !(command == "remove" && len(args) == 1 && args[0] == "--volumes") {
		return fmt.Errorf("unexpected %s arguments", command)
	}
	if err := requirePodman(); err != nil {
		return err
	}
	if err := requireOwned(name); err != nil {
		return err
	}
	switch command {
	case "start", "stop", "restart":
		if err := podman(command, name); err != nil {
			return err
		}
		if setupSSHRequested {
			if err := waitForEntrypoint(name); err != nil {
				return err
			}
			return setupSSH(name)
		}
		return nil
	case "remove":
		return removeSandbox(name, len(args) > 0)
	case "check", "check-full":
		return checkSandbox(name, command == "check-full")
	case "shell":
		args := append([]string{"exec"}, interactiveArgs()...)
		return podman(append(args, "--user", "1000:1000", "--workdir", "/workspace", name, "/bin/bash", "-l")...)
	}
	return fmt.Errorf("%s is not available in this preview", command)
}

func interactiveArgs() []string {
	args := []string{"-i"}
	if terminalAvailable(os.Stdin) && terminalAvailable(os.Stdout) {
		args = append(args, "-t")
	}
	return args
}

func envDefault(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func parseCreate(args []string) (createOptions, error) {
	options := createOptions{pidsLimit: "2048", shmSize: "1g", capabilities: "none", port: "2222", cpus: envDefault("SANDBOX_CPUS", "4"), memory: envDefault("SANDBOX_MEMORY", "8g")}
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		flag := args[i]
		if flag == "--ssh-config" {
			if seen[flag] {
				return options, fmt.Errorf("supply --ssh-config only once")
			}
			seen[flag] = true
			options.sshConfig = true
			continue
		}
		if !strings.HasPrefix(flag, "-") && !seen["workspace"] {
			options.workspace = flag
			options.bind = true
			seen["workspace"] = true
			continue
		}
		if !strings.HasPrefix(flag, "-") && seen["workspace"] && !seen["--ssh-port"] {
			options.port = flag
			seen["--ssh-port"] = true
			continue
		}
		if !slices.Contains([]string{"--agents", "--tools", "--ssh-port", "--cpus", "--memory", "--capabilities"}, flag) {
			return options, fmt.Errorf("unknown up option: %s", flag)
		}
		if seen[flag] || i+1 == len(args) {
			return options, fmt.Errorf("supply %s once, followed by a value", flag)
		}
		seen[flag] = true
		i++
		value := args[i]
		switch flag {
		case "--agents":
			options.agents = value
		case "--tools":
			options.tools = value
		case "--ssh-port":
			options.port = value
		case "--cpus":
			options.cpus = value
		case "--memory":
			options.memory = value
		case "--capabilities":
			options.capabilities = value
		}
	}
	if options.agents == "" || options.agents == "none" {
		return options, fmt.Errorf("creating a sandbox requires --agents with at least one agent (or all)")
	}
	if err := validateCapabilities(options.capabilities); err != nil {
		return options, err
	}
	var err error
	if options.agents, err = selection(options.agents, "agents"); err != nil {
		return options, err
	}
	if seen["--tools"] {
		if options.tools, err = selection(options.tools, "tools"); err != nil {
			return options, err
		}
		if err := toolDependencies(options.tools, options.agents); err != nil {
			return options, err
		}
	}
	port, err := strconv.Atoi(options.port)
	if err != nil || port < 1024 || port > 65535 {
		return options, fmt.Errorf("use an SSH port from 1024 to 65535")
	}
	if !regexp.MustCompile(`^[0-9]+(?:\.[0-9]+)?$`).MatchString(options.cpus) {
		return options, fmt.Errorf("--cpus must be a positive number")
	}
	cpus, err := strconv.ParseFloat(options.cpus, 64)
	if err != nil || cpus <= 0 {
		return options, fmt.Errorf("--cpus must be a positive number")
	}
	if !regexp.MustCompile(`^[1-9][0-9]*[bBkKmMgGtT]?$`).MatchString(options.memory) {
		return options, fmt.Errorf("--memory must be a positive size such as 8g")
	}
	if options.bind {
		options.workspace, err = workspacePath(options.workspace)
		if err != nil {
			return options, err
		}
	}
	return options, nil
}

type catalogEntry struct {
	Agent string   `json:"agent"`
	Login []string `json:"login"`
}

func readCatalog(kind string) (map[string]catalogEntry, error) {
	if kind != "agents" && kind != "tools" {
		return nil, fmt.Errorf("choose agents or tools")
	}
	data, err := sandboxassets.Files.ReadFile("src/container/" + kind + ".json")
	if err != nil {
		return nil, err
	}
	var catalog map[string]catalogEntry
	err = json.Unmarshal(data, &catalog)
	return catalog, err
}
func selection(spec, kind string) (string, error) {
	catalog, err := readCatalog(kind)
	if err != nil {
		return "", err
	}
	if spec == "none" {
		return spec, nil
	}
	if spec == "all" {
		names := make([]string, 0, len(catalog))
		for name := range catalog {
			names = append(names, name)
		}
		sort.Strings(names)
		return strings.Join(names, ","), nil
	}
	seen := map[string]bool{}
	items := []string{}
	for _, item := range strings.Split(spec, ",") {
		item = strings.TrimSpace(item)
		name, version, pinned := strings.Cut(item, "@")
		entry, ok := catalog[name]
		if !ok {
			return "", fmt.Errorf("unknown %s selection: %q", kind, name)
		}
		if seen[name] {
			return "", fmt.Errorf("duplicate %s selection: %s", kind, name)
		}
		seen[name] = true
		if pinned && !regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+/-]*$`).MatchString(version) {
			return "", fmt.Errorf("invalid version/ref for %s: %q", name, version)
		}
		if pinned && entry.Agent != "" && version != "bundled" {
			return "", fmt.Errorf("%s uses its agent's version; omit the tool version pin", name)
		}
		items = append(items, item)
	}
	return strings.Join(items, ","), nil
}
func toolDependencies(tools, agents string) error {
	catalog, err := readCatalog("tools")
	if err != nil {
		return err
	}
	enabled := map[string]bool{}
	for _, item := range strings.Split(agents, ",") {
		name, _, _ := strings.Cut(item, "@")
		enabled[name] = true
	}
	for _, item := range strings.Split(tools, ",") {
		name, _, _ := strings.Cut(item, "@")
		if dependency := catalog[name].Agent; dependency != "" && !enabled[dependency] {
			return fmt.Errorf("tool %s requires --agents to include %s", name, dependency)
		}
	}
	return nil
}

func resourceExists(kind, name string) (bool, error) {
	_, err := capturePodman(true, kind, "exists", name)
	if err == nil {
		return true, nil
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) && exit.ExitCode() == 1 {
		return false, nil
	}
	return false, err
}
func requireOwned(name string) error {
	data, err := capturePodman(false, "inspect", "--format", `{{index .Config.Labels "`+ownerLabel+`"}}`, name)
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(data)) != owner() {
		return fmt.Errorf("container %s is not owned by this configuration", name)
	}
	return nil
}
func requireVolumeOwned(name string) error {
	data, err := capturePodman(false, "volume", "inspect", "--format", `{{index .Labels "`+ownerLabel+`"}}`, name)
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(data)) != owner() {
		return fmt.Errorf("volume %s belongs to another configuration", name)
	}
	return nil
}
func sandboxVolumes(name string) []string {
	return []string{name + "-home", name + "-sshd", name + "-workspace"}
}
func manager(name, kind string, args ...string) error {
	return podman(managerCommand(name, kind, false, args...)...)
}

func managerCommand(name, kind string, interactive bool, args ...string) []string {
	command := []string{"exec"}
	if interactive {
		command = append(command, interactiveArgs()...)
	}
	command = append(command, "--user", "1000:1000", "--workdir", "/workspace", name, "/usr/local/bin/sandbox-"+kind)
	return append(command, args...)
}
func createSandbox(name string, options createOptions) error {
	exists, err := resourceExists("container", name)
	if err != nil {
		return err
	}
	if exists {
		return fmt.Errorf("container exists; use sandboxed-agents %s start", name)
	}
	exists, err = resourceExists("image", imageName())
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("build the image first with sandboxed-agents build")
	}
	listener, err := net.Listen("tcp4", net.JoinHostPort("127.0.0.1", options.port))
	if err != nil {
		return fmt.Errorf("SSH port %s is unavailable: %w", options.port, err)
	}
	if err := listener.Close(); err != nil {
		return err
	}
	missing := []string{}
	for _, volume := range sandboxVolumes(name) {
		if options.bind && volume == name+"-workspace" {
			continue
		}
		exists, err := resourceExists("volume", volume)
		if err != nil {
			return err
		}
		if exists {
			if err := requireVolumeOwned(volume); err != nil {
				return err
			}
		} else {
			missing = append(missing, volume)
		}
	}
	image, runtimeArgs, err := prepareCapabilities(imageName(), options.capabilities)
	if err != nil {
		return err
	}
	if options.bind {
		if err := os.MkdirAll(options.workspace, 0755); err != nil {
			return err
		}
	}
	for _, volume := range missing {
		if err := podman("volume", "create", "--label", ownerLabel+"="+owner(), volume); err != nil {
			return err
		}
	}
	args := createArguments(name, options, image, runtimeArgs)
	if err := podman(args...); err != nil {
		return err
	}
	if err := waitForEntrypoint(name); err != nil {
		return err
	}
	if !options.bind {
		if err := podman("exec", "--user", "0", name, "/bin/chown", "1000:1000", "/workspace"); err != nil {
			return err
		}
	}
	if options.tools != "" {
		if err := manager(name, "tools", "set", "none"); err != nil {
			return err
		}
	}
	if err := manager(name, "agents", "init", options.agents); err != nil {
		return err
	}
	if options.tools != "" {
		err = manager(name, "tools", "init", options.tools)
	} else {
		err = manager(name, "tools", "init")
	}
	if err != nil {
		return err
	}
	if options.sshConfig {
		return setupSSH(name)
	}
	return nil
}

func removeSandbox(name string, removeVolumes bool) error {
	cleanup, release, err := beginSSHRemoval(name)
	if err != nil {
		return err
	}
	defer release()
	volumes := []string{}
	if removeVolumes {
		// Validate every retained volume before stopping or deleting the container.
		for _, volume := range sandboxVolumes(name) {
			exists, err := resourceExists("volume", volume)
			if err != nil {
				return err
			}
			if exists {
				if err := requireVolumeOwned(volume); err != nil {
					return err
				}
				volumes = append(volumes, volume)
			}
		}
	}
	if err := podman("stop", name); err != nil {
		return err
	}
	if err := podman("rm", name); err != nil {
		return err
	}
	if err := cleanup(); err != nil {
		return err
	}
	for _, volume := range volumes {
		if err := podman("volume", "rm", volume); err != nil {
			return err
		}
	}
	fmt.Fprintf(os.Stdout, "Removed container %s.\n", name)
	if removeVolumes {
		fmt.Fprintln(os.Stdout, "Sandbox named volumes deleted.")
	} else {
		fmt.Fprintln(os.Stdout, "Sandbox named volumes retained.")
	}
	return nil
}

func checkSandbox(name string, full bool) error {
	data, err := capturePodman(false, "inspect", name, "--format", "{{json .Mounts}}")
	if err != nil {
		return err
	}
	var mounts []struct{ Type, Source, Name, Destination string }
	if err := json.Unmarshal(data, &mounts); err != nil {
		return fmt.Errorf("invalid mount inventory: %w", err)
	}
	expected := map[string]string{"/workspace": name + "-workspace", "/home/agent": name + "-home", "/var/lib/agent-sshd": name + "-sshd"}
	for _, mount := range mounts {
		if mount.Destination == "/run/user/1000" {
			expected[mount.Destination] = ""
		}
	}
	if len(mounts) != len(expected) {
		return fmt.Errorf("unexpected mount inventory")
	}
	for _, mount := range mounts {
		volume, ok := expected[mount.Destination]
		if ok && mount.Destination == "/run/user/1000" && mount.Type == "tmpfs" {
			delete(expected, mount.Destination)
			continue
		}
		if ok && mount.Destination == "/workspace" && mount.Type == "bind" {
			if _, err := workspacePath(mount.Source); err != nil {
				return err
			}
			delete(expected, mount.Destination)
			continue
		}
		if !ok || mount.Type != "volume" || mount.Name != volume {
			return fmt.Errorf("unexpected mount at %s", mount.Destination)
		}
		delete(expected, mount.Destination)
	}
	fmt.Fprintln(os.Stdout, "Mount policy: workspace storage and named home/SSH volumes: OK")
	args := []string{"exec", "--user", "1000:1000", "--workdir", "/workspace", name, "/usr/local/bin/agent-smoke"}
	if full {
		args = append(args, "--full")
	}
	if err := podman(args...); err != nil {
		return err
	}
	return checkSSH(name)
}

func waitForEntrypoint(name string) error {
	deadline := time.Now().Add(30 * time.Second)
	for {
		_, err := capturePodman(true, "exec", "--user", "0", name, "/bin/test", "-f", "/run/agent-booted")
		if err == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("container %s did not become ready: %w", name, err)
		}
		time.Sleep(250 * time.Millisecond)
	}
}

type containerCreation struct {
	command, cidfile, transaction string
}

func createArguments(name string, options createOptions, image string, runtimeArgs []string) []string {
	return containerArguments(name, options, image, runtimeArgs, containerCreation{command: "run"})
}

func containerArguments(name string, options createOptions, image string, runtimeArgs []string, creation containerCreation) []string {
	workspace := name + "-workspace:/workspace"
	if options.bind {
		workspace = options.workspace + ":/workspace:Z"
	}
	args := []string{creation.command}
	if creation.command == "run" {
		args = append(args, "--detach")
	}
	args = append(args, "--name", name, "--hostname", name, "--label", ownerLabel+"="+owner(), "--label", versionLabel+"="+Version, "--label", capabilitiesLabel+"="+options.capabilities, "--userns=keep-id:uid=1000,gid=1000", "--user", "0:0")
	args = append(args, runtimeArgs...)
	args = append(args, "--network=pasta:--no-map-gw", "--memory="+options.memory, "--cpus="+options.cpus, "--pids-limit="+options.pidsLimit, "--shm-size="+options.shmSize, "--publish", "127.0.0.1:"+options.port+":2222", "--volume", workspace, "--volume", name+"-home:/home/agent", "--volume", name+"-sshd:/var/lib/agent-sshd")
	if creation.transaction != "" {
		args = append(args, "--label", updateTransactionLabel+"="+creation.transaction)
	}
	if creation.cidfile != "" {
		args = append(args, "--cidfile", creation.cidfile)
	}
	return append(args, image)
}
