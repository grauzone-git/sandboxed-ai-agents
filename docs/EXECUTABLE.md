# Standalone executable preview

The Go controller is being implemented under
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36). This preview
implements `version`, `build`, `list`, sandbox lifecycle with optional workspace
binds, agent and tool management and sessions from #46, and sandbox image
updates from #45. At this snapshot, adoption, tools setup, services, and
forwarding remain upcoming executable commands under #47 and #48. Use the
existing scripts for them until those issues land.

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
[shared host contract](HOST-CONTRACT.md) also runs its list scenarios and a
management subset against it:

```sh
export SANDBOX_TEST_LAUNCHER="[\"$PWD/sandboxed-agents\"]"
SANDBOX_TEST_CONTRACT_SLICE=management node tests/test-host-commands.cjs
python3 -B tests/test-list.py
```

The management subset covers sessions, login, agent and tool management, `run`
and `tool`, rejected input, literal arguments, exit status and output, the exact
fake Podman calls, and unchanged host SSH and controller state. See the host
contract for details and the Windows form. It does not establish full parity
with the script contract; the remaining suites follow in #47, #48, and #50.

CI runs Go tests, the list contract, and the management subset on Linux and
Windows and cross-builds linux/amd64, linux/arm64, and windows/amd64. No live
Podman execution is implied by these offline tests. Supported targets are
linux/amd64, linux/arm64, and Windows 11 windows/amd64. macOS and BSD are not
targets, and generic Unix code or tests do not imply support for them.

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

## Agent and tool commands

Manage the selection saved inside a sandbox through its bundled manager:

```sh
sandboxed-agents agent01 agents list
sandboxed-agents agent01 agents check
sandboxed-agents agent01 agents set codex,claude
sandboxed-agents agent01 agents enable hermes
sandboxed-agents agent01 agents disable claude
sandboxed-agents agent01 agents update all
sandboxed-agents agent01 tools enable t3
sandboxed-agents agent01 tools update all
```

Both `agents` and `tools` accept `list`, `check`, `set`, `enable`, `disable`, and
`update`. Omit the operation to list. Selection names and version pins come from
the embedded catalogs; invalid selections fail before contacting Podman.
`update all` updates the enabled selection, without enabling other entries.
`set none` disables the selection. The manager enforces tool dependencies and
keeps cached credentials when an entry is disabled.

Log in through the sandbox's terminal:

```sh
sandboxed-agents agent01 agents login codex
sandboxed-agents agent01 tools login github
```

Managed agent login supports `codex`, `claude`, `copilot`, `opencode`, and
`hermes`. GitHub login uses the in-container GitHub CLI. Credentials stay in the
sandbox home volume. Follow the provider prompts; host SSH setup is not needed.

Run an enabled executable with arguments, or attach its persistent session:

```sh
sandboxed-agents agent01 run codex --version
sandboxed-agents agent01 tool t3 --help
sandboxed-agents agent01 codex
```

Session shortcuts are `copilot`, `claude`, `codex`, `hermes`, `opencode`,
`deepseek`, and `t3`; they accept no extra arguments. `t3` uses the tool manager,
while the other shortcuts use the agent manager. All commands enforce sandbox
ownership and use `podman exec` as the unprivileged agent in `/workspace`.
Interactive commands preserve stdin and allocate a TTY when both stdin and
stdout are terminals. Arguments and exit status pass through to the manager.

## Update sandbox images

```sh
sandboxed-agents agent01 update
sandboxed-agents update --all --no-build
```

Name one sandbox, or pass `--all` without a name to select every container
owned by `SANDBOX_CONTROLLER`. `--all` reports and skips retained update
backups: containers named `NAME-update-backup-…` that still mount the original
sandbox's home and SSH server volumes. A sandbox whose name only looks similar
is updated normally. Update snapshots and validates every selected sandbox
before rebuilding the bundled image without the build cache. Validation checks
ownership, state, the loopback SSH port mapping, and the expected mounts and
volume owners; a bound workspace directory must still exist. On Windows, a WSL
`/mnt/DRIVE/...` bind source is translated back to its Windows path and checked
by the same workspace protection. Binds under a custom WSL automount root are
rejected. `--no-build` uses the current `SANDBOX_IMAGE`. The controller freezes
its image ID and prepares any nested Podman images before stopping containers.
`--capabilities podman|none` overrides the saved capability; otherwise each
sandbox keeps its existing selection.

Sandboxes are replaced one at a time. Each is checked again before replacement,
stopped if running, and renamed to a backup. Its replacement uses the same
workspace volume or bind directory, home and SSH server volumes, port, CPU,
memory, process and shared-memory limits, and the selected capability. The
controller starts it, waits up to 15 seconds for its SSH server to become
ready, and boots the saved agent and tool selections. Output from failed
readiness checks is suppressed while retrying; if the wait times out, the error
reports the last check's failure. An interrupt ends the wait promptly, is
reported as an interruption rather than a timeout, and rolls back. A sandbox
that was stopped before the update is stopped again afterwards. SSH keys and
host configuration are retained.

A failure or interruption during replacement removes only the new container,
restores the original name, and restarts the original if it was running. Update
never calls the normal removal path or deletes volumes. If creation fails or is
interrupted before Podman reports a container ID, rollback looks up the
container now holding the sandbox name. It removes that container only if it is
owned by this configuration, has a valid container ID, and carries this update's
unique transaction label; removal then targets that confirmed ID, not the name.
If another container holds the name, the backup is retained and the error
identifies it. If backup cleanup fails after successful replacement, the healthy
replacement stays in place and the error identifies the stopped backup to
inspect. If rollback itself fails, the error identifies the original container
and backup; their volumes and SSH files remain untouched. With `--all`, the
first failure stops the run: sandboxes already replaced keep their update, and
the remaining ones are not changed.

`list` adds `(outdated)` to the state of a sandbox whose version label is older
than the executable. Unknown or missing version labels are not guessed. Listing
never updates a sandbox automatically.
