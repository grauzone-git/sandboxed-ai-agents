package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"
	"runtime"
	"slices"
	"strings"
)

func fakeWindowsRuntime() bool {
	if runtime.GOOS != "windows" {
		return false
	}
	args := os.Args[1:]
	if log := os.Getenv("SANDBOX_WINDOWS_RUNTIME_LOG"); log != "" {
		file, err := os.OpenFile(log, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			panic(err)
		}
		json.NewEncoder(file).Encode(args)
		file.Close()
	}
	fault := os.Getenv("SANDBOX_FAKE_MACHINE_FAULT")
	if len(args) > 2 && args[0] == "--connection" {
		args = args[2:]
		os.Args = append([]string{os.Args[0]}, args...)
	}
	respond := func(value any) bool { json.NewEncoder(os.Stdout).Encode(value); return true }
	if len(args) > 2 && slices.Equal(args[:3], []string{"system", "connection", "list"}) {
		if fault == "missing" {
			return respond([]any{})
		}
		host, socket := "127.0.0.1", "/run/user/1000/podman/podman.sock"
		if fault == "remote" {
			host = "other.example"
		}
		if fault == "rootful" {
			socket = "/run/podman/podman.sock"
		}
		return respond([]any{map[string]any{"Name": "podman-machine-default", "Default": true, "URI": "ssh://user@" + host + ":50222" + socket}, map[string]any{"Name": "work-machine", "URI": "ssh://user@" + host + ":50223" + socket}, map[string]any{"Name": "renamed-connection", "URI": "ssh://user@" + host + ":50223" + socket}})
	}
	if len(args) > 1 && args[0] == "machine" && args[1] == "list" {
		kind := "wsl"
		if fault == "hyperv" {
			kind = "hyperv"
		}
		return respond([]any{map[string]any{"Name": "podman-machine-default", "VMType": kind, "Running": fault != "stopped"}, map[string]any{"Name": "work-machine", "VMType": kind, "Running": fault != "stopped"}})
	}
	if len(args) > 2 && args[0] == "machine" && args[1] == "inspect" {
		port := 50222
		if args[2] == "work-machine" {
			port = 50223
		}
		if fault == "endpoint" {
			port = 50224
		}
		return respond([]any{map[string]any{"Name": args[2], "Rootful": false, "State": "running", "SSHConfig": map[string]any{"Port": port, "RemoteUsername": "user"}}})
	}
	if args[0] == "version" {
		client, server := "6.0.0", "6.0.0"
		if fault == "old-client" {
			client = "5.9.0"
		}
		if fault == "old-server" {
			server = "5.9.0"
		}
		return respond(map[string]any{"Client": map[string]string{"Version": client}, "Server": map[string]string{"Version": server}})
	}
	if args[0] == "info" && args[len(args)-1] == "json" {
		arch := "amd64"
		if fault == "arch" {
			arch = "arm64"
		}
		controllers := []string{"cpu", "memory", "pids"}
		if fault == "cgroups" {
			controllers = []string{"cpu"}
		}
		return respond(map[string]any{"host": map[string]any{"arch": arch, "os": "linux", "security": map[string]any{"rootless": fault != "rootless", "seccompProfilePath": "/usr/share/containers/seccomp.json"}, "cgroupVersion": "v2", "cgroupControllers": controllers}})
	}
	if len(args) > 3 && args[0] == "machine" && args[1] == "ssh" {
		command := args[3]
		if strings.Contains(command, "sandbox-seccomp") {
			content, err := io.ReadAll(os.Stdin)
			if err != nil {
				panic(err)
			}
			if log := os.Getenv("SANDBOX_GUEST_PROFILE_LOG"); log != "" {
				if err := os.WriteFile(log, content, 0600); err != nil {
					panic(err)
				}
			}
			hashes := regexp.MustCompile(`'([a-f0-9]{64})'`).FindAllStringSubmatch(command, -1)
			if len(hashes) != 2 {
				panic("missing immutable policy identity")
			}
			fmt.Println("/home/user/.local/share/sandboxed-agents/seccomp/" + hashes[0][1] + "/" + hashes[1][1] + ".json")
			return true
		}

		if strings.Contains(command, "/proc/self/") {
			if fault == "subids" {
				fmt.Println("0 1000 1")
				return true
			}
			fmt.Println("0 1000 1\n1 100000 65536")
			return true
		}
		if strings.Contains(command, "/usr/share/containers/seccomp.json") {
			fmt.Println(`{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}`)
			return true
		}
		if strings.Contains(command, "/dev/fuse") {
			if fault == "devices" {
				os.Exit(21)
			}
			return true
		}
	}
	return false
}
