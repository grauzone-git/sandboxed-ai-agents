# Sandbox lifecycle and SSH

For PowerShell command examples, see the [Windows quick guide](QUICKGUIDE-WINDOWS.md).
The storage, SSH, update, and removal behavior described here applies to both
platforms; Linux examples use `./sandbox`.

[Back to the overview](../README.md) · [Agents and tools](AGENT-SETUP.md)

Everything here uses `agent01`, its workspace volume `agent01-workspace`, and
SSH port `2222`. Run the commands on the host from this repository unless a
section says otherwise.

## Create and start

Once you have [built the image](TOOLCHAIN.md#image-options), create a sandbox:

```bash
./sandbox agent01 up --agents codex --ssh-config
```

`up` only ever creates a new container, and it refuses to touch an existing one.

Add `--capabilities podman` to install and enable nested Podman for building and
testing containers. Capabilities default to `none`; see
[nested containers](TOOLCHAIN.md#nested-containers-with-podman) for setup and use.
It also insists on a nonempty `--agents` selection, so you always know what
landed inside. To reuse a container you already have:

```bash
./sandbox agent01 stop
./sandbox agent01 start
```

Stopping kills every running process, tmux sessions included. Starting brings
back the saved agent and tool selections along with any enabled tool services.
Files in the named volumes and the workspace survive both.

## Choose workspace storage

Leave the workspace directory out and the launcher creates or reuses the named
volume `agent01-workspace` at `/workspace`. Nothing appears in a local
`workspaces/agent01` folder, and no host directory is bound into the container
at all. Open `/workspace` through VS Code Remote SSH, or clone a project into it
from `./sandbox agent01 shell`.

To bind a local project instead, name its directory when you create the sandbox:

```bash
./sandbox agent01 up ./workspaces/agent01 2222 --agents codex --ssh-config
```

This replaces the named-volume command above rather than following it. Both
modes mount at `/workspace` and use the same agent, SSH, and tool commands. An
empty string is rejected: leave the argument out entirely to get a volume.

For a custom SSH port without a directory, use `--ssh-port`:

```bash
./sandbox agent02 up --ssh-port 2223 --agents claude --ssh-config
```

`--ssh-port` works with a directory too, but pick either that flag or the
positional port, never both. An existing container keeps its workspace mode when
you `start` it; recreating a container adopts whatever mode its new `up` command
specifies. Switching modes does not copy files between the old directory and the
named volume, so move them yourself.

## SSH setup

Host-side SSH setup is opt-in. `up --ssh-config` and `start --ssh-config` write
the connection files and install the Include; plain `up` and `start` leave your
local SSH files alone. To add SSH to a sandbox that is already running:

```bash
./sandbox agent01 ssh-config --install
./sandbox agent01 ssh-config
./sandbox agent01 shell
```

The second command prints the generated configuration. Each sandbox keeps its
files in its own folder, which is spelled `sanboxed-agents`:

```text
~/.ssh/sanboxed-agents/agent01/
  agent01.conf       SSH host alias, loopback port, and dedicated key paths
  id_ed25519         Client private key
  id_ed25519.pub     Client public key
  known_hosts        Pinned container host key
```

The installer creates `~/.ssh/config` if you do not have one and prepends an
absolute Include pointing at `agent01.conf`. It keeps your other settings, skips
duplicate entries, and writes a `Host *` line after the Include so the defaults
that follow keep their original scope. These files stay on the host and are
never mounted into the container.

Once the Include is in place, plain `ssh agent01` works. The launcher's own SSH
commands read the dedicated config directly. Agent and tool management, `run`,
`tool`, and managed login all go through Podman, so they work without SSH; the
persistent terminal shortcuts, `shell`, `forward`, `check`, and VS Code need it.

Displaying or installing an existing config works even when Podman is not
running, but generating missing files needs a running sandbox. To refresh keys
and config, including for a container that is already up:

```bash
./sandbox agent01 start --ssh-config
```

## VS Code

1. Install Remote - SSH locally and finish the SSH setup above.
2. Run **Remote-SSH: Connect to Host…** and choose `agent01`.
3. Open `/workspace`, then install your workspace extensions in the remote
   environment when prompted.

The VS Code server and its extensions live in the named home volume, so they
survive restarts and recreation. If you would rather not touch `~/.ssh/config`,
point VS Code's `remote.SSH.configFile` at the absolute path of `agent01.conf`
instead. [VS Code Remote SSH documentation](https://code.visualstudio.com/docs/remote/ssh).

When the remote window refuses to open, test the connection on its own first:

```bash
./sandbox agent01 start --ssh-config
./sandbox agent01 shell
ssh -v agent01
```

If SSH itself is fine, the problem is on the VS Code side: check its
Remote - SSH output channel for the server or extension error. A server
installation that finished is not proof that the editor connected.

## Forward ports

For a managed tool, enable it and use the forwarding helper:

```bash
./sandbox agent01 tools enable tokentracker
./sandbox agent01 forward tokentracker
```

Open <http://127.0.0.1:7680> and keep that terminal open. The helper starts the
service if it is not running and waits for it to answer before opening the
tunnel. Closing the tunnel leaves the service up. Ports for the other tools are
in the [tool list](AGENT-SETUP.md#optional-tools).

Running the same tool in two sandboxes means picking a different local port for
the second one, after you have [created it](../README.md#add-agents-and-tools):

```bash
./sandbox agent02 tools enable tokentracker
./sandbox agent02 forward tokentracker 7681
```

For your own application on container port `3000`, plain SSH is enough:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:3000:127.0.0.1:3000 agent01
```

Start the application inside the sandbox first, because a tunnel will not start
it for you. If a managed tool reports "connection refused", check
`./sandbox agent01 service tokentracker status` and
`./sandbox agent01 service tokentracker logs`, then run the helper again. A
local port that is already taken needs a different `LOCAL_PORT`.

## Storage

| Storage | Container path | Contents |
|---|---|---|
| Named volume `agent01-workspace` (default) | `/workspace` | Project files, dependencies, and build output |
| Host bind `./workspaces/agent01` (alternative) | `/workspace` | The local project directory you supplied |
| Named volume `agent01-home` | `/home/agent` | Agent and tool installs, credentials, history, caches, VS Code server and extensions |
| Named volume `agent01-sshd` | `/var/lib/agent-sshd` | SSH server keys and the authorized client public key |

Pick one of the two workspace rows: every sandbox has exactly three explicit
mounts. Only `/workspace` is allowed to be a host bind. The launcher rejects
binds that would expose its own controller files, Git metadata, or SSH state,
symlinks included, so use `workspaces/agent01` or a separate project directory
and never the controller checkout or a parent of it.

Give separate sandboxes separate clones. Sharing one workspace hands both agents
the same files. Git worktrees whose metadata lives outside the mounted directory
will not work unless the main repository and all its worktrees fit under the
workspace root you chose.

## Upgrade the image

Rebuild the image and recreate a sandbox in one step:

```bash
./sandbox agent01 update
```

Update one sandbox per command, or every sandbox owned by this controller
checkout:

```bash
./sandbox update --all
```

The command validates all the selected sandboxes first, then builds the
configured image once with `--pull=always --no-cache`. A failed build leaves
your existing sandboxes running and untouched. After a successful build, the
sandboxes are recreated one at a time from the same image ID. Running sandboxes
come back running; stopped ones are started briefly for validation and then
stopped again.

Each sandbox keeps its workspace mode and path, named volumes, SSH port, memory,
CPU, process, and shared-memory limits. Saved selections, version pins,
credentials, project files, and SSH keys all survive, and enabled services are
restored. Existing local SSH files and Includes stay as they are; update will
not create SSH setup where there was none. Keep the controller checkout at the
same path, because resource ownership is tracked by that path.

**Save your work before updating.** Recreating a container ends terminal and
tmux sessions and drops SSH tunnels. Anything written outside the workspace and
named volumes goes away with the old container's writable layer. Reconnect VS
Code and reopen your tunnels afterward.

For custom build options, build explicitly and tell update to skip its own
rebuild:

```bash
./sandbox build --build-arg WITH_EDGE=0
./sandbox update --all --no-build
```

Both commands read `SANDBOX_IMAGE`, which defaults to
`localhost/agent-sandbox:dev`. Automatic rebuilds use the Containerfile defaults
and do not remember the build arguments you used last time. Updates restore the
agents and tools you had installed without upgrading their packages; use
`./sandbox agent01 agents update all` and `./sandbox agent01 tools update all`
for that.

While a container is being replaced, the stopped original is kept under a
temporary `NAME-update-backup-…` name. If creation, SSH readiness, or service
restoration fails, update tries to bring the old container back along with its
previous running state, and only deletes the backup once validation succeeds. If
the rollback or the backup cleanup itself fails, the error tells you which
container was retained so you can recover by hand. Rollback cannot undo writes
to shared volumes. `update --all` stops at the first failure, and
whatever already completed stays applied.

## Remove a sandbox

Two options, depending on how much you want gone:

| Command | Deletes |
|---|---|
| `./sandbox agent01 remove` | The container and the generated local SSH files and Include |
| `./sandbox agent01 remove --volumes` | All of the above plus the named home, SSH, and workspace volumes if they exist |

Host workspace directories and the image are always kept, and plain removal also
keeps every named volume. `--volumes` deletes the named workspace volume too,
project files and unpushed work included. That covers an owned
`agent01-workspace` volume left over from an earlier container even when the
current one uses a bind. Deleting the home volume takes credentials, history,
installations, and caches with it. Either way, files that existed only in the
container's writable layer are gone.

Both commands remove the sandbox's generated local SSH config, client keys,
pinned host fingerprints, and Include entries. The old `--ssh-config` removal
flag still works for compatibility but no longer does anything you need.

Removal also deduplicates settings throughout `~/.ssh/config` within each
Host/Match scope, collapses repeated Host headers without an intervening scope
change, and removes consecutive duplicate Includes. Comments, distinct settings,
and settings for different hosts are preserved. Includes and Match blocks remain
scope boundaries; order-dependent settings such as SendEnv removals retain their
meaning. Existing duplicate entries are cleaned even if the removed sandbox has
no Include left in the file.

Ownership and cleanup paths are checked before the container is stopped. SSH
cleanup runs as soon as container removal succeeds, before any volume deletion,
and leaves unrelated SSH files and settings alone. If the stop or the container
removal fails, your SSH access stays intact; if a later volume deletion fails,
you are not left with obsolete SSH files. Volumes still in use elsewhere are
never force-deleted. Failures are reported, and removals that already completed
are not rolled back.

After deleting volumes, recreate the sandbox with `--ssh-config` to refresh the
pinned server key, then sign into your agents again. Back up anything you need
from the workspace and volumes first, and remember that home backups contain
credentials.


## Windows-specific behavior

PowerShell shell/check commands use Podman exec and do not require host SSH.
Managed SSH files use current-user-only ACLs, reject reparse points, and require
UTF-8 SSH configuration (with or without a BOM). Repeated setup retains keys.
Removal deletes only the exact generated Include and owned managed files;
unrelated configuration is retained.

An update cannot undo application writes to shared volumes. Each selected
sandbox is a separate transaction, so earlier successful updates remain if a
later one fails. If backup removal fails, the healthy replacement remains and
the stopped backup is reported. If volume deletion fails during removal, the
container and obsolete SSH setup may already be gone; retained volumes can be
recovered explicitly with Podman.
