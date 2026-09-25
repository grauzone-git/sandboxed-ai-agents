package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWindowsUpdateRejectsCustomAutomountAndProtectedTranslatedBind(t *testing.T) {
	for _, protected := range []bool{false, true} {
		t.Run(map[bool]string{false: "custom automount", true: "protected SSH directory"}[protected], func(t *testing.T) {
			fixture := updateFixture()
			command, log, state := updateTestCommand(t, map[string]any{"agent01": fixture}, "agent01", "update", "--no-build")
			source := "/custom/c/workspace"
			if protected {
				home := t.TempDir()
				path := filepath.Join(home, ".ssh")
				if err := os.Mkdir(path, 0700); err != nil {
					t.Fatal(err)
				}
				command.Env = append(command.Env, "HOME="+home, "USERPROFILE="+home)
				source = "/mnt/" + strings.ToLower(path[:1]) + filepath.ToSlash(path[2:])
			}
			fixture["Mounts"].([]map[string]any)[2] = map[string]any{"Type": "bind", "Source": source, "Destination": "/workspace", "RW": true}
			writeUpdateState(t, state, map[string]any{"agent01": fixture})
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("unsafe bind accepted: %s", output)
			}
			assertNoUpdateMutation(t, log, "", "stop", "rename", "create")
		})
	}
}
