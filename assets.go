// Package sandboxassets contains the build context shipped with the controller.
package sandboxassets

import (
	"crypto/sha256"
	"embed"
	"fmt"
	"io/fs"
)

// Files is read-only container source bundled at compile time.
//
//go:embed src/container
var Files embed.FS

// Hash identifies both the paths and contents of the bundled assets.
func Hash() string {
	hash := sha256.New()
	fs.WalkDir(Files, "src/container", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !entry.IsDir() {
			data, err := Files.ReadFile(path)
			if err != nil {
				return err
			}
			fmt.Fprintf(hash, "%s\x00%d\x00", path, len(data))
			hash.Write(data)
		}
		return nil
	})
	return fmt.Sprintf("%x", hash.Sum(nil))
}
