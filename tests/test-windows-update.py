"""Exercise Windows update transactions through the CLI and simulated native processes."""
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
import windows_cli


def fixture(filename):
    spec = importlib.util.spec_from_file_location(filename, Path(__file__).with_name(filename + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        self.host = fixture('test-windows-cli').WindowsCliTests()
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.engine = fixture('test-update').UpdateTests()
        self.engine.setUp()
        self.addCleanup(self.engine.doCleanups)
        self.engine.project = Path(os.path.normcase(str(self.engine.project)))
        for info in self.engine.containers.values():
            info['Config']['Labels']['io.sandboxed-agents.project'] = str(self.engine.project)
        for info in self.engine.volumes.values():
            info['Labels']['io.sandboxed-agents.project'] = str(self.engine.project)
        self.project_patch = patch.object(windows_cli, 'PROJECT', self.engine.project)
        self.project_patch.start()
        self.addCleanup(self.project_patch.stop)
        self.calls = []
        self.fail_after_create = False

    def runner(self, command, **kwargs):
        self.calls.append(list(map(str, command)))
        args = list(map(str, command[1:]))
        if args[:2] == ['--connection', 'machine'] and args[2] not in ('info', 'version'):
            result = self.engine.podman(*args[2:], capture=True, check=False)
            code = 37 if self.fail_after_create and args[2] == 'create' else result.returncode
            return subprocess.CompletedProcess(command, code, result.stdout, 'simulated failure' if code else '')
        return self.host.runner(command, **kwargs)

    def cli(self, *args):
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
            return windows_cli.main(list(args), runner=self.runner)

    def test_update_preserves_storage_settings_and_stopped_state(self):
        self.engine.add('agent02', running=False)
        volumes = copy.deepcopy(self.engine.volumes)
        self.assertEqual(self.cli('update', '--all', '--no-build'), 0, self.output.getvalue())
        self.assertEqual(self.engine.volumes, volumes)
        self.assertTrue(self.engine.get('agent01')['State']['Running'])
        self.assertFalse(self.engine.get('agent02')['State']['Running'])
        creates = [c for c in self.engine.calls if c[0] == 'create']
        self.assertEqual(len(creates), 2)
        self.assertIn('agent01-home:/home/agent', creates[0])
        self.assertIn('agent02-home:/home/agent', creates[1])
        self.assertIn('--cpus=1.5', creates[0])
        self.assertIn('--memory=123456789', creates[0])
        self.assertEqual(len([c for c in self.engine.calls if c[-1] == 'boot']), 4)
        self.assertFalse(any(c[0] == 'build' or c[:2] == ['volume', 'rm'] for c in self.engine.calls))
        self.assertFalse((self.engine.home / '.ssh').exists())
        self.assertTrue(all(c[1:3] == ['--connection', 'machine'] for c in self.calls if 'create' in c or 'rename' in c))

    def test_failed_service_boot_restores_old_container_and_keeps_ssh(self):
        old = self.engine.get('agent01')['Id']
        state = self.engine.home / '.ssh/sanboxed-agents/agent01'
        state.mkdir(parents=True)
        (state / 'id_ed25519').write_text('retained test identity')
        self.engine.failure = lambda args: args[-1] == 'boot'
        self.assertEqual(self.cli('update', 'agent01', '--no-build'), 1, self.output.getvalue())
        self.assertEqual(self.engine.get('agent01')['Id'], old)
        self.assertTrue(self.engine.get('agent01')['State']['Running'])
        self.assertEqual(len(self.engine.containers), 1)
        self.assertEqual((state / 'id_ed25519').read_text(), 'retained test identity')
        self.assertFalse(any(c[:2] == ['volume', 'rm'] for c in self.engine.calls))

    def test_readiness_failure_restores_stopped_container(self):
        self.engine.get('agent01')['State'] = {'Status': 'exited', 'Running': False}
        old = self.engine.get('agent01')['Id']
        self.engine.failure = lambda args: args[0] == 'exec'
        self.assertEqual(self.cli('update', 'agent01', '--no-build'), 1, self.output.getvalue())
        self.assertEqual(self.engine.get('agent01')['Id'], old)
        self.assertFalse(self.engine.get('agent01')['State']['Running'])
        self.assertIn('Restored agent01', self.output.getvalue())

    def test_all_targets_validated_before_build_and_foreign_volume_refused(self):
        self.engine.add('agent02')
        self.engine.volumes['agent02-home']['Labels']['io.sandboxed-agents.project'] = 'foreign'
        self.assertEqual(self.cli('update', 'agent01', 'agent02'), 1)
        self.assertEqual(self.engine.mutations(), [])

    def test_build_failure_does_not_stop_old_container(self):
        self.engine.failure = lambda args: args[0] == 'build'
        self.assertEqual(self.cli('update', 'agent01'), 1)
        self.assertEqual([c[0] for c in self.engine.mutations()], ['build'])
        self.assertTrue(self.engine.get('agent01')['State']['Running'])

    def test_nested_update_uses_guest_seccomp_and_preserves_capability(self):
        from capabilities import CAPABILITIES_LABEL
        self.engine.get('agent01')['Config']['Labels'][CAPABILITIES_LABEL] = 'podman'
        self.assertEqual(self.cli('update', 'agent01', '--no-build'), 0, self.output.getvalue())
        create = next(c for c in self.engine.calls if c[0] == 'create')
        self.assertIn('--security-opt=seccomp=/home/user/.local/share/sandboxed-agents/checkout/profile.json', create)
        self.assertIn('/run/user/1000:rw,nosuid,nodev,noexec,mode=0700', create)
        self.assertIn('--device=/dev/fuse', create)
        self.assertEqual(create[-1], 'e' * 64)
        self.assertFalse(any('--privileged' in c for c in self.engine.calls))

    def test_help_invalid_options_and_duplicate_names_do_not_contact_engine(self):
        for args, code in [(('--help',), 0), ((), 2), (('--all', 'agent01'), 2),
                           (('agent01', 'agent01'), 2), (('agent01', '--capabilities', 'bad'), 2)]:
            self.calls.clear()
            self.assertEqual(self.cli('update', *args), code, self.output.getvalue())
            self.assertEqual(self.calls, [])

    def test_partial_create_failure_uses_cidfile_to_remove_only_replacement(self):
        old = self.engine.get('agent01')['Id']
        self.fail_after_create = True
        self.assertEqual(self.cli('update', 'agent01', '--no-build'), 37, self.output.getvalue())
        self.assertEqual(set(self.engine.containers), {old})
        self.assertEqual(self.engine.get('agent01')['Name'], 'agent01')
        self.assertTrue(self.engine.get('agent01')['State']['Running'])
        self.assertEqual(len(self.engine.volumes), 3)

    def test_backup_removal_failure_retains_healthy_replacement(self):
        old = self.engine.get('agent01')['Id']
        self.engine.failure = lambda args: args == ['rm', old]
        self.assertEqual(self.cli('update', 'agent01', '--no-build'), 1)
        self.assertNotEqual(self.engine.get('agent01')['Id'], old)
        self.assertTrue(self.engine.get('agent01')['State']['Running'])
        self.assertIn('updated, but its stopped backup', self.output.getvalue())
        self.assertIn('update-backup', self.engine.get(old)['Name'])

    def test_build_once_before_any_stops_and_capability_can_be_disabled(self):
        from capabilities import CAPABILITIES_LABEL
        self.engine.add('agent02')
        self.engine.get('agent01')['Config']['Labels'][CAPABILITIES_LABEL] = 'podman'
        self.assertEqual(self.cli('update', '--all', '--capabilities', 'none'), 0, self.output.getvalue())
        builds = [c for c in self.engine.calls if c[0] == 'build']
        self.assertEqual(len(builds), 1)
        self.assertIn('--no-cache', builds[0])
        first_stop = next(i for i, c in enumerate(self.engine.calls) if c[0] == 'stop')
        self.assertLess(self.engine.calls.index(builds[0]), first_stop)
        for create in (c for c in self.engine.calls if c[0] == 'create'):
            self.assertIn('--security-opt=no-new-privileges', create)
            self.assertNotIn('--device=/dev/fuse', create)
            self.assertEqual(create[-1], self.engine.image)

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows drive translation requires Windows')
    def test_update_revalidates_wsl_drive_bind_with_spaces(self):
        workspace = self.engine.project.parent / 'workspace with spaces'
        self.engine.add('agent02', workspace=workspace)
        source = workspace.as_posix()
        self.engine.get('agent02')['Mounts'][-1]['Source'] = '/mnt/' + source[0].lower() + source[2:]
        self.assertEqual(self.cli('update', 'agent02', '--no-build'), 0, self.output.getvalue())
        create = next(c for c in self.engine.calls if c[0] == 'create')
        self.assertIn(f'{workspace.resolve()}:/workspace:Z', create)

    def test_guest_only_workspace_is_rejected_before_mutation(self):
        self.engine.get('agent01')['Mounts'][-1] = dict(Type='bind', Source='/home/user/unsafe', Destination='/workspace')
        self.assertEqual(self.cli('update', 'agent01'), 1)
        self.assertIn('Cannot map', self.output.getvalue())
        self.assertEqual(self.engine.mutations(), [])


if __name__ == '__main__':
    unittest.main()
