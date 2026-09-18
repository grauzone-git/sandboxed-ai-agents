# Sandboxed AI agents

Coding agents want a lot of room: read and write anywhere in the project, run
shell commands, install packages, reach the network. Running several of them
directly on your laptop means they all share one home directory, one set of
credentials, and each other's files.

This repository gives each workspace its own rootless Podman container instead.
You pick which agents go in it, you reach it over SSH from your terminal, VS
Code, or a desktop agent UI, and nothing from your host is mounted unless you
explicitly ask for it.

```bash
./sandbox build
./sandbox up agent01 --agents codex --ssh-config
./sandbox agents agent01 login codex
./sandbox codex agent01
```

That's a working sandbox with Codex installed, signed in, and running in a
persistent terminal.

## What's inside

The image is Debian 12 slim with Node.js 24 and npm, .NET SDKs 9 and 10, Git,
GitHub CLI, Azure CLI with the DevOps extension, tmux, OpenSSH, and the system
dependencies for headless Playwright runs against Chromium, Firefox, and Edge.
Everything above ships in the shared image; agents and dashboards install per
sandbox, into that sandbox's own home volume.

Six agents are available, and none of them installs by default. You name at
least one when you create a sandbox:

| Agent ID | Command | Installed from |
|---|---|---|
| `copilot` | `copilot` | npm `@github/copilot` |
| `claude` | `claude` | npm `@anthropic-ai/claude-code` |
| `codex` | `codex` | npm `@openai/codex` |
| `hermes` | `hermes` | Nous Research installer and source checkout |
| `opencode` | `opencode` | npm `opencode-ai` |
| `deepseek` | `dsh` | npm `@deepseek-ai/dsh` (DeepSeek Harness) |

Four optional web tools have their own separate selection, so `--agents all`
does not drag them in:

| Tool ID | Port | What it is |
|---|---|---|
| `tokentracker` | `7680` | Token usage dashboard |
| `t3` | `3773` | T3 Code headless server |
| `hermes-dashboard` | `9119` | Hermes web dashboard (needs the `hermes` agent) |
| `deepseek-ui` | `3080` | Harness web UI (needs the `deepseek` agent) |

## Requirements

You need a Linux host with rootless Podman 5 or newer, set up the usual way:
subordinate UID/GID ranges, `newuidmap` and `newgidmap`, `pasta`, and delegated
cgroups v2. Add Bash, Python 3.9+, and the OpenSSH client tools. Run
`podman info` as your normal user first; if that works, the launcher will too.

Never run `./sandbox` with `sudo`. Building the image and installing agents the
first time needs internet access and several GB of disk.

## Getting started

Build the image once, then create a sandbox:

```bash
./sandbox build
./sandbox up agent01 --agents codex --ssh-config
./sandbox check agent01
./sandbox agents agent01 login codex
./sandbox codex agent01
```

`login` prints a URL and a one-time code to finish in your desktop browser. The
last command opens a persistent tmux session; detach with Ctrl+B, then D, and
reconnect with the same command later.

Without a workspace directory, your project files live in the `agent01-workspace`
named volume, mounted at `/workspace`. If you would rather bind a folder from
your host, pass it when you create the sandbox:

```bash
./sandbox up agent01 ./workspaces/agent01 --agents codex --ssh-config
```

Those two `up` commands are alternatives, not steps in sequence. See
[workspace storage](docs/SANDBOXES.md#choose-workspace-storage) for the
trade-offs.

`--ssh-config` is what makes `ssh agent01`, VS Code Remote SSH, port forwarding,
and the persistent terminal shortcuts work. It writes a dedicated key and config
under `~/.ssh/sanboxed-agents/agent01/` and adds an Include at the top of
`~/.ssh/config`. Skip it if you only want Podman-based agent management, and
[add SSH later](docs/SANDBOXES.md#ssh-setup) when you need it.

### Open the workspace in VS Code

Install the Remote - SSH extension on your desktop, run **Remote-SSH: Connect to
Host…**, pick `agent01`, and open `/workspace`. VS Code installs its server into
the sandbox's home volume, so it survives restarts. You edit on your desktop
while terminals, Git, the SDKs, and workspace extensions all run in the
container. [SSH and VS Code](docs/SANDBOXES.md#vs-code) covers diagnostics, and
[desktop control](docs/ARCHITECTURE.md#desktop-control) covers UIs that drive
several sandboxes at once.

### Add agents and tools

Selections are editable after creation:

```bash
./sandbox agents agent01 enable claude
./sandbox agents agent01 login claude
./sandbox tools agent01 enable tokentracker
./sandbox forward agent01 tokentracker
```

Leave the forwarding terminal open and open <http://127.0.0.1:7680>.

For a second, independent workspace, create another sandbox with its own name
and SSH port:

```bash
./sandbox up agent02 --ssh-port 2223 --agents claude --ssh-config
```

### Update

```bash
./sandbox update agent01       # rebuild the image, recreate this sandbox
./sandbox update --all         # rebuild once, update every owned sandbox
```

Storage, agent and tool selections, resource limits, ports, and SSH access all
carry over. Save your work first, because recreating a container kills running
sessions. [Image updates](docs/SANDBOXES.md#upgrade-the-image) covers custom
builds and rollback.

## How it works

Each sandbox is one container plus three named volumes: the workspace, the
agent's home directory, and the SSH server state. Only the workspace can be
swapped for a host bind. Podman owns the containers; your editor or agent UI
owns the sessions inside them.

```mermaid
flowchart LR
  UI[Desktop editor or agent UI] -->|SSH port 2222| A[agent01]
  UI -->|SSH port 2223| B[agent02]
  A --> WA[agent01-workspace]
  A --> HA[agent01-home]
  A --> SA[agent01-sshd]
  B --> WB[agent02-workspace]
  B --> HB[agent02-home]
  B --> SB[agent02-sshd]
```

Agents run as UID 1000 inside Podman's rootless user namespace, mapped back to
your host user with `keep-id` so workspace files stay editable from both sides.
`no-new-privileges` is on. The launcher refuses workspace binds that would
expose its own source, Git metadata, or SSH state.

### What this does not protect you from

Every agent in one sandbox shares that sandbox's user, home directory, and
credentials. Disabling an agent takes its command off PATH; it does not revoke
access to files already cached in the home volume. Use separate sandboxes when
you want separation.

Outbound networking is open so installs and model calls work, which also means
an agent can reach the internet and whatever your LAN exposes. Containers share
the host kernel. For code you actively distrust, run the same workflow inside a
dedicated VM.

[SECURITY.md](SECURITY.md) states the threat model in full and explains how to
report a vulnerability.

## Command reference

```text
./sandbox build [podman build args]
./sandbox up NAME [WORKSPACE [SSH_PORT]] --agents LIST [--tools LIST] [--ssh-config]
./sandbox start|stop|shell|check|check-full|fingerprint NAME
./sandbox agents NAME list|check|set|enable|disable|update|login ...
./sandbox tools NAME list|check|set|enable|disable|update ...
./sandbox run NAME AGENT [args...]          # one-off command
./sandbox tool NAME TOOL [args...]          # one-off tool command
./sandbox copilot|claude|codex|hermes|opencode|deepseek|t3 NAME   # persistent terminal
./sandbox service NAME TOOL status|start|stop|restart|logs
./sandbox forward NAME TOOL [LOCAL_PORT]
./sandbox ssh-config NAME [--install]
./sandbox update NAME...|--all [--no-build]
./sandbox remove NAME [--volumes]
```

Run `./sandbox --help` for the full syntax, including environment variables such
as `SANDBOX_IMAGE`, `SANDBOX_CPUS`, and `SANDBOX_MEMORY`.

## Documentation

| If you want to | Read |
|---|---|
| Connect, forward ports, stop, upgrade, or remove a sandbox | [Sandbox lifecycle and SSH](docs/SANDBOXES.md) |
| Pick agents, sign in, pin versions, run dashboards | [Agents and tools](docs/AGENT-SETUP.md) |
| Configure the image, .NET, npm, Playwright, Git, Azure | [Development toolchain](docs/TOOLCHAIN.md) |
| Understand isolation, desktop UIs, and remote workers | [Architecture](docs/ARCHITECTURE.md) |
| Know why it is built this way before changing it | [Project context](CONTEXT.md) |
| Work on this repository as an AI coding agent | [AGENT.md](AGENT.md) |

Every guide uses the same examples: `agent01` on SSH port `2222` is the first
sandbox, `agent02` on `2223` is the second, and `./workspaces/agent01` appears
whenever an example needs an explicit host bind. Host commands run from this
repository; anything marked "inside the sandbox" runs in its terminal at
`/workspace`. `NAME`, `X.Y.Z`, and uppercase words are placeholders.

## Repository layout

```text
sandbox           Entry point; everything else is an implementation detail
src/host/         Host CLI: dispatch, SSH files, lifecycle, image updates
src/container/    Containerfile plus the in-container agent/tool manager
tests/            Offline regression tests, no Podman or network needed
docs/             The guides linked above
```

Run the offline suite with `make test`, or just the syntax, catalog, and
documentation link checks with `make check`. Both need Bash, Python 3.9+,
Node.js, and OpenSSH client tools.

## Credits

This launcher does very little on its own. It creates a container, wires up SSH,
and gets out of the way. Everything that makes a sandbox worth opening was built
by somebody else, so here they are.

The agents you can install:

- [GitHub Copilot CLI](https://github.com/github/copilot-cli) by GitHub
- [Claude Code](https://github.com/anthropics/claude-code) by Anthropic
- [Codex CLI](https://github.com/openai/codex) by OpenAI
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research
- [OpenCode](https://github.com/sst/opencode) by SST
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) by DeepSeek

The optional tools and dashboards:

- [T3 Code](https://github.com/pingdotgg/t3code) by Theo and the T3 team
- [TokenTracker](https://github.com/xiufengsun/TokenTracker) by Xiufeng Sun
- The [Hermes dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
  and the DeepSeek web UI, which ship with their respective agents

And the foundation the image is built on:

- [Podman](https://podman.io) for rootless containers, which is the entire
  reason this approach works without a daemon or root
- [Debian](https://www.debian.org) for the base image
- [Node.js](https://nodejs.org), [.NET](https://dotnet.microsoft.com),
  [Playwright](https://playwright.dev), [tmux](https://github.com/tmux/tmux),
  [OpenSSH](https://www.openssh.com), [Git](https://git-scm.com), the
  [GitHub CLI](https://github.com/cli/cli), and the
  [Azure CLI](https://github.com/Azure/azure-cli)

Each of these projects has its own license and terms. Installing an agent here
means agreeing to them and to whatever provider it talks to. This repository
only automates the installation; it makes no claim on any of the above, and none
of these projects endorse it.

## License

MIT, see [LICENSE](LICENSE). That covers this launcher and its documentation
only. The agents, tools, and SDKs it installs keep their own licenses, and
nothing here is redistributed.

## Status

The launcher and its offline test suite are exercised regularly. Live runs on
Linux x86-64 with rootless Podman have covered sandbox creation, agent
installation, SSH setup, port forwarding, image updates, and removal.

Still unverified end to end: real provider sign-in and model requests, the VS
Code Agents window, the T3 and Kandev desktop integrations, remote-worker
setups, and ARM64 or otherwise customized image builds.
