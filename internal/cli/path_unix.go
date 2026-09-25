//go:build !windows

package cli

import "path/filepath"

func resolveExistingPath(path string) (string, error) { return filepath.EvalSymlinks(path) }
func validateLocalWorkspace(path string) error        { return nil }
