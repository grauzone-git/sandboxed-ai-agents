# Security policy

## What this project is for

This launcher runs AI coding agents in rootless Podman containers so that a
mistake stays inside one workspace. The threat it is built against is an agent
that does something wrong: deletes the wrong directory, installs a bad package,
reads credentials it should not have, or runs a command whose effects nobody
wanted. Containing that blast radius is the goal.

The threat it is **not** built against is code that is actively trying to escape.
If you are running something you believe is hostile, a container sharing your
host kernel is the wrong boundary. Use a dedicated VM. The same SSH workflow
works there, so you lose nothing but the convenience of a local container.

Everything below is a description of the current implementation, not a promise.
The software is provided as is, under the terms in [LICENSE](LICENSE).

## What the sandbox does

By default each sandbox is created with these Podman options, defined in one place at
`src/host/containers.py`:

| Option | Effect |
|---|---|
| `--userns=keep-id:uid=1000,gid=1000` | Rootless user namespace; the agent user maps to your host user |
| `--security-opt=no-new-privileges` | Processes cannot gain privileges through setuid binaries |
| `--network=pasta:--no-map-gw` | User-mode networking with the host gateway address left unmapped |
| `--publish 127.0.0.1:PORT:2222` | Only SSH is published, and only on host loopback |
| `--memory`, `--cpus`, `--pids-limit`, `--shm-size` | Resource caps, 8 GiB / 4 CPUs / 2048 processes / 1 GiB shm by default |

Three named volumes per sandbox: workspace, home, and SSH server state. Only the
workspace may be replaced by a host directory bind, and `src/host/workspace.py`
rejects any bind that would place the controller's own source, Git metadata, or
SSH state inside the container.
The Podman capability adds a tmpfs at `/run/user/1000`, restricted to the agent
user with mode 0700 and mounted with `nosuid,nodev,noexec`. Runtime state clears
when the sandbox stops; persistent inner storage stays in the home volume.

The optional `--capabilities podman` profile passes `/dev/fuse` and `/dev/net/tun`,
omits `no-new-privileges` so the UID/GID mapping helpers can work, disables outer
SELinux/AppArmor separation, and unmasks kernel paths for nested mounts. The
outer container remains rootless with resource limits and a profile derived
from the host's default seccomp policy. Only `sethostname`, `setdomainname`, and
`setns` are allowed unconditionally so inner namespaces can be initialized;
the kernel still enforces namespace capabilities, and all other rules remain.
It does not run with `--privileged`. The mapping helpers have only
`cap_setuid=ep` / `cap_setgid=ep` file capabilities, with their setuid bits
removed. Subordinate IDs exclude container root
and the agent identity. Inner cgroups are disabled, so the outer limits cover
the sandbox as a whole. See [nested Podman](docs/TOOLCHAIN.md#nested-containers-with-podman).

No other host paths are exposed. No credentials, no SSH-agent socket, no
container-engine socket, no display socket, no host networking, no host IPC.
SSH keys are generated per sandbox, the container host key is pinned on first
use, and the generated files stay on the host.

Agents run as UID 1000. Container root exists only for initialization and sshd,
inside the rootless user namespace, where it is not host root.

## What it does not do

**Agents are unrestricted inside their own sandbox.** They can read and write
the entire workspace and home volume, including any provider credentials stored
there. Disabling an agent removes its launcher, not its access to cached files.

**Agents in one sandbox share everything.** Same user, same home, same
credentials. Selection controls what gets installed, not who can read what.
Separation between projects means separate sandboxes.

**Outbound networking is open.** Installs and model calls need it. This is not
an egress firewall, and it is not a guarantee of isolation from services on your
host or LAN. `--no-map-gw` removes the mapped gateway address; it is not a
substitute for a firewall policy.

**The kernel is shared.** Container isolation depends on it. A kernel or Podman
vulnerability is outside what this project can mitigate.

**Browser sandboxing is separate.** Playwright runs with default launch settings
and private shared memory. Chromium's own sandbox is its own subject.

**Agents and their providers are third parties.** What an installed agent sends
to its provider, and what that provider does with it, is governed by that
project and that provider, not by this repository.

## Reporting a vulnerability

Contact the maintainer privately with enough detail to reproduce the issue.
Please do not open a public issue for anything that lets an agent reach host
files, host credentials, or another sandbox.

Expect a reply within a week. This is a personal project without a paid security
team or a bounty programme, so fixes land on a best-effort basis. I would rather
hear about a problem late than not at all.

If you are reporting a vulnerability in one of the installed agents or tools
rather than in this launcher, please report it to that project directly. The
[Credits](README.md#credits) section links to each of them.

## Supported versions

The `main` branch. There are no releases, no version tags, and no backports.
Fixes go to `main`, and picking them up means pulling and running
`./sandbox update --all`.

## Hardening you may want to add

The defaults trade isolation for convenience in a few places. Depending on what
you are running, these are worth considering:

- Run the whole workflow inside a VM when the code is untrusted.
- Restrict outbound traffic at your host firewall rather than relying on
  `--no-map-gw` alone.
- Use separate sandboxes per project so credentials are not shared.
- Prefer named workspace volumes over host binds, which is already the default.
- Keep Podman and your kernel current, since both are the boundary.
