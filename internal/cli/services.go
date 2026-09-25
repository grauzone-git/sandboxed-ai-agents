package cli

import (
	"fmt"
	"os"
	"os/exec"
	"slices"
	"strconv"
	"strings"
)

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'" }

func managedSSH(name string, arguments ...string) (*exec.Cmd, error) {
	files, err := sshPaths(name)
	if err != nil {
		return nil, err
	}
	if err := files.validate(); err != nil {
		return nil, err
	}
	for _, path := range []string{files.entry, files.key, files.known} {
		if info, err := os.Stat(path); err != nil || !info.Mode().IsRegular() {
			return nil, fmt.Errorf("SSH setup is required; run sandboxed-agents %s ssh-config --install", name)
		}
	}
	common := []string{"-F", files.entry, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "IdentityAgent=none", "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ConnectTimeout=10"}
	command := exec.Command("ssh", append(common, arguments...)...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return command, nil
}

func serviceCatalog() (map[string]catalogEntry, []string, error) {
	catalog, err := readCatalog("tools")
	if err != nil {
		return nil, nil, err
	}
	var names []string
	for name, entry := range catalog {
		if entry.Port < 1 || entry.Port > 65535 {
			delete(catalog, name)
			continue
		}
		names = append(names, name)
	}
	slices.Sort(names)
	return catalog, names, nil
}

func serviceHelp() (string, string, error) {
	catalog, names, err := serviceCatalog()
	if err != nil {
		return "", "", err
	}
	var aliases, targets []string
	for _, name := range names {
		if alias := catalog[name].Agent; alias != "" {
			aliases = append(aliases, alias)
			targets = append(targets, name)
		}
	}
	aliasHelp := ""
	if len(aliases) > 0 {
		aliasHelp = strings.Join(aliases, " and ") + " are accepted as aliases for " + strings.Join(targets, " and ") + ".\n"
	}
	return strings.Join(names, "|"), aliasHelp, nil
}

func serviceTarget(id string) (string, int, error) {
	catalog, names, err := serviceCatalog()
	if err != nil {
		return "", 0, err
	}
	if _, ok := catalog[id]; !ok {
		for _, name := range names {
			if alias := catalog[name].Agent; alias != "" && alias == id {
				id = name
				break
			}
		}
	}
	entry, ok := catalog[id]
	if !ok {
		return "", 0, fmt.Errorf("choose a service: %s", strings.Join(names, ", "))
	}
	return id, entry.Port, nil
}

func services(command, name string, parameters []string) error {
	if len(parameters) < 1 || len(parameters) > 2 {
		return fmt.Errorf("use sandboxed-agents NAME %s ID [OPTION]", command)
	}
	id, remotePort, err := serviceTarget(parameters[0])
	if err != nil {
		return err
	}
	operation := "status"
	localPort := remotePort
	if command == "service" {
		if len(parameters) == 2 {
			operation = parameters[1]
		}
		switch operation {
		case "status", "start", "stop", "restart", "logs":
		default:
			return fmt.Errorf("choose service status, start, stop, restart, or logs")
		}
	} else {
		operation = "start"
		if len(parameters) == 2 {
			localPort, err = strconv.Atoi(parameters[1])
		}
		if err != nil || localPort < 1024 || localPort > 65535 {
			return fmt.Errorf("use a local port from 1024 to 65535")
		}
	}
	service, err := managedSSH(name, "-T", name, "/usr/local/bin/sandbox-tools service "+shellQuote(id)+" "+shellQuote(operation))
	if err != nil {
		return err
	}
	if err := requirePodman(); err != nil {
		return err
	}
	if err := requireOwned(name); err != nil {
		return err
	}
	if command == "forward" {
		if err := requireForwardPort(fmt.Sprintf("127.0.0.1:%d", localPort)); err != nil {
			return fmt.Errorf("local port %d is unavailable: %w", localPort, err)
		}
	}
	if err := service.Run(); err != nil {
		return err
	}
	if command == "service" {
		return nil
	}
	tunnel, err := managedSSH(name, "-o", "ExitOnForwardFailure=yes", "-N", "-L", fmt.Sprintf("127.0.0.1:%d:127.0.0.1:%d", localPort, remotePort), name)
	if err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "Forwarding http://127.0.0.1:%d to %s:%d; leave this terminal running.\n", localPort, name, remotePort)
	return tunnel.Run()
}

func toolsSetup(name string, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("use sandboxed-agents NAME tools setup t3|azdo|azure [OPTIONS]")
	}
	var azure azureOptions
	switch args[0] {
	case "azure":
		var err error
		azure, err = parseAzure(args[1:])
		if err != nil {
			return err
		}
	case "t3":
		if len(args) != 1 {
			return fmt.Errorf("use tools setup t3")
		}
	case "azdo":
		if len(args) > 2 || (len(args) == 2 && args[1] != "--persist" && args[1] != "--clear") {
			return fmt.Errorf("use tools setup azdo [--persist|--clear]")
		}
	default:
		return fmt.Errorf("choose setup t3, azdo, or azure")
	}
	if err := requirePodman(); err != nil {
		return err
	}
	if err := requireOwned(name); err != nil {
		return err
	}
	if azure.interactive {
		return azureInteractive(name, args[1:], azure)
	}
	command := append([]string{"exec"}, interactiveArgs()...)
	command = append(command, "--user", "1000:1000", "--workdir", "/workspace", name, "/usr/local/bin/sandbox-tools", "setup")
	return podman(append(command, args...)...)
}
