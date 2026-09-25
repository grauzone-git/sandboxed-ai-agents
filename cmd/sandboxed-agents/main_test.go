package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestMain(m *testing.M) {
	if os.Getenv("SANDBOX_CLI_TEST_CHILD") == "1" {
		if name := strings.TrimSuffix(filepath.Base(os.Args[0]), ".exe"); name == "xdg-open" || name == "rundll32" {
			fakeAzureBrowser()
			return
		}
		if strings.TrimSuffix(filepath.Base(os.Args[0]), ".exe") == "ssh" {
			if fakeAzureSSH() {
				return
			}
			fakeSSH()
			return
		}
		if strings.TrimSuffix(filepath.Base(os.Args[0]), ".exe") == "podman" {
			fakePodman()
			return
		}
		main()
		return
	}
	os.Exit(m.Run())
}

func fakePodman() {
	if fakeWindowsRuntime() {
		return
	}
	if os.Getenv("SANDBOX_LIFECYCLE_LOG") != "" {
		fakeLifecyclePodman()
		return
	}
	args := os.Args[1:]
	if len(args) == 0 {
		os.Exit(2)
	}
	switch args[0] {
	case "info":
		fmt.Println("true")
	case "build":
		context := args[len(args)-1]
		info, err := os.Stat(context)
		if err != nil {
			panic(err)
		}
		if _, err := os.Stat(filepath.Join(context, "Containerfile")); err != nil {
			panic(err)
		}
		record := struct {
			Args []string
			Mode uint32
		}{args, uint32(info.Mode().Perm())}
		data, _ := json.Marshal(record)
		if err := os.WriteFile(os.Getenv("SANDBOX_BUILD_LOG"), data, 0600); err != nil {
			panic(err)
		}
		if os.Getenv("SANDBOX_BUILD_FAIL") == "1" {
			fmt.Fprintln(os.Stderr, "fixture build failure")
			os.Exit(23)
		}
	default:
		fmt.Fprintln(os.Stderr, "unexpected fake Podman operation")
		os.Exit(2)
	}
}

func cliCommand(t *testing.T, args ...string) *exec.Cmd {
	t.Helper()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command(executable, args...)
	home := t.TempDir()
	command.Dir = home
	command.Env = append(os.Environ(), "SANDBOX_CLI_TEST_CHILD=1", "HOME="+home, "USERPROFILE="+home, "XDG_STATE_HOME="+filepath.Join(home, "state"), "LOCALAPPDATA="+filepath.Join(home, "local"))
	return command
}

func TestVersionWithoutRuntimeOrCheckout(t *testing.T) {
	command := cliCommand(t, "version")
	command.Env = append(command.Env, "PATH="+t.TempDir())
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %s", err, output)
	}
	for _, field := range []string{"version", "commit", "assets"} {
		if !bytes.Contains(bytes.ToLower(output), []byte(field)) {
			t.Fatalf("version missing %s: %s", field, output)
		}
	}
	foundHash := false
	for _, word := range strings.Fields(string(output)) {
		if len(word) == 64 && strings.Trim(word, "0123456789abcdef") == "" {
			foundHash = true
		}
	}
	if !foundHash {
		t.Fatalf("version lacks asset SHA256: %s", output)
	}
}

func TestBuildUsesPrivateBundledContextAndCleansIt(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(fmt.Sprint(fail), func(t *testing.T) {
			command := cliCommand(t, "build", "--build-arg", "VALUE=contains spaces")
			bin := t.TempDir()
			filename := "podman"
			if strings.HasSuffix(os.Args[0], ".exe") {
				filename += ".exe"
			}
			executable, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			data, err := os.ReadFile(executable)
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(bin, filename), data, 0700); err != nil {
				t.Fatal(err)
			}
			log := filepath.Join(t.TempDir(), "build.json")
			command.Env = append(command.Env, "PATH="+bin+string(os.PathListSeparator)+os.Getenv("PATH"), "SANDBOX_BUILD_LOG="+log)
			if fail {
				command.Env = append(command.Env, "SANDBOX_BUILD_FAIL=1")
			}
			output, err := command.CombinedOutput()
			if fail {
				if exit, ok := err.(*exec.ExitError); !ok || exit.ExitCode() != 23 {
					t.Fatalf("want exit23, got %v: %s", err, output)
				}
			} else if err != nil {
				t.Fatalf("%v: %s", err, output)
			}
			data, err = os.ReadFile(log)
			if err != nil {
				t.Fatal(err)
			}
			var record struct {
				Args []string
				Mode uint32
			}
			if err = json.Unmarshal(data, &record); err != nil {
				t.Fatal(err)
			}
			context := record.Args[len(record.Args)-1]
			if _, err = os.Stat(context); !os.IsNotExist(err) {
				t.Fatalf("build context not removed: %s %v", context, err)
			}
			if os.PathSeparator == '/' && record.Mode != 0700 {
				t.Fatalf("context mode %o", record.Mode)
			}
			if !strings.Contains(strings.Join(record.Args, "\n"), "io.sandboxed-agents.version=0.1.0-dev") {
				t.Fatalf("missing version label %v", record.Args)
			}
			found := false
			for i, arg := range record.Args {
				if arg == "--build-arg" && i+1 < len(record.Args) && record.Args[i+1] == "VALUE=contains spaces" {
					found = true
				}
			}
			if !found {
				t.Fatalf("build arg changed: %v", record.Args)
			}
		})
	}
}
