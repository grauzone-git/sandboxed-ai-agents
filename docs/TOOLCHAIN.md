# Development toolchain

Windows image builds and nested Podman use the existing container recipes.
See the [Windows quick guide](QUICKGUIDE-WINDOWS.md) for host setup.

[Back to the overview](../README.md) · [Agents and tools](AGENT-SETUP.md)

The shared image carries Debian 12 slim, Node.js 24 with npm, .NET SDKs 9 and
10, Git and the GitHub CLI, Azure CLI with the DevOps extension, PowerShell (`pwsh`), OpenSSH, tmux,
and Playwright's system dependencies. Agents and optional dashboard tools are
not in the image; they install per sandbox, into its named home.

## Image options

Build on the host from the controller repository:

```bash
./sandbox build
```

The default tag is `localhost/agent-sandbox:dev`. To use another tag, set
`SANDBOX_IMAGE` consistently for both build and lifecycle commands. Build
arguments pass straight through to Podman, and you can combine them freely:

| Build argument | Default | Use |
|---|---|---|
| `WITH_NATIVE_BUILD_TOOLS` | `0` | Set to `1` for Python, C/C++ compilers, and pkg-config |
| `PLAYWRIGHT_BROWSERS` | `chromium-firefox` | Set to `all` to add WebKit system dependencies |
| `WITH_EDGE` | `1` | Set to `0` to leave Edge out, which an ARM64 build needs |
| `PLAYWRIGHT_VERSION` | `latest` | Pin the image's Playwright CLI |
| `DOTNET9_VERSION`, `DOTNET10_VERSION` | `latest` | Pin exact SDK versions inside each major release |
| `AZURE_CLI_VERSION`, `AZURE_DEVOPS_VERSION` | `latest` | Pin Azure CLI and DevOps extension versions |
| `POWERSHELL_VERSION` | `latest` | Pin the full Microsoft Debian package version |
| `BASE_IMAGE` | `docker.io/library/node:24-bookworm-slim` | Pin the compatible base, optionally by digest |

For compiler tools plus WebKit dependencies:

```bash
./sandbox build --build-arg WITH_NATIVE_BUILD_TOOLS=1 --build-arg PLAYWRIGHT_BROWSERS=all
```

The default build handles JavaScript packages and prebuilt native addons.
Compiling addons yourself, or building .NET native projects, can need the
compiler tools and further project-specific libraries. Both SDKs are present in
every variant.

Resolved versions are written to `/opt/agent-tools/build-versions.txt` inside
the image. Moving defaults and build caching do not add up to a reproducible
lock, so pin versions or digests when repeatability matters. Existing containers
need [recreation after a rebuild](SANDBOXES.md#upgrade-the-image).

Container defaults are 4 CPUs, 8 GiB RAM, 2,048 processes, and a private 1 GiB
`/dev/shm`. Those are limits, not reservations. Set `SANDBOX_CPUS` and
`SANDBOX_MEMORY` at creation time to change the CPU and RAM limits. Image layers
are shared between sandboxes; installations and caches are not.

## Nested containers with Podman

On the host, select the capability when creating a sandbox:

```bash
./sandbox up agent01 --agents codex --capabilities podman --ssh-config
```

Or enable it on an existing sandbox, retaining its workspace, home, SSH access,
and agent/tool selections:

```bash
./sandbox update agent01 --no-build --capabilities podman
```

Update stops and recreates the sandbox, so save work first. `--no-build` reuses
your existing base image; it still builds the optional Podman layer. The layer
installs Debian's `podman`, `uidmap`, `libcap2-bin`, `fuse-overlayfs`, `slirp4netns`,
and `crun` packages automatically. It is cached across sandboxes using the same base image
and recipe. No Podman packages are added to sandboxes without the capability.
The outer host still needs Podman 5+; the inner Podman version comes from Debian.

The host needs accessible `/dev/fuse` and `/dev/net/tun` devices, enabled unprivileged user namespaces,
and at least 65536 subordinate IDs for your user in both `/etc/subuid` and
`/etc/subgid`. Startup derives the inner allocation from the actual outer
UID/GID mappings, excludes container root and the agent identity, and rejects
an allocation too small to support ordinary images, including UID/GID 65534.

Inside the sandbox, run Podman as `agent`, without sudo or a service daemon:

```bash
podman info
podman build -t localhost/my-app:dev .
podman run --rm localhost/my-app:dev
# Optional live check: pulls Alpine, builds with RUN, then runs as UID/GID 65534.
sandbox-podman-check
```

Nested Podman uses fuse-overlayfs and stores images, named volumes, and container
metadata under `~/.local/share/containers/storage`, in the existing named home
volume. They survive sandbox recreation. Transient runtime state, including
the runroot and libpod temporary files, lives on a tmpfs at `/run/user/1000`
that is cleared whenever the outer sandbox stops. Existing sandboxes need
`./sandbox update NAME --no-build` to adopt this mount. Running inner processes
stop with the outer sandbox; restart your test containers afterwards. Inner cgroups are
disabled because SSH sessions have no delegated cgroup manager. The outer
sandbox's CPU, memory, and process limits still apply to the whole sandbox;
individual inner resource limits are not supported by this configuration.

For a web application, publish to the sandbox's loopback interface:

```bash
# Inside the sandbox; adapt the application's internal port as needed.
podman run --rm -p 127.0.0.1:8080:8080 localhost/my-app:dev
```

Then, from the host with SSH configured:

```bash
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 agent01
```

Open `http://127.0.0.1:8080` on the host. Bind paths passed to inner Podman are
paths inside the sandbox, such as `/workspace`.

Capabilities accept `podman` or `none` (the default), with unknown or duplicate
entries rejected. Normal updates preserve each sandbox's selection. To remove
the capability, use `./sandbox update agent01 --no-build --capabilities none`;
saved inner images and volumes remain in the home volume.

The image replaces the mapping helpers' setuid bits with specific file
capabilities: `cap_setuid=ep` for `newuidmap` and `cap_setgid=ep` for `newgidmap`.
Debian's default setuid helpers can fail with `write to uid_map failed:
Operation not permitted` inside a rootless container. If you have an older
capability image, update the sandbox from the host with
`./sandbox update agent01 --no-build` to build and apply the corrected layer.

The capability allows these file capabilities by omitting `no-new-privileges`,
passes `/dev/fuse` for storage and `/dev/net/tun` for nested networking,
disables outer SELinux/AppArmor separation, and unmasks the
kernel paths needed for inner mounts. It retains the rootless outer namespace,
resource limits, and a seccomp profile derived from the host's default. The
derived profile allows `sethostname`, `setdomainname`, and `setns` for inner
namespace setup while preserving all other host rules. Kernel namespace
permission checks still apply. The configuration does not use `--privileged` or
mount a host engine socket. See the [security policy](../SECURITY.md).

If an older sandbox fails during a build with `sethostname: Operation not
permitted`, recreate it from the host with `./sandbox update agent01 --no-build`.
An existing process cannot relax its inherited seccomp filter. Update generates
the profile under the controller's protected `.local` directory before stopping
the sandbox; a missing or invalid host profile aborts the update.
This follows the [Podman nesting approach documented by Red Hat](https://www.redhat.com/en/blog/podman-inside-container).

## Work inside the sandbox

Open a shell from the host with `./sandbox shell agent01`. Everything in the
sections below runs **inside that terminal**, with `/workspace` as the project
root.

### Git

For an empty workspace, clone your project straight into it:

```bash
cd /workspace
git clone https://github.com/OWNER/REPOSITORY.git .
git config --global user.name 'Your Name'
git config --global user.email 'you@example.com'
```

For private GitHub repositories, complete [GitHub login](#github-login) before
the clone. A dedicated repository SSH credential created inside the sandbox
works as well. None of your host Git settings, SSH keys, or agent sockets are
mounted, which is the point.

### GitHub login

Run this command on the host:

```bash
./sandbox tools agent01 login github
```

The helper runs `gh auth login --hostname github.com --git-protocol https --web`
inside the sandbox. Open the printed URL in your desktop browser, enter the
one-time code, and follow the terminal prompts. Keep the terminal open until it
finishes. After success, it runs `gh auth setup-git --hostname github.com` so Git
can use the same credentials for HTTPS clones, pulls, and pushes.
It then prompts for your Git commit name and email, including a GitHub noreply
address if you prefer, and saves them with `git config --global user.name` and
`git config --global user.email`. These settings apply to all repositories for
the sandbox user; a repository's local Git settings can override them. Both
values are required. Cancelling before entering both leaves the existing
identity unchanged; GitHub authentication remains completed.
See the [GitHub CLI login reference](https://cli.github.com/manual/gh_auth_login)
and [Git credential setup reference](https://cli.github.com/manual/gh_auth_setup-git).

GitHub CLI is built into the image; no tool selection or enabled agent is
required for this login. Credentials and Git configuration stay in the named
home volume and survive recreation while that volume is retained. Agents in
the same sandbox can use them. This is separate from Copilot provider login.

Inside the sandbox, check or remove the login with:

```bash
gh auth status --hostname github.com
gh auth logout --hostname github.com
```

For GitHub Enterprise or other authentication options, run `gh auth login`
and `gh auth setup-git` with the appropriate `--hostname` inside the sandbox.
Existing containers need `./sandbox update agent01` from the host to rebuild
and recreate them with the helper, retaining their named volumes.

### .NET

```bash
dotnet --list-sdks
```

Pick .NET 9 or 10 through your project's `global.json`; without one, the newer
installed SDK wins. The SDK distributions sit side by side without the extra
tooling the full SDK container image would bring. Microsoft's
[support policy](https://dotnet.microsoft.com/en-us/platform/support/policy) is
worth checking when you choose a target.

### npm

Project dependencies install under `/workspace`, either in its named volume or
in the host directory you bound. Global npm installs go to `~/.local` with the
cache in `~/.npm`, both inside the named home volume, so no sudo is needed. For
managed tools such as TokenTracker, use
[tool selection](AGENT-SETUP.md#optional-tools) instead of a global install.

### Playwright: Chromium, Firefox, and Edge

Install Playwright per project so the package and the downloaded browsers match:

```bash
cd /workspace
npm install --save-dev @playwright/test
npx playwright install --only-shell chromium firefox
```

Then add these projects to `playwright.config.ts`:

```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'edge', use: { ...devices['Desktop Edge'], channel: 'msedge' } },
  ],
});
```

Run `npx playwright test`, or narrow it with `--project=edge` or
`--project=firefox`. Chromium's headless shell and Playwright's patched Firefox
persist in `~/.cache/ms-playwright`. If you need full Chromium rather than the
shell, run `npx playwright install chromium`. Edge comes preinstalled in the
image and updates only with a rebuild, and images built with `WITH_EDGE=0`
should drop the Edge project.

For WebKit, build with `PLAYWRIGHT_BROWSERS=all`, install it with
`npx playwright install webkit`, and add a project for it. All of this targets
headless runs; headed browsers need a display setup that is not part of the
image. [Playwright browser documentation](https://playwright.dev/docs/browsers).

### Azure CLI and Azure DevOps

```bash
az version
az extension show --name azure-devops
az devops --help
```

The DevOps extension and PowerShell are installed system-wide in the image.
Rebuild and recreate existing sandboxes with `./sandbox update agent01` to get
`sandbox-azdo` and `pwsh`, retaining their named volumes. PowerShell installs
from [Microsoft's Debian package repository](https://learn.microsoft.com/en-us/powershell/scripting/install/install-debian).
The package installation targets amd64; ARM64 needs a separate PowerShell
installation recipe and has not been validated.

#### Set up Azure DevOps

On the host, choose how the sandbox should keep the PAT:

```bash
# Native Azure DevOps credential storage, using az devops login:
./sandbox tools agent01 setup azdo

# Persist AZURE_DEVOPS_EXT_PAT for new sandbox shells and agents instead:
./sandbox tools agent01 setup azdo --persist
```

Both modes ask for the default organization URL, for example
`https://dev.azure.com/contoso`. Normal setup then runs the native
`az devops login` PAT prompt and sets the default organization. It does not run
`az login`. Credentials stay in the sandbox's Azure CLI credential store
(typically `~/.azure/azuredevops/personalAccessTokens` when no keyring is
available). A successful native setup removes a previously saved environment
PAT so new sessions use native credential storage.

With `--persist`, the helper asks for a PAT with input hidden and saves it as
sandbox environment settings. It never calls either login command and does not
validate the PAT online. It only runs `az devops configure` to set the default
organization. Re-run the same command to replace the PAT and organization.
Empty input or cancellation preserves the previous PAT.

Persistent environment settings are plaintext in
`~/.config/sandbox-azdo/environment`, in the named home volume. The directory
has mode `0700` and the file has mode `0600`. Its first line contains the PAT;
the second contains the organization. This is data, not a script. Never copy
it into a repository. All agents sharing the sandbox user can read it.

New Bash login/interactive shells and PowerShell sessions with profiles enabled
load the saved PAT as `AZURE_DEVOPS_EXT_PAT`. The agent manager also loads it
for commands it launches, and `sandbox-azdo` reads it directly. An environment
variable explicitly supplied by the caller takes precedence. This is an
environment variable for the sandbox user, not Podman's container-wide
configuration or a host system variable. Arbitrary `podman exec` processes
that bypass these startup paths do not automatically load it.

Inside the sandbox after persistent setup, run:

```bash
sandbox-azdo devops project list
# Native CLI also uses the exported PAT and configured default organization:
az devops project list
```

`sandbox-azdo` uses the saved organization when `--organization` is omitted and
isolates the operation from Azure login state. Native `az` uses its usual
configuration and authentication precedence. Configure an optional default
project with `az devops configure --defaults project='My Project'`, or pass
`--project` explicitly to operations that support it. The isolated
`sandbox-azdo` command needs an explicit `--project`.

Remove the saved environment PAT from the host with:

```bash
./sandbox tools agent01 setup azdo --clear
```

This removes the environment file, including the helper's saved organization.
It leaves the native CLI's non-secret default organization and any native
credentials intact. For native credential removal, run inside the sandbox:
`az devops logout --organization https://dev.azure.com/contoso`.

Replacement and removal cannot change environments already inherited by running
processes. Close and reopen shells; restart agents, services and their tmux
servers as needed. In a retained shell, also run `unset AZURE_DEVOPS_EXT_PAT`
(Bash) or `Remove-Item Env:AZURE_DEVOPS_EXT_PAT` (PowerShell). A sandbox restart
clears all running processes while retaining the chosen persisted settings.

Windows PowerShell uses the same setup command through WSL; the PAT is entered
at the sandbox prompt, not on the command line:

```powershell
wsl.exe --distribution Ubuntu --cd /home/me/sandboxed-ai-agents --exec ./sandbox tools agent01 setup azdo --persist
```

#### Azure DevOps with an environment PAT

`AZURE_DEVOPS_EXT_PAT` authenticates Azure DevOps commands without `az login`,
`az devops login`, or device-code authentication. It does not authenticate Azure
Resource Manager commands. See [Microsoft's PAT documentation](https://learn.microsoft.com/en-us/azure/devops/cli/log-in-via-pat?view=azure-devops).

Inside the sandbox, if the variable is already exported, run:

```bash
sandbox-azdo devops project list --organization https://dev.azure.com/contoso
sandbox-azdo repos list --organization https://dev.azure.com/contoso --project 'My Project'
```

The helper fails if neither an environment PAT nor a saved PAT is available.
An explicitly empty variable also fails. It runs the `az`
operation with the supplied PAT and a fresh temporary Azure configuration, so
saved Azure logins cannot take precedence. Pass `--organization` (or `--org`)
on each invocation unless persistent setup supplied an organization, and
`--project` where the operation supports it. Native Azure configuration defaults
are not read by the isolated helper.

To enter a PAT without putting its value in Bash history, inside the sandbox:

```bash
read -rsp 'Azure DevOps PAT: ' AZURE_DEVOPS_EXT_PAT; printf '\n'
export AZURE_DEVOPS_EXT_PAT
sandbox-azdo devops project list --organization https://dev.azure.com/contoso
unset AZURE_DEVOPS_EXT_PAT
```

You can also use the standard CLI directly when you want the current session's
Azure configuration and defaults. Inside the sandbox:

```bash
: "${AZURE_DEVOPS_EXT_PAT:?Set a nonempty PAT in this session first}"
az devops project list --organization https://dev.azure.com/contoso
```

Direct `az` runs use `~/.azure` unless you override `AZURE_CONFIG_DIR`; the
helper's isolation and output redaction apply only to `sandbox-azdo`.

PowerShell is available inside the sandbox by running `pwsh -NoLogo -NoProfile`.
For a variable already set in that PowerShell process, the same command works:

```powershell
sandbox-azdo devops project list --organization https://dev.azure.com/contoso
Remove-Item Env:AZURE_DEVOPS_EXT_PAT -ErrorAction SilentlyContinue
```

If entering a replacement interactively in PowerShell 7, use
`$env:AZURE_DEVOPS_EXT_PAT = Read-Host 'Azure DevOps PAT' -MaskInput`.

#### Explicit host-to-sandbox PAT transport

On the Linux host, with `AZURE_DEVOPS_EXT_PAT` already exported:

```bash
./sandbox azdo agent01 --pat-env -- devops project list --organization https://dev.azure.com/contoso
./sandbox azdo agent01 --pat-env -- repos list --organization https://dev.azure.com/contoso --project 'My Project'
unset AZURE_DEVOPS_EXT_PAT
```

`--pat-env` is required even when the variable exists. The launcher checks
rootless Podman and checkout ownership before sending a JSON-encoded token
over stdin to the selected sandbox as UID 1000. It does not use a TTY, a shell
command containing the token, `podman --env`, SSH credential forwarding, or a
credential mount. Host Azure login state is untouched. Command stdin is reserved
for token transport, and output is buffered until completion (up to 64 MiB per
stream inside the sandbox).

On Windows, use Windows PowerShell or PowerShell 7 with WSL. Create and manage
the sandbox using the Linux launcher and rootless Podman in that WSL distro.
There is no native Windows launcher. With the PAT already in the Windows
PowerShell process environment, temporarily opt it into WSL forwarding:

```powershell
if ([string]::IsNullOrEmpty($env:AZURE_DEVOPS_EXT_PAT)) {
    throw 'Set a nonempty AZURE_DEVOPS_EXT_PAT in this PowerShell process first.'
}
$previousWslEnv = $env:WSLENV
try {
    $env:WSLENV = 'AZURE_DEVOPS_EXT_PAT/u'
    wsl.exe --distribution Ubuntu --cd /home/me/sandboxed-ai-agents --exec ./sandbox azdo agent01 --pat-env -- devops project list --organization https://dev.azure.com/contoso
    if ($LASTEXITCODE -ne 0) { throw "Azure DevOps command failed (exit $LASTEXITCODE)." }
} finally {
    $env:WSLENV = $previousWslEnv
    Remove-Item Env:AZURE_DEVOPS_EXT_PAT -ErrorAction SilentlyContinue
}
```

Replace the distro and checkout path with those used to create your sandbox.
[`WSLENV`](https://learn.microsoft.com/en-us/windows/wsl/filesystems#share-environment-variables-between-windows-and-wsl-with-wslenv)
contains the variable name only; `/u` forwards it toward WSL. This
example temporarily replaces the forwarding list and restores it afterwards.
Never interpolate the PAT into a `wsl.exe`, `podman`, or Azure CLI argument.

#### Lifetime, errors and validation

The host workflow makes the token available only to that `az` invocation and
its children. It does not make it available to subsequent commands, existing
agents, managed terminal sessions, or SSH sessions. Run the host command again
to reuse or replace the token. Inside a shell, an exported variable is inherited
by newly started children until you unset it or close the shell. Unsetting it
does not erase copies already inherited by running agents; stop those processes
to remove their access.

Without `setup azdo --persist`, the environment workflow does not save the PAT.
For operations, the helper creates a private temporary Azure
configuration under `/tmp`, disables Azure file logging and telemetry, and
removes that directory on normal completion, including native CLI failure.
The operation never writes the PAT to a credential file, profile, image, label,
or container configuration. Only explicit persistent setup writes the private
environment settings file described above. A forced kill can leave temporary Azure configuration behind;
no PAT is deliberately stored there. Do not put the PAT in source files,
shell profiles, transcripts or tracing output. Use only the explicit setup
workflow above for persistent environment storage.
Revoking a PAT in Azure DevOps is how to invalidate copies already in use.

The helper supports `devops`, `boards`, `repos`, `pipelines`, and `artifacts`
operations. It rejects login, logout, configure, debug, and verbose commands.
Native authentication and permission errors retain their exit code and diagnostic
text with the token redacted. Check PAT expiration, organization membership and
the scope required by the operation when Azure rejects a request. No fallback
login is attempted.

Offline tests use dummy PATs and fake Podman/Azure CLI executables. They cover
explicit opt-in, missing values, literal transport, isolation, native errors,
setup modes, replacement, cleanup and startup environment loading.
User-reported live validation: Azure DevOps setup completed successfully, and
a DevOps work item was changed from inside the sandbox. The report did not
specify the platform, setup mode, exact command or exit status. This confirms
a successful authenticated operation in the user's environment; it is separate
from the offline suite and was not independently executed by the agent.

Still to record: the read-only `devops project list` example on Linux and
Windows/WSL, and an expired or revoked test PAT failure. Record only the platform,
date, command without secrets, exit status and success/failure summary. Never
include a real PAT in test artifacts, issue comments or validation records.

## Check the toolchain

Run these from the **host**, with SSH configured:

```bash
./sandbox check agent01
# Optional: downloads browsers and packages, builds temporary test projects.
./sandbox check-full agent01
```

`check` verifies the installed tools and the explicit mount inventory.
`check-full` goes further: it builds and runs temporary .NET 9 and 10 apps,
installs an npm package, and launches headless Chromium, Firefox, and Edge if
present. It needs network access and leaves caches behind, though the temporary
projects are cleaned up. Edge is skipped when the image does not have it.


## Windows image and nested-container notes

Git attributes keep detected text files, including PowerShell scripts, in LF
format on Linux and Windows while leaving binary files unchanged. Existing
working files are not rewritten when the attributes change; configure your
editor to save with LF to avoid conversion warnings when editing older CRLF
files. The image recipes also normalize CRLF inputs.
If an older image fails with an `agent-entrypoint` missing-file
error, rebuild it and recreate only the failed container while retaining its
volumes. Changing global Git line-ending settings is unnecessary.

On Windows, nested Podman requires usable `/dev/fuse`, `/dev/net/tun`, and at
least 65536 subordinate UIDs and GIDs in the WSL2 machine. The launcher stages
its seccomp policy under
`~/.local/share/sandboxed-agents/<checkout-hash>/<profile-hash>.json` inside the
machine. Do not delete these profiles while containers reference them. Inner
images and volumes persist in sandbox home storage; runtime state is temporary.
To check an enabled capability without SSH, run from PowerShell:

```powershell
podman exec --user 1000:1000 --workdir /workspace agent01 /usr/local/bin/sandbox-podman-check
```
