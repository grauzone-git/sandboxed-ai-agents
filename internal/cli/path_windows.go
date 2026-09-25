package cli

import (
	"fmt"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows"
)

func validateLocalWorkspace(path string) error {
	if strings.HasPrefix(path, `\\`) || strings.HasPrefix(path, "//") || strings.ContainsRune(path, 0) {
		return fmt.Errorf("use a local drive directory; UNC and device paths are unsupported")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	drive := filepath.VolumeName(absolute)
	if len(drive) != 2 || drive[1] != ':' || strings.Contains(absolute[2:], ":") {
		return fmt.Errorf("use a local drive directory without alternate data streams")
	}
	for _, part := range strings.FieldsFunc(absolute[2:], func(r rune) bool { return r == '/' || r == '\\' }) {
		if part != "." && part != ".." && (strings.HasSuffix(part, ".") || strings.HasSuffix(part, " ")) {
			return fmt.Errorf("workspace path components must not end with a dot or space")
		}
	}
	root, err := windows.UTF16PtrFromString(drive + `\`)
	if err != nil {
		return err
	}
	kind := windows.GetDriveType(root)
	if kind != windows.DRIVE_FIXED && kind != windows.DRIVE_REMOVABLE {
		return fmt.Errorf("use a local drive directory; network drives are unsupported")
	}
	return nil
}

func resolveExistingPath(path string) (string, error) {
	value, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return "", err
	}
	handle, err := windows.CreateFile(value, 0, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE, nil, windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return "", err
	}
	defer windows.CloseHandle(handle)
	buffer := make([]uint16, 512)
	for {
		count, err := windows.GetFinalPathNameByHandle(handle, &buffer[0], uint32(len(buffer)), 0)
		if err != nil {
			return "", err
		}
		if count >= uint32(len(buffer)) {
			buffer = make([]uint16, count+1)
			continue
		}
		resolved := windows.UTF16ToString(buffer[:count])
		if strings.HasPrefix(strings.ToUpper(resolved), `\\?\UNC\`) {
			return "", fmt.Errorf("network paths are unsupported")
		}
		resolved = strings.TrimPrefix(resolved, `\\?\`)
		if err := validateLocalWorkspace(resolved); err != nil {
			return "", err
		}
		return filepath.Clean(resolved), nil
	}
}
