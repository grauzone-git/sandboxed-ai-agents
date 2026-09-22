"""Exercise the live Azure probe through its CLI with an offline Azure executable."""
import json
import importlib.util
import contextlib
import io
from unittest.mock import patch
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROBE = Path(__file__).with_name('azure-auth-probe.py')


@unittest.skipIf(os.name == 'nt', 'Fake executables require a POSIX host; runner orchestration is tested on both hosts')
class AzureLiveProbeTests(unittest.TestCase):
    def test_china_probe_joins_endpoint_and_never_returns_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            az = root / 'az'
            az.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[:2] == ['cloud', 'show']:
    print(json.dumps({'name': 'AzureChinaCloud', 'endpoints': {'resourceManager': 'https://management.chinacloudapi.cn'}}))
elif args[:2] == ['account', 'show']: print('user')
elif args[:2] == ['account', 'get-access-token']:
    print(json.dumps({'expires_on': 4102444800, 'accessToken': 'NEVER-REPORT-THIS'}))
elif args[0] == 'rest':
    assert args[args.index('--url') + 1] == 'https://management.chinacloudapi.cn/tenants?api-version=2020-01-01'
else: sys.exit(2)
''')
            az.chmod(0o755)
            result = subprocess.run([sys.executable, '-B', str(PROBE)],
                                    input=json.dumps({'action': 'azure', 'cloud': 'AzureChinaCloud'}),
                                    env={**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH']},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['cloud'], 'AzureChinaCloud')
            self.assertEqual(data['expires_on'], 4102444800)
            self.assertEqual(data['arm_exit_code'], 0)
            self.assertNotIn('NEVER-REPORT-THIS', result.stdout + result.stderr)

    def test_logout_requires_account_to_be_unavailable(self):
        for account_code in (0, 1):
            with self.subTest(account_code=account_code), tempfile.TemporaryDirectory() as directory:
                az = Path(directory) / 'az'
                az.write_text('#!/usr/bin/env python3\nimport sys\n'
                              + 'sys.exit(' + str(account_code) + " if sys.argv[1:3] == ['account', 'show'] else 0)\n")
                az.chmod(0o755)
                result = subprocess.run([sys.executable, '-B', str(PROBE)], input='{"action":"logout"}',
                                        env={**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH']},
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0 if account_code == 1 else 1)
                if account_code == 1:
                    self.assertTrue(json.loads(result.stdout)['logged_out'])

    def test_saved_pat_setup_uses_stdin_and_reports_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / 'sandbox-tools'
            tool.write_text("#!/usr/bin/env python3\nimport sys\n"
                            "assert sys.argv[1:] == ['setup', 'azdo', '--persist']\n"
                            "assert sys.stdin.read() == 'https://dev.azure.com/test\\nSECRET-PAT\\n'\n"
                            "print('SECRET-PAT')\n")
            tool.chmod(0o755)
            result = subprocess.run([sys.executable, '-B', str(PROBE)],
                                    input=json.dumps({'action': 'azdo_setup', 'mode': 'saved',
                                                      'organization': 'https://dev.azure.com/test', 'pat': 'SECRET-PAT'}),
                                    env={**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH']},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['configured'])
            self.assertNotIn('SECRET-PAT', result.stdout + result.stderr)


class AzureLiveRunnerTests(unittest.TestCase):
    def test_run_records_restart_recreation_and_leaves_skipped_renewal_explicit(self):
        spec = importlib.util.spec_from_file_location('live_azure', PROBE.with_name('live-azure-auth.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                if args[0] == 'ssh' and 'input' in kwargs:
                    action = json.loads(kwargs['input'])['action']
                    data = ({'expires_on': 4102444800, 'cloud': 'AzureChinaCloud', 'arm_exit_code': 0}
                            if action == 'azure' else {'logged_out': True})
                    return subprocess.CompletedProcess(args, 0, json.dumps(data), '')
                return subprocess.CompletedProcess(args, 0, '{}', '')
            with patch.object(runner.subprocess, 'run', side_effect=run), \
                    patch.object(runner.shutil, 'which', side_effect=lambda name: name), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = runner.main(['--azure-environment', 'AzureChinaCloud', '--tenant-id', 'private-tenant',
                                    '--image', 'test-image', '--skip-renewal', '--skip-cancellation',
                                    '--report', str(report)])
            self.assertEqual(code, 0)
            evidence = json.loads(report.read_text())
            checks = {item['check']: item['status'] for item in evidence['checks']}
            self.assertEqual(checks['versions'], 'passed')
            self.assertEqual(checks['restart'], 'passed')
            self.assertEqual(checks['recreation'], 'passed')
            self.assertEqual(checks['remembered_cloud'], 'passed')
            self.assertEqual(checks['renewal'], 'skipped')
            self.assertNotIn('private-tenant', report.read_text())
            self.assertTrue(any('sandbox' in str(args) and 'stop' in args for args in calls))
            self.assertTrue(any('update' in args and '--no-build' in args for args in calls))

    def test_pat_checks_survive_replacement_and_logout_without_secret_arguments(self):
        spec = importlib.util.spec_from_file_location('live_azure', PROBE.with_name('live-azure-auth.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            actions = []
            def run(args, **kwargs):
                self.assertNotIn('SECRET-PAT', ' '.join(args))
                self.assertNotIn('TEST_AZDO_PAT', kwargs.get('env', {}))
                if args[0] == 'ssh' and 'input' in kwargs:
                    payload = json.loads(kwargs['input'])
                    actions.append(payload)
                    if payload['action'] == 'azdo_setup' and payload['mode'] == 'native':
                        return subprocess.CompletedProcess(args, 1, '', 'SECRET-PAT')
                    return subprocess.CompletedProcess(args, 0, json.dumps({'utc': '2026-09-22T17:16:30Z',
                                                                           'expires_on': 4102444800}), '')
                return subprocess.CompletedProcess(args, 0, '{}', '')
            with patch.object(runner.subprocess, 'run', side_effect=run), \
                    patch.dict(os.environ, {'TEST_AZDO_PAT': 'SECRET-PAT'}), \
                    patch.object(runner.shutil, 'which', side_effect=lambda name: name), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = runner.main(['--tenant-id', 'private-tenant', '--skip-renewal', '--skip-cancellation',
                                    '--azdo-pat-env', 'TEST_AZDO_PAT', '--azdo-organization', 'https://dev.azure.com/private',
                                    '--report', str(report)])
            self.assertEqual(code, 1)  # The native credential-store failure must remain visible.
            checks = {item['check']: item['status'] for item in json.loads(report.read_text())['checks']}
            self.assertEqual(checks['azdo_native_setup'], 'failed')
            self.assertEqual(checks['azdo_saved_after_replacement'], 'passed')
            self.assertEqual(checks['azdo_saved_after_logout'], 'passed')
            self.assertTrue(any(p.get('pat') == 'SECRET-PAT' for p in actions))
            self.assertNotIn('SECRET-PAT', report.read_text())
            self.assertNotIn('https://dev.azure.com/private', report.read_text())

    def test_cancellation_requires_exit_130_and_preserved_session(self):
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('live_azure', PROBE.with_name('live-azure-auth.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        for cancel_code in (130, 0):
            with self.subTest(cancel_code=cancel_code), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / 'result.json'
                def run(args, **kwargs):
                    data = {'expires_on': 4102444800} if args[0] == 'ssh' else {}
                    return subprocess.CompletedProcess(args, 0, json.dumps(data), '')
                child = SimpleNamespace(wait=lambda **kwargs: cancel_code, poll=lambda: cancel_code)
                with patch.object(runner.subprocess, 'run', side_effect=run), \
                        patch.object(runner.subprocess, 'Popen', return_value=child), \
                        patch.object(runner.shutil, 'which', side_effect=lambda name: name), \
                        patch('builtins.input', return_value='yes'), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = runner.main(['--tenant-id', 'tenant', '--skip-renewal', '--report', str(report)])
                self.assertEqual(code, 0 if cancel_code == 130 else 1)
                checks = {item['check']: item['status'] for item in json.loads(report.read_text())['checks']}
                self.assertEqual(checks['cancellation'], 'passed' if cancel_code == 130 else 'failed')

    def test_renewal_needs_later_expiry_and_confirmation(self):
        spec = importlib.util.spec_from_file_location('live_azure', PROBE.with_name('live-azure-auth.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        for expiry, answer, expected in [(2000, 'yes', 0), (1000, 'yes', 1), (2000, 'no', 1)]:
            with self.subTest(expiry=expiry, answer=answer), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / 'result.json'
                probes = []
                def run(args, **kwargs):
                    data = {}
                    if args[0] == 'ssh' and 'input' in kwargs and json.loads(kwargs['input'])['action'] == 'azure':
                        probes.append(1)
                        data = {'expires_on': expiry if len(probes) == 4 else 1000}
                    return subprocess.CompletedProcess(args, 0, json.dumps(data), '')
                with patch.object(runner.subprocess, 'run', side_effect=run), \
                        patch.object(runner.shutil, 'which', side_effect=lambda name: name), \
                        patch.object(runner.time, 'time', return_value=1100), \
                        patch('builtins.input', return_value=answer), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = runner.main(['--tenant-id', 'tenant', '--skip-cancellation', '--report', str(report)])
                self.assertEqual(code, expected)
                checks = {item['check']: item['status'] for item in json.loads(report.read_text())['checks']}
                self.assertEqual(checks['renewal'], 'passed' if expected == 0 else 'failed')

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group cleanup')
    def test_stalled_cancellation_reaps_its_process_group(self):
        from unittest.mock import Mock
        spec = importlib.util.spec_from_file_location('live_azure', PROBE.with_name('live-azure-auth.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            child = Mock(pid=98765)
            child.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired('setup', 30), 130]
            child.poll.return_value = None
            def run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, json.dumps({'expires_on': 4102444800}), '')
            with patch.object(runner.subprocess, 'run', side_effect=run), \
                    patch.object(runner.subprocess, 'Popen', return_value=child), \
                    patch.object(runner.os, 'killpg') as kill, \
                    patch('builtins.input', return_value='yes'), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = runner.main(['--tenant-id', 'tenant', '--skip-renewal', '--report', str(report)])
            self.assertEqual(code, 1)
            self.assertTrue(kill.called)
            self.assertEqual(child.wait.call_count, 3)


if __name__ == '__main__':
    unittest.main()
