package cli

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	sandboxassets "github.com/grauzone-git/sandboxed-ai-agents"
)

func withBuildContext(action func(string) error) (result error) {
	context, err := os.MkdirTemp("", "sandboxed-agents-build-")
	if err != nil {
		return err
	}
	defer func() {
		if err := os.RemoveAll(context); err != nil {
			if result == nil {
				result = fmt.Errorf("remove build context: %w", err)
			} else {
				fmt.Fprintf(os.Stderr, "Error: remove build context: %v\n", err)
			}
		}
	}()
	if err := securePrivatePath(context); err != nil {
		return err
	}
	err = fs.WalkDir(sandboxassets.Files, "src/container", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative := strings.TrimPrefix(path, "src/container")
		destination := filepath.Join(context, filepath.FromSlash(relative))
		if entry.IsDir() {
			return os.MkdirAll(destination, 0700)
		}
		data, err := sandboxassets.Files.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.WriteFile(destination, data, 0644); err != nil {
			return err
		}
		// COPY preserves these modes, so image users need read access even under a restrictive host umask.
		return os.Chmod(destination, 0644)
	})
	if err != nil {
		return err
	}
	return action(context)
}

func build(args []string) error {
	return withBuildContext(func(context string) error {
		command := []string{"build", "--pull=always", "-t", imageName(), "-f", filepath.Join(context, "Containerfile")}
		command = append(command, args...)
		command = append(command, "--label", versionLabel+"="+Version, context)
		return podman(command...)
	})
}
