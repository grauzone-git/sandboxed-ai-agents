package main

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestRemoveAcceptsLegacySSHFlagWithoutOptingIn(t *testing.T) {
	for _, installed := range []bool{false, true} {
		for _, flags := range [][]string{{"--ssh-config"}, {"--ssh-config", "--volumes"}, {"--volumes", "--ssh-config"}} {
			newCommand := sshCommand
			if installed {
				newCommand = installedSSHCommand
			}
			command, log, root := newCommand(t, append([]string{"agent01", "remove"}, flags...)...)
			command.Env = append(command.Env, "SANDBOX_VOLUME_OWNER=default")
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("legacy remove flag refused: %v %s", err, output)
			}
			volumes := 0
			for _, call := range lifecycleCalls(t, log) {
				if call[0] == "exec" || call[0] == "ssh" {
					t.Fatalf("remove configured SSH: %v", call)
				}
				if len(call) > 1 && call[0] == "volume" && call[1] == "rm" {
					volumes++
				}
			}
			want := 0
			if slices.Contains(flags, "--volumes") {
				want = 3
			}
			if volumes != want {
				t.Fatalf("removed %d volumes, want %d", volumes, want)
			}
			if installed {
				for _, path := range []string{filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01.conf"), filepath.Join(root, "state", "sandboxed-agents", "ssh", "agent01", "id_ed25519")} {
					if _, err := os.Stat(path); !os.IsNotExist(err) {
						t.Fatalf("managed SSH retained: %s %v", path, err)
					}
				}
			} else if _, err := os.Stat(filepath.Join(root, "home", ".ssh")); !os.IsNotExist(err) {
				t.Fatalf("remove opted into SSH: %v", err)
			}
		}
	}
}
