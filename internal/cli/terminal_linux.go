package cli

import (
	"os"

	"golang.org/x/sys/unix"
)

// Linux and Windows are the supported executable targets; this implementation
// uses Linux termios to distinguish a terminal from other character devices.
func terminalAvailable(file *os.File) bool {
	_, err := unix.IoctlGetTermios(int(file.Fd()), unix.TCGETS)
	return err == nil
}
