package cli

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

func stateDir() (string, error) {
	root := os.Getenv("XDG_STATE_HOME")
	if runtime.GOOS == "windows" {
		root = os.Getenv("LOCALAPPDATA")
		if root == "" {
			return "", fmt.Errorf("LOCALAPPDATA must identify the local state directory")
		}
	}
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		root = filepath.Join(home, ".local", "state")
	}
	if !filepath.IsAbs(root) {
		return "", fmt.Errorf("state directory must be absolute: %s", root)
	}
	return filepath.Join(root, "sandboxed-agents"), nil
}

// canonicalPath resolves existing ancestors too, so a new directory under a
// symlink cannot bypass the protected-path comparison.
func canonicalPath(path string) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	current := filepath.Clean(absolute)
	var missing []string
	for {
		resolved, err := resolveExistingPath(current)
		if err == nil {
			for i := len(missing) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, missing[i])
			}
			return filepath.Clean(resolved), nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", err
		}
		// A dangling symlink is not a nonexistent path component.
		if info, statErr := os.Lstat(current); statErr == nil && info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("cannot resolve symlink %s", current)
		}
		missing = append(missing, filepath.Base(current))
		current = parent
	}
}

func containsPath(parent, child string) bool {
	if runtime.GOOS == "windows" {
		parent = strings.ToLower(parent)
		child = strings.ToLower(child)
	}
	relative, err := filepath.Rel(parent, child)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func workspacePath(path string) (string, error) {
	if path == "" || strings.ContainsAny(path, "\r\n") {
		return "", fmt.Errorf("workspace path must not be empty or contain line breaks")
	}
	if runtime.GOOS != "windows" && strings.Contains(path, ":") {
		return "", fmt.Errorf("unsupported workspace path: %s", path)
	}
	if err := validateLocalWorkspace(path); err != nil {
		return "", err
	}
	workspace, err := canonicalPath(path)
	if err != nil {
		return "", fmt.Errorf("resolve workspace %s: %w", path, err)
	}
	state, err := stateDir()
	if err != nil {
		return "", err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	executable, err := os.Executable()
	if err != nil {
		return "", err
	}
	protected := []string{state, filepath.Join(home, ".ssh"), executable}
	buildParent, err := canonicalPath(os.TempDir())
	if err != nil {
		return "", err
	}
	if containsPath(workspace, buildParent) {
		return "", fmt.Errorf("workspace %s would expose future build contexts in %s. Choose a separate workspace directory.", workspace, buildParent)
	}
	// os.Executable can resolve a launch symlink. Protect its containing
	// directory too, including a project-local node_modules/.bin installation.
	invoked, err := exec.LookPath(os.Args[0])
	if err == nil {
		invoked, err = filepath.Abs(invoked)
		if err != nil {
			return "", err
		}
		parent, err := canonicalPath(filepath.Dir(invoked))
		if err != nil {
			return "", err
		}
		invoked = filepath.Join(parent, filepath.Base(invoked))
		if containsPath(workspace, invoked) || containsPath(invoked, workspace) {
			return "", fmt.Errorf("workspace %s conflicts with executable path %s. Use a global install of sandboxed-agents outside the workspace.", workspace, invoked)
		}
	}
	temporary, err := os.ReadDir(os.TempDir())
	if err != nil {
		return "", fmt.Errorf("inspect temporary build directories: %w", err)
	}
	for _, entry := range temporary {
		if strings.HasPrefix(entry.Name(), "sandboxed-agents-build-") {
			protected = append(protected, filepath.Join(os.TempDir(), entry.Name()))
		}
	}
	for _, path := range protected {
		resolved, err := canonicalPath(path)
		if err != nil {
			return "", fmt.Errorf("resolve protected path %s: %w", path, err)
		}
		if containsPath(workspace, resolved) || containsPath(resolved, workspace) {
			advice := "Choose a separate workspace directory."
			if path == executable {
				advice = "Use a global install of sandboxed-agents outside the workspace."
			}
			return "", fmt.Errorf("workspace %s conflicts with protected path %s. %s", workspace, resolved, advice)
		}
	}
	if info, err := os.Stat(workspace); err == nil && !info.IsDir() {
		return "", fmt.Errorf("workspace must be a directory: %s", workspace)
	} else if err != nil && !os.IsNotExist(err) {
		return "", err
	}
	return workspace, nil
}
