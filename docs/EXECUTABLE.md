# Standalone executable preview

The Go controller is being implemented under
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36). This preview
implements `version`, `build`, `list`, sandbox lifecycle with optional workspace
binds, sandbox image updates from #45, agent and tool management and sessions
from #46, and services, forwarding, and tool setup (T3, Azure DevOps, and Azure,
including host browser sign-in) from #47, and explicit adoption of
checkout-owned sandboxes from #48. No release has been published.

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
rootless. The OpenSSH client (`ssh`, `ssh-keygen`) is required for opt-in SSH
setup, checking configured SSH access, `service`, `forward`, and browser Azure
setup. Browser Azure setup also opens the host's default browser: `xdg-open` on
Linux, or `rundll32.exe` on Windows. Development tools installed inside images are listed in [TOOLCHAIN.md](TOOLCHAIN.md).

`version` reports the version, Git commit, and SHA256 of bundled asset paths and
contents. Development builds use `0.1.0-dev` and `unknown` unless supplied at
build time. `build` accepts additional Podman build arguments, uses a fresh
temporary context, labels the image with the version, and removes the context
after success or failure. `SANDBOX_IMAGE` selects the image tag.

`list` uses owner `default`. Set `SANDBOX_CONTROLLER` to choose another group.
It lists names, state, SSH ports, enabled agents, and workspace storage without
starting containers. Checkout-owned sandboxes stay separate: `list` names them
after the table with the `adopt` command for each, and changes nothing.
Installing or running this preview never adopts them; see
[Adopt checkout-owned sandboxes](#adopt-checkout-owned-sandboxes).

The command grammar is `sandboxed-agents NAME COMMAND [PARAMETERS]`.
Commands such as `build`, `list`, and `version` take no sandbox name; command
names are reserved. Unsupported commands fail with a preview limitation message.

Run `go test ./...` for the executable's offline public CLI tests. The
[shared host contract](HOST-CONTRACT.md) also runs its host command, list, SSH
opt-in, and workspace storage suites against it; that page has the Linux and
PowerShell recipes. CI runs the Go tests and all
four contract suites against the binary on Linux and Windows and cross-builds
linux/amd64, linux/arm64, and windows/amd64. These tests use fake Podman, SSH,
and browser processes only. They do not exercise real Podman, real SSH, or real
Azure sign-in; live validation and stable-release gates remain open under #50.

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
labels; volumes carry the owner label. `up` reuses existing volumes only when
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
Creation does not need to write state there yet. Windows path alias handling
is described under [Windows runtime](#windows-runtime); offline tests do not
establish live Windows bind support.

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
On Windows the policy is also staged in the Podman machine, as described under
[Windows runtime](#windows-runtime).

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
configuration remains. This cleanup always runs. `remove` accepts the legacy
`--ssh-config` flag, alone or with `--volumes`, for compatibility with existing
scripts, but ignores it. Other sandbox keys, unrelated SSH settings, and unknown
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

## Services and forwarding

```sh
sandboxed-agents agent01 ssh-config --install
sandboxed-agents agent01 service t3 status
sandboxed-agents agent01 service tokentracker start
sandboxed-agents agent01 forward t3
sandboxed-agents agent01 forward t3 13773
```

`service ID [status|start|stop|restart|logs]` runs the in-container service
manager over SSH; the operation defaults to `status` and the remote exit status
is returned. `forward ID [LOCAL_PORT]` starts the service, then keeps an SSH
tunnel open in the foreground from `127.0.0.1:LOCAL_PORT` to the service's
loopback port in the sandbox. Stop the command to close the tunnel.

| ID | Alias | Default local port |
| --- | --- | --- |
| `t3` | | 3773 |
| `hermes-dashboard` | `hermes` | 9119 |
| `deepseek-ui` | `deepseek` | 3080 |
| `tokentracker` | | 7680 |

TokenTracker starts through the same service manager as the other tools; enable
it first with `tools enable tokentracker`.

Both commands require managed SSH setup from `up --ssh-config`,
`start --ssh-config`, `restart --ssh-config`, or `ssh-config --install`. When it
is missing, they fail with the `ssh-config --install` command to run, before
contacting Podman. They use the pinned host entry with batch mode, strict host
key checking, and agent and X11 forwarding disabled, and check sandbox ownership
through Podman first. `forward` rejects a local port outside 1024 to 65535
before anything else, and a local port that is already in use before starting
the service or tunnel.

## Tool setup

```sh
sandboxed-agents agent01 tools setup t3
sandboxed-agents agent01 tools setup azdo
sandboxed-agents agent01 tools setup azdo --persist
sandboxed-agents agent01 tools setup azdo --clear
```

T3 Connect and Azure DevOps setup run the in-container setup through
`podman exec` as the unprivileged agent in `/workspace` and need no host SSH
setup. `azdo --persist` saves the PAT environment selection and `--clear`
removes it. The container-side behavior is described in
[AGENT-SETUP.md](AGENT-SETUP.md) and
[TOOLCHAIN.md](TOOLCHAIN.md#set-up-azure-devops). The old `azdo --pat-env`
form is rejected with a pointer to `tools setup azdo --persist`.

### Azure setup

```sh
sandboxed-agents agent01 tools setup azure --tenant TENANT --subscription SUBSCRIPTION
sandboxed-agents agent01 tools setup azure --tenant TENANT --tenant-only
sandboxed-agents agent01 tools setup azure --interactive --tenant TENANT --subscription SUBSCRIPTION
sandboxed-agents agent01 tools setup azure --interactive --cloud AzureChinaCloud --tenant TENANT --subscription SUBSCRIPTION
```

Options are `[--interactive] [--cloud AzureCloud|AzureChinaCloud]
[--tenant TENANT] [--subscription SUBSCRIPTION | --tenant-only]`, each at most
once. Other clouds, empty values, tenant values that are not a tenant ID or
domain name, and `--subscription` together with `--tenant-only` are rejected
before Podman is contacted.

Without `--interactive`, setup runs in the container through `podman exec` and
uses Azure CLI device-code sign-in. No host SSH setup or host browser is
involved.

With `--interactive`, the executable drives a host browser sign-in:

1. It requires managed SSH setup and reports the `ssh-config --install` command
   when it is missing.
2. It starts the sandbox's Azure setup program over the pinned SSH connection.
   A missing tenant or subscription is prompted for on the host terminal.
3. When the sandbox reports the authorization request, the executable checks
   that its authority belongs to the selected cloud and that the redirect is a
   loopback address with a port from 1024 to 65535. It rejects the request if
   that local port is in use, opens an SSH forward for it, and confirms the
   forward reaches the sandbox before continuing.
4. It starts a short-lived redirect server on a random `127.0.0.1` port with a
   random path, and opens that local URL with `xdg-open` on Linux or
   `rundll32.exe url.dll,FileProtocolHandler` on Windows. The authorization URL
   itself is never passed as a browser process argument.
5. After sign-in, the sandbox commits the new session and the command reports
   completion. The redirect server and tunnel close on success, failure,
   cancellation, or the ten-minute timeout.

A failure or cancellation before the commit point keeps the previous sandbox
session. If the command is interrupted, times out, or fails after the sandbox
committed the new sign-in, it warns that the sandbox may already use the new
session and to check with `az account show` inside the sandbox before retrying.
Interruption exits with status 130. The container-side session handling is
described in [AZURE-SETUP.md](AZURE-SETUP.md).

The executable's Azure flow has been exercised only against fake processes and a
scripted protocol. Real sign-in through the executable on Linux or Windows is
unverified and remains part of the #50 validation.

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
memory, process and shared-memory limits, and the selected capability, and
keeps an adopted sandbox's `io.sandboxed-agents.adopted-from` label. The
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

## Adopt checkout-owned sandboxes

Sandboxes created by the checkout scripts are owned by the checkout's absolute
path in `io.sandboxed-agents.project`. The executable manages them only after
explicit adoption:

```sh
sandboxed-agents list
sandboxed-agents agent01 adopt --from /home/me/sandboxed-ai-agents
sandboxed-agents adopt --all --from /home/me/sandboxed-ai-agents
```

`list` is read-only. After its table it prints one line per checkout-owned
sandbox with the quoted `adopt` command to run. `--from` must be the exact
absolute owner path and must differ from the selected controller group. A named
sandbox whose owner label is not that path is refused. `--all` selects only
containers whose owner label equals that path; sandboxes of other checkouts or
controller groups are never included. Nothing is adopted implicitly.

Adoption uses the update path. Every selected sandbox is validated first
(state, port mapping, mounts, and volume owners), along with any legacy SSH
state, then the bundled image is rebuilt without the build cache. Each
container is recreated with the selected controller as owner and
`io.sandboxed-agents.adopted-from=PATH`, keeping the same workspace volume or
bind, home and SSH server volumes, port, limits, and capability. Until the
replacement commits, which includes any [SSH migration](#ssh-migration),
failures and interruptions roll back to the original container as described in
[Update sandbox images](#update-sandbox-images). Once it commits, the adoption
is kept: if removing the stopped backup then fails or is interrupted, the
adopted container stays in place and usable, and the error names the retained
backup to inspect. With `--all`, the first failure stops the run; sandboxes
already adopted stay adopted.

Volumes and their data are not modified, and their owner label keeps the
checkout path. The current container's `adopted-from` label is what makes them
count as owned: `update` and `remove --volumes` accept a volume labelled with
either the controller group or the `adopted-from` path of the container that
mounts it. `up` without an existing container has no such label to consult, so
after a plain `remove` it refuses the checkout-labelled volumes.

After adoption the checkout scripts' ownership check rejects the sandbox. Manage
it with the executable only.

### SSH migration

If `~/.ssh/sanboxed-agents/NAME/` holds the scripts' SSH files for the sandbox,
adoption moves them to the executable's state directory as part of the same
transaction. The key pair and the pinned host key are kept, so existing SSH
clients keep working without a new host key prompt. The scripts' `Include` line
for that sandbox is removed from `~/.ssh/config` and the executable's
`Include "<state>/ssh/*.conf"` line is added; other lines are kept.

Nothing is moved until the replacement container is ready and has been checked:
it must present the pinned host key and authorize the migrated client key,
otherwise adoption rolls back. The move is the last step before the backup is
removed, and rewriting `~/.ssh/config` commits it. If the move fails or is
interrupted before that, the legacy files are restored, the new copies are
removed, and the original container is rolled back. A file that changed in the
meantime is kept and named in the error, and if a legacy file cannot be
restored the new copies are kept too. Once `~/.ssh/config` is rewritten,
the SSH move and the adoption are not rolled back.

Adoption refuses, before anything is stopped, legacy SSH state that is:

- incomplete, or uses symlinks or non-regular files;
- recorded in `owner.json` for another checkout or sandbox name;
- pinned to anything other than one Ed25519 host key for `127.0.0.1` on the
  sandbox's port, or holds an invalid key;
- a key pair whose public key does not match the private key;
- in conflict with executable SSH files that already exist for the sandbox.

It also refuses to finish if the legacy files change during adoption.

When the legacy directory is absent or empty, adoption does not opt in to SSH:
it writes no SSH files and leaves `~/.ssh` untouched. Run
`sandboxed-agents agent01 ssh-config --install` afterwards to opt in.

Adoption has been exercised only with fake Podman and SSH processes. Adoption
of real Linux or Windows sandboxes, including their SSH access, is unverified
and remains part of the #50 validation.
