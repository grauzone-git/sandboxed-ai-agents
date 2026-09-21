"""Validate and pin the Windows launcher to an existing rootless WSL2 engine."""
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


class NativeError(RuntimeError):
    def __init__(self, command, result):
        self.returncode = result.returncode
        super().__init__(f'{command[0]} failed ({result.returncode}): '
                         f'{(result.stderr or "").strip() or " ".join(command[1:3])}')


def native(command, *, runner=subprocess.run, capture=True, input=None, allowed=(0,)):
    result = runner(list(map(str, command)), text=True, encoding='utf-8',
                    stdout=subprocess.PIPE if capture else None,
                    stderr=subprocess.PIPE if capture else None, input=input)
    if result.returncode not in allowed:
        raise NativeError(command, result)
    return result


class Runtime:
    def __init__(self, runner=subprocess.run):
        self.runner = runner
        self.connection = None
        self.machine = None
        self.info = None
        self.executable = shutil.which('podman')
        if not self.executable:
            raise ValueError('Install Podman 6.0+ and configure a rootless WSL2 machine first.')

    def local(self, *args, **kwargs):
        return native([self.executable, *args], runner=self.runner, **kwargs)

    def run(self, *args, **kwargs):
        return self.local('--connection', self.connection, *args, **kwargs)

    def json(self, *args):
        return json.loads(self.run(*args).stdout)

    def exists(self, kind, name):
        return self.run(kind, 'exists', name, allowed=(0, 1)).returncode == 0

    def guest(self, *args, **kwargs):
        # Machine SSH joins its arguments into a remote shell command.
        command = ' '.join(shlex.quote(str(arg)) for arg in args)
        return self.local('machine', 'ssh', self.machine, command, **kwargs)

    def preflight(self):
        if os.name == 'nt':
            version = sys.getwindowsversion()
            if version.build < 22000 or version.product_type != 1 or platform.machine().lower() != 'amd64':
                raise ValueError('The Windows launcher requires Windows 11 x64.')
        if os.environ.get('CONTAINER_HOST'):
            raise ValueError('Unset CONTAINER_HOST; select an existing WSL2 machine connection instead.')
        connections = json.loads(self.local('system', 'connection', 'list', '--format', 'json').stdout)
        selected = os.environ.get('CONTAINER_CONNECTION')
        matches = [item for item in connections if
                   (item.get('Name') == selected if selected else item.get('Default'))]
        if len(matches) != 1:
            raise ValueError('Select one configured rootless WSL2 Podman connection with CONTAINER_CONNECTION.')
        connection = matches[0]
        uri = urlsplit(connection.get('URI', ''))
        if (uri.scheme != 'ssh' or uri.hostname not in ('127.0.0.1', 'localhost', '::1') or
                not re.fullmatch(r'/run/user/[1-9][0-9]*/podman/podman.sock', uri.path)):
            raise ValueError('Select a local rootless WSL2 machine connection, not a rootful or remote engine.')
        machines = json.loads(self.local('machine', 'list', '--format', 'json').stdout)
        matches = [item for item in machines if item.get('Name') == connection['Name']]
        if len(matches) != 1 or matches[0].get('VMType') != 'wsl' or not matches[0].get('Running'):
            raise ValueError('The selected connection must name a running WSL2 machine. Start it yourself with podman machine start NAME.')
        machine = json.loads(self.local('machine', 'inspect', matches[0]['Name']).stdout)[0]
        ssh = machine.get('SSHConfig', {})
        if (machine.get('Rootful') or machine.get('State') != 'running' or
                ssh.get('Port') != uri.port or ssh.get('RemoteUsername') != uri.username):
            raise ValueError('The selected connection does not match the running rootless machine SSH endpoint.')
        self.connection = connection['Name']
        self.machine = machine['Name']
        versions = self.json('version', '--format', 'json')
        for kind in ('Client', 'Server'):
            version = versions.get(kind, {}).get('Version', '')
            if not re.match(r'^(?:[6-9]|[1-9][0-9]+)\.', version):
                raise ValueError('Both the Windows Podman client and machine engine must be version 6.0+.')
        self.info = self.json('info', '--format', 'json')
        host = self.info.get('host', {})
        if host.get('security', {}).get('rootless') is not True or host.get('arch') != 'amd64':
            raise ValueError('The selected engine must be rootless Linux x64.')

    def require_resources(self):
        host = self.info.get('host', {})
        if host.get('cgroupVersion') != 'v2' or not {'cpu', 'memory', 'pids'}.issubset(host.get('cgroupControllers', [])):
            raise ValueError('The WSL2 engine must delegate cgroups v2 cpu, memory, and pids controllers.')
