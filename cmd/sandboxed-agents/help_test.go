package main

import (
	"slices"
	"strings"
	"testing"
)

func TestHelpRetainsImplementedCommandsAndCompatibilityOptions(t *testing.T) {
	command, log := lifecycleCommand(t, "help")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("help failed: %v %s", err, output)
	}
	lines := strings.Split(string(output), "\n")
	usage := func(prefix string) string {
		t.Helper()
		for _, line := range lines {
			if strings.HasPrefix(strings.TrimSpace(line), prefix) {
				return strings.TrimSpace(line)
			}
		}
		t.Fatalf("help omitted %q: %s", prefix, output)
		return ""
	}
	for _, prefix := range []string{"sandboxed-agents NAME update ", "sandboxed-agents update --all "} {
		line := usage(prefix)
		for _, option := range []string{"[--no-build]", "[--capabilities podman|none]"} {
			if !strings.Contains(line, option) {
				t.Fatalf("update help omitted %s: %s", option, line)
			}
		}
	}
	for _, command := range []string{"service", "forward"} {
		line := usage("sandboxed-agents NAME " + command + " ")
		ids := strings.Split(strings.Fields(line)[3], "|")
		slices.Sort(ids)
		if !slices.Equal(ids, []string{"deepseek-ui", "hermes-dashboard", "t3", "tokentracker"}) {
			t.Fatalf("help lost service IDs: %s", line)
		}
	}
	for _, target := range []string{"t3", "azdo", "azure"} {
		usage("sandboxed-agents NAME tools setup " + target)
	}
	for _, prefix := range []string{"sandboxed-agents NAME adopt --from PATH", "sandboxed-agents adopt --all --from PATH"} {
		usage(prefix)
	}
	if line := usage("sandboxed-agents NAME up "); !strings.Contains(line, "[--ssh-config]") {
		t.Fatalf("up help lost SSH opt-in: %s", line)
	}
	if line := usage("sandboxed-agents NAME remove "); !strings.Contains(line, "[--ssh-config]") || !strings.Contains(line, "[--volumes]") {
		t.Fatalf("remove help lost compatibility options: %s", line)
	}
	aliases := map[string]string{}
	for _, line := range lines {
		left, right, ok := strings.Cut(line, " are accepted as aliases for ")
		if !ok {
			continue
		}
		names, targets := strings.Split(left, " and "), strings.Split(strings.TrimSuffix(right, "."), " and ")
		if len(names) != len(targets) {
			t.Fatalf("invalid alias help: %s", line)
		}
		for i, name := range names {
			aliases[name] = targets[i]
		}
	}
	if aliases["hermes"] != "hermes-dashboard" || aliases["deepseek"] != "deepseek-ui" {
		t.Fatalf("help lost service aliases: %v", aliases)
	}
	if calls := lifecycleCalls(t, log); len(calls) != 0 {
		t.Fatalf("help contacted Podman or SSH: %v", calls)
	}
}
