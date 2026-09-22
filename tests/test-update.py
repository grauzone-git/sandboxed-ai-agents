"""Image update transactions use simulated Podman; no user containers are touched."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HOST = Path(__file__).resolve().parents[1] / 'src/host'
sys.path.insert(0, str(HOST))
import update
from containers import LABEL
from capabilities import CAPABILITIES_LABEL


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox-update-test-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'project'
        self.project.mkdir()
        self.home = Path(temporary.name) / 'home'
        self.home.mkdir()
        self.seccomp = self.home / 'seccomp.json'
        self.seccomp.write_text(json.dumps({'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': []}))
        self.calls = []
        self.containers = {}
        self.volumes = {}
        self.failure = None
        self.image = 'f' * 64
        self.created = 0
        self.add('agent01')
        self.addCleanup(patch.stopall)
        patch.object(update, 'podman', self.podman).start()
        patch.object(update.time, 'sleep').start()
        patch.object(update.Path, 'home', return_value=self.home).start()

    def add(self, name, *, running=True, workspace=None, owner=None):
        identity = f'{len(self.containers) + 1:064x}'
        mounts = [dict(Type='volume', Name=f'{name}-{suffix}', Destination=dest, RW=True)
                  for suffix, dest in [('home', '/home/agent'), ('sshd', '/var/lib/agent-sshd'), ('workspace', '/workspace')]]
        if workspace:
            workspace.mkdir(parents=True, exist_ok=True)
            mounts[-1] = dict(Type='bind', Source=str(workspace), Destination='/workspace', RW=True)
        self.containers[identity] = dict(Id=identity, Name=name, Config={'Labels': {LABEL: owner or str(self.project)}},
            State={'Status': 'running' if running else 'exited', 'Running': running}, Mounts=mounts,
            HostConfig={'PortBindings': {'2222/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(2200 + len(self.containers))}]},
                        'Memory': 123456789, 'NanoCpus': 1500000000, 'PidsLimit': 1024, 'ShmSize': 268435456})
        for mount in mounts:
            if mount['Type'] == 'volume':
                self.volumes[mount['Name']] = {'Labels': {LABEL: str(self.project)}}
        return identity

    def get(self, reference):
        return next(c for c in self.containers.values() if reference in (c['Id'], c['Name']))

    def podman(self, *args, capture=False, check=True):
        args = list(map(str, args))
        self.calls.append(args)
        failure = self.failure and self.failure(args)
        if failure:
            if check:
                raise subprocess.CalledProcessError(1, ['podman', *args])
            return subprocess.CompletedProcess(args, 1, '')
        output = ''
        if args[:2] == ['container', 'inspect']:
            output = json.dumps([self.get(args[-1])])
        elif args[:2] == ['volume', 'inspect']:
            output = json.dumps([self.volumes[args[-1]]])
        elif args[:2] == ['image', 'inspect']:
            output = self.image
        elif args[0] == 'info':
            output = json.dumps({'seccompProfilePath': str(self.seccomp)})
        elif args[0] == 'ps':
            output = '\n'.join(c['Name'] for c in self.containers.values() if c['Config']['Labels'][LABEL] == str(self.project))
        elif args[0] == 'build':
            if '--quiet' in args:
                output = 'e' * 64
        elif args[0] in ('start', 'stop'):
            container = self.get(args[-1])
            container['State'] = {'Status': 'running' if args[0] == 'start' else 'exited', 'Running': args[0] == 'start'}
        elif args[0] == 'rename':
            if any(c['Name'] == args[-1] for c in self.containers.values()):
                raise subprocess.CalledProcessError(1, args)
            self.get(args[1])['Name'] = args[-1]
        elif args[0] == 'create':
            self.created += 1
            identity = f'{100 + self.created:064x}'
            name = args[args.index('--name') + 1]
            self.containers[identity] = dict(Id=identity, Name=name, State={'Running': False}, Config={'Labels': {LABEL: str(self.project)}})
            Path(args[args.index('--cidfile') + 1]).write_text(identity)
        elif args[0] == 'rm':
            del self.containers[self.get(args[-1])['Id']]
        elif args[0] in ('exec', 'logs'):
            pass
        else:
            self.fail(f'Unexpected Podman call: {args}')
        return subprocess.CompletedProcess(args, 0, output)

    def run_update(self, *args):
        update.main(args, project=self.project, image='localhost/test:dev')

    def mutations(self):
        return [c for c in self.calls if c[0] in ('build', 'stop', 'start', 'rename', 'create', 'rm')]

    def test_build_once_preserve_settings_volumes_ssh_and_selections(self):
        self.add('agent02', running=False, workspace=self.project / 'workspaces/space in path')
        ssh = self.home / '.ssh/sanboxed-agents/agent01/agent01.conf'
        ssh.parent.mkdir(parents=True)
        ssh.write_text('keep SSH config and key references')
        volumes = copy.deepcopy(self.volumes)
        self.run_update('--all')
        builds = [c for c in self.calls if c[0] == 'build']
        self.assertEqual(len(builds), 1)
        self.assertIn('--pull=always', builds[0])
        self.assertIn('--no-cache', builds[0])
        creates = [c for c in self.calls if c[0] == 'create']
        self.assertEqual(len(creates), 2)
        self.assertIn('agent01-home:/home/agent', creates[0])
        self.assertIn('agent02-home:/home/agent', creates[1])
        self.assertIn('agent01-workspace:/workspace', creates[0])
        self.assertIn(f'{self.project}/workspaces/space in path:/workspace:Z', creates[1])
        self.assertIn('127.0.0.1:2200:2222', creates[0])
        self.assertIn('127.0.0.1:2201:2222', creates[1])
        for command in creates:
            self.assertTrue(Path(command[command.index('--cidfile') + 1]).is_relative_to(self.project / '.local'))
            for value in ('--memory=123456789', '--cpus=1.5', '--pids-limit=1024', '--shm-size=268435456',
                          '--userns=keep-id:uid=1000,gid=1000', '--security-opt=no-new-privileges', '--network=pasta:--no-map-gw'):
                self.assertIn(value, command)
            self.assertEqual(command[-1], self.image)
        self.assertTrue(self.get('agent01')['State']['Running'])
        self.assertFalse(self.get('agent02')['State']['Running'])
        self.assertEqual(len(self.containers), 2)
        self.assertEqual(self.volumes, volumes)
        self.assertEqual(ssh.read_text(), 'keep SSH config and key references')
        boots = [c for c in self.calls if c[-1] == 'boot']
        self.assertEqual(len(boots), 4)
        self.assertTrue(all('1000:1000' in c and '/workspace' in c for c in boots))
        self.assertFalse(any('init' in c or 'set' in c or 'volume' in c and 'rm' in c for c in self.calls))
        self.assertLess(self.calls.index(builds[0]), next(i for i, c in enumerate(self.calls) if c[0] == 'stop'))

    def test_no_build_and_no_ssh_setup(self):
        self.run_update('agent01', '--no-build')
        self.assertFalse(any(c[0] == 'build' for c in self.calls))
        self.assertFalse((self.home / '.ssh').exists())

    def test_capabilities_preserved_and_layer_built_once_before_stopping(self):
        self.add('agent02', running=False)
        self.add('agent03')
        for name in ('agent01', 'agent02'):
            self.get(name)['Config']['Labels'][CAPABILITIES_LABEL] = 'podman'
        # Updating both an old persistent-runtime sandbox and a new tmpfs one
        # must produce the same ephemeral runtime configuration.
        self.get('agent02')['Mounts'].append(dict(Type='tmpfs', Source='tmpfs', Destination='/run/user/1000', RW=True))
        self.run_update('--all', '--no-build')
        builds = [call for call in self.calls if call[0] == 'build']
        self.assertEqual(len(builds), 1)
        self.assertIn('BASE_IMAGE=' + self.image, builds[0])
        self.assertLess(self.calls.index(builds[0]), next(i for i, c in enumerate(self.calls) if c[0] == 'stop'))
        creates = [call for call in self.calls if call[0] == 'create']
        for call in creates[:2]:
            self.assertEqual(call[-1], 'e' * 64)
            self.assertIn('--device=/dev/fuse', call)
            self.assertIn('--device=/dev/net/tun', call)
            self.assertIn(f'--security-opt=seccomp={self.project}/.local/nested-podman-seccomp.json', call)
            self.assertIn(CAPABILITIES_LABEL + '=podman', call)
            self.assertNotIn('--security-opt=no-new-privileges', call)
            self.assertIn('/run/user/1000:rw,nosuid,nodev,noexec,mode=0700', call)
        self.assertEqual(creates[2][-1], self.image)
        self.assertIn('--security-opt=no-new-privileges', creates[2])
        self.assertNotIn('--tmpfs', creates[2])
        self.assertFalse(self.get('agent02')['State']['Running'])

    def test_enable_or_remove_capability_during_update(self):
        original = copy.deepcopy(self.containers)
        for old, desired in [('none', 'podman'), ('podman', 'none')]:
            with self.subTest(desired=desired):
                self.containers = copy.deepcopy(original)
                self.calls = []
                self.get('agent01')['Config']['Labels'][CAPABILITIES_LABEL] = old
                self.run_update('agent01', '--no-build', '--capabilities', desired)
                create = next(c for c in self.calls if c[0] == 'create')
                self.assertIn(CAPABILITIES_LABEL + '=' + desired, create)
                self.assertEqual('--device=/dev/fuse' in create, desired == 'podman')
                self.assertEqual('--device=/dev/net/tun' in create, desired == 'podman')
                self.assertEqual('--security-opt=no-new-privileges' in create, desired == 'none')
                self.assertEqual('--tmpfs' in create, desired == 'podman')

    def test_capability_build_failure_keeps_all_old_containers(self):
        original = copy.deepcopy(self.containers)
        self.failure = lambda args: args[0] == 'build'
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_update('agent01', '--no-build', '--capabilities', 'podman')
        self.assertEqual(self.containers, original)
        self.assertEqual([call[0] for call in self.mutations()], ['build'])

    def test_capability_start_failure_rolls_back_to_previous_settings(self):
        original = copy.deepcopy(self.containers)
        self.failure = lambda args: args[0] == 'exec' and '/bin/sh' in args
        with self.assertRaises(RuntimeError):
            self.run_update('agent01', '--no-build', '--capabilities', 'podman')
        self.assertEqual(self.containers, original)

    def test_missing_seccomp_profile_keeps_old_containers_running(self):
        original = copy.deepcopy(self.containers)
        self.seccomp.unlink()
        with self.assertRaises(OSError):
            self.run_update('agent01', '--no-build', '--capabilities', 'podman')
        self.assertEqual(self.containers, original)
        self.assertEqual(self.mutations(), [])

    def test_unknown_saved_capability_rejected_before_mutation(self):
        self.get('agent01')['Config']['Labels'][CAPABILITIES_LABEL] = 'docker'
        with self.assertRaisesRegex(ValueError, 'Capabilities'):
            self.run_update('agent01')
        self.assertEqual(self.mutations(), [])

    def test_preflight_all_targets_before_build_or_stop(self):
        self.add('agent02', owner='/other-project')
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.run_update('agent01', 'agent02')
        self.assertEqual(self.mutations(), [])
        self.volumes['agent01-home']['Labels'][LABEL] = '/other-project'
        with self.assertRaisesRegex(ValueError, 'belongs to another'):
            self.run_update('agent01')
        self.assertEqual(self.mutations(), [])

    def test_reject_exposed_controller_foreign_mount_and_public_port(self):
        original = copy.deepcopy(self.get('agent01'))
        for corruption in ('bind', 'volume', 'extra', 'port', 'paused'):
            with self.subTest(corruption=corruption):
                self.containers[original['Id']] = copy.deepcopy(original)
                current = self.get('agent01')
                if corruption == 'bind':
                    current['Mounts'][-1] = dict(Type='bind', Source=str(self.project), Destination='/workspace')
                elif corruption == 'volume':
                    current['Mounts'][-1]['Name'] = 'foreign-workspace'
                elif corruption == 'extra':
                    current['Mounts'].append(dict(Type='bind', Source='/tmp', Destination='/extra'))
                elif corruption == 'port':
                    current['HostConfig']['PortBindings']['2222/tcp'][0]['HostIp'] = '0.0.0.0'
                else:
                    current['State']['Status'] = 'paused'
                with self.assertRaises(ValueError):
                    self.run_update('agent01')
                self.assertEqual(self.mutations(), [])

    def test_build_failure_keeps_old_container_running(self):
        self.failure = lambda args: args[0] == 'build'
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_update('agent01')
        self.assertTrue(self.get('agent01')['State']['Running'])
        self.assertEqual([c[0] for c in self.mutations()], ['build'])

    def test_create_start_readiness_and_boot_failures_restore_previous_container(self):
        original = copy.deepcopy(self.containers)
        for phase in ('create', 'start', 'ready', 'boot'):
            with self.subTest(phase=phase):
                self.containers = copy.deepcopy(original)
                self.calls = []
                self.failure = lambda args: (args[0] == phase and (phase != 'start' or args[-1] not in original)
                    or phase == 'ready' and args[0] == 'exec' and '/bin/sh' in args
                    or phase == 'boot' and args[-1] == 'boot')
                with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                    self.run_update('agent01', '--no-build')
                self.assertEqual(self.containers, original)
                self.assertFalse(any(c[:2] == ['volume', 'rm'] for c in self.calls))

    def test_failed_stopped_update_restores_stopped_container(self):
        current = self.get('agent01')
        current['State'] = {'Running': False, 'Status': 'exited'}
        self.failure = lambda args: args[-1] == 'boot'
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_update('agent01', '--no-build')
        self.assertFalse(self.get('agent01')['State']['Running'])
        self.assertEqual(len(self.containers), 1)

    def test_all_excludes_foreign_containers_and_empty_all_skips_build(self):
        foreign = self.add('foreign', owner='/other-project')
        self.run_update('--all', '--no-build')
        self.assertIn(foreign, self.containers)
        self.assertFalse(any(foreign in c for c in self.mutations()))
        self.containers = {foreign: self.containers[foreign]}
        self.calls = []
        self.run_update('--all')
        self.assertEqual(self.mutations(), [])

    def test_backup_cleanup_failure_keeps_healthy_replacement(self):
        old = self.get('agent01')['Id']
        self.failure = lambda args: args == ['rm', old]
        with self.assertRaisesRegex(RuntimeError, 'stopped backup'):
            self.run_update('agent01', '--no-build')
        self.assertTrue(self.get('agent01')['State']['Running'])
        self.assertNotEqual(self.get('agent01')['Id'], old)
        self.assertIn('-update-backup-', self.containers[old]['Name'])
        self.assertFalse(self.containers[old]['State']['Running'])

    def test_changed_settings_after_build_abort_before_stop(self):
        real_podman = self.podman
        def change_after_build(*args, **kwargs):
            result = real_podman(*args, **kwargs)
            if args[0] == 'build':
                self.get('agent01')['HostConfig']['Memory'] = 999
            return result
        with patch.object(update, 'podman', change_after_build):
            with self.assertRaisesRegex(ValueError, 'changed during update'):
                self.run_update('agent01')
        self.assertEqual([c[0] for c in self.mutations()], ['build'])

    def test_partial_creation_failure_removes_only_the_new_container(self):
        original = copy.deepcopy(self.containers)
        real_podman = self.podman
        def fail_after_create(*args, **kwargs):
            result = real_podman(*args, **kwargs)
            if args[0] == 'create':
                raise subprocess.CalledProcessError(1, args)
            return result
        with patch.object(update, 'podman', fail_after_create):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_update('agent01', '--no-build')
        self.assertEqual(self.containers, original)
        self.assertEqual(len([c for c in self.calls if c[:2] == ['rm', '--force']]), 1)

    def test_rollback_failure_reports_retained_backup_and_preserves_volumes(self):
        old = self.get('agent01')['Id']
        volumes = copy.deepcopy(self.volumes)
        self.failure = lambda args: args[-1] == 'boot' or args == ['rename', old, 'agent01']
        errors = io.StringIO()
        with patch('sys.stderr', errors), self.assertRaises(subprocess.CalledProcessError):
            self.run_update('agent01', '--no-build')
        self.assertIn('Rollback failed', errors.getvalue())
        self.assertIn(old, errors.getvalue())
        self.assertIn('-update-backup-', self.containers[old]['Name'])
        self.assertEqual(self.volumes, volumes)
        self.assertEqual(len(self.containers), 1)

    def test_invalid_arguments_do_not_mutate(self):
        for args in [(), ('--all', 'agent01'), ('agent01', 'agent01'), ('--volumes',), ('../agent01',),
                     ('agent01', '--capabilities', 'docker')]:
            with self.subTest(args=args), self.assertRaises((ValueError, SystemExit)):
                self.run_update(*args)
        self.assertEqual(self.mutations(), [])


if __name__ == '__main__':
    unittest.main()
