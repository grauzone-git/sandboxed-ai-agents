"""List checkout-owned sandboxes without starting or modifying any container."""
import json
import re
import subprocess
import sys
from pathlib import Path

from containers import LABEL, owned_names

AGENT_STATE = '/home/agent/.local/state/sandbox-agents/config.json'


def podman(*args, capture=False, check=True):
    return subprocess.run(['podman', *map(str, args)], check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.DEVNULL if not check else None)


def enabled_agents(name, runner):
    # Agents live in the home volume, which is only readable while running.
    result = runner('exec', '--user', '1000:1000', name, '/bin/cat', AGENT_STATE, capture=True, check=False)
    if result.returncode != 0:
        return 'unknown'
    try:
        ids = sorted(json.loads(result.stdout)['enabled'])
    except (ValueError, KeyError, TypeError):
        return 'unknown'
    # The state file is agent-writable; never echo arbitrary text to the terminal.
    if not all(isinstance(item, str) and re.fullmatch(r'[a-z0-9-]+', item) for item in ids):
        return 'unknown'
    return ','.join(ids) or 'none'


def describe(info, runner):
    name = info['Name'].lstrip('/')
    running = (info.get('State') or {}).get('Running') is True
    workspace = '-'
    for mount in info.get('Mounts') or []:
        if mount.get('Destination') == '/workspace':
            workspace = mount.get('Source') if mount.get('Type') == 'bind' else mount.get('Name')
    bindings = ((info.get('HostConfig') or {}).get('PortBindings') or {}).get('2222/tcp') or [{}]
    return {'name': name, 'state': 'running' if running else 'stopped', 'port': bindings[0].get('HostPort') or '-',
            'agents': enabled_agents(name, runner) if running else '-', 'workspace': workspace or '-'}


def sandboxes(owner, runner=podman):
    rows = []
    for name in sorted(set(owned_names(owner, runner))):
        result = runner('container', 'inspect', name, capture=True, check=False)
        if result.returncode != 0:
            continue  # Removed after it was listed.
        try:
            info = json.loads(result.stdout)[0]
            # The label filter narrows the search; ownership is still checked exactly.
            if (info.get('Config', {}).get('Labels') or {}).get(LABEL) == owner:
                rows.append(describe(info, runner))
        except (ValueError, IndexError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f'Podman returned unexpected details for {name}.') from error
    return rows


def render(rows):
    if not rows:
        return 'No sandboxes owned by this checkout.'
    header = {'name': 'NAME', 'state': 'STATE', 'port': 'SSH PORT', 'agents': 'AGENTS', 'workspace': 'WORKSPACE'}
    columns = list(header)
    widths = {key: max(len(str(row[key])) for row in [header, *rows]) for key in columns[:-1]}
    return '\n'.join('  '.join([*(str(row[key]).ljust(widths[key]) for key in columns[:-1]), str(row['workspace'])])
                     for row in [header, *rows])


def main(args):
    if len(args) != 1:
        raise ValueError('Usage: sandboxes.py PROJECT')
    try:
        print(render(sandboxes(str(Path(args[0])))))
    except subprocess.CalledProcessError as error:
        raise RuntimeError('Podman could not list sandboxes.') from error


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(f'Error: {error}')
