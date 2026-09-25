package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type updateContainer struct {
	Name, Id string
	Config   struct{ Labels map[string]string }
	State    struct {
		Running bool
		Status  string
	}
	Mounts []struct {
		Type, Source, Name, Destination string
		RW                              *bool
	}
	HostConfig struct {
		PortBindings                                              map[string][]struct{ HostIp, HostPort string }
		Memory, NanoCpus, CpuQuota, CpuPeriod, PidsLimit, ShmSize int64
	}
}

func (container updateContainer) name() string { return strings.TrimPrefix(container.Name, "/") }

type updatePlan struct {
	capability string
	container  updateContainer
	options    createOptions
}
type updateImage struct {
	image       string
	runtimeArgs []string
}

func update(name string, args []string) error {
	all, noBuild := false, false
	capability := ""
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		flag := args[i]
		if seen[flag] {
			return fmt.Errorf("duplicate update option: %s", flag)
		}
		seen[flag] = true
		switch flag {
		case "--all":
			all = true
		case "--no-build":
			noBuild = true
		case "--capabilities":
			if i+1 == len(args) {
				return fmt.Errorf("supply --capabilities with podman or none")
			}
			i++
			capability = args[i]
			if err := validateCapabilities(capability); err != nil {
				return err
			}
		case "--help", "-h":
			fmt.Fprintln(os.Stdout, "Usage: sandboxed-agents NAME update [--no-build] [--capabilities podman|none]\n       sandboxed-agents update --all [--no-build] [--capabilities podman|none]")
			return nil
		default:
			return fmt.Errorf("unknown update option: %s", flag)
		}
	}
	if all == (name != "") {
		return fmt.Errorf("supply one sandbox name, or update --all")
	}
	if err := requirePodman(); err != nil {
		return err
	}
	names := []string{name}
	if all {
		data, err := capturePodman(false, "ps", "--all", "--filter", "label="+ownerLabel+"="+owner(), "--format", "{{.Names}}")
		if err != nil {
			return err
		}
		names = strings.Fields(string(data))
		slices.Sort(names)
		names = slices.Compact(names)
	}
	if len(names) == 0 {
		fmt.Fprintln(os.Stdout, "No sandboxes owned by this configuration to update.")
		return nil
	}
	plans := make([]updatePlan, 0, len(names))
	for _, name := range names {
		info, err := inspectUpdateContainer(name)
		if err != nil {
			return err
		}
		if all && isUpdateBackup(info) {
			fmt.Fprintf(os.Stdout, "Skipping update backup %s; retain it until recovery is complete.\n", name)
			continue
		}
		plan, err := planUpdate(info)
		if err != nil {
			return err
		}
		if capability != "" {
			plan.capability = capability
		}
		plans = append(plans, plan)
	}
	if len(plans) == 0 {
		return nil
	}
	if !noBuild {
		if err := build([]string{"--no-cache"}); err != nil {
			return err
		}
	}
	data, err := capturePodman(false, "image", "inspect", "--format", "{{.Id}}", imageName())
	if err != nil {
		return err
	}
	frozen := strings.TrimSpace(string(data))
	if !imageIDPattern.MatchString(frozen) {
		return fmt.Errorf("Podman returned an invalid image ID")
	}
	images := map[string]updateImage{}
	for _, plan := range plans {
		selected := plan.selectedCapability()
		if _, ok := images[selected]; ok {
			continue
		}
		image, runtimeArgs, err := prepareCapabilities(frozen, selected)
		if err != nil {
			return err
		}
		images[selected] = updateImage{image, runtimeArgs}
	}
	for _, plan := range plans {
		selected := plan.selectedCapability()
		if err := replaceSandbox(plan, images[selected]); err != nil {
			return err
		}
	}
	return nil
}

func (plan updatePlan) selectedCapability() string {
	if plan.capability != "" {
		return plan.capability
	}
	return plan.options.capabilities
}

var backupNamePattern = regexp.MustCompile(`^(.+)-update-backup-[0-9a-f]+$`)

func isUpdateBackup(info updateContainer) bool {
	match := backupNamePattern.FindStringSubmatch(info.name())
	if match == nil {
		return false
	}
	home, ssh := false, false
	for _, mount := range info.Mounts {
		if mount.Type == "volume" && mount.Destination == "/home/agent" && mount.Name == match[1]+"-home" {
			home = true
		}
		if mount.Type == "volume" && mount.Destination == "/var/lib/agent-sshd" && mount.Name == match[1]+"-sshd" {
			ssh = true
		}
	}
	return home && ssh
}

func readUpdateContainer(reference string) (updateContainer, error) {
	data, err := capturePodman(false, "container", "inspect", reference)
	if err != nil {
		return updateContainer{}, err
	}
	var infos []updateContainer
	if err := json.Unmarshal(data, &infos); err != nil || len(infos) != 1 {
		return updateContainer{}, fmt.Errorf("invalid container snapshot for %s", reference)
	}
	info := infos[0]
	if !containerIDPattern.MatchString(info.Id) {
		return updateContainer{}, fmt.Errorf("invalid container identity: %s", reference)
	}
	if info.Config.Labels[ownerLabel] != owner() {
		return updateContainer{}, fmt.Errorf("container %s is not owned by this configuration", reference)
	}
	return info, nil
}

func inspectUpdateContainer(name string) (updateContainer, error) {
	if !namePattern.MatchString(name) {
		return updateContainer{}, fmt.Errorf("invalid sandbox name: %s", name)
	}
	info, err := readUpdateContainer(name)
	if err != nil {
		return updateContainer{}, err
	}
	if info.name() != name {
		return updateContainer{}, fmt.Errorf("container identity changed: %s", name)
	}
	return info, nil
}

func inspectUpdateContainerID(id string) (updateContainer, error) {
	info, err := readUpdateContainer(id)
	if err != nil {
		return updateContainer{}, err
	}
	if info.Id != id {
		return updateContainer{}, fmt.Errorf("container identity changed: %s", id)
	}
	return info, nil
}

func planUpdate(info updateContainer) (updatePlan, error) {
	var plan updatePlan
	name := info.name()
	if !slices.Contains([]string{"running", "exited", "created", "stopped"}, info.State.Status) || info.State.Running != (info.State.Status == "running") {
		return plan, fmt.Errorf("cannot update %s in state %s", name, info.State.Status)
	}
	capability := info.Config.Labels[capabilitiesLabel]
	if capability == "" {
		capability = "none"
	}
	if err := validateCapabilities(capability); err != nil {
		return plan, err
	}
	options := createOptions{capabilities: capability, memory: strconv.FormatInt(info.HostConfig.Memory, 10),
		pidsLimit: strconv.FormatInt(info.HostConfig.PidsLimit, 10), shmSize: strconv.FormatInt(info.HostConfig.ShmSize, 10)}
	if info.HostConfig.Memory < 0 || info.HostConfig.NanoCpus < 0 || info.HostConfig.PidsLimit < -1 || info.HostConfig.ShmSize < 0 {
		return plan, fmt.Errorf("invalid resource settings for %s", name)
	}
	cpus := float64(info.HostConfig.NanoCpus) / 1e9
	if info.HostConfig.NanoCpus == 0 && info.HostConfig.CpuQuota > 0 {
		period := info.HostConfig.CpuPeriod
		if period == 0 {
			period = 100000
		}
		if period < 0 {
			return plan, fmt.Errorf("invalid CPU period")
		}
		cpus = float64(info.HostConfig.CpuQuota) / float64(period)
	}
	options.cpus = strconv.FormatFloat(cpus, 'f', -1, 64)
	ports := info.HostConfig.PortBindings
	bindings := ports["2222/tcp"]
	if len(ports) != 1 || len(bindings) != 1 || bindings[0].HostIp != "127.0.0.1" {
		return plan, fmt.Errorf("unexpected SSH port mapping in %s", name)
	}
	port, err := strconv.Atoi(bindings[0].HostPort)
	if err != nil || port < 1024 || port > 65535 {
		return plan, fmt.Errorf("invalid SSH port for %s", name)
	}
	options.port = bindings[0].HostPort
	expected := map[string]string{"/workspace": name + "-workspace", "/home/agent": name + "-home", "/var/lib/agent-sshd": name + "-sshd"}
	seen := map[string]bool{}
	for _, mount := range info.Mounts {
		if seen[mount.Destination] {
			return plan, fmt.Errorf("duplicate mount in %s", name)
		}
		seen[mount.Destination] = true
		if mount.RW != nil && !*mount.RW {
			return plan, fmt.Errorf("unexpected read-only mount in %s", name)
		}
		if mount.Destination == "/run/user/1000" && mount.Type == "tmpfs" {
			continue
		}
		volume, ok := expected[mount.Destination]
		if !ok {
			return plan, fmt.Errorf("unexpected mount at %s", mount.Destination)
		}
		if mount.Destination == "/workspace" && mount.Type == "bind" {
			workspace, err := updateWorkspacePath(mount.Source)
			if err != nil {
				return plan, err
			}
			stat, err := os.Stat(workspace)
			if err != nil || !stat.IsDir() {
				return plan, fmt.Errorf("workspace directory is missing: %s", workspace)
			}
			options.bind = true
			options.workspace = workspace
		} else {
			if mount.Type != "volume" || mount.Name != volume {
				return plan, fmt.Errorf("unexpected mount at %s", mount.Destination)
			}
			if err := requireVolumeOwned(volume); err != nil {
				return plan, err
			}
		}
		delete(expected, mount.Destination)
	}
	if len(expected) != 0 {
		return plan, fmt.Errorf("missing sandbox mounts in %s", name)
	}
	plan.container = info
	plan.options = options
	return plan, nil
}

func updateWorkspacePath(source string) (string, error) {
	if runtime.GOOS == "windows" && strings.HasPrefix(source, "/") {
		if len(source) < 7 || !strings.HasPrefix(source, "/mnt/") || source[6] != '/' || !((source[5] >= 'a' && source[5] <= 'z') || (source[5] >= 'A' && source[5] <= 'Z')) {
			return "", fmt.Errorf("unsupported WSL workspace bind %s; use the default /mnt/DRIVE automount root", source)
		}
		source = strings.ToUpper(source[5:6]) + ":" + filepath.FromSlash(source[6:])
	}
	return workspacePath(source)
}

const updateTransactionLabel = "io.sandboxed-agents.update-transaction"

var containerIDPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

func replaceSandbox(plan updatePlan, image updateImage) (result error) {
	capability := plan.selectedCapability()
	ctx, stopSignals := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopSignals()
	run := func(args ...string) error {
		err := updateCommand(ctx, args...).Run()
		if ctx.Err() != nil {
			return updateInterrupted(ctx.Err())
		}
		return err
	}
	name := plan.container.name()
	info, err := inspectUpdateContainer(name)
	if err != nil {
		return err
	}
	current, err := planUpdate(info)
	if err != nil {
		return err
	}
	current.capability = plan.capability
	if !reflect.DeepEqual(current, plan) {
		return fmt.Errorf("%s changed during update; retry with current settings", name)
	}
	state, err := stateDir()
	if err != nil {
		return err
	}
	if err := privateStateDirectory(state, state); err != nil {
		return err
	}
	directory, err := os.MkdirTemp(state, "update-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(directory)
	if err := securePrivatePath(directory); err != nil {
		return err
	}
	cidfile := filepath.Join(directory, "container-id")
	token := make([]byte, 16)
	if _, err := rand.Read(token); err != nil {
		return err
	}
	transaction := hex.EncodeToString(token)
	backup := name + "-update-backup-" + transaction
	oldID := plan.container.Id
	newID := ""
	renamed := false
	renameAttempted := false
	finished := false
	readCandidate := func() string {
		data, err := os.ReadFile(cidfile)
		if err != nil {
			return ""
		}
		candidate := strings.TrimSpace(string(data))
		if !containerIDPattern.MatchString(candidate) || candidate == oldID {
			return ""
		}
		return candidate
	}
	defer func() {
		if finished {
			return
		}
		var rollback error
		if renameAttempted && !renamed {
			original, err := inspectUpdateContainerID(oldID)
			if err != nil {
				rollback = fmt.Errorf("could not confirm the original container after rename: %w", err)
			} else {
				switch original.name() {
				case backup:
					renamed = true
				case name:
				default:
					rollback = fmt.Errorf("original container name changed unexpectedly after rename")
				}
			}
		}
		if newID == "" {
			newID = readCandidate()
			if newID == "" && renamed {
				candidate, err := inspectUpdateContainer(name)
				if err == nil && candidate.Id != oldID && candidate.Config.Labels[updateTransactionLabel] == transaction {
					newID = candidate.Id
				}
			}
		}
		if rollback == nil && newID != "" {
			rollback = podman("rm", "--force", newID)
		}
		if rollback == nil && renamed {
			rollback = podman("rename", oldID, name)
		}
		if rollback == nil && plan.container.State.Running {
			rollback = podman("start", oldID)
		}
		if rollback != nil {
			result = fmt.Errorf("%w; rollback failed: %v. Previous container %s, backup %s; volumes and SSH files retained", result, rollback, oldID, backup)
		}
	}()
	if plan.container.State.Running {
		if err := run("stop", oldID); err != nil {
			return err
		}
	}
	renameAttempted = true
	if err := run("rename", oldID, backup); err != nil {
		return err
	}
	renamed = true
	options := plan.options
	options.capabilities = capability
	args := containerArguments(name, options, image.image, image.runtimeArgs, containerCreation{
		command: "create", cidfile: cidfile, transaction: transaction,
	})
	if err := run(args...); err != nil {
		return err
	}
	newID = readCandidate()
	if newID == "" {
		return fmt.Errorf("Podman returned an invalid replacement container ID")
	}
	if err := run("start", newID); err != nil {
		return err
	}
	if err := updateReady(ctx, newID); err != nil {
		return err
	}
	for _, kind := range []string{"agents", "tools"} {
		if err := run("exec", "--user", "1000:1000", "--workdir", "/workspace", newID, "/usr/local/bin/sandbox-"+kind, "boot"); err != nil {
			return err
		}
	}
	if !plan.container.State.Running {
		if err := run("stop", newID); err != nil {
			return err
		}
	}
	finished = true
	if err := run("rm", oldID); err != nil {
		return fmt.Errorf("%s was updated, but stopped backup %s could not be removed: %w", name, backup, err)
	}
	fmt.Fprintf(os.Stdout, "Updated %s; persistent volumes and SSH files retained.\n", name)
	return nil
}

func updateInterrupted(cause error) error { return fmt.Errorf("update interrupted: %w", cause) }

func updateReady(ctx context.Context, id string) error {
	readyContext, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	lastError := "readiness check did not finish"
	for {
		if readyContext.Err() != nil {
			if ctx.Err() != nil {
				return updateInterrupted(ctx.Err())
			}
			return fmt.Errorf("replacement container did not become ready within 15 seconds: %s", lastError)
		}
		command := updateCommand(readyContext, "exec", "--user", "0", id, "/bin/sh", "-c", "/usr/sbin/sshd -t && /usr/bin/pgrep -x sshd >/dev/null")
		var stderr bytes.Buffer
		command.Stdout, command.Stderr = io.Discard, &stderr
		err := command.Run()
		if err == nil {
			return nil
		}
		if message := strings.TrimSpace(stderr.String()); message != "" {
			lastError = message
		} else {
			lastError = err.Error()
		}
		select {
		case <-readyContext.Done():
		case <-time.After(250 * time.Millisecond):
		}
	}
}

func updateCommand(ctx context.Context, args ...string) *exec.Cmd {
	base := platformPodmanCommand(args...)
	command := exec.CommandContext(ctx, base.Path, base.Args[1:]...)
	command.Env, command.Dir = base.Env, base.Dir
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return command
}
