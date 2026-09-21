# Linux quick guide

Run host commands from this repository as your normal user, never with `sudo`.
You need Bash, Python 3.9+, OpenSSH client tools, and configured rootless
Podman 5+ with subordinate IDs, `pasta`, and delegated cgroups v2. Building and
installing agents require internet access and several GB of disk.

## Build and create

```bash
podman info
./sandbox build
./sandbox up agent01 --agents copilot --ssh-config
```

This creates separate workspace, home, and SSH-state volumes. The workspace is
`/workspace` inside the sandbox. No host project directory is mounted by default.
`--ssh-config` installs a dedicated client key and the `agent01` SSH alias.

To work on a host directory instead, use this creation command in place of the
one above:

```bash
./sandbox up agent01 ./workspaces/agent01 --agents copilot --ssh-config
```

`up` refuses an existing container. To create another sandbox, use a different
name and port, for example `agent02 --ssh-port 2223`.

## Sign in and work

```bash
./sandbox agents agent01 login copilot
./sandbox copilot agent01
```

The second command opens or reconnects a persistent terminal. Detach with
`Ctrl+B`, then `D`. Sign-in credentials stay in the sandbox home volume.

For a shell, run `./sandbox shell agent01` or `ssh agent01`. In VS Code, use
Remote - SSH to connect to `agent01`, then open `/workspace`. The SSH user is
`agent`; root login is disabled.

## Daily commands

| Task | Host command |
|---|---|
| Stop / start | `./sandbox stop agent01` / `./sandbox start agent01` |
| Add SSH to a running sandbox | `./sandbox ssh-config agent01 --install` |
| Check toolchain and mounts | `./sandbox check agent01` |
| List agents / tools | `./sandbox agents agent01 list` / `./sandbox tools agent01 list` |
| Enable T3 | `./sandbox tools agent01 enable t3` |
| Forward T3 to localhost | `./sandbox forward agent01 t3` |
| Rebuild and update | `./sandbox update agent01` |
| Apply an already built image | `./sandbox update agent01 --no-build` |

Update preserves workspace/home data, SSH setup, saved selections, settings,
and running/stopped state. It restores the old container if replacement fails.

At creation, use `--agents 'codex,claude'` to choose other agents, `--tools t3`
for an optional tool, or `--capabilities podman` for nested container builds.
For resource settings, set `SANDBOX_CPUS=2 SANDBOX_MEMORY=6g` before `./sandbox up`.
Nested Podman changes the sandbox's isolation settings; read the
[capability guide](TOOLCHAIN.md#nested-containers-with-podman) before enabling it.

## Remove

```bash
./sandbox remove agent01
```

This removes the container and local managed SSH setup, retaining named volumes.
To permanently delete those volumes, including saved credentials and named
workspace files, use `./sandbox remove agent01 --volumes` instead. Host-bound
workspace directories are always retained.

See [lifecycle and SSH](SANDBOXES.md), [agents and tools](AGENT-SETUP.md), or
`./sandbox --help` for more options.
