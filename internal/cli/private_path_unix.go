//go:build !windows

package cli

import "os"

func securePrivatePath(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	mode := os.FileMode(0600)
	if info.IsDir() {
		mode = 0700
	}
	return os.Chmod(path, mode)
}

func pathRedirected(info os.FileInfo) bool { return info.Mode()&os.ModeSymlink != 0 }
