package main

import (
	"path/filepath"
	"testing"
	"unsafe"

	"golang.org/x/sys/windows"
)

func TestSSHPrivateFilesHaveOwnerOnlyWindowsACL(t *testing.T) {
	command, _, root := sshCommand(t, "agent01", "ssh-config", "--install")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, output)
	}
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(root, "state", "sandboxed-agents", "ssh")
	for _, path := range []string{filepath.Join(state, "agent01", "id_ed25519"), filepath.Join(state, "agent01", "known_hosts"), filepath.Join(state, "agent01.conf"), filepath.Join(root, "home", ".ssh", "config")} {
		descriptor, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
		if err != nil {
			t.Fatal(err)
		}
		acl, _, err := descriptor.DACL()
		if err != nil {
			t.Fatal(err)
		}
		control, _, err := descriptor.Control()
		if err != nil {
			t.Fatal(err)
		}
		if acl == nil || acl.AceCount != 1 || control&windows.SE_DACL_PROTECTED == 0 {
			t.Fatalf("not owner-only: %s %s", path, descriptor.String())
		}
		var ace *windows.ACCESS_ALLOWED_ACE
		if err := windows.GetAce(acl, 0, &ace); err != nil {
			t.Fatal(err)
		}
		if ace.Header.AceType != windows.ACCESS_ALLOWED_ACE_TYPE {
			t.Fatalf("not an allow ACE: %s", descriptor.String())
		}
		// SidStart marks the variable-length SID following the ACE header.
		sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
		if !sid.Equals(user.User.Sid) {
			t.Fatalf("ACL grants another identity: %s", descriptor.String())
		}
	}
}
