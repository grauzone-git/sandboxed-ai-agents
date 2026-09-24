# Windows quick guide

Run host commands from this repository in PowerShell 7. You need Windows 11 x64,
Python 3.9+, OpenSSH client tools, and Podman 6+ with an already configured
rootless WSL2 machine. `pwsh` and `ssh-keygen` must be on PATH for SSH setup.
Building and installing agents require internet access and several GB of disk.

The launcher discovers Python through `py -3`, `python3`, then `python`;
`SANDBOX_PYTHON` can select an executable. Use the machine's default rootless
connection or set `$env:CONTAINER_CONNECTION = 'podman-machine-default'`.
Unset `CONTAINER_HOST`. CPU/memory limits require delegated cgroups v2 and do
not resize the WSL machine. Windows PowerShell 5.1, Hyper-V, and ARM64 are
outside the supported configuration.

## Build and create

```powershell
podman machine list
podman system connection list
podman info
.\sandbox.ps1 build
.\sandbox.ps1 agent01 up --agents copilot --ssh-config
```

The selected connection must match a running rootless WSL2 machine. If your
configured machine is stopped, start it with `podman machine start`. The
launcher does not install prerequisites or provision a machine.

The default workspace is a named volume mounted at `/workspace`. To bind a
Windows directory instead, use this creation command in place of the one above:

```powershell
.\sandbox.ps1 agent01 up '.\workspaces\agent01' --agents copilot --ssh-config
```

Quote paths containing spaces. UNC/network shares and controller/SSH directories
cannot be workspaces. `up` refuses an existing container. Use a different name
and port for another sandbox, for example `agent02 --ssh-port 2223`.

## Sign in and work

```powershell
.\sandbox.ps1 agent01 agents login copilot
.\sandbox.ps1 agent01 copilot
```

Detach from the persistent terminal with `Ctrl+B`, then `D`. These commands use
Podman exec; host SSH setup is optional. Credentials stay in the sandbox home
volume. For SSH access, run `ssh agent01`. In VS Code, connect
to `agent01` with Remote - SSH and open `/workspace`. Use the managed alias;
root login is disabled.

## Daily commands

| Task | PowerShell command |
|---|---|
| List sandboxes | `.\sandbox.ps1 list` |
| Stop / start | `.\sandbox.ps1 agent01 stop` / `.\sandbox.ps1 agent01 start` |
| Restart | `.\sandbox.ps1 agent01 restart` |
| Shell without host SSH setup | `.\sandbox.ps1 agent01 shell` |
| Add SSH to a running sandbox | `.\sandbox.ps1 agent01 ssh-config --install` |
| Check toolchain and mounts | `.\sandbox.ps1 agent01 check` |
| List agents / tools | `.\sandbox.ps1 agent01 agents list` / `.\sandbox.ps1 agent01 tools list` |
| Enable T3 | `.\sandbox.ps1 agent01 tools enable t3` |
| Check / restart T3 | `.\sandbox.ps1 agent01 service t3 status` / `.\sandbox.ps1 agent01 service t3 restart` |
| Forward T3 to localhost | `.\sandbox.ps1 agent01 forward t3` |
| Show pinned host-key fingerprint | `.\sandbox.ps1 agent01 fingerprint` |
| Rebuild and update | `.\sandbox.ps1 agent01 update` |
| Apply an already built image | `.\sandbox.ps1 agent01 update --no-build` |

Update preserves storage, SSH setup, selections, resource/port settings, and
running/stopped state, with rollback on replacement failure. Plain start/restart
do not change host SSH files; add `--ssh-config` to request setup.

SSH setup and removal merge the current host config under a shared lock. If an
operation reports a busy lock or a config edit during file preparation, retry
after the other writer finishes. An interrupted operation can leave
`~/.ssh/sanboxed-agents/.config.lock`; remove that empty directory only after
confirming no SSH setup or removal is running.

At creation, use `--agents 'codex,claude'` for multiple agents, `--tools t3` for
an optional tool, `--cpus 2 --memory 6g` for resource limits, or
`--capabilities podman` for nested containers.
See the [toolchain guide](TOOLCHAIN.md#nested-containers-with-podman) for capability
security settings and details.

## Manage agents and tools

Both `agents` and `tools` support `list` (the default), `check`, and
`set|enable|disable|update LIST`. Quote comma-separated lists in PowerShell:

```powershell
.\sandbox.ps1 agent01 agents enable 'codex,claude'
.\sandbox.ps1 agent01 agents update all
.\sandbox.ps1 agent01 tools set t3
.\sandbox.ps1 agent01 tools setup t3
.\sandbox.ps1 agent01 tools setup azdo
.\sandbox.ps1 agent01 tools login github
.\sandbox.ps1 agent01 run codex --help
.\sandbox.ps1 agent01 tool t3 --help
```

`update all` updates only enabled entries. `set none` disables the selection,
retaining cached installs and credentials. Login supports Codex, Claude,
OpenCode, Copilot, and Hermes; GitHub login, T3 setup, and Azure DevOps setup use `tools`. Login/setup
can prompt for authorization. Agent aliases and `t3` reconnect persistent
sessions; `run`/`tool` pass additional arguments to the selected executable.

Azure DevOps setup accepts `--persist` or `--clear` after `azdo`.
Azure CLI setup uses `./sandbox.ps1 agent01 tools setup azure`; add `--interactive`
for the native host browser, `--cloud AzureChinaCloud` for China, and explicit
`--tenant` plus `--subscription` or `--tenant-only` as needed. Device code is the
default. Browser setup requires opted-in managed SSH. See
[Azure setup](AZURE-SETUP.md) for lifecycle behavior and pending live validation.
See [Azure DevOps authentication](TOOLCHAIN.md#set-up-azure-devops) for the credential storage options.

Non-interactive output supports PowerShell assignment and pipelines, for example
`$listing = .\sandbox.ps1 agent01 agents list`. Native stderr is displayed as
plain text and remains redirectable with `2>` or `2>&1`. Installers also send
progress there; check `$LASTEXITCODE` for command success. Shells, sessions, `run`/`tool`, and
login/setup keep direct console input and output for interactive programs.

Service actions are `status`, `start`, `stop`, `restart`, and `logs`. Forwarding
starts the selected enabled service and requires existing managed SSH setup.
Leave its terminal running and open the printed localhost URL. An optional
port overrides the local port: `.\sandbox.ps1 agent01 forward t3 4773`.
An unavailable local port is rejected before starting the service or SSH tunnel.
Service/forward aliases `hermes` and `deepseek` select their dashboard tools.
These added host command routes have offline coverage; live Windows results
are tracked in [issue #16](https://github.com/grauzone-git/sandboxed-ai-agents/issues/16).

## Remove

```powershell
.\sandbox.ps1 agent01 remove
```

This removes the container and local managed SSH setup, retaining named volumes.
To permanently delete those volumes, including saved credentials and named
workspace files, use `.\sandbox.ps1 agent01 remove --volumes` instead.
Host-bound workspace directories are always retained.

If a command fails, inspect `$LASTEXITCODE` and `podman logs agent01`. Startup
failure can happen before SSH setup; after recovery, run
`.\sandbox.ps1 agent01 ssh-config --install`. Keep volumes while diagnosing.
See `.\sandbox.ps1 --help` and the [shared lifecycle guide](SANDBOXES.md).


## Offline tests

With Git, Python, PowerShell, and OpenSSH on PATH:

```powershell
$testPython = py -3 -c "import sys; print(sys.executable)"
.\tests\run-windows.ps1 -Python $testPython
```

Tests use temporary homes and simulated Podman responses, without touching
running sandboxes. Linux-only permission and image-normalization tests skip
on Windows. Windows Developer Mode may be needed for symlink tests.
The probe fixture execution test also skips if Node is absent on the host;
the live argument check uses Node already included in the container image.

For an optional live argument-transport check with an already built image, run
`py -3 -B tests/live-windows-arguments.py`. This creates a separate container
with no network, mounts, or published ports. Harmless probe executables verify
`run` and `tool` arguments through the real PowerShell, Podman, and container
manager paths. No providers or credentials are used. The container is removed
on success and retained for diagnosis on failure. This check is never run by
the offline suite.

User-run validation used Windows 11 Enterprise x64, PowerShell 7.6.6, Python
3.14.7, WSL 2.7.13.0, Podman client/engine 6.1.2, and OpenSSH 9.5p2.
[Build/create results](https://github.com/grauzone-git/sandboxed-ai-agents/issues/9)
and [lifecycle results](https://github.com/grauzone-git/sandboxed-ai-agents/issues/14)
record exact settings and limits. Live updates used `--no-build`; other update
failure modes and rebuild ordering also have offline coverage.
