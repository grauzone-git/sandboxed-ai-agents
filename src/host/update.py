"""Recreate owned sandboxes on a rebuilt image, retaining storage and SSH state."""
import argparse
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from containers import LABEL, create_args, owned_names
from capabilities import CAPABILITIES_LABEL, parse_capabilities, prepare_image
from workspace import validate_workspace

spec = importlib.util.spec_from_file_location('check_mounts', Path(__file__).with_name('check-mounts.py'))
mount_policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mount_policy)


def podman(*args, capture=False, check=True):
    return subprocess.run(['podman', *map(str, args)], check=check, text=True,
                          stdout=subprocess.PIPE if capture else None)



def snapshot(name, project, home, *, runner=None, workspace_validator=validate_workspace, owner_label=None):
    """Validate every persisted input before any build or container mutation."""
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
        raise ValueError(f'Invalid sandbox name: {name}')
    runner = runner or podman
    owner_label = str(project) if owner_label is None else owner_label
    container = json.loads(runner('container', 'inspect', name, capture=True).stdout)[0]
    if container['Name'].lstrip('/') != name:
        raise ValueError(f'Container name changed: {name}')
    if (container['Config'].get('Labels') or {}).get(LABEL) != owner_label:
        raise ValueError(f'Container {name} is not owned by this configuration.')
    status = container['State']['Status']
    if status not in ('running', 'exited', 'created', 'stopped'):
        raise ValueError(f'Cannot update {name} while its state is {status}.')
    mounts = container['Mounts']
    mount_policy.validate_mounts(name, mounts)
    workspace = None
    for mount in mounts:
        if not mount.get('RW', True):
            raise ValueError(f'Unexpected read-only mount in {name}: {mount["Destination"]}')
        if mount['Type'] == 'volume':
            volume = json.loads(runner('volume', 'inspect', mount['Name'], capture=True).stdout)[0]
            if (volume.get('Labels') or {}).get(LABEL) != owner_label:
                raise ValueError(f'Volume {mount["Name"]} belongs to another configuration.')
        if mount['Destination'] == '/workspace':
            if mount['Type'] == 'bind':
                directory = workspace_validator(Path(mount['Source']), project, home / '.ssh/sanboxed-agents')
                if not directory.is_dir():
                    raise ValueError(f'Workspace directory is missing: {directory}')
                workspace = f'{directory}:/workspace:Z'
            else:
                workspace = f'{name}-workspace:/workspace'
    host = container['HostConfig']
    ports = host.get('PortBindings') or {}
    bindings = ports.get('2222/tcp', [])
    if (set(ports) != {'2222/tcp'} or len(bindings) != 1
            or bindings[0].get('HostIp') != '127.0.0.1'):
        raise ValueError(f'Unexpected SSH port mapping in {name}.')
    port = int(bindings[0]['HostPort'])
    if not 1024 <= port <= 65535:
        raise ValueError(f'Invalid SSH port in {name}.')
    nano_cpus = host.get('NanoCpus', 0)
    cpus = Decimal(nano_cpus) / 1_000_000_000
    if not nano_cpus and host.get('CpuQuota', 0) > 0:
        cpus = Decimal(host['CpuQuota']) / (host.get('CpuPeriod') or 100000)
    capabilities = parse_capabilities((container['Config'].get('Labels') or {}).get(CAPABILITIES_LABEL, 'none'))
    return dict(name=name, identity=container['Id'], running=container['State']['Running'],
                workspace=workspace, port=port, memory=host['Memory'], cpus=str(cpus),
                pids=host['PidsLimit'], shm=host['ShmSize'], capabilities=capabilities)


def ready(identity, *, runner=None):
    runner = runner or podman
    # Persisted host-key files alone do not prove the new sshd has started.
    for attempt in range(30):
        result = runner('exec', '--user', '0', identity, '/bin/sh', '-c',
                        '/usr/sbin/sshd -t && /usr/bin/pgrep -x sshd >/dev/null',
                        capture=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(0.5)
    runner('logs', identity, check=False)
    raise RuntimeError('SSH did not become ready in the replacement container.')


def restore(old, new_id, renamed, *, runner=None):
    """Delete only the replacement we created; leave all persistent files intact."""
    runner = runner or podman
    if new_id:
        runner('rm', '--force', new_id)
    if renamed:
        runner('rename', old['identity'], old['name'])
    if old['running']:
        runner('start', old['identity'])
    print(f'Restored {old["name"]} to its previous container.', file=sys.stderr)


def replace(old, project, home, image, capabilities=None, *, runner=None,
            workspace_validator=validate_workspace, owner_label=None, seccomp=None):
    runner = runner or podman
    name = old['name']
    # Building can take minutes. Reject changed settings before stopping anything.
    if snapshot(name, project, home, runner=runner, workspace_validator=workspace_validator, owner_label=owner_label) != old:
        raise ValueError(f'{name} changed during update; retry with its current settings.')
    backup = f'{name}-update-backup-{uuid.uuid4().hex[:12]}'
    renamed = False
    new_id = None
    print(f'Updating {name} (SSH port {old["port"]})...', flush=True)
    # Keep transaction files inside the controller's protected state boundary;
    # /tmp itself can be an explicitly bound, agent-writable workspace.
    state_dir = project / '.local'
    state_dir.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='update-', dir=state_dir) as directory:
        cidfile = Path(directory) / 'container-id'
        try:
            if old['running']:
                runner('stop', old['identity'])
            runner('rename', old['identity'], backup)
            renamed = True
            args = create_args(name, str(project) if owner_label is None else owner_label, image, old['workspace'], old['port'],
                               old['memory'], old['cpus'], old['pids'], old['shm'], operation='create',
                               capabilities=old['capabilities'] if capabilities is None else capabilities, seccomp=seccomp)
            # The cidfile also identifies a partially created replacement on failure.
            runner(*args[:-1], '--cidfile', cidfile, args[-1])
            candidate = cidfile.read_text().strip()
            if not re.fullmatch(r'[a-f0-9]{64}', candidate):
                raise ValueError('Podman returned an invalid replacement container ID.')
            new_id = candidate
            runner('start', new_id)
            ready(new_id, runner=runner)
            # A fresh writable layer intentionally skips automatic service restore.
            # Restore saved selections directly, including an empty agent selection.
            for manager in ('sandbox-agents', 'sandbox-tools'):
                runner('exec', '--user', '1000:1000', '--workdir', '/workspace', new_id,
                       f'/usr/local/bin/{manager}', 'boot')
            if not old['running']:
                runner('stop', new_id)
        except (Exception, KeyboardInterrupt):
            if not new_id and cidfile.exists():
                candidate = cidfile.read_text().strip()
                if re.fullmatch(r'[a-f0-9]{64}', candidate):
                    new_id = candidate
            try:
                restore(old, new_id, renamed, runner=runner)
            except (Exception, KeyboardInterrupt) as error:
                print(f'Rollback failed: {error}. Previous container ID: {old["identity"]}; '
                      f'backup name: {backup}. Volumes and SSH files were retained.', file=sys.stderr)
            raise
    # A backup-removal failure must not roll back a healthy replacement.
    try:
        runner('rm', old['identity'])
    except (subprocess.CalledProcessError, RuntimeError):
        raise RuntimeError(f'{name} was updated, but its stopped backup {backup} could not be removed.') from None
    print(f'Updated {name}; {"running" if old["running"] else "stopped"}.', flush=True)


def parse_args(args=None, *, prog="./sandbox"):
    # Launchers pass NAME after the command internally; show the public grammar.
    usage = (f'{prog} NAME update [--no-build] [--capabilities LIST]\n'
             f'       {prog} update --all [--no-build] [--capabilities LIST]')
    parser = argparse.ArgumentParser(prog=f'{prog} update', usage=usage, description=__doc__)
    parser.add_argument('--all', action='store_true', dest='all_sandboxes',
                        help='update every sandbox owned by this controller checkout')
    parser.add_argument('--no-build', action='store_true',
                        help='use the existing SANDBOX_IMAGE without rebuilding')
    parser.add_argument('--capabilities', type=parse_capabilities, metavar='LIST',
                        help='replace capabilities with podman or none; omitted preserves each sandbox')
    parser.add_argument('names', nargs='*', metavar='NAME', help='sandbox names to update')
    options = parser.parse_args(args)
    if options.all_sandboxes == bool(options.names):
        parser.error('Supply one sandbox name, or --all.')
    if len(set(options.names)) != len(options.names):
        parser.error('Duplicate sandbox names.')
    return options


def main(args=None, *, project=None, image=None, runner=None,
         workspace_validator=validate_workspace, owner_label=None, prepare=None, options=None):
    runner = runner or podman
    options = options or parse_args(args)
    project = (project or Path(__file__).resolve().parents[2]).resolve()
    image_tag = image or os.environ.get('SANDBOX_IMAGE', 'localhost/agent-sandbox:dev')
    names = options.names
    if options.all_sandboxes:
        names = owned_names(str(project) if owner_label is None else owner_label, runner)
    if len(set(names)) != len(names):
        raise ValueError('Duplicate sandbox names.')
    if not names:
        print('No sandboxes owned by this configuration to update.')
        return
    plans = [snapshot(name, project, Path.home(), runner=runner, workspace_validator=workspace_validator, owner_label=owner_label) for name in names]
    if not options.no_build:
        runner('build', '--pull=always', '--no-cache', '-t', image_tag,
               '-f', project / 'src/container/Containerfile', project / 'src/container')
    # Freeze the image ID so every selected sandbox uses the same build.
    image = runner('image', 'inspect', '--format', '{{.Id}}', image_tag, capture=True).stdout.strip()
    if not re.fullmatch(r'(sha256:)?[a-f0-9]{64}', image):
        raise ValueError('Podman returned an invalid image ID.')
    # Finish every optional image build before stopping any existing sandbox.
    prepare = prepare or (lambda capability, frozen_image: (prepare_image(project, frozen_image, capability, runner=runner), None))
    images = {capability: prepare(capability, image)
              for capability in sorted({options.capabilities or plan['capabilities'] for plan in plans})}
    for plan in plans:
        capability = options.capabilities or plan['capabilities']
        replace(plan, project, Path.home(), images[capability][0], capability, runner=runner,
                workspace_validator=workspace_validator, owner_label=owner_label, seccomp=images[capability][1])


def interrupted(signum, frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'Update failed: {error}')
    except KeyboardInterrupt:
        sys.exit('Update interrupted.')
