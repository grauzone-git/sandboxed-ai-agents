"""Install Python fake commands as native executables on Windows."""
import os
from pathlib import Path
import shutil
import sys


WINDOWS_PODMAN_PREFLIGHT = r'''import json, os, sys
request = sys.argv[1:]
connection = os.environ.get('CONTAINER_CONNECTION') or 'podman-machine-default'
reply = None
if request[:3] == ['system', 'connection', 'list']:
    reply = [{'Name': connection, 'Default': True,
              'URI': 'ssh://user@127.0.0.1:50222/run/user/1000/podman/podman.sock'}]
elif request[:2] == ['machine', 'list']:
    reply = [{'Name': connection, 'VMType': 'wsl', 'Running': True}]
elif request[:2] == ['machine', 'inspect']:
    reply = [{'Name': connection, 'State': 'running', 'Rootful': False,
              'SSHConfig': {'Port': 50222, 'RemoteUsername': 'user'}}]
elif request[:2] == ['--connection', connection]:
    sys.argv = [sys.argv[0], *request[2:]]
    request = sys.argv[1:]
    if request[:1] == ['version']:
        reply = {'Client': {'Version': '6.0.0'}, 'Server': {'Version': '6.0.0'}}
    elif request == ['info', '--format', 'json']:
        reply = {'host': {'arch': 'amd64', 'os': 'linux', 'cgroupVersion': 'v2',
                         'cgroupControllers': ['cpu', 'memory', 'pids'],
                         'security': {'rootless': True,
                                      'seccompProfilePath': '/usr/share/containers/seccomp.json'}}}
else:
    sys.exit('Unexpected unpinned Windows Podman call: ' + repr(request))
if reply is not None:
    print(json.dumps(reply))
    sys.exit(0)
'''


def write_fake(directory, name, source):
    directory = Path(directory)
    if os.name != 'nt':
        command = directory / name
        command.write_text(source, encoding='utf-8')
        command.chmod(0o755)
        return command

    relay = os.environ.get('SANDBOX_TEST_FAKE_COMMAND')
    if not relay:
        raise ValueError('Set SANDBOX_TEST_FAKE_COMMAND to a Go binary built from '
                         './tests/fake-command before running Windows contract tests.')
    if name == 'podman':
        source = WINDOWS_PODMAN_PREFLIGHT + source
    (directory / f'{name}.py').write_text(source, encoding='utf-8')
    (directory / f'{name}.python').write_text(sys.executable, encoding='utf-8')
    command = directory / f'{name}.exe'
    shutil.copy2(relay, command)
    return command
