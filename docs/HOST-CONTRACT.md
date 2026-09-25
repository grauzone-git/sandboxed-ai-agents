# Host behavior contract

The host command, list, SSH opt-in, and workspace storage suites invoke the
public launcher with fake Podman and SSH processes and isolated homes. They
check exit status, output, native-command arguments, and filesystem effects.
They require no running Podman service, network access, or credentials.

By default each suite copies the checkout scripts into its temporary fixture
and runs that copy. Run the contract with:

```sh
node tests/test-host-commands.cjs
python3 -B tests/test-list.py
python3 -B tests/test-ssh-opt-in.py
python3 -B tests/test-workspace-storage.py
```

Set `SANDBOX_TEST_LAUNCHER` to a JSON array to select another launcher. The first
element is the executable; further elements are literal arguments. Use absolute
paths because the suites change working directories. Shell strings are not
evaluated. For example:

```sh
export SANDBOX_TEST_LAUNCHER='["/opt/sandboxed-agents/sandboxed-agents"]'
python3 -B tests/test-list.py
```

`{checkout}` in an argument expands to the suite's temporary checkout. This
allows wrappers around the existing scripts:

```sh
export SANDBOX_TEST_LAUNCHER='["bash", "{checkout}/sandbox"]'
python3 -B tests/test-workspace-storage.py
unset SANDBOX_TEST_LAUNCHER
```

The helper runs the selected launcher's `list` command against a separate fake
Podman and reads the `io.sandboxed-agents.project` label filter it requests.
Fixtures use that discovered value for owned containers and volumes. Discovery
fails if the launcher fails or does not provide one nonempty owner filter;
there is no fallback to checkout ownership. Discovery calls stay outside the
scenario logs, so assertions about validation before Podman still apply.

The JavaScript suite uses `python3` to run the shared discovery helper. Set
`PYTHON` to another Python executable if needed. The override applies to these
four suites only; other regression suites continue testing their existing
components. `./tests/run` runs all offline tests, including this contract.

The contract initially retains the scripts' command behavior. As the executable
issues implement the intentional changes in #36, extend the relevant scenarios
for state-directory SSH files, executable workspace protection, and direct
Podman sessions. Passing a subset for a preview does not establish full parity.

## Run the contract against the executable

The executable's supported targets are Linux on amd64 and arm64, and Windows 11
on amd64. CI cross-builds those three and runs the contract on Linux and
Windows runners. macOS, the BSDs, and Windows on ARM64 are outside the
supported targets: the code may compile there, but nothing tests or supports
it.

The recipes need Go 1.23 or later, Python 3.9 or later, and Node.js. Run them
from the checkout root. On Linux, build the controller and run the four suites:

```sh
go build -o sandboxed-agents ./cmd/sandboxed-agents
export SANDBOX_TEST_LAUNCHER="[\"$PWD/sandboxed-agents\"]"
node tests/test-host-commands.cjs
python3 -B tests/test-list.py
python3 -B tests/test-ssh-opt-in.py
python3 -B tests/test-workspace-storage.py
unset SANDBOX_TEST_LAUNCHER
```

Linux fakes are executable scripts, so no relay is needed. Windows cannot run
script fakes as native commands. There, the suites copy a Go relay built from
`tests/fake-command` for each fake, and `SANDBOX_TEST_FAKE_COMMAND` must point
at it. In PowerShell 7:

```powershell
go build -o sandboxed-agents.exe ./cmd/sandboxed-agents
go build -o fake-command.exe ./tests/fake-command
$env:SANDBOX_TEST_FAKE_COMMAND = (Resolve-Path ./fake-command.exe).Path
$env:SANDBOX_TEST_LAUNCHER = ConvertTo-Json -Compress -InputObject @((Resolve-Path ./sandboxed-agents.exe).Path)
$env:PYTHON = (Get-Command python).Source
node tests/test-host-commands.cjs
python -B tests/test-list.py
python -B tests/test-ssh-opt-in.py
python -B tests/test-workspace-storage.py
```

Check `$LASTEXITCODE` after each suite, because PowerShell continues after a
failing native command. `PYTHON` makes the JavaScript suite use the same Python
as the other three.

CI runs `go test ./...` and all four contract suites against the binary on
Linux and Windows, with `SANDBOX_TEST_FAKE_COMMAND` pointing at the relay. For
the binary, the host command suite runs its executable scenarios, including
services, forwarding, and tool setup; `SANDBOX_TEST_CONTRACT_SLICE=management`
still selects the narrower agent and tool management slice. Lifecycle and other
source-specific branches in the suites keep the script expectations when the
launcher is a checkout script. In the workspace storage suite, five
script-only checkout source protection checks are skipped for the binary, and
two binary-only checks replace them: state, `~/.ssh`, and build-context
protection, and the global-install suggestion for a project-local executable.

These suites use fake commands only. Passing them does not validate real
Podman, SSH, WSL2, or Azure sign-in, and cross-compilation alone does not
validate Windows behavior. Live validation and the stable-release gates remain
open under #50.
