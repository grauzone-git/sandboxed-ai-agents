"""Send an explicitly selected environment PAT to one sandbox over stdin."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main(args):
    if len(args) < 5 or args[1:3] != ["--pat-env", "--"]:
        raise ValueError("Usage: ./sandbox azdo NAME --pat-env -- devops COMMAND "
                         "--organization URL [--project PROJECT]")
    name, command = args[0], args[3:]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name):
        raise ValueError("Use an alphanumeric container name (plus _, ., -).")
    env = dict(os.environ)
    token = env.pop("AZURE_DEVOPS_EXT_PAT", "")
    if not token:
        raise ValueError("Set a nonempty AZURE_DEVOPS_EXT_PAT in the invoking environment, then retry.")
    if any(token in arg for arg in args):
        raise ValueError("Supply the PAT only through AZURE_DEVOPS_EXT_PAT, never in arguments.")
    if hasattr(os, "getuid") and os.getuid() == 0:
        raise ValueError("Use rootless Podman as your normal user.")
    root = str(Path(__file__).resolve().parents[2])

    def podman(arguments, **kwargs):
        result = subprocess.run(["podman", *arguments], env=env, capture_output=True, **kwargs)
        if result.returncode:
            sys.stderr.buffer.write(result.stderr.replace(token.encode(), b"[REDACTED]"))
            raise SystemExit(result.returncode)
        return result

    if podman(["info", "--format", "{{.Host.Security.Rootless}}"]).stdout.strip() != b"true":
        raise ValueError("Podman must be rootless.")
    owner = podman(["inspect", "--format", '{{index .Config.Labels "io.sandboxed-agents.project"}}', name])
    if owner.stdout.decode().strip() != root:
        raise ValueError("Selected container is not owned by this configuration.")
    result = subprocess.run(
        ["podman", "exec", "-i", "--user", "1000:1000", "--workdir", "/workspace",
         name, "/usr/local/bin/sandbox-azdo", "--pat-stdin", *command],
        input=json.dumps(token).encode(), env=env, capture_output=True)
    sys.stdout.buffer.write(result.stdout.replace(token.encode(), b"[REDACTED]"))
    sys.stderr.buffer.write(result.stderr.replace(token.encode(), b"[REDACTED]"))
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (RuntimeError, ValueError) as error:
        sys.exit(f"Error: {error}")
    except OSError:
        # Exception objects from process creation can contain supplied arguments.
        sys.exit("Error: Unable to run Azure DevOps command. Check arguments, a nonempty "
                 "AZURE_DEVOPS_EXT_PAT, Podman on PATH, and sandbox ownership.")
