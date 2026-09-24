# Azure CLI setup

Azure setup creates an independent user session inside one selected sandbox.
Plain setup uses device code. `--interactive` uses a host browser and a temporary
loopback SSH callback forward, all managed by one foreground command.

The Linux controller and native Windows PowerShell controller implement this
workflow. The live validation matrix below records passing runs on both hosts
for both clouds. WSL-shell controller integration with a Windows browser is
separate and remains unverified.

## Commands

On a Linux host:

```bash
./sandbox tools agent01 setup azure --tenant TENANT --subscription SUBSCRIPTION
./sandbox tools agent01 setup azure --interactive --tenant TENANT --subscription SUBSCRIPTION
```

On a native Windows host:

```powershell
./sandbox.ps1 tools agent01 setup azure --tenant TENANT --subscription SUBSCRIPTION
./sandbox.ps1 tools agent01 setup azure --interactive --tenant TENANT --subscription SUBSCRIPTION
```

Quote subscription names containing spaces. Omitted tenant and subscription
values are prompted for. Use `--tenant-only` instead of `--subscription` to
authenticate without subscription discovery. Setup does not list resource groups
or require resource-group permissions. Tenant-only setup requires an Azure CLI
version with `az login --skip-subscription-discovery`; rebuild an older image if
the CLI rejects that option.

`--cloud AzureCloud` and `--cloud AzureChinaCloud` select the public and China
clouds. A successful setup remembers that choice; omission uses the configured
cloud, or AzureCloud if none is configured. Other clouds are rejected. The CLI
configures the selected cloud before login and owns the authority, token
audiences, service endpoints, and subsequent renewal.

Browser setup requires managed SSH access that was explicitly opted into
previously. If missing, the command prints the exact setup command:

```bash
./sandbox ssh-config agent01 --install
```

Use `./sandbox.ps1 ssh-config agent01 --install` on native Windows. Azure setup
does not create SSH files itself. Linux needs OpenSSH and `xdg-open` with a
working default browser. Windows uses Windows OpenSSH and the default Windows
browser; it does not use Unix SSH control sockets. Existing rootless Podman,
ownership, and pinned SSH checks still apply. No host Azure CLI login is needed.

Inside the sandbox, device code remains available:

```bash
sandbox-tools setup azure --tenant TENANT --tenant-only
```

Inside-sandbox `--interactive` prints the corresponding host command template.
Keep browser setup in the foreground until completion. It verifies the callback
through SSH before opening the browser and closes forwarding on success,
failure, cancellation, or the ten-minute timeout. If forwarding or browser launch
fails, check managed SSH access, local port availability, and the default browser,
then rerun explicit setup. No second terminal or copied authorization URL is
needed. Authorization URLs and callback values are held in memory, omitted from
diagnostics, and not supplied as browser process arguments.

## Session ownership and replacement

Every explicit setup starts fresh sign-in in a temporary private configuration.
It replaces the active Azure context only after login and context selection
succeed. Failed or cancelled setup retains the prior session and cloud settings,
including a prior manual `az login` or device-code session. Stop concurrent Azure
commands while replacing a session; setup refuses publication when it detects
changed Azure state. Setup attempts are serialized within the sandbox. Custom
cloud registrations in `clouds.config` are kept. If the host command stops after
it has committed the new sign-in, it says the sandbox may already use it; check
with `az account show` instead of assuming the prior session was kept.

The result is the normal `~/.azure` configuration. Ordinary `az` commands and
agents use it without an environment export. Unset an alternate
`AZURE_CONFIG_DIR` before setup. Setup preserves Azure DevOps native credentials,
its configuration and organization defaults, and separately saved environment
PATs. There is no Azure `--persist` option.

Credentials stay in the sandbox home volume across restart, update, and
recreation retaining that volume. New container code requires an image rebuild
and container recreation; see [sandbox updates](SANDBOXES.md). No host
credentials, sockets, or display directories are mounted. Other agents sharing
the sandbox user can use its credentials.

Inside the sandbox, use ordinary Azure logout:

```bash
az logout
```

Do not delete the shared `~/.azure` directory: it may contain independent Azure
DevOps credentials and configuration. Host logout does not sign out this
sandbox-owned session. Local logout or cache deletion is not global token
revocation.

Azure CLI renews tokens while policy permits. Ordinary commands never trigger
this setup workflow or automatically replay failed operations. If interaction
is required, rerun explicit setup and decide whether to retry the original
operation. Setup reports interaction-required errors separately from permission
and network errors. Permission errors need tenant/subscription/role review;
network errors need connectivity review, rather than repeated sign-in.

The approximately 24-hour restriction was not observed in the earlier #22 POC.
Its cause remains unknown. Actual policy-triggered recovery remains unverified
and is not a release blocker; automated classification tests do not reproduce
tenant policy.

## Live validation runner

`tests/live-azure-auth.py` runs the acceptance sequence on the host where you
invoke it. Run it separately on Linux and native Windows, once per cloud. It
uses Python 3.9+ and the existing launcher, OpenSSH, Podman, and browser setup.
Build a current image first. No host Azure login is needed.

Linux host:

```bash
python3 -B tests/live-azure-auth.py --azure-environment AzureCloud --tenant-id TENANT --image localhost/azure-auth-validation:102276b
```

Native Windows PowerShell:

```powershell
python -B tests/live-azure-auth.py --azure-environment AzureChinaCloud --tenant-id TENANT --image localhost/azure-auth-validation:102276b
```

Replace the image tag with your current validation image. The runner does not
build or pull it. `--ssh-port` defaults to `2299`; select a free port if needed.
`--agents` defaults to `codex`. Each run creates a unique disposable sandbox
with named volumes and explicitly opts into managed SSH configuration. It never
selects an existing working sandbox.

The runner records source revision, image ID, host and CLI versions, and checks:

- Browser login and read-only ARM access after setup closes the callback tunnel.
- Access from fresh SSH sessions after stop/start and update retaining the home.
  The runner waits up to 60 seconds for pinned SSH access before probing Azure;
  it retries only the SSH readiness command, never an Azure operation.
- Renewal after the recorded token expiry plus one minute. Leave the sandbox
  idle during the wait and confirm no intervening commands or login.
- Cancelled replacement: press Ctrl+C in the runner terminal after the browser
  opens, before completing sign-in. The runner checks exit 130 and retained
  Azure access, then asks for fresh sign-in for the remembered-cloud check.
- Explicit fresh sign-in without `--cloud`, verifying the remembered cloud.
- Logout and failure of an ordinary account check afterward.

Optional DevOps checks need an organization and PAT:

```bash
python3 -B tests/live-azure-auth.py --azure-environment AzureCloud --tenant-id TENANT --azdo-organization https://dev.azure.com/ORGANIZATION --azdo-pat
```

`--azdo-pat` takes no value: it prompts without echoing input. Alternatively,
`--azdo-pat-env VARIABLE_NAME` reads an existing environment variable. Never put
the PAT itself in command arguments. The runner sends it over pinned SSH stdin,
not in remote arguments, and omits it from results. Organization and tenant
values are also omitted from the report.

DevOps checks try native credential storage and saved-PAT mode separately. Each
mode checks default organization and project-list access before and after Azure
replacement and logout. Saved-PAT mode additionally checks `sandbox-azdo` with
its isolated Azure configuration. Native credential-store setup failure remains
a failed check even if saved-PAT checks succeed. Browser interaction is needed
again for each successful mode's Azure replacement.

Results are saved after every check in a new `azure-auth-test-*.json` file, or
at `--report PATH`. Existing reports are never overwritten. The report contains
only selected metadata, expiry times, counts, and outcomes, not raw Azure error
output or tokens. Failed checks identify the operation, exit code when available,
and failure type so lifecycle, SSH, and Azure probe failures remain distinct. A failure or interruption returns exit 1 and retains the
sandbox for inspection. Successful runs remove their sandbox and volumes unless
`--keep-sandbox` is supplied. Remove a retained disposable sandbox with
`./sandbox remove NAME --volumes` (use `./sandbox.ps1` on Windows).

`--skip-renewal` and `--skip-cancellation` permit shorter runs and record those
checks as skipped. Exit 0 means the executed checks passed; it does not mean
skipped checks passed or that both host/cloud combinations were validated.
Without PAT options, DevOps checks are explicitly skipped. The runner does not
update GitHub checkboxes automatically. Review its report before recording
acceptance evidence. Device-code login, subscription selection, failed
cross-cloud replacement, and policy-triggered recovery remain separate checks.

The live runner is not part of `./tests/run`. Its offline regression tests run
without Podman, network access, or credentials. Offline tests do not establish
that the new runner has completed a real sign-in.

## Disposable sandbox validation

Run these checks as a human using disposable sandboxes only. Never use a working
sandbox for validation. The #22 POC predates this implementation and is not
completion evidence for issues #25, #26, or #27.

| Host | Cloud | Initial login, tunnel closure, restart, renewal | Recorded versions |
|---|---|---|---|
| Linux | AzureCloud | Passed 2026-09-24 ([#25](https://github.com/grauzone-git/sandboxed-ai-agents/issues/25#issuecomment-5822999605)) | Recorded |
| Native Windows | AzureCloud | Passed 2026-09-24 ([#26](https://github.com/grauzone-git/sandboxed-ai-agents/issues/26#issuecomment-5823379142)) | Recorded |
| Linux | AzureChinaCloud | Passed 2026-09-24 ([#27](https://github.com/grauzone-git/sandboxed-ai-agents/issues/27#issuecomment-5823262476)) | Recorded |
| Native Windows | AzureChinaCloud | Passed 2026-09-24 ([#27](https://github.com/grauzone-git/sandboxed-ai-agents/issues/27#issuecomment-5823418532)) | Recorded |

All four runs used source `dbdaf98`, image `542da81`, and browser login, and
passed every runner check including native Azure DevOps credential storage.
Device-code login is untested because the available tenants allow browser
sign-in only.

1. Record UTC date, host OS, Podman client/server versions, OpenSSH version, and
   the Azure CLI/MSAL versions in the rebuilt image. On Windows also record
   PowerShell, Python, and the selected rootless WSL2 Podman backend versions.
2. Build the image and create a new disposable sandbox with managed SSH:

   ```bash
   ./sandbox build
   ./sandbox up azure-auth-smoke --ssh-port 2299 --agents codex --ssh-config
   ./sandbox tools azure-auth-smoke setup azure --interactive --cloud AzureCloud --tenant TENANT --tenant-only
   ```

   Use the `.ps1` launcher on native Windows. Repeat independently with
   AzureChinaCloud and a tenant in that cloud. Record only sanitized outcomes,
   never authorization URLs, device codes, tokens, or callback values.
3. After the setup command has returned and its tunnel has closed, enter the
   disposable sandbox with `./sandbox shell azure-auth-smoke` (or `.ps1`). Run:

   ```bash
   az cloud show --query name --output tsv
   az account show --query 'user.type' --output tsv
   az account get-access-token --query expires_on --output tsv
   azure_arm_endpoint="$(az cloud show --query endpoints.resourceManager --output tsv)"
   az rest --method get --url "${azure_arm_endpoint%/}/tenants?api-version=2020-01-01" --output none
   ```

   Record the expiry epoch and whether token issuance and read-only ARM access
   succeeded. A permission denial is distinct from an authentication failure.
   Also exercise explicit subscription selection where the test account has a
   subscription, and initial device-code setup where tenant policy permits it.
4. Exit the shell, stop and start the disposable sandbox, then repeat the
   commands without another login. Repeat after `update azure-auth-smoke`, which
   recreates the container retaining its home. Record sanitized outcomes.
5. Wait until after the recorded token expiry. Without intervening login, run
   the same token-expiry and ARM commands. Record the new expiry and outcomes.
   Do not infer renewal from a check before expiry.
6. Check a cancelled fresh sign-in and a failed cloud/context selection retain
   the prior working session. Check Azure DevOps defaults and native/PAT state
   before and after setup and `az logout`; do not print credential files.
7. Remove only the disposable sandbox with
   `./sandbox remove azure-auth-smoke --volumes` (or `.ps1`).

Offline regression tests cover setup defaults, cloud and context selection,
replacement rollback, callback validation/lifecycle, and host argument handling.
Update regression tests cover retaining the home volume across recreation and
rollback. These tests do not establish real login, renewal, native browser
compatibility, or China service availability; the human-run matrix above
records that evidence.
