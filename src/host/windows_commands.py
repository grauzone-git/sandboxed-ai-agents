"""Route Windows agent and tool commands through owned unprivileged managers."""
from dataclasses import dataclass
import json
import os
import re
import shutil
import socket
import sys

from windows_lifecycle import owned
from windows_runtime import native
from windows_ssh import SshSetup

SESSIONS = ('copilot', 'claude', 'codex', 'hermes', 'opencode', 'deepseek', 't3')
COMMANDS = ('agents', 'tools', 'run', 'tool', 'service', 'forward', *SESSIONS)


@dataclass
class Command:
    name: str
    manager: str
    arguments: list
    interactive: bool = False
    forward: tuple = ()


def parse(action, args, project, validate_name, selections):
    if not args:
        raise ValueError(f'Use {action} NAME [list|check|set|enable|disable|update LIST].')
    name = validate_name(args[0])
    if action in ('service', 'forward'):
        catalog = json.loads((project / 'src/container/tools.json').read_text())
        target = {'hermes': 'hermes-dashboard', 'deepseek': 'deepseek-ui'}.get(args[1], args[1]) if len(args) > 1 else ''
        if len(args) not in (2, 3) or target not in catalog or not catalog[target].get('service'):
            raise ValueError(f'Use {action} NAME t3|hermes-dashboard|deepseek-ui|tokentracker [OPTION].')
        if action == 'service':
            operation = args[2] if len(args) == 3 else 'status'
            if operation not in ('status', 'start', 'stop', 'restart', 'logs'):
                raise ValueError('Choose service status, start, stop, restart, or logs.')
            return Command(name, 'tools', ['service', target, operation])
        remote = catalog[target]['port']
        local = args[2] if len(args) == 3 else str(remote)
        if not re.fullmatch(r'[1-9][0-9]{3,4}', local) or not 1024 <= int(local) <= 65535:
            raise ValueError('Use a local port from 1024 to 65535.')
        return Command(name, 'tools', ['service', target, 'start'], forward=(int(local), remote))
    if action in SESSIONS:
        if len(args) != 1:
            raise ValueError(f'Use {action} NAME; use run/tool for additional arguments.')
        return Command(name, 'tools' if action == 't3' else 'agents', ['session', action], True)
    if action in ('run', 'tool'):
        kind = 'agents' if action == 'run' else 'tools'
        catalog = json.loads((project / f'src/container/{kind}.json').read_text())
        if len(args) < 2 or args[1] not in catalog:
            raise ValueError(f'Use {action} NAME followed by a valid {kind[:-1]} ID and optional arguments.')
        return Command(name, kind, ['run', *args[1:]], True)
    operation = args[1] if len(args) > 1 else 'list'
    if operation in ('list', 'check') and len(args) <= 2:
        return Command(name, action, [operation])
    if operation in ('set', 'enable', 'disable', 'update') and len(args) == 3:
        selection = selections(args[2], action)
        if operation == 'update' and args[2] == 'all':
            selection = 'all'  # The manager interprets this as all enabled entries.
        return Command(name, action, [operation, selection])
    if len(args) == 3:
        allowed = ('codex', 'claude', 'opencode', 'copilot', 'hermes') if action == 'agents' else ('github',)
        if operation == 'login' and args[2] in allowed:
            return Command(name, action, ['login', args[2]], True)
        if action == 'tools' and operation == 'setup' and args[2] == 't3':
            return Command(name, action, ['setup', 't3'], True)
    raise ValueError(f'Invalid {action} operation or arguments; use list, check, set, enable, disable, update, login, or tools setup t3.')


def require_forward_port(port):
    # Windows can permit address reuse; request an exclusive bind for this probe.
    # SSH still checks binding after the probe closes, since it cannot reserve a port.
    try:
        with socket.socket() as listener:
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', port))
    except OSError as error:
        raise ValueError(f'Forwarding port {port} is unavailable; choose another LOCAL_PORT.') from error


def execute(runtime, project, command):
    identity = owned(runtime, project, command.name)['Id']
    ssh = None
    config = None
    if command.forward:
        ssh = shutil.which('ssh')
        if not ssh:
            raise ValueError('Install the Windows OpenSSH client before forwarding ports.')
        setup = SshSetup(runtime, project, command.name, require_keygen=False)
        config = setup.state / f'{command.name}.conf'
        if not all(path.is_file() for path in (config, setup.state / 'known_hosts', setup.state / 'id_ed25519')):
            raise ValueError(f'Configure SSH first: ./sandbox.ps1 ssh-config {command.name} --install')
        require_forward_port(command.forward[0])
    interactive = []
    if command.interactive:
        interactive = ['-i'] + (['-t'] if sys.stdin.isatty() and sys.stdout.isatty() else [])
    runtime.run('exec', *interactive, '--user', '1000:1000', '--workdir', '/workspace',
                identity, f'/usr/local/bin/sandbox-{command.manager}', *command.arguments, capture=False)
    if command.forward:
        local, remote = command.forward
        print(f'Forwarding http://127.0.0.1:{local} to {command.name}:{remote}; leave this terminal running.', flush=True)
        native([ssh, '-F', config, '-o', 'ExitOnForwardFailure=yes', '-o', 'BatchMode=yes',
                '-N', '-L', f'127.0.0.1:{local}:127.0.0.1:{remote}', command.name],
               runner=runtime.runner, capture=False)
