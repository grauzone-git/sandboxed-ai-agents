package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strings"
	"text/tabwriter"
)

type containerInfo struct {
	Name       string
	Config     struct{ Labels map[string]string }
	State      struct{ Running bool }
	Mounts     []struct{ Type, Source, Name, Destination string }
	HostConfig struct {
		PortBindings map[string][]struct{ HostPort string }
	}
}

func list() error {
	names, err := capturePodman(false, "ps", "--all", "--filter", "label="+ownerLabel+"="+owner(), "--format", "{{.Names}}")
	if err != nil {
		return err
	}
	sorted := strings.Fields(string(names))
	slices.Sort(sorted)
	sorted = slices.Compact(sorted)
	var rows [][]string
	for _, name := range sorted {
		data, err := capturePodman(true, "container", "inspect", name)
		if err != nil {
			continue
		} // The container may have disappeared since listing.
		var infos []containerInfo
		if err := json.Unmarshal(data, &infos); err != nil || len(infos) != 1 || infos[0].Name == "" {
			return fmt.Errorf("Podman returned unexpected details for %s", name)
		}
		info := infos[0]
		if info.Config.Labels[ownerLabel] != owner() {
			continue
		}
		row := []string{strings.TrimPrefix(info.Name, "/"), "stopped", "-", "-", "-"}
		if info.State.Running {
			row[1] = "running"
			row[3] = enabledAgents(row[0])
		}
		if bindings := info.HostConfig.PortBindings["2222/tcp"]; len(bindings) > 0 && bindings[0].HostPort != "" {
			row[2] = bindings[0].HostPort
		}
		for _, mount := range info.Mounts {
			if mount.Destination == "/workspace" {
				if mount.Type == "bind" {
					row[4] = mount.Source
				} else {
					row[4] = mount.Name
				}
				if row[4] == "" {
					row[4] = "-"
				}
			}
		}
		rows = append(rows, row)
	}
	if len(rows) == 0 {
		fmt.Fprintf(os.Stdout, "No sandboxes owned by controller %s.\n", owner())
		return nil
	}
	writer := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(writer, "NAME\tSTATE\tSSH PORT\tAGENTS\tWORKSPACE")
	for _, row := range rows {
		fmt.Fprintln(writer, strings.Join(row, "\t"))
	}
	return writer.Flush()
}

var agentIDPattern = regexp.MustCompile(`^[a-z0-9-]+$`)

func enabledAgents(name string) string {
	data, err := capturePodman(true, "exec", "--user", "1000:1000", name, "/bin/cat", "/home/agent/.local/state/sandbox-agents/config.json")
	if err != nil {
		return "unknown"
	}
	var state struct {
		Enabled json.RawMessage `json:"enabled"`
	}
	if json.Unmarshal(data, &state) != nil || len(state.Enabled) == 0 || string(state.Enabled) == "null" {
		return "unknown"
	}
	var values map[string]json.RawMessage
	if json.Unmarshal(state.Enabled, &values) != nil {
		return "unknown"
	}
	names := make([]string, 0, len(values))
	for name := range values {
		if !agentIDPattern.MatchString(name) {
			return "unknown"
		}
		names = append(names, name)
	}
	slices.Sort(names)
	if len(names) == 0 {
		return "none"
	}
	return strings.Join(names, ",")
}
