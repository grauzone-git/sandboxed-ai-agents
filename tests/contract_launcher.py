"""Select a host launcher and discover its owner label using offline Podman calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

LABEL_FILTER = 'label=io.sandboxed-agents.project='


def launcher_command(checkout):
    configured = os.environ.get('SANDBOX_TEST_LAUNCHER')
    command = json.loads(configured) if configured is not None else [str(checkout / 'sandbox')]
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError('SANDBOX_TEST_LAUNCHER must be a nonempty JSON array of command arguments.')
    return [arg.replace('{checkout}', str(checkout)) for arg in command]


def discover_owner(command, checkout, env):
    # A separate fake engine keeps the discovery query out of scenario logs.
    with tempfile.TemporaryDirectory(prefix='sandbox-owner-') as temporary:
        root = Path(temporary)
        log = root / 'calls.jsonl'
        podman = root / 'podman'
        podman.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CONTRACT_OWNER_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'info': print('true')
elif sys.argv[1] != 'ps': sys.exit('Unexpected ownership probe command')
''')
        podman.chmod(0o755)
        identity = root / 'id'
        identity.write_text('#!/bin/sh\nprintf "1000\\n"\n')
        identity.chmod(0o755)
        probe_env = {**env, 'PATH': str(root) + os.pathsep + env['PATH'],
                     'CONTRACT_OWNER_LOG': str(log), 'HOME': str(root / 'home'),
                     'USERPROFILE': str(root / 'home'), 'XDG_STATE_HOME': str(root / 'state'),
                     'LOCALAPPDATA': str(root / 'local')}
        result = subprocess.run([*command, 'list'], cwd=checkout, env=probe_env,
                                text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError(f'Launcher owner discovery failed: {result.stderr or result.stdout}')
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        owners = {arg[len(LABEL_FILTER):] for call in calls if call[:1] == ['ps']
                  for arg in call if arg.startswith(LABEL_FILTER)}
        if len(owners) != 1 or '' in owners:
            raise ValueError('Launcher list must query Podman with one nonempty owner label filter.')
        return owners.pop()


def describe_launcher(checkout, env):
    command = launcher_command(checkout)
    return {'command': command, 'owner': discover_owner(command, checkout, env)}


if __name__ == '__main__':
    try:
        print(json.dumps(describe_launcher(Path(sys.argv[1]).resolve(), os.environ)))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(f'Error: {error}')
