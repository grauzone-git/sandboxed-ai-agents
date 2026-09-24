# Agents and tools

For PowerShell, replace `./sandbox` in host agent/tool commands with
`.\sandbox.ps1`. The Windows launcher supports selection, login/setup, direct
runs, persistent sessions, service controls, and SSH forwarding. See the
[Windows quick guide](QUICKGUIDE-WINDOWS.md#manage-agents-and-tools).

[Back to the overview](../README.md) · [Sandbox lifecycle and SSH](SANDBOXES.md)

These examples continue with the `agent01` sandbox from the quickstart.
Commands run on the host unless stated otherwise, and the persistent terminals
and port forwarding need SSH to be configured.

Jump to [agent selection](#select-agents), [login](#log-in),
[terminals](#run-agents), [tools and dashboards](#optional-tools), or
[versions and updates](#versions-and-updates).

## Select agents

| Agent ID | Installed command | Source |
|---|---|---|
| `copilot` | `copilot` | npm `@github/copilot` |
| `claude` | `claude` | npm `@anthropic-ai/claude-code` |
| `codex` | `codex` | npm `@openai/codex` |
| `hermes` | `hermes` | Official Nous Research installer and source checkout |
| `opencode` | `opencode` | npm `opencode-ai` |
| `deepseek` | `dsh` | Official DeepSeek Harness, npm `@deepseek-ai/dsh` |

Pick at least one with `--agents` when you create a sandbox. `--agents all`
takes all six; leaving the flag out or passing `--agents none` fails on purpose.
Nothing quietly defaults to Copilot. To change an existing sandbox:

```bash
./sandbox agent01 agents list
./sandbox agent01 agents enable claude,opencode
./sandbox agent01 agents disable opencode
./sandbox agent01 agents check
```

`enable` adds to the selection and `disable` removes from it, while `set`
replaces the whole thing, as in `./sandbox agent01 agents set codex,claude`. An
existing sandbox can drop every agent with `agent01 agents set none`. Inside the
sandbox, `sandbox-agents` does the same jobs.

Installation runs as the unprivileged `agent` user in the sandbox's named home.
The first install needs internet access; later starts reuse what is already
there. Enabling an agent installs it and nothing more: no conversation starts
and no model call happens.

Disabling takes the managed command off PATH but leaves packages, credentials,
and history in place, and it does not kill CLI sessions that are already
running. Disabling Hermes or DeepSeek also stops and deselects its optional UI
tool, and re-enabling the agent leaves that UI off until you ask for it again.

Every agent in one sandbox shares its user, home, and credentials. Selection
controls what gets provisioned, not who can read what, so use separate sandboxes
when you need actual separation.

## Log in

Enable an agent before signing in. Credentials live in the named home volume and
survive container recreation as long as that volume is retained. Nothing is
copied or forwarded from your host. The managed `login` helper handles Codex,
Claude, OpenCode, Copilot, and Hermes; DeepSeek has its own setup, described
below. For GitHub repository access, use the
[GitHub CLI login helper](TOOLCHAIN.md#github-login).

### Codex device-code login

```bash
./sandbox agent01 agents enable codex
./sandbox agent01 agents login codex
```

The helper runs `codex login --device-auth`. Open the printed URL in your
desktop browser, sign in, and type the one-time code. Leave the terminal open
until login finishes. Your ChatGPT account security settings or workspace
permissions have to allow device-code login. No SSH port forward is involved.
[OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth#login-on-headless-devices).

Check the result with `./sandbox agent01 run codex login status`. Credentials
normally end up in `~/.codex/auth.json`, depending on Codex's credential-store
settings.

### Claude browser login

```bash
./sandbox agent01 agents enable claude
./sandbox agent01 agents login claude
```

The helper runs `claude auth login`. Open the printed URL locally and sign in.
If the browser shows a login code, paste it back into the waiting terminal, and
keep that terminal open until Claude confirms.
[Claude authentication documentation](https://code.claude.com/docs/en/authentication).

Check with `./sandbox agent01 run claude auth status`. For other flows, run
`./sandbox agent01 run claude auth login --console` or `--sso`. If an older
container tells you managed Claude login is unsupported,
[rebuild and recreate it](SANDBOXES.md#upgrade-the-image).

### OpenCode provider login

```bash
./sandbox agent01 agents enable opencode
./sandbox agent01 agents login opencode
```

The helper runs `opencode auth login` interactively inside the sandbox. Choose a
provider and an authentication method, then follow its prompts: some want an API
key, others a browser or code flow. Keep the terminal open until it finishes;
Ctrl+C cancels.

Methods vary by provider. For headless use, take a device-code or API-key option
when one is offered. A method that needs a localhost browser callback also needs
its callback port [forwarded over SSH](SANDBOXES.md#forward-ports), which the
login helper will not do for you.

To see what is stored:

```bash
./sandbox agent01 run opencode auth list
```

OpenCode keeps credentials in `~/.local/share/opencode/auth.json` in the named
home volume. Listing them tells you they exist, not that a model request will
succeed. The helper passes input and output straight through to OpenCode and
does not capture secrets in manager logs.
[OpenCode authentication commands](https://opencode.ai/docs/cli/#auth).

For other CLI options, use `./sandbox agent01 run opencode auth login ...`.
Existing containers need the updated manager, so [rebuild and recreate
them](SANDBOXES.md#upgrade-the-image) with their named volumes and agent
selection retained before the login helper is available.

### Copilot device-code login

```bash
./sandbox agent01 agents enable copilot
./sandbox agent01 agents login copilot
```

The helper runs `copilot login --device-code`. Open the printed URL in your
desktop browser, enter the one-time code, and authorize GitHub Copilot CLI.
Keep the terminal open until Copilot confirms; Ctrl+C cancels. Asking for the
device-code flow explicitly avoids a browser callback inside the container, so
no port forward is needed.
[GitHub Copilot authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli).

Copilot has to be enabled first, since login does not install it. This
authentication stays inside the sandbox and is unrelated to your Git or GitHub
CLI credentials. Once signed in, open a session with
`./sandbox agent01 copilot`.

For a GitHub Enterprise Cloud hostname, call the CLI directly:

```bash
./sandbox agent01 run copilot login --device-code --host HOSTNAME
```

Replace `HOSTNAME` with your instance. Existing containers need an
[image rebuild and recreation](SANDBOXES.md#upgrade-the-image), volumes
retained, to get the new login helper. If a pinned Copilot version has no
`login --device-code`, update it or use `/login` inside its interactive CLI.

### Hermes provider login

```bash
./sandbox agent01 agents enable hermes
./sandbox agent01 agents login hermes
```

The helper runs `hermes model`, the interactive provider and model setup wizard.
Choose a provider, work through its API-key or OAuth prompts, and select your
default model. Keep the terminal open until setup completes; Ctrl+C cancels. The
sandbox preselects no provider. Note that this sets Hermes's default provider
and model rather than only storing a credential.
[Hermes CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/#hermes-model).

Hermes has to be enabled first. The command uses its installed Python
environment and stores settings and credentials in the sandbox's named home. It
does not turn on the optional dashboard. Authentication depends on the provider;
if a flow wants a localhost callback, arrange its
[SSH forward](SANDBOXES.md#forward-ports) yourself or pick a device-code or
API-key method.

To manage credentials without choosing a model:

```bash
./sandbox agent01 run hermes auth
./sandbox agent01 run hermes auth list
```

For setup beyond authentication, run `./sandbox agent01 run hermes setup`.
Existing containers need an [image rebuild and recreation](SANDBOXES.md#upgrade-the-image)
with their named volumes retained to get the login helper.

### DeepSeek setup

Enable DeepSeek, open the [DeepSeek UI](#deepseek-ui), and configure
Settings → Models there.
[Official Harness](https://github.com/deepseek-ai/deepseek-harness).

Signing into an agent and [authenticating Git](TOOLCHAIN.md#git) are separate
jobs.

## Run agents

Every enabled agent gets a persistent terminal shortcut:

```bash
./sandbox agent01 copilot
./sandbox agent01 claude
./sandbox agent01 codex
./sandbox agent01 hermes
./sandbox agent01 opencode
./sandbox agent01 deepseek
```

Each one starts or reconnects its own tmux session in `/workspace`. Detach with
Ctrl+B, then D. Sessions survive an SSH disconnect but end when the container
stops. None of these commands installs or enables anything implicitly.

DeepSeek opens a shell showing Harness CLI help rather than starting the
optional UI. After provider setup, run a task with
`./sandbox agent01 run deepseek headless "YOUR TASK"`.

For one-off commands or custom CLI arguments, use `run`:

```bash
./sandbox agent01 run codex --version
```

If you want a desktop UI to manage a session, start it from that UI. A desktop
will not necessarily adopt a session you launched separately in a terminal.

## Optional tools

Tools have their own selection and do not come along when you select all agents.
Each selected tool runs its managed service on container loopback.

| Tool ID | Requires | Port | Purpose |
|---|---|---|---|
| `t3` | An enabled, authenticated provider for sessions | `3773` | T3 Code headless server |
| `hermes-dashboard` | Enabled `hermes` agent | `9119` | Hermes web dashboard |
| `deepseek-ui` | Enabled `deepseek` agent | `3080` | Harness bundled web UI |
| `tokentracker` | Nothing in particular | `7680` | Token usage dashboard |

Add `--tools tokentracker` to a new sandbox's `up` command, or change an
existing sandbox:

```bash
./sandbox agent01 tools enable tokentracker
./sandbox agent01 tools list
./sandbox agent01 tools check
./sandbox agent01 tool tokentracker --version
```

Tools support `set`, `enable`, `disable`, and `update` exactly like agents, and
`./sandbox agent01 tools set none` clears them all. Inside the sandbox, use
`sandbox-tools`. A fresh home starts with no tools selected. Recreating without
`--tools` reuses the saved selection, `--tools none` wipes it, and `--tools all`
requires both Hermes and DeepSeek to be enabled.

Disabling a tool stops its service but keeps installed files and data. The
Hermes dashboard and DeepSeek UI share their agent's installation and version,
so disabling either agent disables its UI too. Neither UI needs its own volume.

### TokenTracker

```bash
./sandbox agent01 tools enable tokentracker
./sandbox agent01 forward tokentracker
```

Open <http://127.0.0.1:7680> and keep the tunnel running. TokenTracker only sees
this sandbox's usage files; it will not aggregate across sandboxes on its own.
Initial setup may install hooks inside the sandbox.
[TokenTracker documentation](https://github.com/xiufengsun/TokenTracker).

### T3 Code headless server

To set up T3 Connect, run these commands on the host:

```bash
./sandbox agent01 tools enable t3
./sandbox agent01 tools setup t3
```

Setup requires T3 to be enabled and installed. It runs
`t3 connect link --headless` inside the sandbox. Follow the relay installation
and sign-in prompts, open the printed browser link, confirm the code, and
approve access. After the command succeeds, the helper restarts the managed
T3 server to activate the connection. This interrupts current T3 connections.
The sandbox must remain running for remote access.

The managed server runs `t3 serve --host 127.0.0.1 --port 3773` in tmux. It starts
after Connect setup and is restored whenever the existing sandbox starts again,
including after `./sandbox agent01 stop` followed by `./sandbox agent01 start`.
Keep the `t3` tool enabled for this automatic restoration. You do not need to run
`t3 serve` manually or install a systemd service inside the container.

If you used plain `t3 connect` and its background-service setup failed, start the
sandbox-managed server from the host with `./sandbox agent01 service t3 restart`.
Inspect failures with `./sandbox agent01 service t3 logs`. The Connect helper
uses `connect link --headless` to avoid that background-service installer.

Automatic sandbox startup after a host-machine reboot is not configured by this
launcher. Start the sandbox with `./sandbox agent01 start` after the host boots;
enabled T3 is then restored inside it.

Sign in to the same T3 Connect account on your other device and select this
environment. No SSH forward or OAuth callback port is needed. Settings and
credentials remain in the sandbox's named home volume. The existing service
manager supervises the server; setup does not install T3's background service.
See [T3 remote access](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md).

Check saved setup or revoke access from the host with:

```bash
./sandbox agent01 tool t3 connect status
./sandbox agent01 tool t3 connect logout
./sandbox agent01 service t3 restart
```

Status reports saved configuration, not live reachability. Existing containers
need `./sandbox agent01 update` to get this helper. If a pinned T3 version lacks
`connect link --headless`, update T3 first.

For direct pairing through an SSH forward:

```bash
./sandbox agent01 tools enable t3
./sandbox agent01 forward t3
```

In a second host terminal, run `./sandbox agent01 tool t3 pair` and follow the
pairing instructions at <http://127.0.0.1:3773>. `./sandbox agent01 t3` tails the
managed server's logs. Both pairing URLs and logs can contain private tokens, so
treat them accordingly.

The T3 desktop app can also manage its own SSH runtime; see
[desktop control](ARCHITECTURE.md#desktop-control). If you go that route,
disable this tool with `./sandbox agent01 tools disable t3` so you are not
running two servers. Disabling the tool has no effect on the desktop's runtime.

### Hermes dashboard

```bash
./sandbox agent01 agents enable hermes
./sandbox agent01 agents login hermes
./sandbox agent01 tools enable hermes-dashboard
./sandbox agent01 forward hermes-dashboard
```

Open <http://127.0.0.1:9119>. The dashboard shares the agent's checkout, Python
environment, profile, and history, and its frontend is built only when you
select the tool. Enabling the Hermes agent alone installs just the CLI. No
native Electron desktop components are installed and no messaging gateway
starts by itself.

`./sandbox agent01 tools disable hermes-dashboard` turns off the dashboard and
leaves the CLI enabled, with cached assets and settings intact.
[Hermes dashboard documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard).

### DeepSeek UI

```bash
./sandbox agent01 agents enable deepseek
./sandbox agent01 tools enable deepseek-ui
./sandbox agent01 forward deepseek-ui
```

In a second host terminal, run `./sandbox agent01 service deepseek-ui logs` to
find the private access URL. Open it locally, then configure model credentials
and `/workspace`. A request without the access token can come back as HTTP 401,
so use the full URL. When forwarding to a different local port, change only the
host and port and keep the token.

`./sandbox agent01 tools disable deepseek-ui` turns off the UI and leaves the
CLI enabled. The UI ships inside the Harness package, so selection controls its
service lifecycle rather than what gets downloaded.
[Harness CLI reference](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md).

### Service controls

Any tool ID works in place of `tokentracker`:

```bash
./sandbox agent01 service tokentracker status
./sandbox agent01 service tokentracker logs
./sandbox agent01 service tokentracker restart
```

`start` and `stop` work too. A stop is temporary: container startup, forwarding,
or a change in tool selection can bring the service back. Disable the tool if
you want it to stay off. Logs live at
`~/.local/state/sandbox-tools/TOOL.log` inside the sandbox, and some of them
contain access or pairing tokens, so keep them private.

Podman publishes only SSH. See [port forwarding](SANDBOXES.md#forward-ports) for
custom local ports and connection errors. `hermes` and `deepseek` still work as
service and forward aliases for `hermes-dashboard` and `deepseek-ui`.

## Versions and updates

Updates are always explicit; a normal start reuses whatever is cached:

```bash
./sandbox agent01 agents update all
./sandbox agent01 tools update all
```

Here `all` means everything currently enabled in that namespace. To pin a
published version, swap in real values for the placeholders:

```bash
./sandbox agent01 agents enable codex@X.Y.Z
./sandbox agent01 tools enable tokentracker@X.Y.Z
./sandbox agent01 agents enable hermes@FULL_COMMIT_SHA
```

Hermes accepts a branch, a tag, or a full 40-character commit. T3 and
TokenTracker take their own version pins. The bundled UI tools follow their
agent's version, so updating a UI on its own only prepares it for the agent
revision you already have, while updating the agent refreshes its selected UI
too.

Pins stay pinned through `update`. Use something like `enable codex@latest` to
start following the moving version again. Prefer these manager updates over an
agent's built-in updater so the recorded version stays accurate.

## Installation problems and older containers

A failed install may leave downloads behind, but it does not commit a partial
new selection. Fix whatever broke and run the selection command again. Unmanaged
commands that collide in `~/.local/bin` have to be moved or uninstalled first.

A recognized older global TokenTracker install is migrated, with its data, when
you enable the managed tool. Saved T3 agent selections migrate to tools; use
`agent01 tool t3 ...` for one-off commands. Older manually started web servers
may need stopping if they sit on a managed tool's port.

If a command is missing or acts like an older version,
[rebuild and recreate the container](SANDBOXES.md#upgrade-the-image) with its
volumes preserved. Host script changes apply immediately; image changes need
recreation.
