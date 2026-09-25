# Executable handover

This page tracks what must happen before the checkout launchers can be retired
in favor of the standalone executable described in [EXECUTABLE.md](EXECUTABLE.md).
The requirements come from
[#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36) and
[#50](https://github.com/grauzone-git/sandboxed-ai-agents/issues/50). Manual
validation on real Linux and Windows machines is owned by @grauzone-git.

Issue #50 has four parts: documentation, manual validation, the first stable
release, and script removal. This page and the related guides are the documentation
part. The owner has reported in #50 that Linux host prerequisites are set up;
that is an owner report, not runtime validation (see
[Release and removal sequence](#release-and-removal-sequence)). As recorded
through `76f2bab`, no manual validation procedure had run, no release had been
published, the stable release and
script removal had not started, and every gate below was open. A
gate closes only with a recorded owner run or CI result for the release
candidate. Offline test results do not close a runtime gate.

## Evidence so far

Every runtime validation status on this page, such as "not run", "has not been
run", "unverified", "not verified", or "exercised offline only", is a snapshot
recorded through `76f2bab`. Results produced after that commit are recorded in
#50, not on this page. This does not mean that any validation has run since,
and it does not change the gates or what the procedures require. The "not run"
entries in the [validation record template](#validation-record-template) are
defaults to fill in, not status.

| Change | Commit | Result |
| --- | --- | --- |
| #45 image updates | `dfd0d28` | Native CI run [36129892990](https://github.com/grauzone-git/sandboxed-ai-agents/actions/runs/36129892990) passed |
| #47 services, forwarding, tool setup | `0de5ab8f240485b6065b560e780c344968c3ae58` | Native CI run [36130698062](https://github.com/grauzone-git/sandboxed-ai-agents/actions/runs/36130698062) passed |
| #48 adoption | `d0394f2e996b4ce3c5d407bdc635860e410a4a1f` | Native CI run [36131589663](https://github.com/grauzone-git/sandboxed-ai-agents/actions/runs/36131589663) passed |
| #49 release packaging | `eed5f28ad62b3af96699a645943f132c8b9b2802` | Native CI run [36132018293](https://github.com/grauzone-git/sandboxed-ai-agents/actions/runs/36132018293) passed all six jobs |
| #50 handover documentation | `76f2babe7058e7a9abddb88dc914fb0615de93b2` | Native CI run [36133905583](https://github.com/grauzone-git/sandboxed-ai-agents/actions/runs/36133905583) passed all six jobs |

The #49 run passed the offline `make test` job; the native Linux and Windows
executable jobs, each running `go test ./...`, all four shared host contract
suites, and `tests/test-packages.py` against the natively built binary; and the
three cross-builds. Repeated local `packaging/build.py` builds also produced
byte-identical binaries. As recorded through `76f2bab`, no tag had been pushed,
the release workflow had not run, and nothing had been attested or published to
GitHub, npm, or nuget.org.

A passing run is evidence for its commit and the tests configured at that
commit only. The evidence above is recorded through `76f2bab`. The behavior
contract gate needs a passing run on the commit selected for tagging; CI on a
later release candidate commit must be checked and recorded in #50.

As recorded through `76f2bab`, nothing had been run against real Podman, real
SSH, real Azure sign-in, or a real package installation. The Linux ARM64 binary
had not run on an ARM64 host, and the Windows NuGet `PATH` update had not been
checked in a new terminal.

## Gates

| Gate | Evidence required | Status |
| --- | --- | --- |
| Behavior contract | Native CI on Linux and Windows passes `go test ./...`, all four shared host contract suites, and the package tests against the binary, on the release candidate commit | Open; recorded passes run through `76f2bab`, and a pass on the tagged commit must be recorded in #50 |
| Linux runtime | Owner record: build, `up`, SSH, named volumes, binds, protection refusals, `update`, and update rollback on a real rootless Podman host | Open |
| Windows runtime | Owner record: the same on Windows 11 x64 with a rootless WSL2 Podman 6 machine | Open |
| Adoption | Owner record of `adopt` on real checkout-owned sandboxes on Linux and Windows, keeping data and SSH access | Open |
| Services and Azure | Owner record of `service`, `forward`, and `tools setup azure --interactive` with real OAuth, on Linux and Windows | Open |
| Package installation | Owner record from a published prerelease: direct binary, npm on Linux and Windows, and NuGet on Windows, including the `PATH` change seen from a new terminal, and removal | Open |
| Release provenance | `gh attestation verify` succeeds for each published binary, and a rebuild of the tag reproduces the published `SHA256SUMS` | Open |
| First stable release | A stable release with full command parity with the scripts, tagged on the same commit as the last fully validated prerelease, with passing native CI on that commit; see [Command parity](#command-parity) and [Release and removal sequence](#release-and-removal-sequence) | Open |
| Script removal | The release after the first stable release; see [Release and removal sequence](#release-and-removal-sequence) | Open |

The checkout launchers stay in the repository, and AGENT.md keeps `./sandbox` as
the entry point, until the script removal gate is reached.

## Release and removal sequence

1. Get a passing native CI run on the release candidate commit and record its
   run ID in #50. A run on an earlier commit, such as `76f2bab`, does not
   count.
2. Tag a `v0.x` prerelease with notes at `docs/releases/vX.Y.Z.md`, as described
   in [RELEASES.md](RELEASES.md#prepare-a-release). The current source
   implements every command in its help, so the notes' `## Omitted commands`
   section states that none are omitted. The release workflow creates and
   verifies attestations before publishing. The notes are fixed at tagging
   time, so they cannot contain this prerelease's validation results.
3. Run the procedures on this page on Linux and Windows against that
   prerelease, installed from its published files. Record results in #50 as
   they are produced, and copy them into the next release's notes. Fix failures
   and repeat with a new prerelease.
4. Once a prerelease has passed every procedure and command parity is
   confirmed, prepare the final prerelease commit. It adds two new release
   notes files: `docs/releases/vX.Y.Z.md` for the final prerelease, with a
   version not used before, and `docs/releases/v1.0.0.md`, or the chosen
   stable version, for the stable release. The stable notes must exist before
   either tag, because the stable tag goes on this same commit. Both notes may
   cite earlier prereleases' results from #50 only as history, naming the
   release each result covers; those results do not validate this commit. The
   stable notes must not describe the final prerelease as validated; they say
   that its validation, on the same commit, is recorded in #50.
5. Get passing native CI on that commit, tag the final prerelease on it, and
   run every procedure again against it, installed from its published files.
   Record the results in #50 as they are produced. This run is the only
   validation of the commit. If anything fails, leave the failed prerelease,
   its tag, and its notes as they are. Make a new final prerelease commit with
   the fix, a new notes file for a new prerelease version, and the stable
   notes updated to name that version and to list the failed prerelease's
   results as history. Then repeat this step on the new commit.
6. Tag the first stable release, `v1.0.0` or later without a suffix, on the
   same commit as the final prerelease. `sandboxed-agents version` from both
   releases must show the same `commit` and `assets` values; only the version
   differs, so the binaries and `SHA256SUMS` differ. Copy the final
   prerelease's results, and any validation of the stable release itself, from
   #50 into the next release's notes.
7. In the next release, not the stable one, remove the checkout launchers:
   `sandbox`, `sandbox.ps1`, the host scripts under `src/host/` that only they
   run, and the script-only tests, and rewrite the Linux and Windows quick
   guides for the executable. `src/container/` stays because the executable
   bundles it. In the same change, switch the README quickstart to the
   executable, point the host contract at the binary by default, and rewrite
   the AGENT.md and CONTEXT.md rules that name `./sandbox` as the entry point,
   checkout-path ownership, and `src/host/workspace.py` protected paths. The
   in-container manager path `/usr/local/lib/sandbox-agents/manager.cjs` stays
   load-bearing.

Implementation CI evidence is recorded in [Evidence so far](#evidence-so-far).
The owner has reported in #50 that the Linux host prerequisites are set up on
an Omarchy host. That is an owner report, not runtime validation. As recorded
through `76f2bab`, no release had been published, and none of the runtime
procedures on this page had run.

## Command parity

Compare `./sandbox --help`, `.\sandbox.ps1 --help`, and
`sandboxed-agents --help` from the same commit. In the current source, every
script command form has an executable equivalent with the same name-first
grammar. The executable adds `version`, `adopt`, and `--cpus`/`--memory` on
Linux. Every command in `sandboxed-agents --help` is implemented; the executable
is still a preview until the gates close.

Differences and their decisions:

- `remove --ssh-config`: the Linux `./sandbox` accepts it as a no-op for older
  callers, and `sandbox.ps1` rejects it. The executable accepts it as the same
  legacy no-op, alone or with `--volumes`, since #47. Removal always deletes the
  sandbox's managed SSH files after a successful removal, with or without the
  flag.
- The scripts are owned by a checkout path, the executable by a controller
  group. This is intended and is what `adopt` bridges.
- `SANDBOX_PYTHON` is a launcher prerequisite of `sandbox.ps1` only, telling it
  where Python is. The executable needs no Python and ignores the variable.

Parity has been checked by reading help text, source, and offline tests only.

## Validation safety

- Never use `agent01`, `agent02`, or any sandbox, volume, image tag, SSH file,
  or port somebody works with. The procedures use owner groups `handover-check`
  and `handover-other`, images `localhost/agent-sandbox:handover`,
  `localhost/agent-sandbox:handover-broken`, and
  `localhost/agent-sandbox:handover-legacy`, sandboxes `handover01` to
  `handover04` and `handover-adopt`, SSH ports 2295 to 2299, and local forward
  port 17680.
- Run the preflight before changing anything. If it finds any of these names,
  SSH state for them, or a port in use, stop. Do not delete or reuse what it
  found; it may belong to someone else or to an unfinished earlier run, and
  `up` silently reuses volumes owned by the same group. Record it, and either
  have its owner clean it up or choose unused names and change every command.
- Keep all files under one disposable directory, `~/handover` on Linux and
  `$HOME\handover` on Windows. It must not exist before the run.
- Use a fresh clone of the repository, pinned to the commit given below, as the
  legacy checkout. `adopt --from` and `adopt --all --from` only select
  sandboxes owned by that exact path, so sandboxes of your real checkout stay
  out of scope. Never run `adopt --all` against a real checkout path.
- Never run the executable or the checkout launchers with `sudo`.
- The checkout launcher and the executable both write `~/.ssh/config`. Save a
  copy before starting and keep it until the comparison at the end is clean.
- Run the procedure in a new terminal and close it at the end, so the variables
  it sets do not reach other work. The final cleanup also unsets them.
- Record `sandboxed-agents version` for every run. The asset hash identifies
  the bundled image recipe.

None of the procedures below has been run. They follow the current CLI grammar,
but the expected results come from the source and the offline tests, not from
observation. Where a step may not behave as described, the text says so.

## Linux procedure

Run in Bash as your normal user. Besides the
[host prerequisites](EXECUTABLE.md#host-prerequisites), the adoption steps need
Git and the checkout launcher's own requirements from the
[README](../README.md#requirements): Bash, Python 3.9 or newer, and OpenSSH.

### Preflight

Each check should print its "no ..." or "free" line and nothing else. Stop if
it does not; see [Validation safety](#validation-safety).

```bash
podman version
podman info --format '{{.Host.Security.Rootless}}'
ssh -V
sandboxed-agents version
env | grep -E '^(SANDBOX_|CONTAINER_)' || echo 'no SANDBOX_ or CONTAINER_ variables'
podman ps --all --format '{{.Names}}' | grep '^handover' || echo 'no handover containers'
podman volume ls --format '{{.Name}}' | grep '^handover' || echo 'no handover volumes'
for tag in handover handover-broken handover-legacy; do
  podman image exists "localhost/agent-sandbox:$tag" && echo "localhost/agent-sandbox:$tag exists" || echo "no :$tag image"
done
ls -d "${XDG_STATE_HOME:-$HOME/.local/state}"/sandboxed-agents/ssh/handover* 2>/dev/null || echo 'no executable SSH state'
ls -d ~/.ssh/sanboxed-agents/handover* 2>/dev/null || echo 'no legacy SSH state'
grep -n handover ~/.ssh/config || echo 'no handover lines in ~/.ssh/config'
ss -ltn | grep -E ':(229[5-9]|17680) ' || echo 'ports free'
test -e ~/handover && echo '~/handover exists' || echo 'no ~/handover'
```

Also record whether `podman image exists docker.io/library/debian:12-slim`
succeeds; the rollback step pulls that image, and cleanup removes it only if it
was not there before. Then save the starting state:

```bash
mkdir ~/handover
cp ~/.ssh/config ~/handover/ssh-config.before
podman image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' > ~/handover/images.before
```

If `~/.ssh/config` does not exist, skip the copy and record that. At the end,
the file should again be absent or contain nothing from this run.

### Build, create, and SSH

```bash
export SANDBOX_CONTROLLER=handover-check
export SANDBOX_IMAGE=localhost/agent-sandbox:handover
sandboxed-agents build
sandboxed-agents handover01 up --agents codex --ssh-port 2299 --ssh-config
sandboxed-agents list
sandboxed-agents handover01 check
ssh handover01 true
sandboxed-agents handover01 fingerprint
podman exec --user 1000:1000 handover01 sh -c 'date > /workspace/handover-marker'
podman volume ls --filter label=io.sandboxed-agents.project=handover-check
```

Expected: three volumes, `handover01-workspace`, `handover01-home`, and
`handover01-sshd`, labelled with `handover-check`.

### Volumes, binds, and protection

Plain `remove` keeps the volumes, and `up` reuses them:

```bash
sandboxed-agents handover01 remove
podman volume ls --filter label=io.sandboxed-agents.project=handover-check
sandboxed-agents handover01 up --agents codex --ssh-port 2299 --ssh-config
podman exec --user 1000:1000 handover01 cat /workspace/handover-marker
ssh handover01 true
```

A bound directory with a space in its path:

```bash
mkdir -p ~/handover/'bind workspace'
sandboxed-agents handover02 up ~/handover/'bind workspace' --agents codex --ssh-port 2298
sandboxed-agents handover02 check
podman exec --user 1000:1000 handover02 sh -c 'date > /workspace/bind-marker'
ls -l ~/handover/'bind workspace'/bind-marker
```

Another controller group must not be able to touch these sandboxes. Both
commands should fail with an ownership error and change nothing:

```bash
SANDBOX_CONTROLLER=handover-other sandboxed-agents handover01 remove --volumes
SANDBOX_CONTROLLER=handover-other sandboxed-agents handover02 stop
podman ps --all --filter name=handover
```

A bind that contains the executable must be refused before Podman creates
anything. This needs a directly installed binary, not the npm launcher:

```bash
mkdir -p ~/handover/bin
cp "$(command -v sandboxed-agents)" ~/handover/bin/sandboxed-agents
~/handover/bin/sandboxed-agents handover03 up ~/handover/bin --agents codex --ssh-port 2297
podman ps --all --filter name=handover03
```

Expected: an error naming the conflicting path and suggesting a global
install, and no `handover03` container.

### Update and rollback

```bash
sandboxed-agents handover01 update
podman exec --user 1000:1000 handover01 cat /workspace/handover-marker
ssh handover01 true
sandboxed-agents handover01 agents list
```

There is no command that forces an update to fail. As recorded through
`76f2bab`, the following candidate had not been tried. It points `update --no-build` at a stock Debian image,
which has no SSH server, so the replacement should fail the readiness check
after the original has been stopped and renamed:

```bash
podman inspect handover01 --format '{{.Id}}'
podman pull docker.io/library/debian:12-slim
podman tag docker.io/library/debian:12-slim localhost/agent-sandbox:handover-broken
SANDBOX_IMAGE=localhost/agent-sandbox:handover-broken sandboxed-agents handover01 update --no-build
podman ps --all --filter name=handover01 --format '{{.Names}} {{.ID}} {{.Status}}'
podman exec --user 1000:1000 handover01 cat /workspace/handover-marker
ssh handover01 true
```

Count this as a rollback run only if the error says the replacement container
did not become ready. Then `handover01` should have its original ID, be running,
keep the marker and SSH access, and no `handover01-update-backup-*` container
should remain. Any other error means replacement was never reached; record it
and leave the rollback row open.

Optional, nested Podman:

```bash
sandboxed-agents handover04 up --agents codex --ssh-port 2295 --capabilities podman
sandboxed-agents handover04 check-full
sandboxed-agents handover04 remove --volumes
```

### Services and forwarding

```bash
sandboxed-agents handover01 tools enable tokentracker
sandboxed-agents handover01 service tokentracker status
sandboxed-agents handover01 forward tokentracker 17680
```

Leave `forward` running. In a second terminal:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:17680/
```

Record the status code, stop `forward` with Ctrl+C, and repeat the `curl`,
which should now fail to connect. Then confirm that a sandbox without SSH setup
is refused with the `ssh-config --install` hint:

```bash
sandboxed-agents handover02 forward tokentracker 17680
```

### Azure browser sign-in

Use an Azure account you may use for testing.

```bash
sandboxed-agents handover01 tools setup azure --interactive --tenant TENANT --subscription SUBSCRIPTION
sandboxed-agents handover01 shell
```

Inside the sandbox:

```bash
az account show --query '{tenant:tenantId, subscription:id, user:user.name}'
exit
```

Record whether the browser opened, whether sign-in completed, and the account
shown. Run setup again and press Ctrl+C before finishing sign-in, then run
`echo $?`; expected is a cancellation message and 130. Optionally repeat with
`--tenant TENANT --tenant-only`, and with `--cloud AzureChinaCloud` if you have
such an account.

### Adoption

Create a checkout-owned sandbox from a disposable clone pinned to commit
`b9ee4a0bc35b6b2d7ccb1ff2363cbe9cd59a5ebe`, an ancestor of the current source
that contains both checkout launchers and no Go executable. Do not use the
default branch, which will lose them when the scripts are removed. The clone's
launcher ignores `SANDBOX_CONTROLLER` and owns the sandbox by the clone's path. It builds into
its own image tag so that the executable's `handover` image is not replaced.

```bash
git clone https://github.com/grauzone-git/sandboxed-ai-agents.git ~/handover/legacy-checkout
git -C ~/handover/legacy-checkout checkout --detach b9ee4a0bc35b6b2d7ccb1ff2363cbe9cd59a5ebe
git -C ~/handover/legacy-checkout rev-parse HEAD
SANDBOX_IMAGE=localhost/agent-sandbox:handover-legacy ~/handover/legacy-checkout/sandbox build
SANDBOX_IMAGE=localhost/agent-sandbox:handover-legacy ~/handover/legacy-checkout/sandbox handover-adopt up --agents codex --ssh-port 2296 --ssh-config
podman inspect handover-adopt --format '{{.ImageName}} {{.Id}}'
podman exec --user 1000:1000 handover-adopt sh -c 'date > /workspace/handover-marker'
ssh handover-adopt true
cp ~/.ssh/config ~/handover/ssh-config.legacy
sandboxed-agents list
```

Record the commit that `rev-parse` prints. `podman inspect` should show the
`handover-legacy` tag; if it does not, the pinned launcher did not use
`SANDBOX_IMAGE`, so record that. `SANDBOX_CONTROLLER` is still `handover-check`
from the earlier export.

`list` should end with a line such as
`handover-adopt is checkout-owned. To adopt: sandboxed-agents handover-adopt adopt --from '/home/YOU/handover/legacy-checkout'`.

First check a refusal with an absolute path that is not the owner; it should
fail without stopping anything:

```bash
sandboxed-agents handover-adopt adopt --from ~/handover/not-the-checkout
podman ps --all --filter name=handover-adopt --format '{{.Names}} {{.ID}} {{.Status}}'
```

Expected: `container handover-adopt is not owned by this configuration`. The
error `--from must be the absolute checkout owner path` means the path was not
accepted as absolute and the ownership comparison was never reached; record it
and leave the row open.

#### Adoption rollback attempt

This must happen before the real adoption, because an adopted sandbox cannot be
returned to its checkout. It is an attempt, not a verified procedure: adoption
rollback is covered by offline tests only, and the interrupt may miss the
window.

The rollback window opens when the image build output ends and the original
container is stopped and renamed. It closes when adoption commits: after the
replacement is ready, presents the pinned host key, and authorizes the migrated
client key, the SSH files are moved and `~/.ssh/config` is rewritten. Until
then, an interrupt or failure removes the replacement, restores the legacy SSH
files, and restarts the original. After it, the adoption is kept. As recorded
through `76f2bab`, how long the window lasts had not been measured, so there is
no reliable moment to press
Ctrl+C. An interrupt during the build ends the command before anything is
replaced, which tests nothing.

Run the exact command that `list` printed, and press Ctrl+C as soon as the
build output ends. Then check:

```bash
podman ps --all --filter name=handover-adopt --format '{{.Names}} {{.ID}} {{.Status}}'
podman inspect handover-adopt --format '{{index .Config.Labels "io.sandboxed-agents.project"}} {{index .Config.Labels "io.sandboxed-agents.adopted-from"}}'
podman events --stream=false --since 10m --filter type=container --format '{{.Time}} {{.Status}} {{.Name}}' | grep handover-adopt
podman exec --user 1000:1000 handover-adopt cat /workspace/handover-marker
ssh handover-adopt true
ls -la ~/.ssh/sanboxed-agents/handover-adopt
diff ~/handover/ssh-config.legacy ~/.ssh/config
```

Count it as a rollback only if the events show a new `handover-adopt` container
created and then removed, and the container has its original ID, is still
owned by the clone path with no `adopted-from` label, keeps the marker, SSH
access, and legacy SSH files, and `~/.ssh/config` matches the copy. Record the
error text. If the interrupt came too early, you may try once more; otherwise
record what happened and leave the row as "not verified".

If the adoption completed, the sandbox is adopted and SSH access should work;
skip the next step's `adopt` command and continue with its checks. If the error
says the stopped backup could not be removed, the adoption was also kept: record
the backup name it gives, check that it is the stopped checkout-owned
container, remove it with `podman rm NAME` (not `--volumes`), and continue.

#### Adopt

Run the exact command that `list` printed, without interrupting it, then check:

```bash
podman inspect handover-adopt --format '{{index .Config.Labels "io.sandboxed-agents.project"}} {{index .Config.Labels "io.sandboxed-agents.adopted-from"}}'
for volume in handover-adopt-workspace handover-adopt-home handover-adopt-sshd; do
  podman volume inspect "$volume" --format '{{.Name}} {{index .Labels "io.sandboxed-agents.project"}}'
done
podman exec --user 1000:1000 handover-adopt cat /workspace/handover-marker
ssh handover-adopt true
grep -n -i include ~/.ssh/config
ls -la ~/.ssh/sanboxed-agents/handover-adopt
~/handover/legacy-checkout/sandbox handover-adopt check
sandboxed-agents handover-adopt update
ssh handover-adopt true
```

Expected from the source: owner `handover-check` with `adopted-from` set to the
clone path; volumes still labelled with the clone path; the marker present;
`ssh` working without a new host key prompt; the clone's `Include` line for
this sandbox gone and the executable's `Include` line present; the legacy SSH
directory for this sandbox emptied or gone; the clone's launcher refusing the
container; `update` succeeding.

### Cleanup

`remove --volumes` accepts the adopted sandbox's checkout-labelled volumes
because its container still carries `adopted-from`.

`handover03` exists only if the executable bind refusal failed, and
`handover04` only if the optional nested Podman step stopped early. The
preflight showed both names free, so either one comes from this run; record it
before the loop removes it. `SANDBOX_CONTROLLER` is still `handover-check`, so
`remove` refuses anything that group does not own. `--ignore` skips image tags
that this run did not create, for example when the rollback step was skipped.

```bash
sandboxed-agents handover01 remove --volumes
sandboxed-agents handover02 remove --volumes
sandboxed-agents handover-adopt remove --volumes
for name in handover03 handover04; do
  podman container exists "$name" && sandboxed-agents "$name" remove --volumes
done
podman ps --all --format '{{.Names}}' | grep '^handover' || echo 'no handover containers'
podman volume ls --format '{{.Name}}' | grep '^handover' || echo 'no handover volumes'
ls -d "${XDG_STATE_HOME:-$HOME/.local/state}"/sandboxed-agents/ssh/handover* 2>/dev/null || echo 'no executable SSH state'
ls -d ~/.ssh/sanboxed-agents/handover* 2>/dev/null || echo 'no legacy SSH state'
podman image rm --ignore localhost/agent-sandbox:handover-broken localhost/agent-sandbox:handover localhost/agent-sandbox:handover-legacy
podman image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' | diff ~/handover/images.before -
diff ~/handover/ssh-config.before ~/.ssh/config
unset SANDBOX_CONTROLLER SANDBOX_IMAGE
```

Remove `docker.io/library/debian:12-slim` only if the preflight recorded it as
absent. Remove any other image that the image `diff` shows as new only after
checking its name. After the last managed sandbox is removed, the executable's
`Include` line should be gone. The checkout launcher may leave a `Host *` line
from its own `Include` block.

Record what the SSH config `diff` shows before deleting anything:

- If it shows no difference, delete the directory with `rm -rf ~/handover`.
  This also deletes the bound directory, which `remove` kept, and the clone.
- If it shows differences, copy `~/handover/ssh-config.before` outside
  `~/handover` first, and then delete the directory. Do not copy the backup
  over `~/.ssh/config`: you or another program may have changed it during the
  run. Remove by hand only the lines you can trace to this run, such as the
  checkout launcher's leftover block, and compare again. Delete the backup once
  the comparison is clean.

Close the terminal at the end.

## Windows procedure

Run in PowerShell 7 as your normal user, in a new window. Start the Podman
machine yourself; the executable never creates or starts one. Set
`$env:CONTAINER_CONNECTION` if more than one connection exists. `podman` reads
the same variable, so the direct `podman exec` checks use the same machine.

The steps below follow the Linux procedure section by section and in the same
order. Expected results and how to count them are the same as on Linux unless
stated. The SSH directories are `$env:LOCALAPPDATA\sandboxed-agents\ssh` for the
executable and `$HOME\.ssh\sanboxed-agents` for the checkout launcher. The
adoption steps also need Git and the checkout launcher's own requirements from
the [Windows quick guide](QUICKGUIDE-WINDOWS.md): PowerShell 7 and Python 3.9
or newer. If Python is not on `PATH`, `sandbox.ps1` accepts its path in
`SANDBOX_PYTHON`. The executable ignores that variable, so it is the one
`SANDBOX_` variable the preflight allows; set it before the preflight and
record its value.

### Preflight on Windows

```powershell
podman version
podman machine list
podman system connection list
ssh -V
sandboxed-agents version
Get-ChildItem Env:SANDBOX_*, Env:CONTAINER_* -ErrorAction SilentlyContinue
podman ps --all --filter name=handover --format '{{.Names}}'
podman volume ls --filter name=handover --format '{{.Name}}'
foreach ($tag in 'handover', 'handover-broken', 'handover-legacy') {
  podman image exists "localhost/agent-sandbox:$tag"
  if ($LASTEXITCODE -eq 0) { "localhost/agent-sandbox:$tag exists" }
}
podman image exists docker.io/library/debian:12-slim; "debian:12-slim present: $($LASTEXITCODE -eq 0)"
Get-ChildItem -Force "$env:LOCALAPPDATA\sandboxed-agents\ssh\handover*", "$HOME\.ssh\sanboxed-agents\handover*" -ErrorAction SilentlyContinue
Select-String -Path "$HOME\.ssh\config" -Pattern handover -ErrorAction SilentlyContinue
Get-NetTCPConnection -State Listen -LocalPort 2295, 2296, 2297, 2298, 2299, 17680 -ErrorAction SilentlyContinue
Test-Path "$HOME\handover"
```

Apart from the version lines and the Debian line, every check should print
nothing, except `CONTAINER_CONNECTION` and `SANDBOX_PYTHON` if you set them,
and `Test-Path` should print `False`. Stop otherwise. Then save the starting state:

```powershell
New-Item -ItemType Directory "$HOME\handover" | Out-Null
Copy-Item "$HOME\.ssh\config" "$HOME\handover\ssh-config.before"
podman image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' > "$HOME\handover\images.before"
```

If `$HOME\.ssh\config` does not exist, skip the copy and record that.

### Build, create, and SSH on Windows

```powershell
$env:SANDBOX_CONTROLLER = 'handover-check'
$env:SANDBOX_IMAGE = 'localhost/agent-sandbox:handover'
sandboxed-agents build
sandboxed-agents handover01 up --agents codex --ssh-port 2299 --ssh-config
sandboxed-agents list
sandboxed-agents handover01 check
ssh handover01 true
sandboxed-agents handover01 fingerprint
podman exec --user 1000:1000 handover01 sh -c 'date > /workspace/handover-marker'
podman volume ls --filter label=io.sandboxed-agents.project=handover-check
icacls "$env:LOCALAPPDATA\sandboxed-agents\ssh\handover01\id_ed25519"
```

Also record whether Windows OpenSSH reads the managed `Include` line, which uses
forward slashes and a `*.conf` pattern; `ssh handover01 true` succeeding without
a host key prompt shows that it does.

### Volumes, binds, and protection on Windows

```powershell
sandboxed-agents handover01 remove
podman volume ls --filter label=io.sandboxed-agents.project=handover-check
sandboxed-agents handover01 up --agents codex --ssh-port 2299 --ssh-config
podman exec --user 1000:1000 handover01 cat /workspace/handover-marker
ssh handover01 true
New-Item -ItemType Directory -Force "$HOME\handover\bind workspace" | Out-Null
sandboxed-agents handover02 up "$HOME\handover\bind workspace" --agents codex --ssh-port 2298
sandboxed-agents handover02 check
podman exec --user 1000:1000 handover02 sh -c 'date > /workspace/bind-marker'
Get-Item "$HOME\handover\bind workspace\bind-marker"
$env:SANDBOX_CONTROLLER = 'handover-other'
sandboxed-agents handover01 remove --volumes
sandboxed-agents handover02 stop
$env:SANDBOX_CONTROLLER = 'handover-check'
podman ps --all --filter name=handover
```

The executable protection check needs the binary itself, which a direct or
NuGet install provides:

```powershell
New-Item -ItemType Directory -Force "$HOME\handover\bin" | Out-Null
Copy-Item (Get-Command sandboxed-agents).Source "$HOME\handover\bin\sandboxed-agents.exe"
& "$HOME\handover\bin\sandboxed-agents.exe" handover03 up "$HOME\handover\bin" --agents codex --ssh-port 2297
podman ps --all --filter name=handover03
```

### Update and rollback on Windows

```powershell
sandboxed-agents handover01 update
podman exec --user 1000:1000 handover01 cat /workspace/handover-marker
ssh handover01 true
sandboxed-agents handover01 agents list
sandboxed-agents handover02 update
podman exec --user 1000:1000 handover02 cat /workspace/bind-marker
```

Updating `handover02` is Windows-specific: `update` re-reads the bind source
reported by Podman and translates a WSL `/mnt/DRIVE/...` path back to Windows.
As recorded through `76f2bab`, that translation had only been exercised
offline.

Rollback candidate, as on Linux; as recorded through `76f2bab`, it was
unverified:

```powershell
podman inspect handover01 --format '{{.Id}}'
podman pull docker.io/library/debian:12-slim
podman tag docker.io/library/debian:12-slim localhost/agent-sandbox:handover-broken
$env:SANDBOX_IMAGE = 'localhost/agent-sandbox:handover-broken'
sandboxed-agents handover01 update --no-build
$env:SANDBOX_IMAGE = 'localhost/agent-sandbox:handover'
podman ps --all --filter name=handover01 --format '{{.Names}} {{.ID}} {{.Status}}'
podman exec --user 1000:1000 handover01 cat /workspace/handover-marker
ssh handover01 true
```

Optional, nested Podman, which on Windows also stages the seccomp profile
inside the machine:

```powershell
sandboxed-agents handover04 up --agents codex --ssh-port 2295 --capabilities podman
sandboxed-agents handover04 check-full
sandboxed-agents handover04 remove --volumes
```

### Services and forwarding on Windows

Run the Linux commands unchanged. For the check from a second PowerShell
window, use:

```powershell
curl.exe -sS -o NUL -w '%{http_code}\n' http://127.0.0.1:17680/
```

### Azure browser sign-in on Windows

Run the Linux commands unchanged. For the cancel case, read `$LASTEXITCODE`
instead of `$?`.

### Adoption on Windows

```powershell
git clone https://github.com/grauzone-git/sandboxed-ai-agents.git "$HOME\handover\legacy-checkout"
git -C "$HOME\handover\legacy-checkout" checkout --detach b9ee4a0bc35b6b2d7ccb1ff2363cbe9cd59a5ebe
git -C "$HOME\handover\legacy-checkout" rev-parse HEAD
$env:SANDBOX_IMAGE = 'localhost/agent-sandbox:handover-legacy'
& "$HOME\handover\legacy-checkout\sandbox.ps1" build
& "$HOME\handover\legacy-checkout\sandbox.ps1" handover-adopt up --agents codex --ssh-port 2296 --ssh-config
$env:SANDBOX_IMAGE = 'localhost/agent-sandbox:handover'
podman inspect handover-adopt --format '{{.ImageName}} {{.Id}}'
podman exec --user 1000:1000 handover-adopt sh -c 'date > /workspace/handover-marker'
ssh handover-adopt true
Copy-Item "$HOME\.ssh\config" "$HOME\handover\ssh-config.legacy"
sandboxed-agents list
```

The Windows launcher records the checkout path in normalized case, so copy the
`adopt` command from `list` instead of typing the path. The refusal case needs
a Windows absolute path; `/tmp/...` is not absolute on Windows and is rejected
before the ownership comparison:

```powershell
sandboxed-agents handover-adopt adopt --from "$HOME\handover\not-the-checkout"
podman ps --all --filter name=handover-adopt --format '{{.Names}} {{.ID}} {{.Status}}'
```

Expected and counted as on Linux: the ownership error, not the absolute path
error. Then make the rollback attempt, before the real adoption and under the
same conditions as on Linux, and check it with:

```powershell
podman ps --all --filter name=handover-adopt --format '{{.Names}} {{.ID}} {{.Status}}'
podman inspect handover-adopt --format '{{json .Config.Labels}}'
podman events --stream=false --since 10m --filter type=container --format '{{.Time}} {{.Status}} {{.Name}}' | Select-String handover-adopt
podman exec --user 1000:1000 handover-adopt cat /workspace/handover-marker
ssh handover-adopt true
Get-ChildItem -Force "$HOME\.ssh\sanboxed-agents\handover-adopt"
Compare-Object (Get-Content "$HOME\handover\ssh-config.legacy") (Get-Content "$HOME\.ssh\config")
```

Then run the `adopt` command without interrupting it, and check:

```powershell
podman inspect handover-adopt --format '{{json .Config.Labels}}'
foreach ($volume in 'handover-adopt-workspace', 'handover-adopt-home', 'handover-adopt-sshd') {
  podman volume inspect $volume --format '{{json .Labels}}'
}
podman exec --user 1000:1000 handover-adopt cat /workspace/handover-marker
ssh handover-adopt true
Select-String -Path "$HOME\.ssh\config" -Pattern include
Get-ChildItem -Force "$HOME\.ssh\sanboxed-agents\handover-adopt" -ErrorAction SilentlyContinue
& "$HOME\handover\legacy-checkout\sandbox.ps1" handover-adopt check
sandboxed-agents handover-adopt update
ssh handover-adopt true
```

### Cleanup on Windows

```powershell
sandboxed-agents handover01 remove --volumes
sandboxed-agents handover02 remove --volumes
sandboxed-agents handover-adopt remove --volumes
foreach ($name in 'handover03', 'handover04') {
  podman container exists $name
  if ($LASTEXITCODE -eq 0) { sandboxed-agents $name remove --volumes }
}
podman ps --all --filter name=handover --format '{{.Names}}'
podman volume ls --filter name=handover --format '{{.Name}}'
Get-ChildItem -Force "$env:LOCALAPPDATA\sandboxed-agents\ssh\handover*", "$HOME\.ssh\sanboxed-agents\handover*" -ErrorAction SilentlyContinue
podman image rm --ignore localhost/agent-sandbox:handover-broken localhost/agent-sandbox:handover localhost/agent-sandbox:handover-legacy
Compare-Object (Get-Content "$HOME\handover\images.before") (podman image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}')
Compare-Object (Get-Content "$HOME\handover\ssh-config.before") (Get-Content "$HOME\.ssh\config")
Remove-Item Env:SANDBOX_CONTROLLER, Env:SANDBOX_IMAGE -ErrorAction SilentlyContinue
```

Record a `handover03` or `handover04` container before the loop removes it, as
on Linux. Remove `Env:SANDBOX_PYTHON` too if you set it for this run. Handle the
Debian image, other new images, and the SSH config comparison as on Linux. Only
when `Compare-Object` prints nothing, or after copying the backup
outside the directory, delete it with
`Remove-Item -Recurse -Force "$HOME\handover"`. Close the window at the end.

## Packages, PATH, and provenance

These steps need a published prerelease. Work from a
directory outside any checkout, and create a new `~/handover-packages` or
`$HOME\handover-packages` directory for the records below; it must not exist
before. Installation and
verification follow [RELEASES.md](RELEASES.md); this list is what to record.

Before the first install, save the sandbox and SSH state. After each install
and each removal, run the same commands with `after` in place of `before` and
compare the two files; they should not differ.

```bash
{ podman ps --all --format '{{.Names}} {{.ID}}'; podman volume ls --format '{{.Name}}'; sha256sum ~/.ssh/config; } > ~/handover-packages/state.before
```

```powershell
& { podman ps --all --format '{{.Names}} {{.ID}}'; podman volume ls --format '{{.Name}}'; (Get-FileHash "$HOME\.ssh\config").Hash } > "$HOME\handover-packages\state.before"
```

Provenance, on Linux, for every binary in the release:

```bash
sha256sum --check --ignore-missing SHA256SUMS
gh attestation verify sandboxed-agents-linux-amd64 --repo grauzone-git/sandboxed-ai-agents
gh attestation verify sandboxed-agents-linux-arm64 --repo grauzone-git/sandboxed-ai-agents
gh attestation verify sandboxed-agents-windows-amd64.exe --repo grauzone-git/sandboxed-ai-agents
```

Reproducibility: in a clean clone at the tag, with Go 1.27.1, build to a new
directory and compare:

```bash
python3 -B packaging/build.py --version X.Y.Z --commit "$(git rev-parse HEAD)" --output /tmp/handover-rebuild
diff /tmp/handover-rebuild/SHA256SUMS SHA256SUMS
```

Installs to record, each followed by `sandboxed-agents version` from an
unrelated directory and removal:

- Linux direct binary into `~/.local/bin`.
- npm on Linux, and npm on Windows. With the npm install active, also run the
  [npm bind refusal](#npm-bind-refusal) below.
- NuGet on Windows with `install-command.ps1`, in PowerShell 7, following the
  execution policy guidance in [RELEASES.md](RELEASES.md#nuget-on-windows).
  Record whether the policy blocked the script. Then open a new PowerShell
  window and record `(Get-Command sandboxed-agents).Source` and
  `[Environment]::GetEnvironmentVariable('Path', 'User')`. After
  `remove-command.ps1`, open another new window and confirm
  `Get-Command sandboxed-agents -ErrorAction SilentlyContinue` finds nothing.
- On an ARM64 Linux host, if available, `sandboxed-agents version` from the
  direct binary and the npm package.

Delete the records directory once the comparisons are recorded.

### npm bind refusal

A bind of npm's global command directory must be refused before Podman is
called. This step may run days after the runtime procedures, so check again
right before it that nothing uses its names. It points `SANDBOX_IMAGE` at the
disposable `handover` tag, which the runtime cleanup removed and which must not
exist now, and never at the shared `localhost/agent-sandbox:dev` image. The
step needs no image: the refusal happens before the image check, so if it
regresses, `up` stops with `build the image first` and creates nothing, which
still counts as a failed refusal.

On Linux, each check should print its "no ..." or "free" line and nothing else:

```bash
env | grep -E '^(SANDBOX_|CONTAINER_)' || echo 'no SANDBOX_ or CONTAINER_ variables'
podman ps --all --format '{{.Names}}' | grep '^handover03$' || echo 'no handover03 container'
podman volume ls --format '{{.Name}}' | grep '^handover03-' || echo 'no handover03 volumes'
podman image exists localhost/agent-sandbox:handover && echo 'localhost/agent-sandbox:handover exists' || echo 'no :handover image'
ss -ltn | grep -E ':2297 ' || echo 'port 2297 free'
```

Stop if a check finds anything; see [Validation safety](#validation-safety).
Otherwise:

```bash
SANDBOX_CONTROLLER=handover-check SANDBOX_IMAGE=localhost/agent-sandbox:handover sandboxed-agents handover03 up "$(npm prefix -g)/bin" --agents codex --ssh-port 2297
podman ps --all --format '{{.Names}}' | grep '^handover03$' || echo 'no handover03 container'
podman volume ls --format '{{.Name}}' | grep '^handover03-' || echo 'no handover03 volumes'
```

On Windows, in PowerShell 7, where the command directory is the prefix itself,
every check should print nothing, except `CONTAINER_CONNECTION` and
`SANDBOX_PYTHON` if you set them, and `podman image exists` should print
`False`:

```powershell
Get-ChildItem Env:SANDBOX_*, Env:CONTAINER_* -ErrorAction SilentlyContinue
podman ps --all --filter name=handover03 --format '{{.Names}}'
podman volume ls --filter name=handover03 --format '{{.Name}}'
podman image exists localhost/agent-sandbox:handover; $LASTEXITCODE -eq 0
Get-NetTCPConnection -State Listen -LocalPort 2297 -ErrorAction SilentlyContinue
```

Stop if a check finds anything. Otherwise:

```powershell
$env:SANDBOX_CONTROLLER = 'handover-check'
$env:SANDBOX_IMAGE = 'localhost/agent-sandbox:handover'
sandboxed-agents handover03 up (npm prefix -g) --agents codex --ssh-port 2297
podman ps --all --filter name=handover03 --format '{{.Names}}'
podman volume ls --filter name=handover03 --format '{{.Name}}'
```

Expected on both: an error naming the conflicting path and suggesting a global
install, and no `handover03` container or volumes. If any appear, record them
and remove them with the same controller, in the same window on Windows, so
that `remove` refuses anything `handover-check` does not own. It never deletes
the bound directory:

```bash
SANDBOX_CONTROLLER=handover-check sandboxed-agents handover03 remove --volumes
```

```powershell
sandboxed-agents handover03 remove --volumes
```

On Windows, finish with
`Remove-Item Env:SANDBOX_CONTROLLER, Env:SANDBOX_IMAGE -ErrorAction SilentlyContinue`
whether or not a cleanup was needed.

## Validation record template

Copy one block per host and release into #50 as soon as the run finishes.
Copy the collected blocks into the next release's notes, under a heading naming
the release they validate; a release's own notes are published before its
validation runs. Leave a row as "not run" or "not verified" rather than removing
it.

```markdown
### Validation: HOST_LABEL, vX.Y.Z

- Date:
- Tester:
- Host OS and version, kernel or Windows build, CPU architecture:
- Podman client and engine versions:
- WSL2 machine and connection (Windows only):
- OpenSSH version:
- Install method (direct binary, npm, NuGet, source build):
- `sandboxed-agents version` output:
- Native CI run ID for this commit:
- Legacy checkout commit (`git rev-parse HEAD` of the clone):
- `SANDBOX_` and `CONTAINER_` variables set before the run:
- Preflight findings, and any names changed because of them:

| Step | Expected | Observed | Result |
| --- | --- | --- | --- |
| Build | Image built with version label | | not run |
| Create with SSH | Running, three volumes owned by `handover-check` | | not run |
| `check`, `ssh`, `fingerprint` | Pass, pinned host key matches | | not run |
| Plain remove and re-create | Volumes kept, marker present | | not run |
| Bind with space | Bound at /workspace, marker visible on host | | not run |
| Other group refused | Ownership error, nothing changed | | not run |
| Executable bind refused | Error before Podman, no container | | not run |
| Update | Marker, SSH, and selection kept | | not run |
| Update rollback | "did not become ready", original ID restored, no backup left | | not run |
| Service and forward | HTTP status through 17680, tunnel closes on Ctrl+C | | not run |
| Forward without SSH setup | Refused with `ssh-config --install` hint | | not run |
| Azure interactive | Browser sign-in, `az account show` shows the account | | not run |
| Azure cancel | Cancellation message, exit 130 | | not run |
| Adoption refusal | Ownership error for an absolute non-owner path, nothing stopped | | not run |
| Adoption rollback attempt, before adoption | Replacement created and removed in events, original ID, still checkout-owned, SSH files and config unchanged; error text recorded | | not run |
| Adoption | Owner changed, volumes and marker kept, SSH works without new host key | | not run |
| Checkout rejects adopted | Ownership error from the clone's launcher | | not run |
| Update after adoption | Succeeds, SSH works | | not run |
| Nested Podman (optional) | `check-full` passes | | not run |
| Cleanup | Containers, volumes, images, managed SSH files, and `Include` removed; SSH config compared; variables unset | | not run |
| Package install | `version` works from an unrelated directory, no sandbox or SSH change | | not run |
| npm bind refused | Error before Podman, no container | | not run |
| Windows PATH | New terminal finds the command; gone after removal | | not run |
| ARM64 (if available) | `version` runs on an ARM64 host | | not run |
| Provenance | Every binary verified; rebuild matches `SHA256SUMS` | | not run |

Limitations or deviations observed:
```
