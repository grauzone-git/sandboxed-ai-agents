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
.\sandbox.ps1 up agent01 --agents copilot --ssh-config
```

The selected connection must match a running rootless WSL2 machine. If your
configured machine is stopped, start it with `podman machine start`. The
launcher does not install prerequisites or provision a machine.

The default workspace is a named volume mounted at `/workspace`. To bind a
Windows directory instead, use this creation command in place of the one above:

```powershell
.\sandbox.ps1 up agent01 '.\workspaces\agent01' --agents copilot --ssh-config
```

Quote paths containing spaces. UNC/network shares and controller/SSH directories
cannot be workspaces. `up` refuses an existing container. Use a different name
and port for another sandbox, for example `agent02 --ssh-port 2223`.

## Sign in and work

```powershell
.\sandbox.ps1 agents agent01 login copilot
.\sandbox.ps1 copilot agent01
```

Detach from the persistent terminal with `Ctrl+B`, then `D`. These commands use
Podman exec; host SSH setup is optional. Credentials stay in the sandbox home
volume. For SSH access, run `ssh agent01`. In VS Code, connect
to `agent01` with Remote - SSH and open `/workspace`. Use the managed alias;
root login is disabled.

## Daily commands

| Task | PowerShell command |
|---|---|
| Stop / start | `.\sandbox.ps1 stop agent01` / `.\sandbox.ps1 start agent01` |
| Restart | `.\sandbox.ps1 restart agent01` |
| Shell without host SSH setup | `.\sandbox.ps1 shell agent01` |
| Add SSH to a running sandbox | `.\sandbox.ps1 ssh-config agent01 --install` |
| Check toolchain and mounts | `.\sandbox.ps1 check agent01` |
| List agents / tools | `.\sandbox.ps1 agents agent01 list` / `.\sandbox.ps1 tools agent01 list` |
| Enable T3 | `.\sandbox.ps1 tools agent01 enable t3` |
| Check / restart T3 | `.\sandbox.ps1 service agent01 t3 status` / `.\sandbox.ps1 service agent01 t3 restart` |
| Forward T3 to localhost | `.\sandbox.ps1 forward agent01 t3` |
| Show pinned host-key fingerprint | `.\sandbox.ps1 fingerprint agent01` |
| Rebuild and update | `.\sandbox.ps1 update agent01` |
| Apply an already built image | `.\sandbox.ps1 update agent01 --no-build` |

Update preserves storage, SSH setup, selections, resource/port settings, and
running/stopped state, with rollback on replacement failure. Plain start/restart
do not change host SSH files; add `--ssh-config` to request setup.

At creation, use `--agents 'codex,claude'` for multiple agents, `--tools t3` for
an optional tool, `--cpus 2 --memory 6g` for resource limits, or
`--capabilities podman` for nested containers.
See the [toolchain guide](TOOLCHAIN.md#nested-containers-with-podman) for capability
security settings and details.

## Manage agents and tools

Both `agents` and `tools` support `list` (the default), `check`, and
`set|enable|disable|update LIST`. Quote comma-separated lists in PowerShell:

```powershell
.\sandbox.ps1 agents agent01 enable 'codex,claude'
.\sandbox.ps1 agents agent01 update all
.\sandbox.ps1 tools agent01 set t3
.\sandbox.ps1 tools agent01 setup t3
.\sandbox.ps1 tools agent01 login github
.\sandbox.ps1 run agent01 codex --help
.\sandbox.ps1 tool agent01 t3 --help
```

`update all` updates only enabled entries. `set none` disables the selection,
retaining cached installs and credentials. Login supports Codex, Claude,
OpenCode, Copilot, and Hermes; GitHub login and T3 setup use `tools`. Login/setup
can prompt for authorization. Agent aliases and `t3` reconnect persistent
sessions; `run`/`tool` pass additional arguments to the selected executable.

Non-interactive output supports PowerShell assignment and pipelines, for example
`$listing = .\sandbox.ps1 agents agent01 list`. Native stderr is displayed as
plain text and remains redirectable with `2>` or `2>&1`. Installers also send
progress there; check `$LASTEXITCODE` for command success. Shells, sessions, `run`/`tool`, and
login/setup keep direct console input and output for interactive programs.

Service actions are `status`, `start`, `stop`, `restart`, and `logs`. Forwarding
starts the selected enabled service and requires existing managed SSH setup.
Leave its terminal running and open the printed localhost URL. An optional
port overrides the local port: `.\sandbox.ps1 forward agent01 t3 4773`.
An unavailable local port is rejected before starting the service or SSH tunnel.
Service/forward aliases `hermes` and `deepseek` select their dashboard tools.
These added host command routes have offline coverage; live Windows results
are tracked in [issue #16](https://github.com/grauzone-git/sandboxed-ai-agents/issues/16).

## Remove

```powershell
.\sandbox.ps1 remove agent01
```

This removes the container and local managed SSH setup, retaining named volumes.
To permanently delete those volumes, including saved credentials and named
workspace files, use `.\sandbox.ps1 remove agent01 --volumes` instead.
Host-bound workspace directories are always retained.

If a command fails, inspect `$LASTEXITCODE` and `podman logs agent01`. Startup
failure can happen before SSH setup; after recovery, run
`.\sandbox.ps1 ssh-config agent01 --install`. Keep volumes while diagnosing.
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
