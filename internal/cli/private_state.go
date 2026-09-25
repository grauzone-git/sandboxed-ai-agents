package cli

import (
	"fmt"
	"os"
	"path/filepath"
)

// Check ancestors before creating state so a redirected parent cannot move
// private controller files outside the selected state directory.
func privateStateDirectory(state, directory string) error {
	parent := filepath.Dir(directory)
	if parent != directory {
		if err := privateStateDirectory(state, parent); err != nil {
			return err
		}
	}
	info, err := os.Lstat(directory)
	if os.IsNotExist(err) {
		if err := os.Mkdir(directory, 0700); err != nil {
			return err
		}
	} else if err != nil {
		return err
	} else if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("private state directory must not be redirected: %s", directory)
	}
	// Do not change permissions on the user's home or other state ancestors.
	if containsPath(state, directory) {
		return os.Chmod(directory, 0700)
	}
	return nil
}
