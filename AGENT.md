# AGENT.md

Instructions for AI coding agents working on this repository. For background on
what the project is and why it is built this way, read [CONTEXT.md](CONTEXT.md)
first.

## What this repository is

A launcher that gives each workspace its own rootless Podman container with
selected AI coding agents inside it. The code is Bash, Python, and CommonJS
JavaScript, with no package manager, no build step, and no third-party
dependencies. If you find yourself adding a dependency, stop and reconsider.

## Commands

```bash
make check     # syntax, JSON catalogs, local documentation links
make test      # the above plus every offline regression test
./tests/run    # identical to make test
```

`make test` needs Bash, Python 3.9+, Node.js, and OpenSSH client tools. It does
not need network access, Podman, or credentials, and it must stay that way. Run
it before you hand any change back.

Anything involving a real container is a manual step the user runs, not
something you run unprompted:

```bash
./sandbox build
./sandbox smoke-test up --ssh-port 2299 --agents codex --ssh-config
./sandbox smoke-test check
./sandbox smoke-test remove --volumes
```

Never point a runtime test at `agent01` or any other sandbox somebody works in,
and never run `./sandbox` with `sudo`.

## Rules that are not negotiable

These are security and data-loss boundaries. Breaking one is worse than leaving
a feature unimplemented, so if a task seems to require it, say so instead.

Only `/workspace` may ever be a host bind. Home and SSH server state always use
named volumes. Never mount host credentials, SSH-agent sockets, container-engine
sockets, or display sockets to make something work.

`src/host/workspace.py` rejects binds that would expose the controller's own
source, tests, metadata, or SSH state, symlinks included. When you add a
host-executed file, add it to `protected_paths()` and extend the guard tests in
`tests/test-workspace-storage.py`.

Host SSH setup is opt-in. `up --ssh-config` and `start --ssh-config` write local
SSH files and install the Include; plain `up` and `start` must not touch
`~/.ssh` at all.

Update must never call the normal `remove` path, because `remove` deletes local
SSH access. Update keeps the old container under a `NAME-update-backup-…` name
until readiness and service restoration succeed, and its rollback must never
delete named volumes.

Container and volume ownership is labelled with the absolute path of the
checkout (`io.sandboxed-agents.project`). Do not change how that label is
derived; existing sandboxes would be orphaned.

`./sandbox` stays the entry point and
`/usr/local/lib/sandbox-agents/manager.cjs` stays the manager path. Both are
load-bearing for containers that already exist.

## Code conventions

Bash uses `set -euo pipefail` and resolves the repository root from
`BASH_SOURCE` with `cd -- ... && pwd -P`. Errors go through the shared `fail`
helper. Host library modules live in `src/host/lib/` and are sourced by
`cli.sh`; they define functions and run nothing at import time.

Python is standard library only, no third-party imports, and targets 3.9. Files
carry a one-line module docstring explaining what the file protects or does.
Scripts run under `python3 -B` so no `__pycache__` appears. Validation functions
raise `ValueError` with a message the user can act on, and `main()` catches
`OSError`, `RuntimeError`, and `ValueError` to exit with `Error: ...`.

JavaScript is CommonJS in `.cjs` files, two-space indent, single quotes, and
`node:`-prefixed builtins. The manager and services are factory functions
(`createManager`, `createServices`) that accept injected `spawn` and `spawnSync`
so tests can mock processes. Do not restructure source purely to expose
internals for a test; inject instead.

Comments explain a constraint or a reason. Do not narrate what the next line
does.

## Tests

Python tests use `unittest`, a `tempfile.TemporaryDirectory`, an isolated
`HOME`, and a fake `podman` executable placed on `PATH` that logs its arguments
to a JSONL file. JavaScript tests use `node:assert/strict` and no framework.
Every new externally visible behaviour needs a test in the matching suite:

| Suite | Covers |
|---|---|
| `tests/test-host-commands.cjs` | Host CLI interface, routing, login dispatch |
| `tests/test-manager.cjs` | In-container selection, installs, dependent UI disablement |
| `tests/test-workspace-storage.py` | Workspace modes, volume reuse, bind rejection |
| `tests/test-update.py` | Build ordering, preservation, rollback |
| `tests/test-*-ssh-config.py`, `test-ssh-opt-in.py` | SSH file generation, Include handling, cleanup |
| `tests/test-validators.py` | Selection and mount validation |
| `tests/test-list.py` | Listing checkout-owned sandboxes |
| `tests/test-image-recipe.py` | Image packages, recorded versions, smoke checks |

`tests/check-sources.py` parses every file under `src/` and `tests/`, validates
the JSON catalogs, and resolves local markdown links in `README.md` and
`docs/*.md`. It does not check links in root-level files like this one, so
verify those by hand.

## Where changes take effect

Anything under `src/host/` applies on the next `./sandbox` invocation. Anything
under `src/container/` needs an image rebuild and container recreation, and new
runtime modules have to be copied explicitly by the Containerfile or they will
not exist inside the image.

## Adding an agent or tool

Add metadata to `src/container/agents.json` or `tools.json`, which the host
validator and the container manager both read. npm-based entries declare their
package and executable; anything else installs in `installers.cjs`. A tool with
an `agent` field shares that agent's binary and version, and `disableWithAgent`
controls automatic disablement when the agent goes away. Managed server
arguments, the loopback port, and readiness expectations belong in the tool
catalog; supervision belongs in `services.cjs`; host aliases and forwarding
dispatch belong in `src/host/cli.sh`.

Every externally visible command change also updates `src/host/lib/args.sh`
help text, the relevant guide in `docs/`, and the tests.

## Documentation

The README is the quickstart and index. Lifecycle and SSH go in
`docs/SANDBOXES.md`, agents and dashboards in `docs/AGENT-SETUP.md`, image and
SDK details in `docs/TOOLCHAIN.md`, design decisions in
`docs/ARCHITECTURE.md`.

Use `agent01` on port `2222` for the main example, `agent02` on `2223` for a
second sandbox, and `./workspaces/agent01` when showing an explicit bind. Label
commands that run inside the sandbox. Link to the canonical procedure instead of
repeating it.

Write plainly. No em dashes, no curly quotes, no emoji, no bold-word bullet
headers, and no marketing adjectives. Say what something does and what it costs.

Never write that something was verified live when you only ran the offline
suite.

## Reporting back

State what you changed, what you actually ran, and what remains unverified.
Offline tests passing is not the same as a live Podman run. If you could not
test something, say which part and why.
