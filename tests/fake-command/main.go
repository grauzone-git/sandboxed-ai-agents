// fake-command lets Windows contracts execute Python fakes through os/exec.
package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

func main() {
	if err := run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			os.Exit(exit.ExitCode())
		}
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	base := strings.TrimSuffix(executable, ".exe")
	python, err := os.ReadFile(base + ".python")
	if err != nil {
		return err
	}
	args := append([]string{"-B", base + ".py"}, os.Args[1:]...)
	command := exec.Command(string(python), args...)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	return command.Run()
}
