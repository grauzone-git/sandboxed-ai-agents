package main

import (
	"fmt"
	"os"
	"slices"
	"testing"

	"golang.org/x/sys/unix"
)

func TestSessionsAllocateTTYOnlyForTerminalStreams(t *testing.T) {
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|unix.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer master.Close()
	if err := unix.IoctlSetPointerInt(int(master.Fd()), unix.TIOCSPTLCK, 0); err != nil {
		t.Fatal(err)
	}
	number, err := unix.IoctlGetInt(int(master.Fd()), unix.TIOCGPTN)
	if err != nil {
		t.Fatal(err)
	}
	terminal, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR|unix.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer terminal.Close()
	null, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer null.Close()
	for _, test := range []struct {
		name   string
		stream *os.File
		tty    bool
	}{{"terminal", terminal, true}, {"null-device", null, false}} {
		t.Run(test.name, func(t *testing.T) {
			command, log := lifecycleCommand(t, "agent01", "codex")
			command.Stdin, command.Stdout, command.Stderr = test.stream, test.stream, test.stream
			if err := command.Run(); err != nil {
				t.Fatal(err)
			}
			calls := lifecycleCalls(t, log)
			last := calls[len(calls)-1]
			if slices.Contains(last, "-t") != test.tty || !slices.Contains(last, "-i") {
				t.Fatalf("incorrect interactive arguments: %v", last)
			}
		})
	}
}
