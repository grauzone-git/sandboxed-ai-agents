"""Remove only one sandbox's generated host SSH files and Include references."""
import errno
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile


def parse_directive(line):
    tokens = shlex.split(line, comments=True)
    if not tokens:
        return '', []
    directive, separator, value = tokens[0].partition('=')
    args = ([value] if separator and value else []) + tokens[1:]
    if args[:1] == ['=']:
        args = args[1:]
    return directive.lower(), args


def split_comment(line):
    """Keep inline comments without mistaking quoted or escaped # for comments."""
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == '\\' and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == '#':
            return line[:index], line[index:]
    return line, ''


def deduplicate_entries(lines):
    """Deduplicate settings within a scope, retaining ordering and scope changes."""
    result = []
    scope = None
    seen = set()
    previous = None
    for line in lines:
        try:
            directive, args = parse_directive(line)
        except ValueError:
            result.append(line)
            scope, previous = None, None
            seen.clear()
            continue
        if not directive:
            result.append(line)
            continue
        body, comment = split_comment(line)
        # Retain argument quoting: command-valued settings execute through a
        # shell, so equal shlex tokens alone do not imply equivalent commands.
        value = re.sub(r'^\s*[^\s=]+(?:\s*=\s*|\s+)?', '', body).strip()
        entry = (directive, value)
        duplicate = False
        if directive == 'host':
            host = (directive, tuple(args))
            duplicate = scope == host
            if not duplicate:
                scope = host
                seen.clear()
        elif directive == 'match':
            # Match conditions may execute commands or depend on parsing passes.
            scope = None
            seen.clear()
        elif directive == 'include':
            duplicate = entry == previous
            if not duplicate:
                # An included file can leave parsing inside a Host/Match block.
                scope = None
                seen.clear()
        elif directive == 'sendenv':
            # SendEnv supports removals (-PATTERN); intervening changes matter.
            duplicate = entry == previous
        else:
            duplicate = entry in seen
            seen.add(entry)
        if duplicate:
            if comment:
                indentation = line[:len(line) - len(line.lstrip())]
                result.append(indentation + comment)
        else:
            result.append(line)
            previous = entry
    return result


def remove_includes(content, source):
    path = str(source.absolute())
    result = []
    for line in content.decode('utf-8', errors='surrogateescape').splitlines(keepends=True):
        try:
            directive, args = parse_directive(line)
        except ValueError:
            result.append(line)
            continue
        if directive != 'include' or path not in args:
            result.append(line)
            continue
        remaining = [arg for arg in args if arg != path]
        if remaining:
            newline = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            indentation = line[:len(line) - len(line.lstrip())]
            result.append(indentation + 'Include ' + shlex.join(remaining) + newline)
    return ''.join(deduplicate_entries(result)).encode('utf-8', errors='surrogateescape')


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
