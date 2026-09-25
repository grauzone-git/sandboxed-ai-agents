package cli

import (
	"net"
	"strconv"

	"golang.org/x/sys/windows"
)

func requireForwardPort(address string) error {
	_, value, err := net.SplitHostPort(address)
	if err != nil {
		return err
	}
	port, err := strconv.Atoi(value)
	if err != nil {
		return err
	}
	var winsock windows.WSAData
	if err := windows.WSAStartup(0x202, &winsock); err != nil {
		return err
	}
	defer windows.WSACleanup()
	socket, err := windows.Socket(windows.AF_INET, windows.SOCK_STREAM, windows.IPPROTO_TCP)
	if err != nil {
		return err
	}
	defer windows.Closesocket(socket)
	// Winsock defines SO_EXCLUSIVEADDRUSE as the complement of SO_REUSEADDR.
	if err := windows.SetsockoptInt(socket, windows.SOL_SOCKET, ^windows.SO_REUSEADDR, 1); err != nil {
		return err
	}
	// Windows permits same-user wildcard and loopback listeners to coexist.
	// The exclusive wildcard bind detects either without opening a listener.
	return windows.Bind(socket, &windows.SockaddrInet4{Port: port})
}
