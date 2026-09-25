package cli

import (
	"fmt"
	"os"
	"slices"
	"strings"
)

const adoptedFromLabel = "io.sandboxed-agents.adopted-from"

func adopt(name string, args []string) error {
	source := ""
	all := false
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		flag := args[i]
		if seen[flag] {
			return fmt.Errorf("duplicate adoption option: %s", flag)
		}
		seen[flag] = true
		switch flag {
		case "--all":
			all = true
		case "--from":
			i++
			if i == len(args) {
				return fmt.Errorf("supply the exact checkout owner path with --from PATH")
			}
			source = args[i]
		case "--help", "-h":
			fmt.Fprintln(os.Stdout, "Usage: sandboxed-agents NAME adopt --from PATH\n       sandboxed-agents adopt --all --from PATH")
			return nil
		default:
			return fmt.Errorf("unknown adoption option: %s", flag)
		}
	}
	if !checkoutOwner(source) || source == owner() {
		return fmt.Errorf("--from must be the absolute checkout owner path, different from the selected controller")
	}
	if all == (name != "") {
		return fmt.Errorf("supply one sandbox name, or adopt --all")
	}
	if err := requirePodman(); err != nil {
		return err
	}
	names := []string{name}
	if all {
		data, err := capturePodman(false, "ps", "--all", "--filter", "label="+ownerLabel+"="+source, "--format", "{{.Names}}")
		if err != nil {
			return err
		}
		names = strings.Fields(string(data))
		slices.Sort(names)
		names = slices.Compact(names)
	}
	if len(names) == 0 {
		fmt.Fprintln(os.Stdout, "No sandboxes owned by that checkout to adopt.")
		return nil
	}
	plans := make([]updatePlan, 0, len(names))
	migrations := make([]*adoptionSSHMigration, 0, len(names))
	for _, name := range names {
		if all {
			info, err := inspectUpdateContainer(name, source)
			if err != nil {
				return err
			}
			if isUpdateBackup(info) {
				fmt.Fprintf(os.Stdout, "Skipping update backup %s; retain it until recovery is complete.\n", name)
				continue
			}
		}
		plan, err := snapshotUpdateForOwner(name, source)
		if err != nil {
			return err
		}
		migrate, err := prepareAdoptionSSH(name, source, plan.options.port)
		if err != nil {
			return err
		}
		plans = append(plans, plan)
		migrations = append(migrations, migrate)
	}
	if len(plans) == 0 {
		return nil
	}
	images, err := prepareUpdateImages(plans, true)
	if err != nil {
		return err
	}
	for i, plan := range plans {
		target := replacementTarget{owner: owner(), labels: map[string]string{adoptedFromLabel: source}}
		if migration := migrations[i]; migration != nil {
			target.checkReady, target.beforeCommit = migration.checkReady, migration.commit
		}
		if err := replaceSandboxForOwner(plan, plan.options.capabilities, images[plan.options.capabilities], target); err != nil {
			return err
		}
		fmt.Fprintf(os.Stdout, "Adopted %s from %s; volume labels and data retained.\n", strings.TrimPrefix(plan.container.Name, "/"), source)
	}
	return nil
}
