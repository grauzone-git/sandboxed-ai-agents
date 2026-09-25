# Standalone executable preview

The Go controller is being implemented under
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36). This preview
implements `version`, `build`, `list`, and sandbox lifecycle with optional workspace binds. Use the
existing scripts for agent/tool management and updates until their
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
rootless. OpenSSH is required for opt-in SSH setup and checking configured SSH access.
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
also runs its full checks. Without explicit SSH setup, lifecycle commands create no local SSH files.
They write nothing beside the executable.

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

## Windows runtime

Windows 11 x64 requires Podman 6.0+ on both the client and its existing rootless
WSL2 machine. Start and configure the machine yourself. The controller never
creates or starts a machine. `CONTAINER_CONNECTION` selects a configured local
machine connection; otherwise the default connection is used. `CONTAINER_HOST`,
remote engines, rootful engines, and Hyper-V machines are rejected. The selected
connection must match the running machine's SSH endpoint. Every engine command
uses that connection explicitly. The machine must delegate cgroups v2 CPU,
memory, and process limits.

Local workspace paths retain their Windows drive spelling when passed to
Podman. Junctions and short-name aliases resolve through Windows file handles
before protection checks; comparisons ignore case. UNC paths, device paths,
network drives, alternate data streams, and names ending with a dot or space
are rejected. Temporary build contexts and controller state receive a private
ACL for the current user.

For `--capabilities podman`, the controller checks the machine's devices and
subordinate UID/GID ranges, reads its seccomp policy, and preserves restrictions
unrelated to nested namespace setup. The versioned policy is stored locally,
then copied over machine SSH into a private, content-addressed guest file that
survives machine restarts. Its guest path becomes the seccomp argument. This
adds no host bind and does not edit machine configuration.

Windows CI runs the offline command tests on a current Windows Server runner.
Windows builds older than Windows 11's build 22000 and non-x64 hosts are rejected.
Live Windows 11 and WSL2 container validation remains a separate release gate.

## Opt in to SSH

```sh
sandboxed-agents agent01 up --agents codex --ssh-config
# Or add SSH access to an existing running sandbox:
sandboxed-agents agent01 ssh-config --install
ssh agent01
sandboxed-agents agent01 fingerprint
```

`start --ssh-config` and `restart --ssh-config` also configure SSH explicitly.
Without those flags, creation and startup leave `~/.ssh` untouched. Setup runs
OpenSSH `ssh-keygen`, installs only the public key in the container, and reads
the server host key directly through Podman. The generated configuration uses
that pinned key and disables agent and X11 forwarding. Private keys remain on
the host. Linux file modes and Windows protected ACLs restrict access to the user.

Keys and pinned hosts are stored in `<state>/ssh/NAME/`; the corresponding
configuration is `<state>/ssh/NAME.conf`. Setup adds one
`Include "<state>/ssh/*.conf"` line at the beginning of `~/.ssh/config` and
preserves unrelated content. Repeating setup keeps the existing private key.
`ssh-config` displays saved configuration, and an existing complete configuration
can be reinstalled with `--install` while Podman is offline. SSH-only ownership
metadata prevents another controller group or differently cased sandbox name
from reusing stale keys; it never authorizes container operations or adoption.

`check` runs the container smoke test through Podman and checks SSH only if a
private key exists. `remove` deletes this sandbox's managed SSH files after
successful container removal. It removes the shared Include only when no managed
configuration remains. Other sandbox keys, unrelated SSH settings, and unknown
files are retained. Unsafe symlinks or reparse points in managed paths block SSH
setup and removal before a container is stopped.
