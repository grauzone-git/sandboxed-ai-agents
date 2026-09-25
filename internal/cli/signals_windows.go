package cli

import (
	"os"
	"syscall"
)

func azureSignals() []os.Signal { return []os.Signal{os.Interrupt, syscall.SIGTERM} }
