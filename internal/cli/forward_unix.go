//go:build !windows

package cli

import "net"

func requireForwardPort(address string) error {
	listener, err := net.Listen("tcp4", address)
	if err != nil {
		return err
	}
	return listener.Close()
}
