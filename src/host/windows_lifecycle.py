"""Operate only checkout-owned Windows sandboxes while preserving persistent data."""
import sys

from containers import LABEL
from update import mount_policy
from windows_runtime import native
from windows_ssh import SshSetup

COMMANDS = ('start', 'stop', 'restart', 'remove', 'shell', 'check', 'check-full', 'fingerprint')


def parse(action, args, validate_name):
    flag = '--ssh-config' if action in ('start', 'restart') else '--volumes' if action == 'remove' else None
    if not args or len(args) > 2 or (len(args) == 2 and (flag is None or args[1] != flag)):
        raise ValueError(f'Use {action} NAME' + (f' [{flag}]' if flag else '') + '.')
    return validate_name(args[0]), len(args) == 2


def owned(runtime, project, name):
    info = runtime.json('inspect', name)[0]
    if info.get('Name', '').lstrip('/') != name or (info.get('Config', {}).get('Labels') or {}).get(LABEL) != project:
        raise ValueError(f'Container {name} is not owned by this checkout.')
    return info


def execute(runtime, project, action, name, option, wait_ready):
    info = owned(runtime, project, name)
    identity = info['Id']
    if action in ('start', 'stop', 'restart'):
        ssh = SshSetup(runtime, project, name) if option else None
        runtime.run(action, identity, capture=False)
        if ssh is not None:
            wait_ready(runtime, identity)
            ssh.install()
        return
    if action == 'remove':
        ssh = SshSetup(runtime, project, name, require_keygen=False)
        volumes = []
        if option:
            for volume in (f'{name}-home', f'{name}-sshd', f'{name}-workspace'):
                if runtime.exists('volume', volume):
                    labels = runtime.json('volume', 'inspect', volume)[0].get('Labels') or {}
                    if labels.get(LABEL) != project:
                        raise ValueError(f'Volume {volume} belongs to another checkout.')
                    volumes.append(volume)
        runtime.run('stop', identity, capture=False)
        runtime.run('rm', identity, capture=False)
        ssh.remove()
        for volume in volumes:
            runtime.run('volume', 'rm', volume, capture=False)
        print(f'Removed {name}. Host workspace directories retained. '
              f'Named volumes {"deleted" if option else "retained"}. Local SSH setup removed.')
        return
    if action == 'fingerprint':
        ssh = SshSetup(runtime, project, name)
        known = ssh.state / 'known_hosts'
        if not known.is_file():
            raise ValueError(f'No pinned host key; run ./sandbox.ps1 ssh-config {name} --install first.')
        native([ssh.keygen, '-lf', known], runner=runtime.runner, capture=False)
        return
    if action in ('check', 'check-full'):
        for description in mount_policy.validate_mounts(name, info['Mounts']):
            print(description)
        runtime.run('exec', '--user', '1000:1000', '--workdir', '/workspace', identity,
                    '/usr/local/bin/agent-smoke', *(['--full'] if action == 'check-full' else []), capture=False)
        return
    if action == 'shell':
        interactive = ['-i'] + (['-t'] if sys.stdin.isatty() and sys.stdout.isatty() else [])
        runtime.run('exec', *interactive, '--user', '1000:1000', '--workdir', '/workspace',
                    identity, '/bin/bash', capture=False)
        return
    raise ValueError(f'Unsupported lifecycle command: {action}')
