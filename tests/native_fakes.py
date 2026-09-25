"""Install Python fake commands as native executables on Windows."""
import os
from pathlib import Path
import shutil
import sys


WINDOWS_PODMAN_PREFLIGHT = r'''import json, os, shlex, sys
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
elif request[:2] == ['machine', 'ssh']:
    guest = shlex.split(request[3])
    if guest[:3] == ['podman', 'unshare', 'cat']:
        print('0 1000 1\n1 100000 65536')
    elif guest[:2] == ['cat', '--']:
        source = os.environ.get('TEST_SECCOMP')
        if source:
            with open(source) as stream: print(stream.read())
        else:
            print(json.dumps({'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': []}))
    elif guest[:2] == ['sh', '-c'] and 'mktemp' in guest[2]:
        sys.stdin.read()
        print('/home/user/.local/share/sandboxed-agents/seccomp/' + guest[-2] + '/' + guest[-1] + '.json')
    elif not (guest[:2] == ['sh', '-c'] and 'test -c /dev/fuse' in guest[2]):
        sys.exit('Unexpected guest Podman call: ' + repr(guest))
    sys.exit(0)
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


def write_node_fake(directory, name, source, node):
    if os.name != 'nt':
        return write_fake(directory, name, source)
    script = Path(directory) / f'{name}.cjs'
    script.write_text(source, encoding='utf-8')
    relay = ('import subprocess, sys\n'
             f'sys.exit(subprocess.call([{node!r}, {str(script)!r}, *sys.argv[1:]]))\n')
    return write_fake(directory, name, relay)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('name')
    parser.add_argument('--node')
    args = parser.parse_args()
    try:
        source = sys.stdin.read()
        if args.node:
            write_node_fake(args.directory, args.name, source, args.node)
        else:
            write_fake(args.directory, args.name, source)
    except (OSError, ValueError) as error:
        sys.exit(f'Error: {error}')
