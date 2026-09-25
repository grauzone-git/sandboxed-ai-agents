"""Exercise package installers offline without touching sandbox or SSH state."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from native_fakes import write_fake

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ('sandboxed-agents-linux-amd64', 'sandboxed-agents-linux-arm64',
             'sandboxed-agents-windows-amd64.exe')


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox packages ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        for name in ARTIFACTS:
            (self.artifacts / name).write_bytes(b'#!/bin/sh\nprintf "package fixture\\n"\n')
        (self.artifacts / 'SHA256SUMS').write_text(''.join(
            hashlib.sha256((self.artifacts / name).read_bytes()).hexdigest() + '  ' + name + '\n'
            for name in ARTIFACTS))
        self.output = self.root / 'packages'
        self.ssh = self.root / 'home/.ssh/config'
        self.ssh.parent.mkdir(parents=True)
        self.ssh.write_text('existing SSH config\n')
        self.state = self.root / 'state/sandboxed-agents/keep'
        self.state.parent.mkdir(parents=True)
        self.state.write_text('existing state\n')
        self.env = {**os.environ, 'HOME': str(self.root / 'home'),
                    'USERPROFILE': str(self.root / 'home'),
                    'LOCALAPPDATA': str(self.root / 'local'),
                    'XDG_STATE_HOME': str(self.root / 'state'),
                    'npm_config_cache': str(self.root / 'cache')}
        result = self.prepare(self.output)
        self.assertEqual(result.returncode, 0, result.stderr)

    def prepare(self, output):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'packaging/prepare.py'),
                               '--version', '0.1.0-test', '--artifacts', str(self.artifacts),
                               '--output', str(output)], env=self.env, capture_output=True, text=True)

    def assertHostStateKept(self):
        self.assertEqual(self.ssh.read_text(), 'existing SSH config\n')
        self.assertEqual(self.state.read_text(), 'existing state\n')

    def test_nuget_pack_contains_declared_license_readme_and_tools(self):
        nuget = shutil.which('nuget')
        if os.name != 'nt' and not nuget:
            self.skipTest('NuGet CLI packaging is required by the native Windows CI job.')
        self.assertIsNotNone(nuget, 'Native Windows package tests require nuget on PATH.')
        packed = self.root / 'packed'
        packed.mkdir()
        result = subprocess.run([nuget, 'pack', str(self.output / 'nuget/sandboxed-agents.nuspec'),
                                 '-OutputDirectory', str(packed), '-NonInteractive'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list(packed.glob('*.nupkg'))
        self.assertEqual(len(archives), 1)
        payload = ('LICENSE', 'README.md', 'install-command.ps1', 'remove-command.ps1',
                   'package-functions.ps1', 'sandboxed-agents-windows-amd64.exe', 'SHA256SUMS')
        with zipfile.ZipFile(archives[0]) as archive:
            self.assertEqual({name for name in archive.namelist() if name.startswith('tools/')},
                             {'tools/' + name for name in payload})
            for name in payload:
                self.assertEqual(archive.read('tools/' + name),
                                 (self.output / 'nuget/tools' / name).read_bytes(), name)
        self.assertHostStateKept()

    @unittest.skipIf(os.name == 'nt', 'Shell fixture executes only on Unix; Windows CI tests real binaries.')
    def test_npm_install_exposes_command_from_unrelated_directory(self):
        package = self.output / 'npm'
        subprocess.run(['npm', 'pack', '--ignore-scripts', '--offline'], cwd=package,
                       env=self.env, check=True, capture_output=True)
        archive = next(package.glob('*.tgz'))
        prefix = self.root / 'prefix'
        result = subprocess.run(['npm', 'install', '--global', '--offline', '--no-audit',
                                 '--no-fund', '--prefix', str(prefix), str(archive)],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(prefix / 'bin/sandboxed-agents'), 'version'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'package fixture\n')
        subprocess.run(['npm', 'uninstall', '--global', '--offline', '--prefix', str(prefix),
                        'sandboxed-agents'], cwd=self.root, env=self.env,
                       check=True, capture_output=True)
        self.assertHostStateKept()

    def test_npm_install_rejects_modified_binary(self):
        package = self.output / 'npm'
        for name in ARTIFACTS:
            (package / 'artifacts' / name).write_bytes(b'corrupt artifact')
        npm = shutil.which('npm')
        subprocess.run([npm, 'pack', '--ignore-scripts', '--offline'], cwd=package,
                       env=self.env, check=True, capture_output=True, shell=os.name == 'nt')
        archive = next(package.glob('*.tgz'))
        result = subprocess.run([npm, 'install', '--global', '--offline', '--no-audit',
                                 '--no-fund', '--prefix', str(self.root / 'prefix'), str(archive)],
                                cwd=self.root, env=self.env, capture_output=True, text=True,
                                shell=os.name == 'nt')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum mismatch', result.stderr)
        result = subprocess.run(['node', str(package / 'cli.cjs'), 'version'], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum mismatch', result.stderr)

    @unittest.skipIf(os.name == 'nt', 'Non-Windows platform diagnostics only')
    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed')
    def test_nuget_requires_explicit_no_path_update_outside_windows(self):
        destination = self.root / 'unsupported-install'
        for script, operation in (('install-command.ps1', 'this installer'),
                                  ('remove-command.ps1', 'command removal')):
            result = subprocess.run(['pwsh', '-NoProfile', '-File',
                                     str(self.output / 'nuget/tools' / script),
                                     '-InstallDirectory', str(destination)], env=self.env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Use -NoPathUpdate when testing ' + operation + ' outside Windows.', result.stderr)
            self.assertFalse(destination.exists())
        self.assertHostStateKept()

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed')
    def test_nuget_remove_only_deletes_installed_command(self):
        tools = self.output / 'nuget/tools'
        destination = self.root / 'installed bin'
        destination.mkdir()
        sentinel = destination / 'unrelated.txt'
        sentinel.write_text('keep me')
        for script in ('install-command.ps1', 'remove-command.ps1'):
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(tools / script),
                                     '-InstallDirectory', str(destination), '-NoPathUpdate'],
                                    env=self.env, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((destination / 'sandboxed-agents.exe').exists())
        self.assertEqual(sentinel.read_text(), 'keep me')

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed')
    def test_nuget_install_checks_hash_before_creating_command(self):
        tools = self.output / 'nuget/tools'
        install = tools / 'install-command.ps1'
        destination = self.root / 'installed bin'
        command = ['pwsh', '-NoProfile', '-File', str(install),
                   '-InstallDirectory', str(destination), '-NoPathUpdate']
        result = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = destination / 'sandboxed-agents.exe'
        self.assertEqual(installed.read_bytes(), (tools / ARTIFACTS[2]).read_bytes())
        installed.unlink()
        (tools / ARTIFACTS[2]).write_bytes(b'corrupt artifact')
        result = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum mismatch', result.stderr)
        self.assertFalse(installed.exists())
        self.assertHostStateKept()

    def test_prepare_rejects_malformed_checksum_manifest(self):
        for manifest in ('missing-digest\n', 'z' * 64 + '  artifact\n', 'a' * 64 + '  one extra\n'):
            (self.artifacts / 'SHA256SUMS').write_text(manifest)
            result = self.prepare(self.root / 'invalid')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Invalid SHA256SUMS line 1', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse((self.root / 'invalid').exists())

    def test_prepare_reports_missing_required_checksum_entry(self):
        manifest = self.artifacts / 'SHA256SUMS'
        missing = ARTIFACTS[-1]
        manifest.write_text(''.join(line + '\n' for line in manifest.read_text().splitlines()
                                    if not line.endswith('  ' + missing)))
        output = self.root / 'missing-entry'
        result = self.prepare(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA256SUMS has no entry for ' + missing, result.stderr)
        self.assertIn('regenerate it with packaging/build.py', result.stderr)
        self.assertNotIn('Checksum mismatch', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())
        self.assertHostStateKept()

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed')
    def test_nuget_remove_refuses_a_replaced_command_and_preserves_state(self):
        tools = self.output / 'nuget/tools'
        destination = self.root / 'installed bin'
        destination.mkdir()
        installed = destination / 'sandboxed-agents.exe'
        installed.write_bytes(b'newer installed command')
        result = subprocess.run(['pwsh', '-NoProfile', '-File', str(tools / 'remove-command.ps1'),
                                 '-InstallDirectory', str(destination), '-NoPathUpdate'],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Installed command differs', result.stderr)
        self.assertEqual(installed.read_bytes(), b'newer installed command')
        self.assertHostStateKept()

    @unittest.skipUnless(os.name == 'nt', 'Registry value preservation requires native Windows')
    def test_user_path_preserves_raw_values_kind_and_noop_removal(self):
        # Use a disposable registry key; never rewrite the user's Environment key.
        script = self.root / 'registry-test.ps1'
        functions = self.output / 'nuget/tools/package-functions.ps1'
        script.write_text(". '" + str(functions).replace("'", "''") + "'\n" + r'''
$keyName = 'Software/SandboxedAgentsTest-' + [Guid]::NewGuid().ToString('N')
$key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($keyName)
try {
    foreach ($kind in @([Microsoft.Win32.RegistryValueKind]::ExpandString, [Microsoft.Win32.RegistryValueKind]::String)) {
        $raw = '%UNEXPANDED_TEST_VAR%\bin;;C:\existing;'
        $key.SetValue('Path', $raw, $kind)
        Update-PackageUserPath -Directory 'C:\new-bin' -Remove -RegistryKey $key
        if ($key.GetValue('Path', '', 'DoNotExpandEnvironmentNames') -cne $raw -or $key.GetValueKind('Path') -ne $kind) { throw 'No-op removal rewrote PATH' }
        Update-PackageUserPath -Directory 'C:\new-bin' -RegistryKey $key
        if ($key.GetValue('Path', '', 'DoNotExpandEnvironmentNames') -cne ($raw + ';C:\new-bin') -or $key.GetValueKind('Path') -ne $kind) { throw 'Install expanded PATH or changed its type' }
        Update-PackageUserPath -Directory 'C:\new-bin' -Remove -RegistryKey $key
        if ($key.GetValue('Path', '', 'DoNotExpandEnvironmentNames') -cne $raw -or $key.GetValueKind('Path') -ne $kind) { throw 'Removal expanded PATH or changed its type' }
    }
    $key.DeleteValue('Path')
    Update-PackageUserPath -Directory 'C:\absent' -Remove -RegistryKey $key
    if ($key.GetValueNames() -contains 'Path') { throw 'No-op removal created PATH' }
} finally {
    $key.Dispose()
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($keyName)
}
''')
        result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.environ.get('SANDBOX_TEST_PACKAGE_BINARY'), 'Set SANDBOX_TEST_PACKAGE_BINARY to test an installed native executable')
    def test_installed_npm_command_protects_global_and_local_launch_directories(self):
        binary = Path(os.environ['SANDBOX_TEST_PACKAGE_BINARY']).read_bytes()
        for name in ARTIFACTS:
            (self.artifacts / name).write_bytes(binary)
        (self.artifacts / 'SHA256SUMS').write_text(''.join(
            hashlib.sha256(binary).hexdigest() + '  ' + name + '\n' for name in ARTIFACTS))
        shutil.rmtree(self.output)
        result = self.prepare(self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        npm = shutil.which('npm')
        package = self.output / 'npm'
        subprocess.run([npm, 'pack', '--offline', '--ignore-scripts'], cwd=package, env=self.env,
                       check=True, capture_output=True, shell=os.name == 'nt')
        archive = next(package.glob('*.tgz'))
        fake_bin = self.root / 'fake-bin'
        fake_bin.mkdir()
        log = self.root / 'podman-calls'
        write_fake(fake_bin, 'podman', """#!/usr/bin/env python3
import json, os, pathlib, sys
with pathlib.Path(os.environ['PACKAGE_PODMAN_LOG']).open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(123)
""")
        runtime_env = {**self.env, 'PATH': str(fake_bin) + os.pathsep + self.env['PATH'],
                       'PACKAGE_PODMAN_LOG': str(log)}
        for global_install in (True, False):
            prefix = self.root / ('global-prefix' if global_install else 'local-project')
            subprocess.run([npm, 'install', *(['--global'] if global_install else []), '--offline',
                            '--no-audit', '--no-fund', '--prefix', str(prefix), str(archive)],
                           cwd=self.root, env=self.env, check=True, capture_output=True, shell=os.name == 'nt')
            directory = (prefix if os.name == 'nt' else prefix / 'bin') if global_install else prefix / 'node_modules/.bin'
            command = directory / ('sandboxed-agents.cmd' if os.name == 'nt' else 'sandboxed-agents')
            version = subprocess.run([str(command), 'version'], cwd=self.root, env=runtime_env,
                                     capture_output=True, text=True, shell=os.name == 'nt')
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn('sandboxed-agents version', version.stdout)
            result = subprocess.run([str(command), 'demo', 'up', str(directory), '--agents', 'codex'],
                                    cwd=self.root, env=runtime_env, capture_output=True, text=True, shell=os.name == 'nt')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('global install', result.stderr.lower())
            self.assertFalse(log.exists(), 'Unsafe package workspace reached Podman')
        self.assertHostStateKept()


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox-build-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'release'
        self.log = self.root / 'calls.jsonl'
        write_fake(self.root, 'go', '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ['BUILD_CALLS'])
with log.open('a') as stream:
    stream.write(json.dumps({'args': args, 'env': {key: os.environ.get(key) for key in ('GOENV', 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GOTOOLCHAIN', 'GOFLAGS', 'GOEXPERIMENT', 'HOME', 'USERPROFILE', 'LOCALAPPDATA')}}) + '\\n')
if args == ['env', 'GOVERSION']:
    print(os.environ.get('BUILD_GO_VERSION', 'go1.27.1'))
elif args[:1] == ['build']:
    content = os.environ['GOOS'] + '/' + os.environ['GOARCH']
    if os.environ.get('BUILD_DIFFERENT'): content += str(len(log.read_text().splitlines()))
    pathlib.Path(args[args.index('-o') + 1]).write_text(content)
else: sys.exit('Unexpected fake Go invocation')
''')
        (self.root / 'home').mkdir()
        (self.root / 'local').mkdir()
        self.env = {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
                    'HOME': str(self.root / 'home'), 'USERPROFILE': str(self.root / 'home'),
                    'LOCALAPPDATA': str(self.root / 'local'),
                    'BUILD_CALLS': str(self.log), 'GOENV': '/untrusted/go-env',
                    'GOFLAGS': '-race', 'GOEXPERIMENT': 'untrusted'}

    def build(self, **environment):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'packaging/build.py'),
                               '--version', '0.1.0-test', '--commit', 'a' * 40, '--output', str(self.output)],
                              env={**self.env, **environment}, capture_output=True, text=True)

    def test_rebuilds_every_target_with_fixed_flags_and_clean_go_settings(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(calls[0]['args'], ['env', 'GOVERSION'])
        self.assertEqual(len(calls), 7)
        targets = [(call['env']['GOOS'], call['env']['GOARCH']) for call in calls[1:]]
        self.assertEqual(targets, [('linux', 'amd64')] * 2 + [('linux', 'arm64')] * 2 + [('windows', 'amd64')] * 2)
        for call in calls:
            self.assertEqual(call['env']['GOENV'], 'off')
            self.assertEqual(call['env']['GOTOOLCHAIN'], 'local')
            self.assertEqual(call['env']['GOFLAGS'], '')
            self.assertEqual(call['env']['GOEXPERIMENT'], '')
            self.assertEqual(call['env']['HOME'], str(self.root / 'home'))
            self.assertEqual(call['env']['USERPROFILE'], str(self.root / 'home'))
            self.assertEqual(call['env']['LOCALAPPDATA'], str(self.root / 'local'))
        for call in calls[1:]:
            self.assertEqual(call['env']['CGO_ENABLED'], '0')
            self.assertIn('-trimpath', call['args'])
            self.assertIn('-buildvcs=false', call['args'])
            self.assertIn('-buildid= -X main.version=0.1.0-test -X main.commit=' + 'a' * 40, call['args'])
        expected = ''.join(hashlib.sha256((self.output / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in ARTIFACTS)
        self.assertEqual((self.output / 'SHA256SUMS').read_text(), expected)

    def test_wrong_toolchain_fails_before_build_or_output_creation(self):
        result = self.build(BUILD_GO_VERSION='go1.26.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Release builds require go1.27.1', result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_different_rebuild_fails_without_release_manifest(self):
        result = self.build(BUILD_DIFFERENT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Repeated build differs', result.stderr)
        self.assertFalse((self.output / 'SHA256SUMS').exists())


if __name__ == '__main__':
    unittest.main()
