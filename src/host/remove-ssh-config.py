"""Remove only one sandbox's generated host SSH files and Include references."""
import errno
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile


def remove_includes(content, source):
    path = str(source.absolute())
    result = []
    for line in content.decode('utf-8', errors='surrogateescape').splitlines(keepends=True):
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            result.append(line)
            continue
        if not tokens:
            result.append(line)
            continue
        directive, separator, value = tokens[0].partition('=')
        args = ([value] if separator and value else []) + tokens[1:]
        if args[:1] == ['=']:
            args = args[1:]
        if directive.lower() != 'include' or path not in args:
            result.append(line)
            continue
        remaining = [arg for arg in args if arg != path]
        if remaining:
            newline = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            indentation = line[:len(line) - len(line.lstrip())]
            result.append(indentation + 'Include ' + shlex.join(remaining) + newline)
        # Keep Host * resets: removing one could change following settings' scope.
    return ''.join(result).encode('utf-8', errors='surrogateescape')


def cleanup(name, home, check=False):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
        raise ValueError('Invalid sandbox name.')
    state = home / '.ssh/sanboxed-agents' / name
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise ValueError(f'Refusing to traverse unexpected SSH state path: {state}')
    source = state / f'{name}.conf'
    files = [source, *(state / item for item in ('id_ed25519', 'id_ed25519.pub', 'known_hosts'))]
    for file in files:
        if file.exists() and not file.is_symlink() and not file.is_file():
            raise ValueError(f'Refusing to delete unexpected SSH file type: {file}')
    config = home / '.ssh/config'
    target = config.resolve()
    original = target.read_bytes() if target.exists() else b''
    updated = remove_includes(original, source)
    if check:
        return
    if updated != original:
        descriptor, temporary = tempfile.mkstemp(prefix='.sandbox-ssh-', dir=target.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(target.stat().st_mode))
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for file in files:
        file.unlink(missing_ok=True)  # Never follow a file symlink to its target.
    try:
        state.rmdir()  # Preserve unrelated files and directories.
    except OSError as error:
        if error.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
            raise


if __name__ == '__main__':
    try:
        args = sys.argv[1:]
        check = args[:1] == ['--check']
        if check:
            args = args[1:]
        if len(args) != 1:
            raise ValueError('Usage: remove-ssh-config.py [--check] NAME')
        cleanup(args[0], Path.home(), check)
    except (OSError, ValueError) as error:
        sys.exit(f'Error: {error}')
