package cli

import (
	"os"

	"golang.org/x/sys/windows"
)

func terminalAvailable(file *os.File) bool {
	var mode uint32
	return windows.GetConsoleMode(windows.Handle(file.Fd()), &mode) == nil
}
