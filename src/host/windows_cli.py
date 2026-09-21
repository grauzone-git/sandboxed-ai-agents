"""Dispatch PowerShell sandbox operations through shared ownership and storage policies."""
import importlib.util
import math
import os
import re
import socket
import time
from pathlib import Path
import subprocess
import sys

from capabilities import parse_capabilities
from containers import LABEL, create_args
from windows_capabilities import prepare_nested
from windows_paths import checkout_identity, workspace_path
from windows_runtime import NativeError, Runtime
from windows_ssh import SshSetup
import windows_lifecycle
import windows_commands
import windows_update
import update

PROJECT = Path(__file__).resolve().parents[2]
HELP = '''Usage:
  ./sandbox.ps1 build [additional podman build arguments]
  ./sandbox.ps1 up NAME [WORKSPACE] --agents LIST [--tools LIST] [--ssh-port PORT]
                   [--capabilities podman|none] [--ssh-config] [--cpus N] [--memory SIZE]
  ./sandbox.ps1 agents|tools NAME [list|check]
  ./sandbox.ps1 agents|tools NAME set|enable|disable|update LIST
  ./sandbox.ps1 agents NAME login codex|claude|opencode|copilot|hermes
  ./sandbox.ps1 tools NAME login github
  ./sandbox.ps1 tools NAME setup t3
  ./sandbox.ps1 run NAME AGENT [arguments...]
  ./sandbox.ps1 tool NAME TOOL [arguments...]
  ./sandbox.ps1 copilot|claude|codex|hermes|opencode|t3|deepseek NAME
  ./sandbox.ps1 service NAME t3|hermes-dashboard|deepseek-ui|tokentracker [status|start|stop|restart|logs]
  ./sandbox.ps1 forward NAME t3|hermes-dashboard|deepseek-ui|tokentracker [LOCAL_PORT]
  ./sandbox.ps1 ssh-config NAME --install
  ./sandbox.ps1 start|restart NAME [--ssh-config]
  ./sandbox.ps1 stop|shell|check|check-full|fingerprint NAME
  ./sandbox.ps1 remove NAME [--volumes]
  ./sandbox.ps1 update NAME...|--all [--no-build] [--capabilities podman|none]

Requires Windows 11 x64, PowerShell 7, Python 3.9+, and rootless WSL2 Podman 6.0+.
Set SANDBOX_IMAGE, SANDBOX_CPUS, SANDBOX_MEMORY, or SANDBOX_PYTHON as needed.
Quote comma-separated selections in PowerShell: --agents 'codex,claude'.
Start/restart touch host SSH files only with --ssh-config.
Remove always cleans managed SSH setup; --volumes also deletes owned named data.
Host workspace directories are always retained. Update preserves storage, SSH,
resources, selections, capabilities, and running/stopped state; failed replacement
rolls back without deleting volumes. --no-build reuses the base image; optional
capability layers may still build. Shell/check and agent/tool commands use
Podman exec without host SSH. Agent/tool login, setup, runs and sessions retain
interactive input. Update all means all enabled agents/tools; set none disables
selection but retains cached installations. Service/forward aliases: hermes and
deepseek. Forwarding starts the selected service and requires existing managed
SSH setup; it binds only localhost and never installs host SSH automatically.
'''


def selections(value, kind='agents', agents=None):
    spec = importlib.util.spec_from_file_location('agent_selection', PROJECT / 'src/host/agent-selection.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_selection(value, kind, agents)


def parse_up(args):
    values, positional = {}, []
    options = {'--agents', '--tools', '--ssh-port', '--capabilities', '--cpus', '--memory'}
    index = 0
    while index < len(args):
        option = args[index]
        if option.startswith('-'):
            if option in values:
                raise ValueError(f'Duplicate option: {option}')
            if option == '--ssh-config':
                values[option] = True
            elif option in options and index + 1 < len(args):
                index += 1
                values[option] = args[index]
            else:
                raise ValueError(f'Unknown option or missing value: {option}')
        else:
            positional.append(option)
        index += 1
    if not 1 <= len(positional) <= 3:
        raise ValueError('Use up NAME [WORKSPACE [SSH_PORT]] --agents LIST.')
    name = validate_name(positional[0])
    if len(positional) == 3 and '--ssh-port' in values:
        raise ValueError('Use either positional SSH_PORT or --ssh-port, not both.')
    port = values.get('--ssh-port', positional[2] if len(positional) == 3 else '2222')
    if not re.fullmatch(r'[1-9][0-9]{3,4}', port) or not 1024 <= int(port) <= 65535:
        raise ValueError('Use an SSH port from 1024 to 65535.')
    agents = selections(values.get('--agents', 'none'))
    if agents == 'none':
        raise ValueError('Creating a sandbox requires --agents with at least one agent.')
    tools = selections(values['--tools'], 'tools', agents) if '--tools' in values else None
    cpus = values.get('--cpus', os.environ.get('SANDBOX_CPUS', '4'))
    memory = values.get('--memory', os.environ.get('SANDBOX_MEMORY', '8g'))
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', cpus) or not math.isfinite(float(cpus)) or float(cpus) <= 0:
        raise ValueError('CPUs must be a positive number.')
    if not re.fullmatch(r'[1-9][0-9]*[bkmgBKMG]?', memory):
        raise ValueError('Memory must be a positive integer with an optional b, k, m, or g suffix.')
    return {'name': name, 'workspace': positional[1] if len(positional) > 1 else None,
            'port': port, 'agents': agents, 'tools': tools, 'cpus': cpus, 'memory': memory,
            'capabilities': parse_capabilities(values.get('--capabilities', 'none')),
            'ssh': values.get('--ssh-config', False)}


def validate_name(name):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name) or name.endswith('.'):
        raise ValueError('Use an alphanumeric container name (plus _, ., -), without a trailing dot.')
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(10)], *[f'LPT{i}' for i in range(10)]}:
        raise ValueError('The container name must not be a Windows reserved filename.')
    return name


def report_startup_failure(runtime, name):
    try:
        state = runtime.json('inspect', name)[0].get('State', {})
        print(f"Container {name}: {state.get('Status', 'unknown')} "
              f"(exit code {state.get('ExitCode', 'unknown')}, "
              f"OOM killed: {state.get('OOMKilled', False)}).", file=sys.stderr)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, IndexError):
        # Diagnostics must never replace the original native failure/exit code.
        pass
    print(f'Inspect startup logs: podman --connection {runtime.connection} logs --tail 100 {name}\n'
          'Container and volumes were retained; no data was deleted.', file=sys.stderr)


def wait_ready(runtime, name):
    for _ in range(30):
        try:
            result = runtime.run('exec', '--user', '0', name, '/usr/bin/test', '-f',
                                 '/var/lib/agent-sshd/ssh_host_ed25519_key.pub', allowed=(0, 1))
        except NativeError:
            report_startup_failure(runtime, name)
            raise
        if result.returncode == 0:
            return
        time.sleep(0.5)
    report_startup_failure(runtime, name)
    raise ValueError('SSH initialization did not complete.')


def create(runtime, options, image, project):
    name = options['name']
    ssh_root = Path.home() / '.ssh/sanboxed-agents'
    directory = workspace_path(options['workspace'], PROJECT, ssh_root) if options['workspace'] is not None else None
    ssh = SshSetup(runtime, project, name) if options['ssh'] else None
    runtime.require_resources()
    if runtime.exists('container', name):
        raise ValueError(f'Container {name} already exists. Creation never modifies it; use Podman to manage the existing container.')
    if not runtime.exists('image', image):
        raise ValueError('Build the image first with ./sandbox.ps1 build.')
    try:
        with socket.socket() as listener:
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', int(options['port'])))
    except OSError as error:
        raise ValueError(f"SSH port {options['port']} is unavailable; choose --ssh-port.") from error
    volumes = [f'{name}-home', f'{name}-sshd']
    if directory is None:
        volumes.append(f'{name}-workspace')
    missing = []
    for volume in volumes:
        if runtime.exists('volume', volume):
            labels = runtime.json('volume', 'inspect', volume)[0].get('Labels') or {}
            if labels.get(LABEL) != project:
                raise ValueError(f'Volume {volume} belongs to another checkout.')
        else:
            missing.append(volume)
    seccomp = None
    if options['capabilities'] == 'podman':
        image, seccomp = prepare_nested(runtime, project, image)
    for volume in missing:
        runtime.run('volume', 'create', '--label', f'{LABEL}={project}', volume)
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
        directory = workspace_path(str(directory), PROJECT, ssh_root)
    mount = f'{directory}:/workspace:Z' if directory is not None else f'{name}-workspace:/workspace'
    try:
        runtime.run(*create_args(name, project, image, mount, options['port'], options['memory'], options['cpus'],
                                 capabilities=options['capabilities'], seccomp=seccomp), capture=False)
        wait_ready(runtime, name)
        if directory is None:
            runtime.run('exec', '--user', '0', name, '/bin/chown', '1000:1000', '/workspace')
    except (NativeError, ValueError):
        if ssh is not None:
            print('Host SSH setup has not run for this creation attempt. Once the container is running, use:\n'
                  f'  ./sandbox.ps1 ssh-config {name} --install\n'
                  f'Then connect with ssh {name} (root login is disabled).', file=sys.stderr)
        raise
    if ssh is not None:
        ssh.install()
    if options['tools'] is not None:
        runtime.run('exec', '--user', '1000:1000', '--workdir', '/workspace', name,
                    '/usr/local/bin/sandbox-tools', 'set', 'none', capture=False)
    runtime.run('exec', '--user', '1000:1000', '--workdir', '/workspace', name,
                '/usr/local/bin/sandbox-agents', 'init', options['agents'], capture=False)
    runtime.run('exec', '--user', '1000:1000', '--workdir', '/workspace', name,
                '/usr/local/bin/sandbox-tools', 'init', *([options['tools']] if options['tools'] is not None else []), capture=False)


def main(args, *, runner=subprocess.run):
    try:
        if not args or args[0] in ('help', '--help', '-h'):
            print(HELP)
            return 0
        if args[0] not in ('build', 'up', 'ssh-config', 'update', *windows_lifecycle.COMMANDS, *windows_commands.COMMANDS):
            raise ValueError('Unknown command. Run ./sandbox.ps1 --help.')
        update_options = update.parse_args(args[1:], prog='./sandbox.ps1 update') if args[0] == 'update' else None
        if update_options is not None:
            for name in update_options.names:
                validate_name(name)
        options = parse_up(args[1:]) if args[0] == 'up' else None
        if args[0] == 'ssh-config':
            if len(args) != 3 or args[2] != '--install':
                raise ValueError('Use ssh-config NAME --install.')
            validate_name(args[1])
        command = windows_commands.parse(args[0], args[1:], PROJECT, validate_name, selections) if args[0] in windows_commands.COMMANDS else None
        lifecycle = windows_lifecycle.parse(args[0], args[1:], validate_name) if args[0] in windows_lifecycle.COMMANDS else None
        project = checkout_identity(PROJECT)
        runtime = Runtime(runner)
        runtime.preflight()
        if command is not None:
            windows_commands.execute(runtime, project, command)
            return 0
        if update_options is not None:
            windows_update.run(runtime, PROJECT, project, update_options)
            return 0
        if lifecycle is not None:
            windows_lifecycle.execute(runtime, project, args[0], *lifecycle, wait_ready)
            return 0
        if args[0] == 'ssh-config':
            ssh = SshSetup(runtime, project, args[1])
            ssh.install()
            return 0
        image = os.environ.get('SANDBOX_IMAGE', 'localhost/agent-sandbox:dev')
        if options is not None:
            create(runtime, options, image, project)
            return 0
        context = PROJECT / 'src/container'
        runtime.run('build', '--pull=always', '-t', os.environ.get('SANDBOX_IMAGE', 'localhost/agent-sandbox:dev'),
                    '-f', context / 'Containerfile', *args[1:], context, capture=False)
        return 0
    except SystemExit as error:
        return error.code
    except KeyboardInterrupt:
        print("Error: Operation interrupted; inspect retained containers before retrying.", file=sys.stderr)
        return 130
    except NativeError as error:
        print(f'Error: {error}', file=sys.stderr)
        return error.returncode
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, IndexError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
