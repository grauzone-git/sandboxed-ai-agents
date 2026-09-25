package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"slices"
	"strconv"
	"strings"

	"golang.org/x/sys/windows"
)

const podmanPrerequisites = "Podman is not installed/on PATH. Install Podman 6.0+ and configure a rootless WSL2 machine first"

type machineRuntime struct {
	connection, machine, seccomp string
}

var selectedMachine machineRuntime

func platformPodmanCommand(args ...string) *exec.Cmd {
	if selectedMachine.connection != "" {
		args = append([]string{"--connection", selectedMachine.connection}, args...)
	}
	return exec.Command("podman", args...)
}
func localPodmanJSON(target any, args ...string) error {
	command := exec.Command("podman", args...)
	var errors bytes.Buffer
	command.Stderr = &errors
	data, err := command.Output()
	if err != nil {
		return fmt.Errorf("Podman %s failed: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(errors.String()))
	}
	if err := json.Unmarshal(data, target); err != nil {
		return fmt.Errorf("Podman returned invalid %s data: %w", strings.Join(args, " "), err)
	}
	return nil
}
func platformPodmanPreflight() error {
	if runtime.GOARCH != "amd64" || windows.RtlGetVersion().BuildNumber < 22000 {
		return fmt.Errorf("the Windows controller requires Windows 11 x64 or a newer compatible Windows build")
	}
	if os.Getenv("CONTAINER_HOST") != "" {
		return fmt.Errorf("unset CONTAINER_HOST; select an existing rootless WSL2 connection with CONTAINER_CONNECTION")
	}
	var connections []struct {
		Name, URI string
		Default   bool
	}
	if err := localPodmanJSON(&connections, "system", "connection", "list", "--format", "json"); err != nil {
		return err
	}
	choice := os.Getenv("CONTAINER_CONNECTION")
	matches := 0
	name, uriText := "", ""
	for _, item := range connections {
		if (choice != "" && item.Name == choice) || (choice == "" && item.Default) {
			matches++
			name, uriText = item.Name, item.URI
		}
	}
	if matches != 1 || name == "" {
		return fmt.Errorf("select one configured rootless WSL2 Podman connection with CONTAINER_CONNECTION")
	}
	uri, err := url.Parse(uriText)
	if err != nil || uri.Scheme != "ssh" || !slices.Contains([]string{"localhost", "127.0.0.1", "::1"}, uri.Hostname()) || !regexp.MustCompile(`^/run/user/[1-9][0-9]*/podman/podman.sock$`).MatchString(uri.Path) || uri.User == nil || uri.RawQuery != "" || uri.Fragment != "" {
		return fmt.Errorf("select a local rootless WSL2 machine connection, not a rootful or remote engine")
	}
	port, err := strconv.Atoi(uri.Port())
	if err != nil || port < 1 || port > 65535 {
		return fmt.Errorf("selected machine connection must include a valid SSH port")
	}
	var machines []struct {
		Name, VMType string
		Running      bool
	}
	if err := localPodmanJSON(&machines, "machine", "list", "--format", "json"); err != nil {
		return err
	}
	matches = 0
	machineName := ""
	for _, item := range machines {
		if item.VMType != "wsl" || !item.Running {
			continue
		}
		var inspected []struct {
			Name, State string
			Rootful     bool
			SSHConfig   struct {
				Port           int
				RemoteUsername string
			}
		}
		if err := localPodmanJSON(&inspected, "machine", "inspect", item.Name); err != nil {
			return err
		}
		if len(inspected) != 1 || inspected[0].Name != item.Name {
			return fmt.Errorf("Podman returned unexpected machine inspection data")
		}
		machine := inspected[0]
		if !machine.Rootful && machine.State == "running" && machine.SSHConfig.Port == port && machine.SSHConfig.RemoteUsername == uri.User.Username() {
			matches++
			machineName = machine.Name
		}
	}
	if matches != 1 {
		return fmt.Errorf("selected connection must match exactly one running rootless WSL2 machine SSH endpoint; inspect podman machine list and start the intended machine yourself")
	}
	var versions struct{ Client, Server struct{ Version string } }
	if err := localPodmanJSON(&versions, "--connection", name, "version", "--format", "json"); err != nil {
		return err
	}
	for _, version := range []string{versions.Client.Version, versions.Server.Version} {
		major, err := strconv.Atoi(strings.SplitN(version, ".", 2)[0])
		if err != nil || major < 6 {
			return fmt.Errorf("Windows Podman client and machine engine must both be version 6.0+")
		}
	}
	var info struct {
		Host struct {
			Arch, OS, CgroupVersion string
			CgroupControllers       []string
			Security                struct {
				Rootless           bool
				SeccompProfilePath string
			}
		}
	}
	if err := localPodmanJSON(&info, "--connection", name, "info", "--format", "json"); err != nil {
		return err
	}
	if !info.Host.Security.Rootless || info.Host.Arch != "amd64" || (info.Host.OS != "" && info.Host.OS != "linux") {
		return fmt.Errorf("selected engine must be rootless Linux x64")
	}
	if info.Host.CgroupVersion != "v2" {
		return fmt.Errorf("WSL2 engine must delegate cgroups v2 cpu, memory, and pids controllers")
	}
	for _, controller := range []string{"cpu", "memory", "pids"} {
		if !slices.Contains(info.Host.CgroupControllers, controller) {
			return fmt.Errorf("WSL2 engine must delegate cgroups v2 cpu, memory, and pids controllers")
		}
	}
	selectedMachine = machineRuntime{connection: name, machine: machineName, seccomp: info.Host.Security.SeccompProfilePath}
	return nil
}

func guestPodman(input []byte, args ...string) ([]byte, error) {
	quoted := make([]string, len(args))
	for i, arg := range args {
		quoted[i] = "'" + strings.ReplaceAll(arg, "'", "'\"'\"'") + "'"
	}
	command := exec.Command("podman", "machine", "ssh", selectedMachine.machine, strings.Join(quoted, " "))
	command.Stdin = bytes.NewReader(input)
	var errors bytes.Buffer
	command.Stderr = &errors
	data, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("Podman machine %s command failed: %w: %s", selectedMachine.machine, err, strings.TrimSpace(errors.String()))
	}
	return data, nil
}

func platformNestedSeccomp() (string, error) {
	if _, err := guestPodman(nil, "sh", "-c", "test -c /dev/fuse && test -r /dev/fuse && test -w /dev/fuse && test -c /dev/net/tun && test -r /dev/net/tun && test -w /dev/net/tun"); err != nil {
		return "", err
	}
	for _, kind := range []string{"uid", "gid"} {
		mapping, err := guestPodman(nil, "podman", "unshare", "cat", "/proc/self/"+kind+"_map")
		if err != nil {
			return "", err
		}
		var total uint64
		for _, line := range strings.Split(strings.TrimSpace(string(mapping)), "\n") {
			fields := strings.Fields(line)
			if len(fields) != 3 {
				return "", fmt.Errorf("machine returned invalid namespace ID mapping")
			}
			for _, field := range fields {
				if _, err := strconv.ParseUint(field, 10, 64); err != nil {
					return "", fmt.Errorf("machine returned invalid namespace ID mapping")
				}
			}
			size, _ := strconv.ParseUint(fields[2], 10, 64)
			if ^uint64(0)-total < size {
				return "", fmt.Errorf("machine namespace ID mapping overflow")
			}
			total += size
		}
		if total < 65537 {
			return "", fmt.Errorf("nested Podman requires at least 65536 subordinate UIDs and GIDs in the machine")
		}
	}
	source := selectedMachine.seccomp
	if !strings.HasPrefix(source, "/") || strings.ContainsAny(source, "\x00\r\n") {
		return "", fmt.Errorf("engine must report an absolute seccomp profile path")
	}
	data, err := guestPodman(nil, "cat", "--", source)
	if err != nil {
		return "", err
	}
	content, err := nestedSeccompContent(data)
	if err != nil {
		return "", err
	}
	if _, err := storeNestedSeccomp(content); err != nil {
		return "", err
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(content))
	group := fmt.Sprintf("%x", sha256.Sum256([]byte(owner()+"\x00"+Version)))
	const script = `set -eu
umask 077
for directory in "$HOME" "$HOME/.local" "$HOME/.local/share" "$HOME/.local/share/sandboxed-agents" "$HOME/.local/share/sandboxed-agents/seccomp"; do
 test ! -L "$directory" || { echo 'Refusing symlinked profile directory' >&2; exit 1; }
done
directory="$HOME/.local/share/sandboxed-agents/seccomp/$1"
test ! -L "$directory"
mkdir -p "$directory"
test -O "$directory"
chmod 700 "$HOME/.local/share/sandboxed-agents" "$HOME/.local/share/sandboxed-agents/seccomp" "$directory"
target="$directory/$2.json"
test ! -L "$target"
test ! -e "$target" || test -f "$target"
temporary=$(mktemp "$directory/.seccomp.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
cat > "$temporary"
mv -f "$temporary" "$target"
printf '%s\n' "$target"
`
	staged, err := guestPodman(content, "sh", "-c", script, "sandbox-seccomp", group, digest)
	if err != nil {
		return "", err
	}
	target := strings.TrimSpace(string(staged))
	if !strings.HasPrefix(target, "/") || strings.ContainsAny(target, "\x00\r\n") || !strings.HasSuffix(target, "/"+group+"/"+digest+".json") {
		return "", fmt.Errorf("machine returned an invalid staged seccomp path")
	}
	return target, nil
}
