package main

import (
	"os"

	"github.com/grauzone-git/sandboxed-ai-agents/internal/cli"
)

var version = "0.1.0-dev"
var commit = "unknown"

func main() {
	cli.Version, cli.Commit = version, commit
	os.Exit(cli.Run(os.Args[1:]))
}
