# Standalone executable preview

The Go controller is being implemented under
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36). This preview
implements `version`, `build`, and `list`. Use the existing scripts for lifecycle,
SSH, agent/tool management, and updates until their executable issues land.

Build with Go 1.23 or later:

```sh
go build -o sandboxed-agents ./cmd/sandboxed-agents
./sandboxed-agents version
./sandboxed-agents build
./sandboxed-agents list
```

On Windows, build `sandboxed-agents.exe`. The binary bundles the container
build context and can run from an unrelated directory without the checkout,
Go, Python, Bash, or PowerShell. Podman must already be installed and configured
rootless. The full executable will also require OpenSSH for opt-in SSH setup.
Development tools installed inside images are listed in [TOOLCHAIN.md](TOOLCHAIN.md).

`version` reports the version, Git commit, and SHA256 of bundled asset paths and
contents. Development builds use `0.1.0-dev` and `unknown` unless supplied at
build time. `build` accepts additional Podman build arguments, uses a fresh
temporary context, labels the image with the version, and removes the context
after success or failure. `SANDBOX_IMAGE` selects the image tag.

`list` uses owner `default`. Set `SANDBOX_CONTROLLER` to choose another group.
It lists names, state, SSH ports, enabled agents, and workspace storage without
starting containers. Checkout-owned sandboxes remain separate until explicit
adoption is implemented. Installing or running this preview never adopts them.

The future command grammar is `sandboxed-agents NAME COMMAND [PARAMETERS]`.
Commands such as `build`, `list`, and `version` take no sandbox name; command
names are reserved. Unsupported commands fail with a preview limitation message.

Run `go test ./...` for the executable's offline public CLI tests. The
[shared host contract](HOST-CONTRACT.md) also runs list scenarios against it.
CI runs Go tests and list contracts on Linux and Windows and cross-builds
linux/amd64, linux/arm64, and windows/amd64. No live Podman execution is implied
by these offline tests.
