package main

import (
	"strings"
	"testing"
)

func TestNameFirstGrammar(t *testing.T) {
	for _, test := range []struct {
		args    []string
		message string
	}{
		{[]string{"up", "agent01"}, "sandbox name first"},
		{[]string{"build", "up"}, "cannot be used as a sandbox name"},
		{[]string{"version", "up"}, "cannot be used as a sandbox name"},
		{[]string{"agent01", "build"}, "does not take a sandbox name"},
		{[]string{"agent01", "wat"}, "Unknown command"},
		{[]string{"agent01", "up"}, "not available in this preview"},
	} {
		t.Run(strings.Join(test.args, "_"), func(t *testing.T) {
			command := cliCommand(t, test.args...)
			command.Env = append(command.Env, "PATH="+t.TempDir())
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), test.message) {
				t.Fatalf("wanted failure containing %q, got %v: %s", test.message, err, output)
			}
		})
	}
}
