"""Prepend one generated sandbox config to the user's OpenSSH configuration."""
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile


def includes_only(line, source):
    """Recognize standalone equivalents, without rewriting multi-file Includes."""
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        return False
    if not tokens:
        return False
    directive, separator, value = tokens[0].partition("=")
    if directive.lower() != "include":
        return False
    args = ([value] if separator and value else []) + tokens[1:]
    if args[:1] == ["="]:
        args = args[1:]
    return len(args) == 1 and args[0] == str(source)


def install_include(source, config):
    source = source.absolute()
    if not source.is_file():
        raise ValueError(f"SSH configuration does not exist: {source}")
    if any(char in str(source) for char in '\r\n"%$\\*?[]'):
        raise ValueError("Include path contains unsupported SSH expansion characters.")
    # Resolve config symlinks so dotfile-managed links stay intact. Only create
    # ~/.ssh here; a broken link into a missing dotfiles directory is an error.
    target = config.resolve()
    original = target.read_bytes() if target.exists() else b""
    text = original.decode("utf-8", errors="surrogateescape")
    newline = "\r\n" if "\r\n" in text else "\n"
    header = f'Include "{source}"{newline}Host *{newline}{newline}'
    # Includes can leave parsing inside their last Host block. Reset the context
    # so the user's following global defaults still apply to every host.
    remaining = text.replace(header, "")
    remaining = "".join(line for line in remaining.splitlines(keepends=True)
                        if not includes_only(line, source))
    updated = (header + remaining).encode("utf-8", errors="surrogateescape")
    if updated == original:
        return False
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".sandbox-ssh-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: install-ssh-config.py GENERATED_CONFIG")
        config = Path.home() / ".ssh/config"
        changed = install_include(Path(sys.argv[1]), config)
        print(f"{'Installed Include at the top of' if changed else 'Include already installed in'} {config}")
    except (OSError, ValueError) as error:
        sys.exit(f"Error: {error}")
