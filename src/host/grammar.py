"""Map the name-first launcher grammar onto the internal command-first form.

Both launchers accept `LAUNCHER NAME COMMAND [SUBCOMMAND] [PARAMETERS]`. Only
build, list and update --all omit NAME. Command names are reserved so the first
argument is never ambiguous, and old command-first forms fail with a hint.
"""
import re
import shlex
import sys

HELP = ('help', '--help', '-h')
GLOBAL = ('build', 'list', 'update')
SESSIONS = ('copilot', 'claude', 'codex', 'hermes', 'opencode', 'deepseek', 't3')
SANDBOX = ('up', 'start', 'stop', 'restart', 'remove', 'shell', 'ssh-config', 'check', 'check-full',
           'fingerprint', 'agents', 'tools', 'run', 'tool', 'service', 'forward', 'update', *SESSIONS)
RESERVED = frozenset((*HELP, *GLOBAL, *SANDBOX, 'azdo'))


def command_line(prog, args):
    return ' '.join([prog, *(arg if arg in ('NAME', 'COMMAND') else shlex.quote(arg) for arg in args)])


def removed_azdo(prog, name='NAME'):
    return ValueError('The azdo --pat-env command was removed. Save a PAT for the sandbox with: '
                      + command_line(prog, [name, 'tools', 'setup', 'azdo', '--persist']))


def update_names(args):
    """Positional sandbox names in old-style update arguments."""
    names, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg == '--capabilities':
            skip = True
        elif not arg.startswith('-'):
            names.append(arg)
    return names


def normalize(args, prog):
    """Return [COMMAND, NAME, *PARAMETERS] for sandbox commands, or [COMMAND, *PARAMETERS]."""
    if not args or args[0] in HELP:
        return ['help']
    first, rest = args[0], list(args[1:])
    if first in RESERVED and rest and rest[0] in SANDBOX:
        raise ValueError(f"'{first}' is a command name and cannot be used as a sandbox name.")
    if first in ('build', 'list'):
        return [first, *rest]
    if first == 'update':
        if '--all' in rest or any(arg in HELP for arg in rest):
            return [first, *rest]
        names = update_names(rest)
        options = [arg for arg in rest if arg not in names]
        hints = [command_line(prog, [name, 'update', *options]) for name in names]
        raise ValueError('Update one sandbox with its name first, or all of them with --all: '
                         + ', '.join(hints or [command_line(prog, ['NAME', 'update'])])
                         + f', {command_line(prog, ["update", "--all"])}')
    if first == 'azdo':
        raise removed_azdo(prog, rest[0] if rest and not rest[0].startswith('-') else 'NAME')
    if first in SANDBOX:
        name, parameters = (rest[0], rest[1:]) if rest and not rest[0].startswith('-') else ('NAME', rest)
        raise ValueError('Commands take the sandbox name first: '
                         + command_line(prog, [name, first, *parameters]))
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', first):
        raise ValueError('Use an alphanumeric container name (plus _, ., -).')
    if not rest:
        raise ValueError(f'Use {command_line(prog, ["NAME", "COMMAND"])} [SUBCOMMAND] [PARAMETERS]. '
                         f'Run {prog} --help.')
    command, parameters = rest[0], rest[1:]
    if command == 'azdo':
        raise removed_azdo(prog, first)
    if command in ('build', 'list'):
        raise ValueError(f'{command_line(prog, [command])} does not take a sandbox name.')
    if command == 'update' and '--all' in parameters:
        raise ValueError(f'Use {command_line(prog, ["update", "--all"])} without a sandbox name.')
    if command == 'update' and update_names(parameters):
        raise ValueError(f'Update one sandbox per command: {command_line(prog, [first, "update"])} [--no-build] '
                         '[--capabilities LIST], or update --all.')
    if command not in SANDBOX:
        raise ValueError(f'Unknown command: {command}. Run {prog} --help.')
    return [command, first, *parameters]


if __name__ == '__main__':
    # Print NUL-terminated arguments so Bash can read them back without quoting.
    try:
        sys.stdout.write(''.join(f'{arg}\0' for arg in normalize(sys.argv[2:], sys.argv[1])))
    except ValueError as error:
        sys.exit(f'Error: {error}')
