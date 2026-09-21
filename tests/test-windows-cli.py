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

    def test_ssh_install_preserves_edits_made_during_provisioning(self):
        from windows_paths import checkout_identity
        from windows_runtime import Runtime
        from windows_ssh import SshSetup
        self.prepare_host_key()
        config = self.home / '.ssh/config'
        config.parent.mkdir()
        config.write_bytes(b'Host original\r\n    HostName original.example\r\n')
        runtime = Runtime(self.runner)
        runtime.connection = 'machine'
        setup = SshSetup(runtime, checkout_identity(PROJECT), 'agent01')
        updated = config.read_bytes() + b'Host added-during-build\r\n    HostName new.example\r\n'
        config.write_bytes(updated)
        setup.install()
        self.assertTrue(config.read_bytes().endswith(updated))

    def test_overlapping_ssh_setups_preserve_both_includes(self):
        from windows_paths import checkout_identity
        from windows_runtime import Runtime
        from windows_ssh import SshSetup
        self.prepare_host_key()
        runtime = Runtime(self.runner)
        runtime.connection = 'machine'
        setups = [SshSetup(runtime, checkout_identity(PROJECT), name) for name in ('agent01', 'agent02')]
        for setup in setups:
            setup.install()
        content = (self.home / '.ssh/config').read_text()
        for name in ('agent01', 'agent02'):
            self.assertEqual(content.count(f'/{name}/{name}.conf'), 1)

    def test_ssh_config_lock_prevents_install_and_remove_overwriting_another_writer(self):
        from windows_paths import checkout_identity
        from windows_runtime import Runtime
        from windows_ssh import SshSetup
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        runtime = Runtime(self.runner)
        runtime.connection = 'machine'
        setup = SshSetup(runtime, checkout_identity(PROJECT), 'agent01')
        original = setup.config.read_bytes()
        lock = setup.root / '.config.lock'
        lock.mkdir()
        try:
            for operation in (setup.install, setup.remove):
                with self.assertRaisesRegex(ValueError, 'SSH config update is locked'):
                    operation()
                self.assertEqual(setup.config.read_bytes(), original)
                self.assertTrue((setup.state / 'id_ed25519').is_file())
                self.assertTrue(lock.is_dir())
        finally:
            lock.rmdir()
        setup.remove()
        self.assertFalse(setup.state.exists())
        self.assertFalse(lock.exists())

    def test_ssh_config_edit_during_file_preparation_is_not_overwritten(self):
        from windows_paths import checkout_identity
        from windows_runtime import Runtime
        from windows_ssh import SshSetup
        self.prepare_host_key()
        runtime = Runtime(self.runner)
        runtime.connection = 'machine'
        setup = SshSetup(runtime, checkout_identity(PROJECT), 'agent01')
        edited = b'Host edited-during-write\r\n    HostName kept.example\r\n'
        secure = setup.secure

        def edit_during_acl_update(path):
            secure(path)
            if path.parent == setup.config.parent:
                setup.config.write_bytes(edited)

        with patch.object(setup, 'secure', side_effect=edit_during_acl_update):
            with self.assertRaisesRegex(ValueError, 'SSH config changed'):
                setup.install()
        self.assertEqual(setup.config.read_bytes(), edited)
        self.assertFalse((setup.root / '.config.lock').exists())
        self.assertEqual(list(setup.config.parent.glob('.sandbox-ssh-*')), [])
        setup.install()
        self.assertTrue(setup.config.read_bytes().endswith(edited))

    def test_remove_checks_ssh_config_lock_before_container_mutation(self):
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        config = self.home / '.ssh/config'
        original = config.read_bytes()
        lock = self.home / '.ssh/sanboxed-agents/.config.lock'
        lock.mkdir()
        self.calls.clear()
        try:
            self.assertEqual(self.cli('remove', 'agent01'), 1)
            self.assertIn('SSH config update is locked', self.output.getvalue())
            self.assertFalse(any(call[2:3] in (['stop'], ['rm']) for call in self.calls))
            self.assertEqual(config.read_bytes(), original)
        finally:
            lock.rmdir()
        self.assertEqual(self.cli('remove', 'agent01'), 0, self.output.getvalue())

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

    def test_agent_and_tool_management_routes_owned_unprivileged_commands(self):
        cases = [
            (('agents', 'agent01'), 'agents', ['list']),
            (('tools', 'agent01', 'check'), 'tools', ['check']),
            (('agents', 'agent01', 'set', 'codex,claude'), 'agents', ['set', 'codex,claude']),
            (('tools', 'agent01', 'enable', 't3@1.2.3'), 'tools', ['enable', 't3@1.2.3']),
            (('agents', 'agent01', 'disable', 'none'), 'agents', ['disable', 'none']),
            (('tools', 'agent01', 'update', 'all'), 'tools', ['update', 'all']),
        ]
        for arguments, kind, expected in cases:
            self.calls.clear()
            self.assertEqual(self.cli(*arguments), 0, self.output.getvalue())
            self.assertIn(['--connection', 'machine', 'exec', '--user', '1000:1000',
                           '--workdir', '/workspace', 'c' * 64,
                           f'/usr/local/bin/sandbox-{kind}', *expected], self.calls)
        self.assertFalse((self.home / '.ssh').exists())

    def test_login_setup_runs_and_sessions_keep_stdin_and_literal_arguments(self):
        cases = [
            (('agents', 'agent01', 'login', 'copilot'), 'agents', ['login', 'copilot']),
            (('tools', 'agent01', 'login', 'github'), 'tools', ['login', 'github']),
            (('tools', 'agent01', 'setup', 't3'), 'tools', ['setup', 't3']),
            (('run', 'agent01', 'codex', 'space and "quote"', '', '$literal;value'),
             'agents', ['run', 'codex', 'space and "quote"', '', '$literal;value']),
            (('tool', 'agent01', 't3', '--help'), 'tools', ['run', 't3', '--help']),
            (('copilot', 'agent01'), 'agents', ['session', 'copilot']),
            (('t3', 'agent01'), 'tools', ['session', 't3']),
        ]
        for arguments, kind, expected in cases:
            self.calls.clear()
            self.assertEqual(self.cli(*arguments), 0, self.output.getvalue())
            call = next(call for call in self.calls if 'exec' in call)
            self.assertIn('-i', call)
            self.assertNotIn('-t', call)  # Captured output is not a terminal.
            self.assertEqual(call[call.index(f'/usr/local/bin/sandbox-{kind}'):],
                             [f'/usr/local/bin/sandbox-{kind}', *expected])
        self.assertFalse((self.home / '.ssh').exists())

    def test_manager_validation_and_ownership_precede_execution(self):
        invalid = [('agents',), ('agents', 'agent01', 'set', 'bad'),
                   ('tools', 'agent01', 'list', 'extra'), ('agents', 'agent01', 'login', 'deepseek'),
                   ('tools', 'agent01', 'setup', 'github'), ('copilot', 'agent01', '--help'),
                   ('run', 'agent01'), ('tool', 'agent01', 'not-a-tool')]
        for arguments in invalid:
            self.calls.clear()
            self.assertEqual(self.cli(*arguments), 1)
            self.assertEqual(self.calls, [])
        self.owner_override = 'foreign'
        self.assertEqual(self.cli('tools', 'agent01', 'set', 't3'), 1)
        self.assertFalse(any('exec' in call for call in self.calls))
        self.owner_override = None
        self.native_failure = ('exec', 39)
        self.assertEqual(self.cli('agents', 'agent01', 'check'), 39)

    def test_forward_refuses_occupied_port_before_service_or_ssh(self):
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        files = {path: path.read_bytes() for path in (self.home / '.ssh').rglob('*') if path.is_file()}
        self.calls.clear()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = str(listener.getsockname()[1])
            self.assertEqual(self.cli('forward', 'agent01', 't3', port), 1, self.output.getvalue())
        self.assertIn(f'Forwarding port {port} is unavailable', self.output.getvalue())
        self.assertFalse(any('exec' in call or '-L' in call for call in self.calls))
        self.assertEqual(files, {path: path.read_bytes() for path in files})
        self.assertEqual(self.cli('forward', 'agent01', 't3', port), 0, self.output.getvalue())
        self.assertTrue(any('-L' in call for call in self.calls))

    @patch('windows_commands.require_forward_port')
    def test_tool_services_and_forwarding_use_catalog_ports_and_managed_ssh(self, port_check):
        self.assertEqual(self.cli('service', 'agent01', 'hermes', 'restart'), 0, self.output.getvalue())
        self.assertTrue(any(call[-3:] == ['service', 'hermes-dashboard', 'restart'] for call in self.calls))
        self.calls.clear()
        self.assertEqual(self.cli('forward', 'agent01', 't3'), 1)
        self.assertFalse(any('exec' in call for call in self.calls))
        self.assertFalse((self.home / '.ssh').exists())
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        self.calls.clear()
        self.assertEqual(self.cli('forward', 'agent01', 't3', '4773'), 0, self.output.getvalue())
        self.assertTrue(any(call[-3:] == ['service', 't3', 'start'] for call in self.calls))
        forward = next(call for call in self.calls if '-L' in call)
        self.assertIn('127.0.0.1:4773:127.0.0.1:3773', forward)
        self.assertIn('ExitOnForwardFailure=yes', forward)
        self.assertEqual(forward[forward.index('-F') + 1], str(self.home / '.ssh/sanboxed-agents/agent01/agent01.conf'))
        self.assertEqual(forward[-1], 'agent01')
        self.calls.clear()
        self.assertEqual(self.cli('forward', 'agent01', 'deepseek'), 0)
        self.assertTrue(any('127.0.0.1:3080:127.0.0.1:3080' in call for call in self.calls))
        self.assertEqual([call.args[0] for call in port_check.call_args_list], [4773, 3080])

    def test_invalid_service_and_forward_options_do_not_contact_engine(self):
        for arguments in [('service', 'agent01', 't3', 'bad'), ('service', 'agent01', 'copilot'),
                          ('forward', 'agent01', 't3', '22'), ('forward', 'agent01', 'unknown'),
                          ('forward', 'agent01', 't3', '3773', 'extra')]:
            self.calls.clear()
            self.assertEqual(self.cli(*arguments), 1)
            self.assertEqual(self.calls, [])

    def test_interactive_manager_allocates_tty_only_for_terminal(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        with patch('sys.stdin', Terminal()), contextlib.redirect_stdout(Terminal()), contextlib.redirect_stderr(io.StringIO()):
            result = main(['agents', 'agent01', 'login', 'codex'], runner=self.runner)
        self.assertEqual(result, 0)
        call = next(call for call in self.calls if 'exec' in call)
        self.assertIn('-i', call)
        self.assertIn('-t', call)

    @patch('windows_commands.require_forward_port')
    def test_forward_failure_preserves_ssh_files_and_native_status(self, port_check):
        self.prepare_host_key()
        self.assertEqual(self.cli('ssh-config', 'agent01', '--install'), 0)
        files = {path: path.read_bytes() for path in (self.home / '.ssh').rglob('*') if path.is_file()}
        self.calls.clear()
        self.native_failure = ('exec', 41)
        self.assertEqual(self.cli('forward', 'agent01', 't3'), 41)
        self.assertFalse(any('-L' in call for call in self.calls))
        self.native_failure = ('-L', 255)
        self.assertEqual(self.cli('forward', 'agent01', 't3'), 255)
        self.assertEqual(files, {path: path.read_bytes() for path in files})
        self.owner_override = 'foreign'
        self.calls.clear()
        self.assertEqual(self.cli('forward', 'agent01', 't3'), 1)
        self.assertFalse(any('exec' in call or '-L' in call for call in self.calls))

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
