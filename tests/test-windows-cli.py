"""Exercise Windows orchestration through its CLI and external process boundary."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src/host'))
from windows_cli import main


class WindowsCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox windows ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.calls = []
        self.native_failure = None
        self.volumes = {}
        self.exists = False
        self.owner_override = None
        self.container_state = {'Status': 'running', 'ExitCode': 0, 'OOMKilled': False}
        self.connection = {'Name': 'machine', 'Default': True, 'IsMachine': True,
                           'URI': 'ssh://user@127.0.0.1:12345/run/user/1000/podman/podman.sock'}
        self.machine = {'Name': 'machine', 'VMType': 'wsl', 'Running': True}
        self.info = {'host': {'security': {'rootless': True, 'seccompProfilePath': '/usr/share/containers/seccomp.json'}, 'arch': 'amd64',
                              'cgroupVersion': 'v2', 'cgroupControllers': ['cpu', 'memory', 'pids']}}
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'USERPROFILE': str(self.home)})
        self.env.start()
        for key in ('CONTAINER_CONNECTION', 'CONTAINER_HOST', 'SANDBOX_IMAGE', 'SANDBOX_CPUS', 'SANDBOX_MEMORY'):
            os.environ.pop(key, None)
        self.addCleanup(self.env.stop)
        self.which = patch('shutil.which', side_effect=lambda name: name)
        self.which.start()
        self.addCleanup(self.which.stop)

    def runner(self, command, **kwargs):
        args = [str(item) for item in command[1:]]
        self.calls.append(args)
        if command[0] in ('ssh-keygen', 'pwsh'):
            return subprocess.run(command, **kwargs)
        code, output = 0, ''
        if self.native_failure and self.native_failure[0] in args:
            code = self.native_failure[1]
        elif 'version' in args:
            output = json.dumps({'Client': {'Version': '6.0.0'}, 'Server': {'Version': '6.0.0'}})
        elif args[:3] == ['system', 'connection', 'list']:
            output = json.dumps([self.connection])
        elif args[:2] == ['machine', 'list']:
            output = json.dumps([self.machine])
        elif args[:2] == ['machine', 'inspect']:
            output = json.dumps([{'Name': 'machine', 'State': 'running', 'Rootful': False,
                                  'SSHConfig': {'Port': 12345, 'RemoteUsername': 'user'}}])
        elif args[:2] == ['machine', 'ssh']:
            command_text = args[-1]
            if 'cat -- /usr/share/containers/seccomp.json' in command_text:
                output = json.dumps({'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': [{'names': ['read'], 'action': 'SCMP_ACT_ALLOW'}]})
            elif '/proc/self/' in command_text:
                output = '0 1000 1\n1 100000 65536\n'
            elif 'mktemp' in command_text:
                self.staged_policy = json.loads(kwargs['input'])
                output = '/home/user/.local/share/sandboxed-agents/checkout/profile.json\n'
        elif 'info' in args:
            output = json.dumps(self.info)
        elif args[:2] == ['--connection', 'machine']:
            args = args[2:]
            if args[:2] == ['image', 'inspect']:
                output = 'a' * 64
            elif args[0] == 'build':
                output = 'b' * 64
            elif args[:2] == ['container', 'exists']:
                code = 0 if self.exists else 1
            elif args[0] == 'inspect':
                from windows_paths import checkout_identity
                output = json.dumps([{'Id': 'c' * 64, 'Name': 'agent01', 'Mounts': [{'Type': 'volume', 'Name': 'agent01-' + suffix, 'Destination': dest} for suffix, dest in [('home', '/home/agent'), ('sshd', '/var/lib/agent-sshd'), ('workspace', '/workspace')]], 'State': self.container_state, 'Config': {'Labels': {'io.sandboxed-agents.project': self.owner_override or checkout_identity(PROJECT)}}}])
            elif args[0] == 'port':
                output = '127.0.0.1:2297\n'
            elif args[0] == 'exec' and args[-1] == '/var/lib/agent-sshd/ssh_host_ed25519_key.pub' and '/bin/cat' in args:
                output = self.host_public_key
            elif args[:2] == ['volume', 'exists']:
                code = 0 if args[-1] in self.volumes else 1
            elif args[:2] == ['volume', 'inspect']:
                output = json.dumps([{'Labels': {'io.sandboxed-agents.project': self.volumes[args[-1]]}}])
            elif args[:2] == ['volume', 'create']:
                self.volumes[args[-1]] = args[args.index('--label') + 1].split('=', 1)[1]
        return subprocess.CompletedProcess(command, code, output, 'native failure' if code > 1 else '')

    def cli(self, *args):
        if args and args[0] == 'up' and '--ssh-port' not in args:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                args = (*args, '--ssh-port', str(listener.getsockname()[1]))
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
            result = main(list(args), runner=self.runner)
        return result

    def test_creation_uses_isolated_volumes_and_explicit_selections(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = str(listener.getsockname()[1])
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex', '--tools', 't3',
                                  '--ssh-port', port, '--memory', '6g', '--cpus', '2'),
                         0, self.output.getvalue())
        run = next(call for call in self.calls if 'run' in call)
        mounts = [run[i + 1] for i, value in enumerate(run) if value == '--volume']
        self.assertEqual(mounts, ['agent01-workspace:/workspace', 'agent01-home:/home/agent',
                                  'agent01-sshd:/var/lib/agent-sshd'])
        self.assertIn(f'127.0.0.1:{port}:2222', run)
        self.assertIn('--memory=6g', run)
        self.assertIn('--cpus=2', run)
        self.assertTrue(any('/usr/local/bin/sandbox-agents' in call and call[-2:] == ['init', 'codex']
                            for call in self.calls))
        self.assertFalse((self.home / '.ssh').exists())

    def test_invalid_selection_and_foreign_volumes_do_not_provision(self):
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'unknown'), 1)
        self.assertEqual(self.calls, [])
        self.volumes['agent01-workspace'] = 'another-checkout'
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex'), 1)
        self.assertIn('another checkout', self.output.getvalue())
        self.assertFalse(any('run' in call or call[2:4] == ['volume', 'create'] for call in self.calls))

    def test_unavailable_or_rootful_runtime_and_native_errors_fail_closed(self):
        self.machine['Running'] = False
        self.assertEqual(self.cli('build'), 1)
        self.machine['Running'] = True
        self.info['host']['security']['rootless'] = False
        self.assertEqual(self.cli('build'), 1)
        self.info['host']['security']['rootless'] = True
        self.native_failure = ('exists', 125)
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex'), 125)
        self.assertFalse(any('build' in call or 'run' in call for call in self.calls))

    def test_bind_with_spaces_and_owned_volume_reuse_preserve_data(self):
        directory = self.root / 'workspace with spaces'
        directory.mkdir()
        (directory / 'keep.txt').write_text('keep')
        from windows_paths import checkout_identity
        self.volumes['agent01-home'] = checkout_identity(PROJECT)
        self.volumes['agent01-sshd'] = checkout_identity(PROJECT)
        self.assertEqual(self.cli('up', 'agent01', str(directory), '--agents', 'codex'), 0,
                         self.output.getvalue())
        self.assertEqual((directory / 'keep.txt').read_text(), 'keep')
        run = next(call for call in self.calls if 'run' in call)
        self.assertIn(f'{directory.resolve()}:/workspace:Z', run)
        self.assertFalse(any('/bin/chown' in call or call[2:4] == ['volume', 'create'] for call in self.calls))

    def test_existing_container_and_unsafe_workspace_are_rejected(self):
        self.exists = True
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex'), 1)
        self.assertIn('already exists', self.output.getvalue())
        self.exists = False
        for target in [str(PROJECT), str(PROJECT / 'src'), str(PROJECT / 'sandbox.ps1'), str(self.home)]:
            self.calls.clear()
            self.assertEqual(self.cli('up', 'agent01', target, '--agents', 'codex'), 1)
            self.assertFalse(any('run' in call or call[2:4] == ['volume', 'create'] for call in self.calls))

    def prepare_host_key(self):
        host_key = self.root / 'server-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(host_key)], check=True)
        self.host_public_key = host_key.with_suffix('.pub').read_text()

    def test_creation_with_ssh_opt_in_installs_the_managed_alias(self):
        self.prepare_host_key()
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'copilot', '--ssh-config'), 0,
                         self.output.getvalue())
        state = self.home / '.ssh/sanboxed-agents/agent01'
        self.assertTrue((state / 'id_ed25519').is_file())
        config = (state / 'agent01.conf').read_text()
        self.assertIn('Host agent01\n', config)
        self.assertIn('User agent\n', config)
        self.assertIn('IdentityFile', config)
        self.assertIn((state / 'agent01.conf').as_posix(), (self.home / '.ssh/config').read_text())
        self.assertIn('SSH configured: ssh agent01', self.output.getvalue())

    def test_ssh_is_opt_in_and_can_be_added_later_idempotently(self):
        self.prepare_host_key()
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex'), 0)
        self.assertFalse((self.home / '.ssh').exists())
        self.calls.clear()
        ssh_dir = self.home / '.ssh'
        ssh_dir.mkdir()
        config = ssh_dir / 'config'
        config.write_text('Host unrelated\n    HostName example.test\n')
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0, self.output.getvalue())
        state = ssh_dir / 'sanboxed-agents/agent01'
        before = (state / 'id_ed25519').read_bytes()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0, self.output.getvalue())
        self.assertEqual((state / 'id_ed25519').read_bytes(), before)
        self.assertEqual(config.read_text().count('Include '), 1)
        self.assertIn('Host unrelated\n    HostName example.test\n', config.read_text())
        self.assertIn('StrictHostKeyChecking yes', (state / 'agent01.conf').read_text())
        self.assertIn('[127.0.0.1]:2297 ssh-ed25519 ', (state / 'known_hosts').read_text())
        self.assertFalse(any('run' in call for call in self.calls))

    def test_nested_podman_uses_guest_policy_and_retains_isolation(self):
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex', '--capabilities', 'podman'),
                         0, self.output.getvalue())
        run = next(call for call in self.calls if 'run' in call)
        self.assertEqual(run[-1], 'b' * 64)
        self.assertIn('--device=/dev/fuse', run)
        self.assertIn('--device=/dev/net/tun', run)
        self.assertIn('--security-opt=seccomp=/home/user/.local/share/sandboxed-agents/checkout/profile.json', run)
        self.assertIn('/run/user/1000:rw,nosuid,nodev,noexec,mode=0700', run)
        self.assertNotIn('--privileged', run)
        self.assertNotIn('--security-opt=seccomp=unconfined', run)
        self.assertEqual(self.staged_policy['defaultAction'], 'SCMP_ACT_ERRNO')
        self.assertEqual(self.staged_policy['syscalls'][0], {'names': ['read'], 'action': 'SCMP_ACT_ALLOW'})

    def test_missing_dependency_and_invalid_options_fail_before_mutation(self):
        with patch('shutil.which', return_value=None):
            self.assertEqual(self.cli('build'), 1)
        self.assertEqual(self.calls, [])
        for options in [('--agents', 'none'), ('--agents', 'codex', '--agents', 'claude'),
                        ('--agents', 'codex', '--tools', 'hermes-dashboard'),
                        ('--agents', 'codex', '--cpus', 'NaN'), ('--agents', 'codex', '--memory', '-1g'),
                        ('--agents', 'codex', '--ssh-port', '1023'),
                        ('--agents', 'codex', '--capabilities', 'podman,podman')]:
            self.assertEqual(self.cli('up', 'agent01', *options), 1)
        self.assertEqual(self.calls, [])

    def test_busy_port_and_foreign_ssh_owner_leave_state_untouched(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex', '--ssh-port',
                                      str(listener.getsockname()[1])), 1)
        self.assertFalse(self.volumes)
        self.owner_override = 'another-checkout'
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 1)
        self.assertFalse((self.home / '.ssh').exists())

    def test_missing_ssh_dependency_does_not_create_container_or_volumes(self):
        with patch('shutil.which', side_effect=lambda name: None if name == 'ssh-keygen' else name):
            self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex', '--ssh-config'), 1)
        self.assertFalse(self.volumes)
        self.assertFalse(any('run' in call for call in self.calls))

    def test_connection_override_is_pinned_and_mismatched_machine_is_refused(self):
        with patch.dict(os.environ, {'CONTAINER_CONNECTION': 'other'}):
            self.assertEqual(self.cli('build'), 1)
        self.connection['URI'] = 'ssh://user@127.0.0.1:54321/run/user/1000/podman/podman.sock'
        self.assertEqual(self.cli('build'), 1)
        self.assertFalse(any('build' in call for call in self.calls))

    def test_failed_capability_build_preserves_volumes(self):
        self.native_failure = ('build', 34)
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex', '--capabilities', 'podman'), 34)
        self.assertFalse(self.volumes)
        self.assertFalse(any('run' in call for call in self.calls))

    def test_failed_container_creation_retains_owned_volumes(self):
        self.native_failure = ('run', 35)
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'codex'), 35)
        self.assertEqual(set(self.volumes), {'agent01-home', 'agent01-sshd', 'agent01-workspace'})
        self.assertFalse(any('init' in call or 'rm' in call for call in self.calls))
        self.assertFalse((self.home / '.ssh').exists())

    def test_stopped_container_reports_state_and_log_command_without_removing_data(self):
        self.native_failure = ('exec', 125)
        self.container_state = {'Status': 'exited', 'ExitCode': 1, 'OOMKilled': False}
        self.assertEqual(self.cli('up', 'agent01', '--agents', 'copilot', '--tools', 't3', '--ssh-config'), 125)
        self.assertFalse((self.home / '.ssh').exists())
        self.assertIn('Host SSH setup has not run', self.output.getvalue())
        self.assertIn('ssh-config agent01 --install', self.output.getvalue())
        self.assertIn('exited', self.output.getvalue())
        self.assertIn('exit code 1', self.output.getvalue())
        self.assertIn('podman --connection machine logs --tail 100 agent01', self.output.getvalue())
        self.assertEqual(len(self.volumes), 3)
        self.assertFalse(any('rm' in call or 'init' in call for call in self.calls))

    def test_start_stop_and_restart_enforce_ownership_without_implicit_ssh(self):
        for action in ('start', 'stop', 'restart'):
            self.calls.clear()
            self.assertEqual(self.cli(action, 'agent01'), 0, self.output.getvalue())
            self.assertTrue(any(call[2:] == [action, 'c' * 64] for call in self.calls))
            self.assertFalse((self.home / '.ssh').exists())
        self.owner_override = 'other'
        self.calls.clear()
        self.assertEqual(self.cli('stop', 'agent01'), 1)
        self.assertFalse(any('stop' in call for call in self.calls))

    def test_remove_retains_volumes_and_cleans_only_owned_ssh_after_success(self):
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        config = self.home / '.ssh/config'
        with config.open('a') as stream:
            stream.write('Host unrelated\n    HostName example.test\n')
        self.native_failure = ('rm', 42)
        self.assertEqual(self.cli('remove', 'agent01'), 42, self.output.getvalue())
        self.assertTrue((self.home / '.ssh/sanboxed-agents/agent01/id_ed25519').exists())
        self.native_failure = None
        self.calls.clear()
        self.assertEqual(self.cli('remove', 'agent01'), 0, self.output.getvalue())
        self.assertEqual(config.read_text(), 'Host unrelated\n    HostName example.test\n')
        self.assertFalse((self.home / '.ssh/sanboxed-agents/agent01').exists())
        self.assertFalse(any(call[2:4] == ['volume', 'rm'] for call in self.calls))

    def test_remove_checks_all_volume_owners_before_mutation(self):
        from windows_paths import checkout_identity
        self.volumes = {'agent01-home': checkout_identity(PROJECT), 'agent01-sshd': 'foreign'}
        self.assertEqual(self.cli('remove', 'agent01', '--volumes'), 1)
        self.assertFalse(any('stop' in call or 'rm' in call for call in self.calls))
        self.volumes['agent01-sshd'] = checkout_identity(PROJECT)
        self.assertEqual(self.cli('remove', 'agent01', '--volumes'), 0, self.output.getvalue())
        removed = [call[2:] for call in self.calls if call[2:4] == ['volume', 'rm']]
        self.assertEqual(removed, [['volume', 'rm', 'agent01-home'], ['volume', 'rm', 'agent01-sshd']])

    def test_start_with_opt_in_and_diagnostics_use_owned_sandbox(self):
        self.prepare_host_key()
        self.assertEqual(self.cli('start', 'agent01', '--ssh-config'), 0, self.output.getvalue())
        self.assertTrue((self.home / '.ssh/sanboxed-agents/agent01/id_ed25519').exists())
        self.assertEqual(self.cli('fingerprint', 'agent01'), 0, self.output.getvalue())
        self.assertEqual(self.cli('shell', 'agent01'), 0, self.output.getvalue())
        self.assertTrue(any(call[-1] == '/bin/bash' and '1000:1000' in call for call in self.calls))
        self.assertEqual(self.cli('check-full', 'agent01'), 0, self.output.getvalue())
        self.assertTrue(any(call[-2:] == ['/usr/local/bin/agent-smoke', '--full'] for call in self.calls))

    def test_lifecycle_invalid_arguments_fail_before_runtime(self):
        for args in [('stop',), ('remove', 'agent01', '--force'), ('start', 'agent01', '--volumes'),
                     ('shell', 'agent01', 'extra'), ('restart', 'agent01', '--ssh-config', '--ssh-config')]:
            self.calls.clear()
            self.assertEqual(self.cli(*args), 1)
            self.assertEqual(self.calls, [])

    def test_remove_refuses_foreign_ssh_state_before_container_mutation(self):
        state = self.home / '.ssh/sanboxed-agents/agent01'
        state.mkdir(parents=True)
        (state / 'owner.json').write_text(json.dumps({'project': 'foreign', 'name': 'agent01'}))
        self.assertEqual(self.cli('remove', 'agent01'), 1)
        self.assertFalse(any('stop' in call or 'rm' in call for call in self.calls))
        self.assertTrue((state / 'owner.json').exists())

    def test_remove_without_ssh_does_not_require_keygen_or_write_host_files(self):
        with patch('shutil.which', side_effect=lambda name: None if name == 'ssh-keygen' else name):
            self.assertEqual(self.cli('remove', 'agent01'), 0, self.output.getvalue())
        self.assertFalse((self.home / '.ssh').exists())

    def test_remove_volume_failure_cleans_obsolete_ssh_but_keeps_data(self):
        from windows_paths import checkout_identity
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        self.volumes['agent01-home'] = checkout_identity(PROJECT)
        original = self.runner
        def fail_volume(command, **kwargs):
            if list(command[3:5]) == ['volume', 'rm']:
                return subprocess.CompletedProcess(command, 43, '', 'volume in use')
            return original(command, **kwargs)
        with patch.object(self, 'runner', side_effect=fail_volume):
            self.assertEqual(self.cli('remove', 'agent01', '--volumes'), 43, self.output.getvalue())
        self.assertFalse((self.home / '.ssh/sanboxed-agents/agent01').exists())
        self.assertIn('agent01-home', self.volumes)

    def test_build_preserves_extra_arguments_and_native_exit_code(self):
        self.assertEqual(self.cli('build', '--build-arg', 'VALUE=a b"c'), 0, self.output.getvalue())
        build = next(call for call in self.calls if 'build' in call)
        self.assertIn('VALUE=a b"c', build)
        self.assertEqual(build[-1], str(PROJECT / 'src/container'))
        self.native_failure = ('build', 37)
        self.assertEqual(self.cli('build'), 37, self.output.getvalue())
        self.assertFalse((self.home / '.ssh').exists())


if __name__ == '__main__':
    unittest.main()
