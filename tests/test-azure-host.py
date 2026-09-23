"""Exercise callback validation and host browser setup without Azure credentials."""
import sys
import contextlib
import http.client
import io
import json
import os
import socket
import signal
import shutil
import subprocess
import tempfile
import time
from unittest.mock import patch
from pathlib import Path
import unittest
from urllib.parse import urlencode

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src/host'))
from azure_auth import callback, interactive, launch_browser


class AzureHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox azure host ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.children = []
        self.config = self.root / 'demo.conf'
        for filename in ('demo.conf', 'known_hosts', 'id_ed25519'):
            (self.root / filename).write_text('fixture')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        self.ssh = self.root / 'ssh'
        self.ssh.write_text('''#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
root = Path(os.environ['AZURE_TEST_ROOT'])
kind = 'forward' if '-L' in sys.argv else 'login'
with (root / 'arguments').open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')
def end(*args):
    (root / (kind + '-closed')).touch()
    sys.exit(0)
signal.signal(signal.SIGTERM, end)
if kind == 'forward':
    if os.environ.get('AZURE_TEST_FAIL_FORWARD'): sys.exit(1)
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            (root / 'verified').touch()
            self.send_response(501)
            self.end_headers()
        def log_message(self, *args): pass
    server = HTTPServer(('127.0.0.1', int(os.environ['AZURE_TEST_PORT'])), Handler)
    if not os.environ.get('AZURE_TEST_NO_READY'): print('SANDBOX_AZURE_FORWARD', flush=True)
    server.serve_forever()
else:
    print(json.dumps({'event': 'browser', 'cloud': os.environ['AZURE_TEST_CLOUD'], 'value': os.environ['AZURE_TEST_URL']}), flush=True)
    while not (root / 'browser-opened').exists(): time.sleep(0.01)
    while os.environ.get('AZURE_TEST_HANG'): time.sleep(0.1)
    print('{"event":"ready"}', flush=True)
    if json.loads(sys.stdin.readline()).get('commit'):
        print('{"event":"complete"}', flush=True)
    end()
''')
        self.ssh.chmod(0o755)

    def run_login(self, failure=None, timeout=5, extra=None, cloud='AzureCloud'):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        url = self.url(authority='login.chinacloudapi.cn' if cloud == 'AzureChinaCloud' else 'login.microsoftonline.com',
                       redirect=f'http://localhost:{self.port}')
        def browser(local_url):
            self.assertTrue((self.root / 'verified').exists(), 'Browser launched before callback verification')
            self.assertNotIn('dummy-state', local_url)
            from urllib.parse import urlsplit
            parsed = urlsplit(local_url)
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port)
            try:
                connection.request('GET', parsed.path)
                response = connection.getresponse()
                self.assertEqual(response.status, 302)
                self.assertEqual(response.getheader('Location'), url)
            finally:
                connection.close()
            if failure:
                raise failure
            (self.root / 'browser-opened').touch()
        def popen(command, **kwargs):
            # A Python fake SSH runs with native argv boundaries on either OS.
            child = subprocess.Popen([sys.executable, str(self.ssh), *command[1:]], **kwargs)
            self.children.append(child)
            return child
        with patch.dict(os.environ, {'AZURE_TEST_ROOT': str(self.root), 'AZURE_TEST_PORT': str(self.port),
                                     'AZURE_TEST_URL': url, 'AZURE_TEST_CLOUD': cloud, **(extra or {})}), \
                patch('shutil.which', return_value=str(self.ssh)), contextlib.redirect_stdout(io.StringIO()):
            interactive('demo', self.config, ['--interactive', '--cloud', cloud, '--tenant', 'tenant', '--tenant-only'],
                        popen=popen, browser=browser, timeout=timeout)

    def url(self, authority='login.microsoftonline.com', redirect='http://localhost:45678', **values):
        return f'https://{authority}/tenant/oauth2/v2.0/authorize?' + urlencode({
            'redirect_uri': redirect, 'response_type': 'code', 'state': 'dummy-state', **values})

    def test_only_selected_cloud_authority_and_loopback_callback_are_accepted(self):
        self.assertEqual(callback(self.url(), 'AzureCloud'), 45678)
        for url in (self.url('evil.example'), self.url(redirect='https://localhost:45678'),
                    self.url(redirect='http://example.test:45678'), self.url(response_type='token'),
                    self.url(redirect='http://localhost:22'), self.url() + '&redirect_uri=http://localhost:9999'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                callback(url, 'AzureCloud')

    def test_forward_verified_before_browser_and_closed_after_success(self):
        self.run_login()
        self.assertEqual(len(self.children), 2)
        self.assertTrue(all(child.poll() is not None for child in self.children))
        arguments = (self.root / 'arguments').read_text()
        self.assertNotIn('dummy-state', arguments)
        self.assertNotIn('oauth2', arguments)
        self.assertIn(str(self.config), arguments)

    def test_china_login_uses_china_authority_and_closes_forward(self):
        self.run_login(cloud='AzureChinaCloud')
        self.assertTrue(all(child.poll() is not None for child in self.children))
        with self.assertRaises(ValueError):
            callback(self.url(), 'AzureChinaCloud')
        with self.assertRaises(ValueError):
            callback(self.url('login.chinacloudapi.cn'), 'AzureCloud')

    def test_timeout_closes_login_and_forward(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            self.run_login(timeout=0.7, extra={'AZURE_TEST_HANG': '1'})
        self.assertTrue(all(child.poll() is not None for child in self.children))

    def test_native_windows_browser_uses_shell_open_with_local_url(self):
        with patch('os.name', 'nt'), patch('os.startfile', create=True) as start:
            launch_browser('http://127.0.0.1:45678/local-route')
            start.assert_called_once_with('http://127.0.0.1:45678/local-route')

    def test_browser_failure_and_cancellation_close_both_processes(self):
        for failure in (ValueError('Browser failed'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)):
                    self.run_login(failure=failure)
                self.assertTrue(all(child.poll() is not None for child in self.children))

    def test_forward_failure_never_opens_browser(self):
        with self.assertRaisesRegex(ValueError, 'forwarding failed'):
            self.run_login(extra={'AZURE_TEST_FAIL_FORWARD': '1'})
        self.assertFalse((self.root / 'browser-opened').exists())
        self.assertTrue(all(child.poll() is not None for child in self.children))

    def test_http_listener_without_ssh_readiness_never_opens_browser(self):
        with self.assertRaisesRegex(ValueError, 'forwarding failed'):
            self.run_login(timeout=0.7, extra={'AZURE_TEST_NO_READY': '1'})
        self.assertFalse((self.root / 'browser-opened').exists())
        self.assertTrue(all(child.poll() is not None for child in self.children))

    @unittest.skipIf(os.name == 'nt', 'Exercise the Linux CLI signal exit status')
    def test_linux_cli_ctrl_c_closes_transport_and_returns_130(self):
        home = self.root / 'home'
        state = home / '.ssh/sanboxed-agents/demo'
        state.mkdir(parents=True)
        for filename in ('demo.conf', 'known_hosts', 'id_ed25519'):
            shutil.copy2(self.root / filename, state / filename)
        opener = self.root / 'xdg-open'
        opener.write_text('#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n'
                          "(Path(os.environ['AZURE_TEST_ROOT']) / 'browser-opened').touch()\n")
        opener.chmod(0o755)
        env = {**os.environ, 'HOME': str(home), 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
               'AZURE_TEST_ROOT': str(self.root), 'AZURE_TEST_PORT': str(self.port),
               'AZURE_TEST_URL': self.url(redirect=f'http://localhost:{self.port}'),
               'AZURE_TEST_CLOUD': 'AzureCloud', 'AZURE_TEST_HANG': '1'}
        child = subprocess.Popen([sys.executable, '-B', str(PROJECT / 'src/host/azure_auth.py'),
                                  'demo', '--interactive', '--cloud', 'AzureCloud', '--tenant', 'tenant',
                                  '--tenant-only'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.root / 'browser-opened').exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / 'browser-opened').exists(), 'Fake browser did not open')
            child.send_signal(signal.SIGINT)
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 130, stdout + stderr)
            self.assertTrue((self.root / 'login-closed').exists())
            self.assertTrue((self.root / 'forward-closed').exists())
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)

    def test_missing_ssh_opt_in_prints_exact_command(self):
        self.config.unlink()
        with self.assertRaisesRegex(ValueError, r'./sandbox(?:.ps1)? ssh-config demo --install'):
            self.run_login()
        self.assertFalse((self.root / 'arguments').exists())


if __name__ == '__main__':
    unittest.main()
