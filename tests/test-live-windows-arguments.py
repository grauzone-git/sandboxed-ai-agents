"""Keep the manual argument probe isolated and retain its container on failure."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('live_arguments', Path(__file__).with_name('live-windows-arguments.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class LiveArgumentProbeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Offline probe execution needs host Node; the live check uses container Node')
    def test_generated_probes_accept_only_exact_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([shutil.which('node'), '-e', probe.BOOTSTRAP, directory],
                           input=json.dumps(probe.ARGUMENTS), text=True, check=True)
            for kind, name in [('agents', 'copilot'), ('tools', 't3')]:
                executable = Path(directory) / '.local/share' / ('sandbox-' + kind) / name / 'bin' / name
                executable.read_bytes().decode('utf-8')
                for arguments, code in [(probe.ARGUMENTS, 0), (probe.ARGUMENTS[1:], 1)]:
                    result = subprocess.run([shutil.which('node'), str(executable), *arguments],
                                            capture_output=True, text=True, encoding='utf-8')
                    self.assertEqual(result.returncode, code, result.stderr)

    def test_isolation_and_success_only_cleanup(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                calls = []
                runtime = SimpleNamespace(connection='test-machine', preflight=lambda: None,
                                          run=lambda *args, **kwargs: calls.append((args, kwargs)))
                failure = subprocess.CalledProcessError(7, 'pwsh') if fail else None
                with patch.object(probe, 'os', SimpleNamespace(name='nt', environ=dict(os.environ))), \
                        patch.object(probe, 'Runtime', return_value=runtime), \
                        patch.object(probe.shutil, 'which', return_value='pwsh'), \
                        patch.object(probe.subprocess, 'run', side_effect=failure) as invoke, \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if fail:
                        with self.assertRaises(subprocess.CalledProcessError):
                            probe.main(['--image', 'test-image'])
                    else:
                        probe.main(['--image', 'test-image'])
                creation = calls[0][0]
                self.assertEqual(creation[creation.index('--network') + 1], 'none')
                self.assertEqual(creation[creation.index('--user') + 1], '1000:1000')
                self.assertIn('--pull=never', creation)
                bootstrap = calls[1][0]
                self.assertIn('/usr/local/bin/node', bootstrap)
                self.assertNotIn('/usr/bin/python3', bootstrap)
                self.assertFalse(any(arg in ('--volume', '-v', '--mount', '--publish', '-p') for arg in creation))
                name = creation[creation.index('--name') + 1]
                self.assertTrue(name.startswith('windows-arguments-'))
                self.assertEqual(invoke.call_args.kwargs['env']['SANDBOX_TEST_NAME'], name)
                self.assertEqual(invoke.call_args.kwargs['env']['CONTAINER_CONNECTION'], 'test-machine')
                removals = [args for args, _ in calls if args[0] == 'rm']
                self.assertEqual(removals, [] if fail else [('rm', '--force', name)])


if __name__ == '__main__':
    unittest.main()
