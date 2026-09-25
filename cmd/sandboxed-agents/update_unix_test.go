//go:build !windows

package main

import (
	"bytes"
	"os"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestInterruptedUpdateRestoresOriginalContainer(t *testing.T) {
	for _, phase := range []string{"tools-boot", "ready", "create-no-cid", "rename-partial"} {
		t.Run(phase, func(t *testing.T) { interruptedUpdateRestoresOriginal(t, phase) })
	}
}

func interruptedUpdateRestoresOriginal(t *testing.T, phase string) {
	command, log, state := updateTestCommand(t, map[string]any{"agent01": updateFixture()}, "agent01", "update", "--no-build")
	command.Env = append(command.Env, "SANDBOX_UPDATE_BLOCK="+phase)
	var stderr bytes.Buffer
	command.Stderr = &stderr
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	ready := false
	for time.Now().Before(deadline) {
		data, _ := os.ReadFile(log)
		_, created := os.Stat(state + ".created")
		_, renamed := os.Stat(state + ".renamed")
		if (phase == "tools-boot" && strings.Contains(string(data), `"/usr/local/bin/sandbox-tools","boot"`)) || (phase == "ready" && strings.Contains(string(data), `"/usr/sbin/sshd -t`)) || (phase == "create-no-cid" && created == nil) || (phase == "rename-partial" && renamed == nil) {
			ready = true
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !ready {
		command.Process.Kill()
		command.Wait()
		t.Fatal("update never reached service restoration")
	}
	if err := command.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("interrupted update succeeded")
		}
	case <-time.After(5 * time.Second):
		command.Process.Kill()
		t.Fatal("interrupted update did not complete")
	}
	if !strings.Contains(stderr.String(), "update interrupted") || strings.Contains(stderr.String(), "within 15 seconds") || strings.Contains(stderr.String(), "readiness attempt not ready") {
		t.Fatalf("interruption diagnostic: %s", stderr.String())
	}
	assertOriginalUpdateRestored(t, state, true)
}
