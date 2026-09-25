# Standalone executable preview

The Go controller is being implemented under
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36). This preview
implements `version`, `build`, `list`, and sandbox lifecycle with optional workspace binds. Use the
existing scripts for SSH setup, agent/tool management, and updates until their
executable issues land.

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

The command grammar is `sandboxed-agents NAME COMMAND [PARAMETERS]`.
Commands such as `build`, `list`, and `version` take no sandbox name; command
names are reserved. Unsupported commands fail with a preview limitation message.

Run `go test ./...` for the executable's offline public CLI tests. The
[shared host contract](HOST-CONTRACT.md) also runs list scenarios against it.
CI runs Go tests and list contracts on Linux and Windows and cross-builds
linux/amd64, linux/arm64, and windows/amd64. No live Podman execution is implied
by these offline tests.

## Create and manage a sandbox

```sh
sandboxed-agents agent01 up --agents codex --cpus 4 --memory 8g
sandboxed-agents agent01 shell
sandboxed-agents agent01 check
sandboxed-agents agent01 stop
sandboxed-agents agent01 start
sandboxed-agents agent01 restart
sandboxed-agents agent01 remove
```

`up` requires an explicit, nonempty `--agents` selection. Agent and tool names,
version pins, and tool dependencies use the bundled catalogs. Pass `--tools LIST`
to replace retained tool selections; omitting it preserves the saved selection.
`--ssh-port` defaults to `2222`. CPU and memory defaults are `4` and `8g`, with
`SANDBOX_CPUS` and `SANDBOX_MEMORY` overrides. Both platforms accept these flags.

By default, workspace, home, and SSH server state use `agent01-workspace`, `agent01-home`,
and `agent01-sshd` named volumes. Containers carry owner and executable version
labels; volumes carry the owner label. Existing volumes are reused only when
owned by the selected controller. Lifecycle commands reject foreign containers,
and `remove --volumes` validates every volume before stopping the container.

Plain `remove` retains all volumes and their data. `remove --volumes` deletes the
sandbox's named volumes, including credentials and workspace files. It never
forces volume deletion. `shell` and `check` use `podman exec` without SSH setup.
`check` validates mounts before running the in-container smoke test; `check-full`
also runs its full checks. These lifecycle commands create no local SSH files or
other host state and write nothing beside the executable.

## Bind a workspace

```sh
sandboxed-agents agent01 up ./workspaces/agent01 --agents codex --ssh-port 2222
```

An explicit directory replaces only the workspace volume. A missing directory
is created after validation; home and SSH server state remain named volumes.
Paths containing spaces work when quoted. Removal never deletes the host
workspace. The positional port form `NAME up WORKSPACE PORT` also works.

Workspace protection rejects binds containing, or contained by, the controller
state directory, `~/.ssh`, active temporary build directories, or the executable.
It resolves symlinks, including existing parents of new paths. A project-local
executable or launcher symlink produces an error suggesting a global install.
State belongs under `$XDG_STATE_HOME/sandboxed-agents`, defaulting to
`~/.local/state/sandboxed-agents`, or `%LOCALAPPDATA%\\sandboxed-agents` on Windows.
Creation does not need to write state there yet. Windows machine/path alias
validation remains part of #43; offline tests do not establish live Windows
bind support.

## Nested Podman

On Linux, add `--capabilities podman` to `up` to install the nested Podman image
layer and permit inner rootless containers. The default is `none`. The derived
image uses an immutable base image ID and the bundled capability recipe.

The capability permits `/dev/fuse`, `/dev/net/tun`, mapping helpers, and the
namespace operations described in [ARCHITECTURE.md](ARCHITECTURE.md). It removes
`no-new-privileges`, disables SELinux/AppArmor separation, and unmasks kernel
paths. It does not enable privileged mode or mount host sockets. Runtime state
uses a tmpfs at `/run/user/1000`; persistent inner storage remains in the home
volume.

The generated seccomp policy retains the engine's deny-by-default host rules
except for the required hostname and namespace calls. It is saved under
`<state>/seccomp/<version>/nested-podman.json`, with a private directory and file,
and replaced when its content differs. Nothing is written beside the executable.
Windows guest profile staging remains part of #43.
