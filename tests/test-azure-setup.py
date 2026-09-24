"""Exercise sandbox Azure session replacement through setup and ordinary az."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import signal
import socket
import time
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src/container'))
import azure_setup


class AzureSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox-azure-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        self.env = {**os.environ, 'HOME': str(self.home),
                    'PATH': f'{self.bin}:{os.environ["PATH"]}'}
        self.env.pop('AZURE_CONFIG_DIR', None)
        az = self.bin / 'az'
        az.write_text('''#!/usr/bin/env python3
import configparser, json, os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ.get('AZURE_CONFIG_DIR', str(Path.home() / '.azure')))
root.mkdir(exist_ok=True)
config = configparser.ConfigParser()
config.read(root / 'config')
if args[:2] == ['cloud', 'set']:
    if not config.has_section('cloud'): config.add_section('cloud')
    config.set('cloud', 'name', args[args.index('--name') + 1])
    with (root / 'config').open('w') as stream: config.write(stream)
elif args[0] == 'login':
    if '--use-device-code' not in args: sys.exit(20)
    if (root / 'msal_token_cache.json').exists(): sys.exit(21)
    print('To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code ABCDEFGHI to authenticate.', file=sys.stderr, flush=True)
    if os.environ.get('LOGIN_ERROR'):
        print(os.environ['LOGIN_ERROR'], file=sys.stderr)
        sys.exit(1)
    if os.environ.get('LOGIN_WAIT'):
        import time
        Path(os.environ['LOGIN_WAIT']).touch()
        while True: time.sleep(0.1)
    (root / 'msal_token_cache.json').write_text('new-session')
    tenant_only = '--skip-subscription-discovery' in args
    (root / 'azureProfile.json').write_text(json.dumps({'id': 'tenant-1' if tenant_only else 'subscription-1', 'tenantId': 'tenant-1', 'name': 'N/A(tenant level account)' if tenant_only else 'Example', 'user': {'name': 'user@example.test', 'type': 'user'}}))
elif args[:2] == ['account', 'list']:
    print('[' + (root / 'azureProfile.json').read_text() + ']')
elif args[:2] == ['account', 'show']:
    print((root / 'azureProfile.json').read_text())
elif args[:2] == ['account', 'set']:
    if args[args.index('--subscription') + 1] != 'subscription-1': sys.exit(2)
else: sys.exit(99)
''')
        az.chmod(0o755)

    def setup(self, *args, extra=None):
        return subprocess.run([sys.executable, '-B', str(PROJECT / 'src/container/azure_setup.py'),
                               '--tenant', 'tenant-1', *([] if '--tenant-only' in args else ['--subscription', 'subscription-1']), *args],
                              env={**self.env, **(extra or {})}, text=True, capture_output=True)

    def test_default_device_code_is_available_to_ordinary_az(self):
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ABCDEFGHI', result.stdout)
        account = subprocess.run(['az', 'account', 'show'], env=self.env,
                                 capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(account.stdout)['id'], 'subscription-1')

    def test_replacement_preserves_devops_and_failure_preserves_manual_session(self):
        active = self.home / '.azure'
        active.mkdir()
        (active / 'config').write_text('[cloud]\nname = AzureCloud\n[defaults]\norganization = https://dev.azure.com/example\n')
        (active / 'azureProfile.json').write_text('{"id": "manual-session"}')
        (active / 'msal_token_cache.json').write_text('manual-cache')
        (active / 'azuredevops').mkdir()
        (active / 'azuredevops/config').write_text('independent devops defaults')
        before = {str(p.relative_to(active)): p.read_bytes() for p in active.rglob('*') if p.is_file()}
        result = self.setup(extra={'LOGIN_ERROR': 'AADSTS50076 secret-test-value'})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('secret-test-value', result.stdout + result.stderr)
        self.assertIn('interaction required', result.stderr.lower())
        self.assertEqual(before, {str(p.relative_to(active)): p.read_bytes() for p in active.rglob('*') if p.is_file()})
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('organization = https://dev.azure.com/example', (active / 'config').read_text())
        self.assertEqual((active / 'azuredevops/config').read_text(), 'independent devops defaults')

    def test_tenant_only_and_explicit_cloud_validation(self):
        result = self.setup('--tenant-only', '--cloud', 'AzureCloud')
        self.assertEqual(result.returncode, 0, result.stderr)
        account = subprocess.run(['az', 'account', 'show'], env=self.env,
                                 capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(account.stdout)['name'], 'N/A(tenant level account)')
        result = self.setup('--cloud', 'unsupported')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported Azure cloud', result.stderr)

    def test_both_clouds_are_remembered_and_failed_cloud_change_rolls_back(self):
        import configparser
        for cloud in ('AzureCloud', 'AzureChinaCloud'):
            result = self.setup('--cloud', cloud)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.setup()
            self.assertEqual(result.returncode, 0, result.stderr)
            config = configparser.ConfigParser()
            config.read(self.home / '.azure/config')
            self.assertEqual(config.get('cloud', 'name'), cloud)
        result = self.setup('--cloud', 'AzureCloud', extra={'LOGIN_ERROR': 'network timeout'})
        self.assertNotEqual(result.returncode, 0)
        config.read(self.home / '.azure/config')
        self.assertEqual(config.get('cloud', 'name'), 'AzureChinaCloud')
        self.assertIn('network', result.stderr)

    def test_cancelled_replacement_preserves_session_and_removes_temporary_cache(self):
        self.assertEqual(self.setup().returncode, 0)
        original = (self.home / '.azure/msal_token_cache.json').read_bytes()
        ready = self.home / 'login-waiting'
        child = subprocess.Popen([sys.executable, '-B', str(PROJECT / 'src/container/azure_setup.py'),
                                  '--tenant', 'tenant-1', '--tenant-only'],
                                 env={**self.env, 'LOGIN_WAIT': str(ready)},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            child.send_signal(signal.SIGTERM)
            stdout, stderr = child.communicate(timeout=5)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn('cancelled', stderr)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
        self.assertEqual((self.home / '.azure/msal_token_cache.json').read_bytes(), original)
        self.assertEqual(list(self.home.glob('.azure-login-*')), [])

    def test_direct_interactive_points_to_host_without_starting_login(self):
        result = self.setup('--interactive')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'./sandbox {socket.gethostname()} tools setup azure --tenant tenant-1 --subscription subscription-1 --interactive', result.stderr)
        self.assertFalse((self.home / '.azure').exists())

    def test_replacement_keeps_user_cloud_registrations(self):
        (self.home / '.azure').mkdir()
        (self.home / '.azure/clouds.config').write_text('[MyCloud]\nendpoint_resource_manager = https://example.test/\n')
        self.assertEqual(self.setup().returncode, 0)
        self.assertIn('[MyCloud]', (self.home / '.azure/clouds.config').read_text())

    def test_hosted_errors_carry_their_category(self):
        self.assertEqual(azure_setup.failure('AADSTS50076: interaction_required').category, 'interaction')
        self.assertEqual(azure_setup.failure('(AuthorizationFailed) denied').category, 'permission')
        self.assertEqual(azure_setup.failure('unexpected').category, 'setup')
        self.assertEqual(azure_setup.cancelled_error().category, 'cancelled')

    def test_publication_io_failure_rolls_back_all_prior_azure_files(self):
        self.assertEqual(self.setup().returncode, 0)
        active = self.home / '.azure'
        (active / 'azureProfile.json').write_text('{"id":"prior-manual-subscription"}')
        before = {file.name: file.read_bytes() for file in active.iterdir()}
        replace = os.replace
        failed = False
        def replace_once(source, destination):
            nonlocal failed
            if Path(destination) == active / 'msal_token_cache.json' and not failed:
                failed = True
                raise OSError('simulated publication failure')
            return replace(source, destination)
        with patch.dict(os.environ, self.env, clear=True), patch('os.replace', side_effect=replace_once):
            with self.assertRaises(OSError):
                azure_setup.main(['--tenant', 'tenant-1', '--subscription', 'subscription-1'])
        self.assertTrue(failed)
        self.assertEqual({file.name: file.read_bytes() for file in active.iterdir()}, before)
        self.assertEqual(list(self.home.glob('.azure-login-*')), [])

    def test_hosted_browser_requires_commit_and_preserves_session_on_disconnect(self):
        # Replace only the external CLI package. The real browser adapter and
        # setup protocol run in child processes, with no installed Azure CLI.
        for package in ('azure', 'azure/cli', 'azure/cli/core'):
            directory = self.home / package
            directory.mkdir(exist_ok=True)
            (directory / '__init__.py').write_text('')
        (self.home / 'azure/cli/core/__init__.py').write_text('''
import configparser, json, os, webbrowser
from pathlib import Path
class CLI:
    def invoke(self, args):
        try:
            webbrowser.get()
        except webbrowser.Error:
            return 51
        root = Path(os.environ['AZURE_CONFIG_DIR'])
        config = configparser.ConfigParser()
        config.read(root / 'config')
        authority = 'login.chinacloudapi.cn' if config.get('cloud', 'name') == 'AzureChinaCloud' else 'login.microsoftonline.com'
        url = 'https://' + authority + '/tenant/oauth2/v2.0/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A45678&response_type=code&state=dummy'
        # MSAL 1.36 explicitly selects installed Edge on Linux unless BROWSER
        # states another preference, bypassing webbrowser's preferred adapter.
        if 'BROWSER' not in os.environ or 'microsoft-edge' in os.environ['BROWSER']:
            class Edge(webbrowser.BaseBrowser):
                def open(self, *args, **kwargs):
                    (Path.home() / 'local-browser-launched').touch()
                    return True
            webbrowser.register('msal-edge', None, Edge())
            webbrowser.get('msal-edge').open(url)
        else:
            webbrowser.open(url)
        (root / 'msal_token_cache.json').write_text('browser-session')
        (root / 'azureProfile.json').write_text('{"id":"subscription-1", "tenantId":"tenant-1"}')
        return 0
def get_default_cli(): return CLI()
''')
        self.assertEqual(self.setup().returncode, 0)
        for commit in (False, True):
            with self.subTest(commit=commit):
                browser_env = {key: value for key, value in self.env.items() if key != 'BROWSER'}
                if commit:
                    browser_env['BROWSER'] = 'microsoft-edge'
                child = subprocess.Popen([sys.executable, '-B', str(PROJECT / 'src/container/azure_setup.py'),
                                          '--host-protocol', '--interactive', '--tenant', 'tenant-1',
                                          '--subscription', 'subscription-1', '--cloud', 'AzureChinaCloud'],
                                         env={**browser_env, 'PYTHONPATH': str(self.home)},
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    event = json.loads(child.stdout.readline())
                    self.assertEqual(event['event'], 'browser')
                    self.assertFalse((self.home / 'local-browser-launched').exists())
                    self.assertIn('login.chinacloudapi.cn', event['value'])
                    self.assertEqual(json.loads(child.stdout.readline())['event'], 'ready')
                    self.assertEqual((self.home / '.azure/msal_token_cache.json').read_text(), 'new-session')
                    if commit:
                        child.stdin.write('{"commit":true}\n')
                        child.stdin.flush()
                        self.assertEqual(json.loads(child.stdout.readline())['event'], 'complete')
                        child.wait(timeout=5)
                        self.assertEqual(child.returncode, 0)
                    else:
                        child.stdin.close()
                        child.wait(timeout=5)
                        self.assertNotEqual(child.returncode, 0)
                    self.assertEqual((self.home / '.azure/msal_token_cache.json').read_text(),
                                     'browser-session' if commit else 'new-session')
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                    for stream in (child.stdin, child.stdout, child.stderr):
                        stream.close()
                self.assertEqual(list(self.home.glob('.azure-login-*')), [])


if __name__ == '__main__':
    unittest.main()
