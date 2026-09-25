"""Validate a bind mount before exposing host files or provisioning a sandbox."""
from pathlib import Path
import sys


def protected_paths(project, ssh_root):
    """Include lexical paths and resolved targets of source-file symlinks."""
    paths = [ssh_root, project / ".local", project / ".git", project / "sandbox",
             project / "sandbox.ps1", project / "src", project / "tests", project / "Makefile",
             project / "cmd", project / "internal", project / "assets.go", project / "go.mod",
             project / "go.sum", project / "sandboxed-agents", project / "sandboxed-agents.exe"]
    # A linked worktree stores Git metadata outside its checkout. Protect both
    # that worktree's directory and the common object/configuration directory.
    git = project / ".git"
    if git.is_file():
        reference = git.read_text(encoding="utf-8").strip()
        if reference.startswith("gitdir: "):
            metadata = (project / reference[len("gitdir: "):]).resolve()
            paths.append(metadata)
            common = metadata / "commondir"
            if common.is_file():
                paths.append((metadata / common.read_text(encoding="utf-8").strip()).resolve())
    # rglob does not follow directory symlinks. Each link itself is still checked
    # against its resolved target, without traversing arbitrary external trees.
    for directory in (project / "src", project / "tests", project / "cmd", project / "internal"):
        paths.extend(directory.rglob("*"))
    for item in paths:
        yield item.absolute()
        yield item.resolve()


def validate_workspace(workspace, project, ssh_root):
    workspace = workspace.resolve()
    for protected in protected_paths(project, ssh_root):
        if protected.is_relative_to(workspace):
            raise ValueError("Workspace must not contain host SSH/controller files or state. "
                             "Use a separate workspace such as workspaces/NAME.")
        if workspace.is_relative_to(protected):
            raise ValueError("Workspace must not be inside host SSH/controller files or state. "
                             "Use a separate workspace such as workspaces/NAME.")
    if workspace == Path("/") or any(char in str(workspace) for char in (":", "\n")):
        raise ValueError("Unsupported workspace path.")
    return workspace


def main(args):
    if len(args) != 3:
        raise ValueError("Usage: workspace.py WORKSPACE PROJECT SSH_ROOT")
    print(validate_workspace(*(Path(arg) for arg in args)))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(f"Error: {error}")
