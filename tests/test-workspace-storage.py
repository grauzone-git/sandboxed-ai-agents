"""Exercise workspace creation and retention without a Podman daemon."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

from contract_launcher import describe_launcher
from native_fakes import write_fake

PROJECT = Path(__file__).resolve().parents[1]


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sandbox-workspace-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "controller"
        self.checkout.mkdir()
        shutil.copy2(PROJECT / "sandbox", self.checkout / "sandbox")
        shutil.copytree(PROJECT / "src", self.checkout / "src")
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.state = self.root / "volumes.json"
        self.state.write_text("{}")
        self.seccomp = self.root / 'seccomp.json'
        self.seccomp.write_text(json.dumps({'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': []}))
        self.env = {**os.environ, "HOME": str(self.home),
                    'USERPROFILE': str(self.home), 'LOCALAPPDATA': str(self.root / 'local'),
                    'XDG_STATE_HOME': str(self.root / 'state'),
                    "PATH": str(self.bin) + os.pathsep + os.environ['PATH'],
                    "TEST_LOG": str(self.log),
                    "TEST_VOLUMES": str(self.state), "TEST_SECCOMP": str(self.seccomp)}
        write_fake(self.bin, "id", "#!/usr/bin/env python3\nprint(1000)\n")
        write_fake(self.bin, "podman", '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
state = Path(os.environ['TEST_VOLUMES'])
volumes = json.loads(state.read_text())
if args[0] == 'info':
    print(json.dumps({'seccompProfilePath': os.environ['TEST_SECCOMP']}) if args[-1] == '{{json .Host.Security}}' else 'true')
elif args[:2] == ['container', 'exists']: sys.exit(1)
elif args[0] == 'inspect': print(os.environ['TEST_OWNER'])
elif args[:2] == ['image', 'inspect']: print('a' * 64)
elif args[0] == 'build':
    if os.environ.get('TEST_BUILD_FAIL'): sys.exit(1)
    print('b' * 64)
elif args[0] == 'volume':
    name = args[-1]
    if args[1] == 'exists': sys.exit(0 if name in volumes else 1)
    elif args[1] == 'inspect': print(volumes[name])
    elif args[1] == 'create':
        volumes[name] = args[args.index('--label') + 1].split('=', 1)[1]
    elif args[1] == 'rm': del volumes[name]
    else: sys.exit('Unexpected volume call')
    state.write_text(json.dumps(volumes))
elif args[0] == 'exec' and '/bin/chown' in args and os.environ.get('TEST_CHOWN_FAIL'): sys.exit(1)
elif args[0] in ('exec', 'run', 'image', 'stop', 'start', 'rm'): pass
else: sys.exit('Unexpected call: ' + repr(args))
''')
        launcher = describe_launcher(self.checkout, self.env, details=True)
        self.command = launcher['command']
        self.owner = launcher['owner']
        self.env['TEST_OWNER'] = self.owner
        self.executable = launcher['executable']
        self.version = launcher.get('version')
        self.port = 2222
        if self.executable:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                self.port = listener.getsockname()[1]

    def cli(self, *args, success=True, env=None):
        positional_port = len(args) > 3 and not args[2].startswith('-') and args[3].isdigit()
        if self.executable and len(args) > 1 and args[1] == 'up' and '--ssh-port' not in args and not positional_port:
            args = (*args, '--ssh-port', str(self.port))
        result = subprocess.run([*self.command, *args],
                                cwd=self.checkout, env={**self.env, **(env or {})},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_powershell_entry_point_is_protected_before_provisioning(self):
        if self.executable:
            self.skipTest("Checkout source protection applies to the script launcher.")
        entry = self.checkout / 'sandbox.ps1'
        entry.write_text('# PowerShell host entry point')
        self.cli('demo', 'up', str(entry), '--agents', 'codex', success=False)
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(any(call[0] == 'run' for call in self.calls()))

    def test_go_controller_sources_are_protected_before_provisioning(self):
        if self.executable:
            self.skipTest("Checkout source protection applies to the script launcher.")
        for relative in ('go.mod', 'assets.go', 'cmd/sandboxed-agents/main.go', 'internal/cli/cli.go',
                         'sandboxed-agents', 'sandboxed-agents.exe'):
            entry = self.checkout / relative
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text('// controller fixture')
            self.cli('demo', 'up', str(entry.parent if '/' in relative else entry),
                     '--agents', 'codex', success=False)
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(any(call[0] == 'run' for call in self.calls()))

    def test_packaging_scripts_and_link_targets_are_protected_before_provisioning(self):
        if self.executable:
            self.skipTest("Checkout source protection applies to the script launcher.")
        packaging = self.checkout / 'packaging'
        packaging.mkdir()
        script = packaging / 'prepare.py'
        script.write_text('# Host packaging script')
        external = self.root / 'external-packaging'
        external.mkdir()
        installer = external / 'install.cjs'
        installer.write_text('// Host package installer')
        (packaging / 'install.cjs').symlink_to(installer)
        for workspace in (packaging, script, external, installer):
            with self.subTest(workspace=workspace):
                result = self.cli('demo', 'up', str(workspace), '--agents', 'codex', success=False)
                self.assertIn('host SSH/controller', result.stderr)
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(any(call[0] in ('run', 'volume') for call in self.calls()))

    def test_live_azure_validation_files_are_protected_before_provisioning(self):
        if self.executable:
            self.skipTest("Checkout source protection applies to the script launcher.")
        tests = self.checkout / 'tests'
        tests.mkdir()
        for name in ('live-azure-auth.py', 'azure-auth-probe.py'):
            entry = tests / name
            entry.write_text('# Host-run Azure validation')
            self.cli('demo', 'up', str(entry), '--agents', 'codex', success=False)
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(any(call[0] == 'run' for call in self.calls()))

    def test_up_requires_a_sandbox_name(self):
        result = self.cli("up", "--agents", "codex", success=False)
        self.assertIn("sandboxed-agents NAME up" if self.executable else "./sandbox NAME up --agents codex", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_omitted_workspace_uses_only_named_volumes(self):
        self.cli("agent01", "up", "--agents", "codex")
        run = next(call for call in self.calls() if call[0] == "run")
        mounts = [run[i + 1] for i, arg in enumerate(run) if arg == "--volume"]
        self.assertEqual(mounts, ["agent01-workspace:/workspace", "agent01-home:/home/agent",
                                  "agent01-sshd:/var/lib/agent-sshd"])
        self.assertIn(f"127.0.0.1:{self.port}:2222", run)
        self.assertEqual(set(json.loads(self.state.read_text())),
                         {"agent01-workspace", "agent01-home", "agent01-sshd"})
        self.assertEqual(set(json.loads(self.state.read_text()).values()), {self.owner})
        self.assertIn('io.sandboxed-agents.project=' + self.owner, run)
        self.assertFalse((self.checkout / "workspaces").exists())
        self.assertFalse((self.home / ".ssh").exists())
        chown = next(call for call in self.calls() if "/bin/chown" in call)
        self.assertEqual(chown, ["exec", "--user", "0", "agent01", "/bin/chown", "1000:1000", "/workspace"])
        self.assertLess(self.calls().index(chown), next(i for i, call in enumerate(self.calls()) if "init" in call))

    def test_explicit_directory_stays_a_bind_with_either_port_syntax(self):
        directory = self.root / "workspace with spaces"
        for port_args in [("2231",), ("--ssh-port", "2231")]:
            with self.subTest(port_args=port_args):
                self.log.unlink(missing_ok=True)
                self.cli("demo", "up", str(directory), *port_args, "--agents", "codex")
                run = next(call for call in self.calls() if call[0] == "run")
                sources = [arg.removesuffix(':/workspace:Z') for arg in run if arg.endswith(':/workspace:Z')]
                self.assertEqual(len(sources), 1)
                self.assertTrue(Path(sources[0]).samefile(directory))
                self.assertIn("127.0.0.1:2231:2222", run)
                self.assertTrue(directory.is_dir())
                self.assertNotIn("demo-workspace", json.loads(self.state.read_text()))
                self.assertFalse(any("/bin/chown" in call for call in self.calls()))

    def test_capabilities_install_podman_only_when_selected(self):
        for flags in [(), ('--capabilities', 'none'), ('--capabilities', 'podman')]:
            with self.subTest(flags=flags):
                self.log.unlink(missing_ok=True)
                self.cli('demo', 'up', '--agents', 'codex', *flags)
                calls = self.calls()
                run = next(call for call in calls if call[0] == 'run')
                enabled = 'podman' in flags
                builds = [call for call in calls if call[0] == 'build']
                self.assertEqual(len(builds), int(enabled))
                self.assertEqual('--security-opt=no-new-privileges' in run, not enabled)
                self.assertEqual('--device=/dev/fuse' in run, enabled)
                self.assertEqual('--device=/dev/net/tun' in run, enabled)
                self.assertEqual('--security-opt=apparmor=unconfined' in run, enabled)
                self.assertEqual('--security-opt=label=disable' in run, enabled)
                self.assertEqual('--security-opt=unmask=ALL' in run, enabled)
                profiles = [arg.removeprefix('--security-opt=seccomp=') for arg in run
                            if arg.startswith('--security-opt=seccomp=')]
                self.assertEqual(len(profiles), int(enabled))
                if enabled:
                    if self.executable:
                        state = Path(self.env['LOCALAPPDATA'] if os.name == 'nt' else self.env['XDG_STATE_HOME'])
                        local_profile = state / 'sandboxed-agents/seccomp' / self.version / 'nested-podman.json'
                        self.assertTrue(local_profile.is_file())
                        if os.name == 'nt':
                            self.assertTrue(profiles[0].startswith('/home/user/.local/share/sandboxed-agents/seccomp/'))
                        else:
                            self.assertTrue(Path(profiles[0]).samefile(local_profile))
                    else:
                        self.assertEqual(profiles[0], str(self.checkout / '.local/nested-podman-seccomp.json'))
                self.assertNotIn('--security-opt=seccomp=unconfined', run)
                self.assertFalse(any(arg.startswith('--cap-add') for arg in run))
                tmpfs = [run[i + 1] for i, arg in enumerate(run) if arg == '--tmpfs']
                self.assertEqual(tmpfs, ['/run/user/1000:rw,nosuid,nodev,noexec,mode=0700'] if enabled else [])
                self.assertNotIn('--privileged', run)
                self.assertEqual(run[-1], 'b' * 64 if enabled else 'localhost/agent-sandbox:dev')
                self.assertIn('io.sandboxed-agents.capabilities=' + ('podman' if enabled else 'none'), run)
                if enabled:
                    self.assertIn('BASE_IMAGE=' + 'a' * 64, builds[0])
                    self.assertLess(calls.index(builds[0]), calls.index(run))

    def test_invalid_capabilities_fail_before_podman(self):
        for flags in ['', 'docker', 'podman,none', 'podman,podman', 'podman,', 'all']:
            with self.subTest(flags=flags):
                self.cli('demo', 'up', '--agents', 'codex', '--capabilities', flags, success=False)
                self.assertEqual(self.calls(), [])
        for flags in [('--capabilities',), ('--capabilities', 'none', '--capabilities', 'podman')]:
            self.cli('demo', 'up', '--agents', 'codex', *flags, success=False)
            self.assertEqual(self.calls(), [])

    def test_capability_build_failure_leaves_volumes_unmodified(self):
        self.cli('demo', 'up', '--agents', 'codex', '--capabilities', 'podman',
                 success=False, env={'TEST_BUILD_FAIL': '1'})
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(any(call[0] == 'run' for call in self.calls()))

    def test_named_volume_reused_after_removal_then_deleted_on_request(self):
        self.cli("demo", "up", "--ssh-port", "2232", "--agents", "codex")
        run = next(call for call in self.calls() if call[0] == "run")
        self.assertIn("127.0.0.1:2232:2222", run)
        self.cli("demo", "remove")
        self.assertEqual(len(json.loads(self.state.read_text())), 3)
        self.log.unlink()
        self.cli("demo", "up", "--agents", "codex")
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in self.calls()))
        self.cli("demo", "remove", "--volumes")
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_foreign_workspace_volume_rejected_before_mutation(self):
        self.state.write_text(json.dumps({"demo-workspace": "/another-controller"}))
        for args in [("demo", "up", "--agents", "codex"), ("demo", "remove", "--volumes")]:
            with self.subTest(args=args):
                self.log.unlink(missing_ok=True)
                result = self.cli(*args, success=False)
                self.assertIn("belongs to another configuration", result.stderr)
                self.assertFalse(any(call[0] in ("run", "stop", "rm") or
                                     call[:2] in (["volume", "create"], ["volume", "rm"])
                                     for call in self.calls()))
                self.assertFalse((self.checkout / "workspaces").exists())

    def test_invalid_ports_and_empty_directory_fail_before_podman(self):
        options = [("--ssh-port",), ("--ssh-port", ""), ("--ssh-port", "1023"),
                   ("--ssh-port", "65536"), ("--ssh-port", "nope"),
                   ("--ssh-port", "2222", "--ssh-port", "2223"),
                   ("./project", "2222", "--ssh-port", "2223"), ("",)]
        for option in options:
            with self.subTest(option=option):
                self.cli("demo", "up", "--agents", "codex", *option, success=False)
        self.assertEqual(self.calls(), [])

    def test_failed_workspace_initialization_does_not_install_agents(self):
        self.cli("demo", "up", "--agents", "codex", success=False, env={"TEST_CHOWN_FAIL": "1"})
        self.assertFalse(any("init" in call for call in self.calls()))

    def test_grammar_host_script_cannot_be_exposed_as_workspace(self):
        if self.executable:
            self.skipTest("Checkout source protection applies to the script launcher.")
        script = self.checkout / "src/host/grammar.py"
        link = self.root / "grammar-link"
        link.symlink_to(script)
        for workspace in (script, link):
            with self.subTest(workspace=workspace):
                self.log.unlink(missing_ok=True)
                result = self.cli("demo", "up", str(workspace), "--agents", "codex", success=False)
                self.assertIn("host SSH/controller files", result.stderr)
                self.assertFalse(any(call[0] == "run" or call[:2] == ["volume", "create"]
                                     for call in self.calls()))

    def test_azure_host_transport_cannot_be_exposed_as_workspace(self):
        if self.executable:
            self.skipTest("Checkout source protection applies to the script launcher.")
        script = self.checkout / 'src/host/azure_auth.py'
        link = self.root / 'azure-link'
        link.symlink_to(script)
        for workspace in (script, link):
            with self.subTest(workspace=workspace):
                self.log.unlink(missing_ok=True)
                result = self.cli('demo', 'up', str(workspace), '--agents', 'codex', success=False)
                self.assertIn('host SSH/controller files', result.stderr)
                self.assertFalse(any(call[0] == 'run' or call[:2] == ['volume', 'create']
                                     for call in self.calls()))


    def test_executable_protects_state_ssh_and_future_build_contexts(self):
        if not self.executable:
            self.skipTest("Executable state protection is covered by the executable launcher.")
        state_root = Path(self.env['LOCALAPPDATA'] if os.name == 'nt' else self.env['XDG_STATE_HOME'])
        protected = state_root / 'sandboxed-agents'
        for workspace in (state_root, protected / 'new-workspace', self.home, self.home / '.ssh/new'):
            with self.subTest(workspace=workspace):
                result = self.cli('demo', 'up', str(workspace), '--agents', 'codex', success=False)
                self.assertIn('workspace', result.stderr.lower())
                self.assertEqual(self.calls(), [])
        temporary = self.root / 'future-builds'
        temporary.mkdir()
        self.cli('demo', 'up', str(temporary), '--agents', 'codex', success=False,
                 env={'TMPDIR': str(temporary), 'TEMP': str(temporary), 'TMP': str(temporary)})
        self.assertEqual(self.calls(), [])
        protected.mkdir(parents=True)
        alias = self.root / 'state-alias'
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(protected)],
                           check=True, capture_output=True)
        else:
            alias.symlink_to(protected, target_is_directory=True)
        self.cli('demo', 'up', str(alias / 'new-workspace'), '--agents', 'codex', success=False)
        self.assertEqual(self.calls(), [])
        self.assertFalse((protected / 'new-workspace').exists())

    def test_project_local_executable_suggests_global_install(self):
        if not self.executable or len(self.command) != 1:
            self.skipTest("Relocating the executable requires a direct binary launcher.")
        workspace = self.root / 'project-local'
        workspace.mkdir()
        installed = workspace / Path(self.command[0]).name
        shutil.copy2(self.command[0], installed)
        self.command = [str(installed)]
        result = self.cli('demo', 'up', str(workspace), '--agents', 'codex', success=False)
        self.assertIn('global install', result.stderr.lower())
        self.assertEqual(self.calls(), [])
        self.assertEqual(json.loads(self.state.read_text()), {})


if __name__ == "__main__":
    unittest.main()
