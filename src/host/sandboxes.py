"""List checkout-owned sandboxes without starting or modifying any container."""
import json
import re
import subprocess
import sys
from pathlib import Path

from containers import LABEL

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
    state = info['State']
    running = state.get('Running', False)
    status = 'running' if running else 'stopped' if state.get('Status') in ('exited', 'stopped', 'created') else state.get('Status', 'unknown')
    workspace = '-'
    for mount in info.get('Mounts') or []:
        if mount.get('Destination') == '/workspace':
            workspace = mount.get('Source') if mount.get('Type') == 'bind' else mount.get('Name')
    bindings = ((info.get('HostConfig') or {}).get('PortBindings') or {}).get('2222/tcp') or [{}]
    return {'name': name, 'state': status, 'port': bindings[0].get('HostPort') or '-',
            'agents': enabled_agents(name, runner) if running else '-', 'workspace': workspace or '-'}


def sandboxes(owner, runner=podman):
    names = runner('ps', '--all', '--filter', f'label={LABEL}={owner}', '--format', '{{.Names}}',
                   capture=True).stdout.split()
    rows = []
    for name in sorted(set(names)):
        info = json.loads(runner('container', 'inspect', name, capture=True).stdout)[0]
        # The label filter narrows the search; ownership is still checked exactly.
        if (info.get('Config', {}).get('Labels') or {}).get(LABEL) == owner:
            rows.append(describe(info, runner))
    return rows


def render(rows):
    if not rows:
        return 'No sandboxes owned by this checkout.'
    header = {'name': 'NAME', 'state': 'STATE', 'port': 'SSH PORT', 'agents': 'AGENTS', 'workspace': 'WORKSPACE'}
    columns = list(header)
    widths = {key: max(len(str(row[key])) for row in [header, *rows]) for key in columns[:-1]}
    return '\n'.join('  '.join([*(str(row[key]).ljust(widths[key]) for key in columns[:-1]), str(row['workspace'])])
                     for row in [header, *rows])


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: sandboxes.py PROJECT')
    try:
        print(render(sandboxes(str(Path(sys.argv[1])))))
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.CalledProcessError) as error:
        sys.exit(f'Error: {error}')
