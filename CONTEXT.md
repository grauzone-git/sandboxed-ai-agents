# CONTEXT.md

Orientation for anyone, human or agent, picking this repository up. The
[README](README.md) shows how to use the launcher; this file explains what it is
for, how it is put together, and which decisions are deliberate.

## The problem

A coding agent needs broad access to be useful: read and write across the
project, run shell commands, install packages, reach the network. Running
several of them directly on a laptop means they all share one home directory,
one set of provider credentials, one npm cache, and each other's files. A
mistake or a bad suggestion in one has nothing standing between it and
everything else.

The usual answers do not fit well. A plain container per project loses the
editor integration. Dev Containers brings mounts and credential forwarding that
an extension can extend without asking. A full VM per workspace is heavy enough
that people stop making new ones.

This repository takes a narrower position: one rootless Podman container per
workspace, an explicit list of agents in each, SSH as the only way in, and no
host directory mounted unless you name it.

## Design decisions

**One sandbox per workspace, not per agent.** Agents sharing a container share
its user, home, and credentials, which is fine for several agents working the
same project and wrong for separating two projects. Separation comes from
creating another sandbox.

**Nothing installs by default.** `up` refuses an empty `--agents` selection.
There is no fallback agent, deliberately, so nobody ends up authenticated to a
provider they did not choose.

**Three named volumes, one optional bind.** Workspace, home, and SSH server
state are named volumes. Only the workspace can be swapped for a host directory,
and `src/host/workspace.py` rejects any bind that would put the controller's own
source, Git metadata, or SSH state inside the container. Credentials, agent
sockets, engine sockets, and display sockets are never mounted.
The Podman capability also mounts `/run/user/1000` as tmpfs so nested runtime
state disappears on stop/start while inner storage remains in the home volume.

**SSH rather than Dev Containers.** SSH keeps the mount layout explicit, works
the same for a local container and a remote worker, and is what VS Code Remote
SSH, T3, and Kandev already speak. The cost is running sshd in the image and
managing keys, which the launcher does per sandbox with a pinned host key.

**Debian 12 slim, not Alpine.** The SDKs, the Playwright browsers, and the VS
Code server all want glibc. Alpine would be smaller and would break the workflow
the whole thing exists for.

**Host SSH setup is opt-in.** Writing to somebody's `~/.ssh/config` without
being asked is not acceptable, so it happens only with `--ssh-config`. Removal
is the asymmetric case: it always cleans up the files it created, because
leaving stale host entries and pinned keys behind is worse.

**Script ownership by checkout path.** Containers and volumes are labelled with the
absolute path of the controller checkout (`io.sandboxed-agents.project`). That
is what lets `update --all` and the ownership guards know which resources are
theirs. It also means moving the checkout orphans existing sandboxes.

The standalone executable preview uses a stable controller group instead:
`default`, or the name in `SANDBOX_CONTROLLER`. A group and a checkout are
separate owners. The executable does not implicitly adopt checkout resources.

## How the pieces fit

```
./sandbox                     resolves the repo root, execs the host CLI
  └── src/host/cli.sh         parses, checks Podman, dispatches
        ├── lib/args.sh       argument parsing and the help text
        ├── lib/podman.sh     rootless and ownership checks, manager transport
        ├── lib/ssh.sh        host keys, pinned config, Include installation
        ├── lib/lifecycle.sh  container create, start, remove
        ├── workspace.py      bind validation (the security guard)
        ├── update.py         rebuild, recreate, validate, roll back
        └── containers.py     the shared Podman creation options
```

Inside the container, a separate program owns everything agent-related:

```
src/container/
  Containerfile        the image; this directory is its build context
  entrypoint.sh        root-only SSH and bootstrap work
  sandbox-agents       unprivileged wrapper, holds the mutation lock
  agent-manager.cjs    selection state, launchers, dispatch
  installers.cjs       npm, T3, and Hermes installation
  services.cjs         tmux supervision, loopback readiness
  agents.json          six supported agents
  tools.json           four optional web tools
```

The host never installs agents itself. It sends commands to the in-container
manager, which runs as the unprivileged `agent` user and writes only into that
sandbox's home volume. The same JSON catalogs are read on both sides, so the
host validator and the manager cannot disagree about what exists.

The host and container halves update on different schedules. A change under
`src/host/` is live on the next `./sandbox` call. A change under
`src/container/` needs an image rebuild and a container recreation, which is why
`update` exists and why several documentation sections tell users to rebuild
before a new feature appears.

## What is actually isolated

Agents run as UID 1000 inside Podman's rootless user namespace, mapped back to
the host user with `keep-id` so workspace files stay editable from both sides.
`no-new-privileges` is on by default. The optional `podman` capability installs
a derived image layer and permits nested rootless containers by passing
`/dev/fuse` and `/dev/net/tun`, allowing mapping-helper file capabilities,
disabling SELinux/AppArmor separation,
and unmasking kernel paths. Its seccomp profile retains the host's rules except
for allowing hostname and setns operations needed by inner namespaces.
Selection is stored in a container label and
preserved by updates unless explicitly overridden. There is no host bind by
default, no host networking, no host IPC, and no forwarded credentials.

What that does not give you: agents can do anything they like inside their own
sandbox, including to credentials stored there, and disabling an agent removes
its launcher rather than its access to cached files. Outbound networking is open
because installs and model calls need it, so this is not an egress firewall.
Containers share the host kernel. For code you actively distrust, the same SSH
workflow inside a dedicated VM is the honest answer.

These limits are stated in the README and in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) on purpose. Do not let them quietly
become marketing.

## Conventions worth knowing before you read code

The scripts have no third-party dependencies. The Go executable permits the
standard library and `golang.org/x/sys`. `make test` runs the script regressions
offline against fake Podman executables and isolated home directories;
`go test ./...` runs the executable tests. GitHub Actions runs the script suite,
Go tests, and executable list contract, and cross-builds the supported targets.

Python is standard library only and targets 3.9. Bash is `set -euo pipefail`
with a shared `fail` helper. JavaScript is CommonJS with factory functions that
accept injected process functions, so tests mock `spawn` rather than the source
exposing internals.

Documentation examples are fixed: `agent01` on port `2222`, `agent02` on `2223`,
`./workspaces/agent01` for an explicit bind. Keeping them consistent is what
lets guides cross-reference each other without re-explaining setup.

## Known rough edges

The SSH state directory is spelled `~/.ssh/sanboxed-agents/`, missing a `d`. It
is baked into `args.sh`, `remove-ssh-config.py`, and `update.py`, so the
documentation matches reality. Renaming it would orphan the SSH setup of every
existing sandbox and needs a migration path, not a find and replace.

The executable is a preview. Its current command coverage and handover limits
are documented in [docs/EXECUTABLE.md](docs/EXECUTABLE.md).

Several integrations are documented from research rather than from use: the VS
Code Agents window, T3 desktop, Kandev, remote workers, and ARM64 or otherwise
customized image builds.

## Where to go next

| You want to | Read |
|---|---|
| Use the launcher | [README.md](README.md) |
| Change the code | [AGENT.md](AGENT.md) |
| Understand isolation and desktop UIs | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Work with sandboxes day to day | [docs/SANDBOXES.md](docs/SANDBOXES.md) |
| Set up agents and dashboards | [docs/AGENT-SETUP.md](docs/AGENT-SETUP.md) |
| Configure the image or the SDKs | [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md) |
