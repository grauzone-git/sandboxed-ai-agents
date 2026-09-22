# Azure CLI setup

Azure setup creates an independent user session inside one selected sandbox.
Plain setup uses device code. `--interactive` uses a host browser and a temporary
loopback SSH callback forward, all managed by one foreground command.

The Linux controller and native Windows PowerShell controller implement this
workflow. The live validation matrix below is still pending for this
implementation. WSL-shell controller integration with a Windows browser is
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
changed Azure state. Setup attempts are serialized within the sandbox.

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

## Disposable sandbox validation

Run these checks as a human using disposable sandboxes only. Never use a working
sandbox for validation. The #22 POC predates this implementation and is not
completion evidence for issues #25, #26, or #27.

| Host | Cloud | Initial login, tunnel closure, restart, renewal | Recorded versions |
|---|---|---|---|
| Linux | AzureCloud | Pending | Pending |
| Native Windows | AzureCloud | Pending | Pending |
| Linux | AzureChinaCloud | Pending | Pending |
| Native Windows | AzureChinaCloud | Pending | Pending |

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
   az rest --method get --url "$(az cloud show --query endpoints.resourceManager --output tsv)tenants?api-version=2020-01-01" --output none
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
compatibility, or China service availability. The implementation issues remain
incomplete until the required human-run matrix has recorded evidence.
