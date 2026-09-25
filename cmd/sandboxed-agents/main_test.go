package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

type buildRecord struct {
	Args  []string
	Files map[string]uint32
	Dirs  map[string]uint32
}

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
		if _, err := os.Stat(filepath.Join(context, "Containerfile")); err != nil {
			panic(err)
		}
		record := buildRecord{Args: args, Files: map[string]uint32{}, Dirs: map[string]uint32{}}
		if err := filepath.WalkDir(context, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			info, err := entry.Info()
			if err != nil {
				return err
			}
			relative, err := filepath.Rel(context, path)
			if err != nil {
				return err
			}
			if entry.IsDir() {
				record.Dirs[relative] = uint32(info.Mode().Perm())
			} else {
				record.Files[relative] = uint32(info.Mode().Perm())
			}
			return nil
		}); err != nil {
			panic(err)
		}
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
	for _, scenario := range []struct {
		fail        bool
		restrictive bool
	}{{false, false}, {true, false}, {false, true}, {true, true}} {
		t.Run(fmt.Sprintf("fail=%t/umask077=%t", scenario.fail, scenario.restrictive), func(t *testing.T) {
			if scenario.restrictive && os.PathSeparator != '/' {
				t.Skip("umask requires a Unix host")
			}
			command := cliCommand(t, "build", "--build-arg", "VALUE=contains spaces")
			if scenario.restrictive {
				shell, err := exec.LookPath("sh")
				if err != nil {
					t.Fatal(err)
				}
				command.Args = append([]string{shell, "-c", `umask 077; exec "$@"`, "sh"}, command.Args...)
				command.Path = shell
			}
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
			if scenario.fail {
				command.Env = append(command.Env, "SANDBOX_BUILD_FAIL=1")
			}
			output, err := command.CombinedOutput()
			if scenario.fail {
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
			var record buildRecord
			if err = json.Unmarshal(data, &record); err != nil {
				t.Fatal(err)
			}
			context := record.Args[len(record.Args)-1]
			if _, err = os.Stat(context); !os.IsNotExist(err) {
				t.Fatalf("build context not removed: %s %v", context, err)
			}
			if _, ok := record.Files["agent-manager.cjs"]; !ok {
				t.Fatal("build context omitted the manager")
			}
			if os.PathSeparator == '/' {
				for path, mode := range record.Dirs {
					if mode != 0700 {
						t.Errorf("context directory %s mode %o, want 700", path, mode)
					}
				}
				for path, mode := range record.Files {
					if mode != 0644 {
						t.Errorf("context file %s mode %o, want 644 for non-root image users", path, mode)
					}
				}
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
