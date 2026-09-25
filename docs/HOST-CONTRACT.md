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

## Executable management subset

When `SANDBOX_TEST_LAUNCHER` selects the executable, `tests/test-host-commands.cjs`
does not run the script scenarios. Set `SANDBOX_TEST_CONTRACT_SLICE=management`
to run the shared management cases in `tests/host-binary-management.cjs`; without
it the suite fails with a message naming the supported slice. The variable has no
effect when the launcher is the checkout script. On Linux:

```sh
go build -o sandboxed-agents ./cmd/sandboxed-agents
export SANDBOX_TEST_LAUNCHER="[\"$PWD/sandboxed-agents\"]"
SANDBOX_TEST_CONTRACT_SLICE=management node tests/test-host-commands.cjs
python3 -B tests/test-list.py
unset SANDBOX_TEST_LAUNCHER
```

The subset drives the public CLI against fake Podman and SSH commands and covers:

- Session shortcuts for `copilot`, `claude`, `codex`, `hermes`, `opencode`,
  `deepseek`, and `t3`, with `t3` routed to the tool manager.
- `agents login` for the managed agents and `tools login github`.
- `agents` and `tools` with no operation, `list`, `check`, `set`, `enable`,
  `disable`, and `update`, including `set none` and `update all`.
- `run` for an agent and `tool` for a tool.
- Rejected input, such as unknown or misplaced names, empty or malformed lists,
  extra arguments, and missing operations. Each fails with an error before any
  Podman or SSH call.
- Literal argument passing: `--`, spaces, quotes, shell syntax, empty strings,
  newlines, and `--help` reach the manager unchanged.
- Exit status 0 and 7, stdout, and stderr passing through from the manager.
- The exact fake Podman call: one `podman exec` as `1000:1000` in `/workspace`,
  with `-i` for interactive commands, and no SSH or init call.
- A foreign-owned container rejected before `exec`.
- Unchanged host `~/.ssh` and controller state directory after all cases.

The list and management contracts support Windows through a native fake-command
relay. The management subset also needs Node on `PATH`:

```powershell
go build -o sandboxed-agents.exe ./cmd/sandboxed-agents
go build -o fake-command.exe ./tests/fake-command
$env:SANDBOX_TEST_FAKE_COMMAND = (Resolve-Path ./fake-command.exe).Path
$env:SANDBOX_TEST_LAUNCHER = ConvertTo-Json -Compress -InputObject @((Resolve-Path ./sandboxed-agents.exe).Path)
$env:SANDBOX_TEST_CONTRACT_SLICE = 'management'
node tests/test-host-commands.cjs
python -B tests/test-list.py
```

CI runs `go test ./...`, the list contract, and the management subset against
the executable on Linux and Windows. The subset is not full parity with the four
suites. The remaining command, SSH opt-in, and workspace storage scenarios for
the executable remain with #47, #48, and #50. Cross-compilation alone does not
validate Windows behavior.

Supported executable targets are linux/amd64, linux/arm64, and Windows 11
windows/amd64. macOS and BSD are not targets. POSIX fake commands and generic
Unix code paths in the tests do not imply support for them.
