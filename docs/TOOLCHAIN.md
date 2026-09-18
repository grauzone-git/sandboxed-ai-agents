# Development toolchain

[Back to the overview](../README.md) · [Agents and tools](AGENT-SETUP.md)

The shared image carries Debian 12 slim, Node.js 24 with npm, .NET SDKs 9 and
10, Git and the GitHub CLI, Azure CLI with the DevOps extension, OpenSSH, tmux,
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

Private GitHub repositories need `gh auth login` and `gh auth setup-git` before
the clone. A dedicated repository SSH credential created inside the sandbox
works as well. None of your host Git settings, SSH keys, or agent sockets are
mounted, which is the point.

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

Authenticate inside the sandbox using whatever flow your organization approves
before you touch Azure resources or DevOps projects.
[Azure DevOps CLI setup](https://learn.microsoft.com/en-us/azure/devops/cli/).
Login and configuration state goes to `~/.azure` in the named home. The DevOps
extension is installed system-wide in the image, so it is still there when you
attach an existing home volume. Both tools update through image builds.

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
