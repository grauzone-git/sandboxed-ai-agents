# SandboxedAgents for Windows

After NuGet extracts this package, run `tools/install-command.ps1` explicitly.
It verifies the bundled executable's SHA-256 checksum, copies the command to
`%LOCALAPPDATA%\Programs\sandboxed-agents`, and adds that directory to your user
`PATH`. Then run `sandboxed-agents version`. Other terminals need reopening to
receive the updated `PATH`.

The executable requires Podman and OpenSSH. Package installation creates no
sandboxes and makes no SSH changes. This package is not a project library and
does not use NuGet's legacy install hooks.

Run `tools/remove-command.ps1` before deleting the extracted package to remove
its installed command. Sandbox data and SSH configuration are preserved.
Both scripts accept `-InstallDirectory PATH -NoPathUpdate` for a custom command
directory whose `PATH` entry you manage yourself.

See https://github.com/grauzone-git/sandboxed-ai-agents for release documentation.
