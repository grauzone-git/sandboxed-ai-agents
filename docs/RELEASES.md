# Executable releases and packages

As recorded through commit `76f2bab`, no executable release had been published
and no release tag had been pushed. This page describes what the release workflow in
`.github/workflows/release.yml` and the scripts under `packaging/` are written
to do. Command behavior is documented in [EXECUTABLE.md](EXECUTABLE.md); open
release gates are tracked in
[#50](https://github.com/grauzone-git/sandboxed-ai-agents/issues/50).

## Validation status

What has run locally: the offline suite (`./tests/run`, which is what
`make test` runs), including the offline package tests on Linux, and repeated
`packaging/build.py` builds that produced identical binaries. Regular CI
evidence is recorded through the #50 documentation commit `76f2bab`: it passed
there, and on the #49 commit `eed5f28`, including the native package tests on
Linux and Windows. That evidence covers those commits only. CI on a later
release candidate commit must be checked and recorded in
[#50](https://github.com/grauzone-git/sandboxed-ai-agents/issues/50). Native CI
results are recorded per commit in [HANDOVER.md](HANDOVER.md#evidence-so-far).
Every statement on this page that something has not run, been tested, or been
checked describes the state as recorded through `76f2bab`.

Before the first preview tag, these automated checks must pass on the release
candidate commit:

- regular CI on Linux and Windows, including the native package tests against
  the natively built binary
- the release workflow itself, which runs the tests again, rebuilds each target
  twice, and creates and verifies attestations before it publishes anything

The following checks need a published preview, so they cannot run before it.
They gate the first stable release instead:

- installing from a real GitHub release, and from a public registry if the
  packages are published there
- `gh attestation verify` by a user against the downloaded binaries, and a
  rebuild of the tag that reproduces the published `SHA256SUMS`
- a real Windows NuGet install that updates the user `PATH`, checked from a
  newly opened terminal
- running the Linux ARM64 binary, natively or through the npm package, on an
  ARM64 host

As recorded through `76f2bab`, none had run. The owner-run procedures are in [HANDOVER.md](HANDOVER.md),
and results are recorded in
[#50](https://github.com/grauzone-git/sandboxed-ai-agents/issues/50).

## Release contents

Pushing a `vX.Y.Z` or `vX.Y.Z-SUFFIX` tag runs the workflow. It builds the same
executable for `linux/amd64`, `linux/arm64`, and `windows/amd64` and attaches
these files to a GitHub release:

- `sandboxed-agents-linux-amd64`
- `sandboxed-agents-linux-arm64`
- `sandboxed-agents-windows-amd64.exe`
- `SHA256SUMS`, covering the three binaries
- `sandboxed-agents-X.Y.Z.tgz`, the npm package with all three binaries and `SHA256SUMS`
- `SandboxedAgents.X.Y.Z.nupkg`, the NuGet package with the Windows binary and `SHA256SUMS`

Build provenance attestations are created for the three binaries only.
`SHA256SUMS` and the two package files are not attested and have no published
checksum of their own. Package installation checks the bundled binary against
the `SHA256SUMS` copy inside the same package, which detects a damaged binary
but not a substituted package. To check a package's binary against the release,
compare it with the release's `SHA256SUMS` and attestation.

Installing or removing a package does not call Podman, create sandboxes, or
change SSH configuration, and leaves sandbox data alone. The executable has no
self-update command; [Upgrade and reinstall](EXECUTABLE.md#upgrade-and-reinstall)
covers what happens to existing sandboxes.

## Install a downloaded binary

This method needs only the
[host prerequisites](EXECUTABLE.md#host-prerequisites): Podman, set up as
described there, and OpenSSH. It does not need Node.js, NuGet, or PowerShell.

Download the matching binary and `SHA256SUMS` from the same release. On Linux,
the commands below install into `~/.local/bin`, which must already be on your
`PATH` for the last line to work; any other directory on `PATH` works too:

```bash
sha256sum --check --ignore-missing SHA256SUMS
install -D -m 0755 sandboxed-agents-linux-amd64 ~/.local/bin/sandboxed-agents
sandboxed-agents version
```

On Windows, compare the output of
`Get-FileHash -Algorithm SHA256 sandboxed-agents-windows-amd64.exe` with the
matching line in `SHA256SUMS`, case-insensitively, then copy the file as
`sandboxed-agents.exe` into a directory on `PATH`.

Keep the executable outside any directory you plan to bind as a workspace, and
do not install it under the name `sandbox`; see
[Shorter command name](EXECUTABLE.md#shorter-command-name) for an alias and the
`/usr/bin/sandbox` conflict.

To verify provenance with the GitHub CLI:

```bash
gh attestation verify sandboxed-agents-linux-amd64 --repo grauzone-git/sandboxed-ai-agents
```

Repeat with the exact filename for another platform. Checksums detect accidental
changes; provenance verification identifies the workflow run that built the
binary. The workflow is written to create attestations with `actions/attest`
and verify each one with `gh attestation verify` before publishing, using
[GitHub's artifact attestation support](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
As recorded through `76f2bab`, that job had not run.

To upgrade, repeat these steps with the newer release and replace the file.

## npm

The npm package needs Node.js 18 or newer on the host in addition to Podman and
OpenSSH. Its command is a Node.js launcher that runs the bundled binary.

```bash
npm install --global ./sandboxed-agents-0.1.0.tgz
sandboxed-agents version
```

Use the downloaded package's actual version. npm places the command in its
global command directory.

The executable refuses a workspace bind that contains, or lies inside, the npm
command it was launched through, that command's `.cmd` or `.ps1` shims, or its
launch link, whether in the global command directory or a project's
`node_modules/.bin`. A subdirectory of that command directory that holds none of
these paths is not refused. The check fails before Podman is called. Install
globally, outside any directory you plan to bind.

The package's `postinstall` step selects the binary for the current platform,
checks it against `SHA256SUMS`, and on Linux sets its executable bit. A mismatch
fails installation. The launcher repeats the checksum check on every invocation,
then forwards arguments, standard streams, and exit status. Supported targets
are Linux x64, Linux ARM64, and Windows x64. The Linux ARM64 binary is only
cross-built and compared across repeated builds; as recorded through `76f2bab`,
running it through the npm package on an ARM64 host had not been tested. On other platforms, including
Windows ARM64, installation fails with an unsupported platform error.

Do not disable install scripts. Skipping them does not bypass the checksum check,
because the launcher also performs it, but on Linux the binary may then lack its
executable bit. As recorded through `76f2bab`, whether npm preserves that bit
from the package archive had not been checked.

To upgrade, install the newer package the same way. To remove the command:

```bash
npm uninstall --global sandboxed-agents
```

## NuGet on Windows

The NuGet package is a command package, not a project library. Do not add it as a
`PackageReference`. Modern NuGet project installs do not run install scripts, as
described in
[Microsoft's migration documentation](https://learn.microsoft.com/en-us/nuget/consume-packages/migrate-packages-config-to-package-reference),
so the package ships an explicit PowerShell setup script instead.

Extracting the package needs `nuget.exe`. Run its setup scripts in PowerShell 7
(`pwsh`), the version the rest of this repository targets on Windows. After
setup, the command itself needs only Podman and OpenSSH.

Do not change the machine or user execution policy for these scripts. If
PowerShell refuses to run `install-command.ps1` or `remove-command.ps1` because
of the execution policy, first check the package's binary against the release as
described in [Release contents](#release-contents), then relax the policy for
the current window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

The setting ends when the window closes. Run the setup script in that same
window, not through a new `pwsh -File` process, so its change to the session's
`PATH` stays visible. A policy set by Group Policy cannot be overridden this
way; ask whoever manages the machine. As recorded through `76f2bab`, whether
scripts extracted from a downloaded package are blocked by the default policy
had not been checked on a real host.

Place the downloaded `.nupkg` in a local directory used as a package source:

```powershell
nuget install SandboxedAgents -Version 0.1.0 -Source C:\Downloads\sandbox-packages -OutputDirectory .\packages -NonInteractive
& .\packages\SandboxedAgents.0.1.0\tools\install-command.ps1
sandboxed-agents version
```

Substitute the release version, and add `-Prerelease` for a version with a
prerelease suffix. `install-command.ps1` checks the bundled binary against
`SHA256SUMS`, copies it to `%LOCALAPPDATA%\Programs\sandboxed-agents\sandboxed-agents.exe`,
checks the copy again, and adds that directory to the user `PATH` and to the
current session's `PATH`. Other terminals must be reopened to see the change. No
administrator rights are needed. Outside Windows, the script runs only with
`-NoPathUpdate`.

The user `PATH` is edited as stored in the registry: entries such as
`%USERPROFILE%\bin` stay unexpanded, and the value keeps its registry type.

`-InstallDirectory PATH` selects another directory. `-NoPathUpdate` leaves `PATH`
unchanged. The two options are independent; use both for a directory whose
`PATH` entry you manage yourself.

To upgrade, extract the newer package and run its `install-command.ps1`. It
replaces the installed binary.

To remove the command, run the removal script from the package that installed
it, with the same options used during installation:

```powershell
& .\packages\SandboxedAgents.0.1.0\tools\remove-command.ps1
```

Removal refuses to delete an installed binary whose checksum differs from the
package, for example a newer version; use that version's package instead. Unless
`-NoPathUpdate` is given, it removes the directory from the user and session
`PATH` even when no binary is present. If the directory is not in the user
`PATH`, the registry value is left unchanged. Deleting the extracted package does not
remove the installed command.

## Prepare a release

Do not tag a preview until the automated checks in
[Validation status](#validation-status) pass on the release candidate commit.
Do not tag a stable release until the gates in
[HANDOVER.md](HANDOVER.md#gates) that precede it hold, including the owner-run
Linux and Windows validation of a published preview. The checkout launchers stay
in place until every handover condition in
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36) is met, and
are removed no earlier than the release after the first stable release.

Commit release notes at `docs/releases/vX.Y.Z.md` before tagging. The workflow
fails without that file. For every `v0.*` tag
and every tag with a prerelease suffix, the notes need a nonempty
`## Omitted commands` section naming unsupported commands, or stating explicitly
that none are omitted. Also record known platform limitations.

The notes file is fixed when the tag is pushed and becomes the release body, but
manual validation runs against the published release, so a release's notes
cannot contain its own validation results. Record each release's results in
[#50](https://github.com/grauzone-git/sandboxed-ai-agents/issues/50) as they
are produced, and copy them into the next release's notes under a section
naming the release they validate. Do not describe validation as done in notes
written before it ran.

The stable release is tagged on the same commit as the last fully validated
preview, with passing regular CI on that commit, so both report the same
`commit` and `assets` values in `sandboxed-agents version`. Its notes must
therefore already be in that commit: add them together with the final
preview's notes, citing earlier previews' results from #50 only as history
for those releases, then validate the final preview and record the results in
#50 and in the notes of the release after the stable one. If that validation
fails, never edit or retag the failed preview; fix it in a new commit with a
new preview version and notes file and updated stable notes, and validate
again.
[HANDOVER.md](HANDOVER.md#release-and-removal-sequence) gives the full
sequence.

The workflow publishes every `v0.*` tag and every tag with a suffix as a GitHub
prerelease. Under the current workflow, a stable release therefore needs a tag
of `v1.0.0` or later without a suffix. The first stable release requires full
command parity with the scripts.

### What the workflow does

As recorded through `76f2bab`, the workflow had not run. It is written to:

1. Check the tag format and release notes.
2. Run `go test ./...` and `make test` on Linux.
3. Run `packaging/build.py`, which builds each target twice and fails if the
   two builds differ, then writes `SHA256SUMS`.
4. In a separate job that alone holds the attestation permissions, attest the
   three binaries and verify each attestation.
5. On Windows, stage packages with `packaging/prepare.py`, which refuses
   binaries that do not match `SHA256SUMS`, pack the npm and NuGet packages,
   run `tests/test-packages.py` against the release binary, and install both
   packages and run `sandboxed-agents version` from outside the checkout.
6. On Linux, install the npm package and run `sandboxed-agents version` from
   outside the checkout.
7. Create the GitHub release with the binaries, `SHA256SUMS`, and both packages,
   only after every earlier job succeeds.

Attestation requires a repository plan that supports it and the permissions
declared in the workflow.

Regular CI does not publish anything. Its executable job is configured to run
`tests/test-packages.py` against a natively built binary on both Ubuntu and
Windows, which includes the npm launch path protection test and, on Windows,
the registry `PATH` test. See
[Validation status](#validation-status) for what has run.

### Reproducible builds

`packaging/build.py` requires Go 1.27.1 exactly and fails before building
anything with another version. It ignores persisted `go env -w` settings and
clears `GOFLAGS` and `GOEXPERIMENT`, and builds with `GOTOOLCHAIN=local`,
`CGO_ENABLED=0`, `-trimpath`, `-buildvcs=false`, an empty build ID, and the
version and commit embedded. Rebuilding a tag to identical bytes requires the
same Go toolchain and unchanged source.

### Build locally

With Go 1.27.1 on `PATH` (not fetched automatically), Python 3.9 or newer, and
Node.js:

```bash
python3 -B packaging/build.py --version 0.1.0 --commit "$(git rev-parse HEAD)" --output /tmp/sandbox-release
python3 -B packaging/prepare.py --version 0.1.0 --artifacts /tmp/sandbox-release --output /tmp/sandbox-packages
npm pack /tmp/sandbox-packages/npm --ignore-scripts
```

`--version` takes a semantic version without the leading `v`; `--commit` takes a
full commit hash. `prepare.py` fails if the output directory already contains
`npm` or `nuget`, so use a fresh directory. On Windows, pack the staged NuGet
package with `nuget pack PATH\nuget\sandboxed-agents.nuspec`. Neither script
publishes anything.

### Registry publication

GitHub release packages can be installed directly without a public registry.
Publishing the same files to npm or nuget.org is a separate maintainer step that
the workflow does not perform. It requires reserving the package names
`sandboxed-agents` on npm and `SandboxedAgents` on nuget.org and configuring
credentials. Whether either name is available or reserved is not recorded in
this repository.

```bash
npm publish sandboxed-agents-0.1.0.tgz --access public --tag preview
nuget push SandboxedAgents.0.1.0.nupkg -Source https://api.nuget.org/v3/index.json -ApiKey YOUR_NUGET_API_KEY
```

Use an npm dist-tag such as `preview` for `0.x` and prerelease packages. Publish
the files attached to the GitHub release; do not rebuild them. Authenticode
signing of the Windows executable is a planned follow-up and is not performed.
