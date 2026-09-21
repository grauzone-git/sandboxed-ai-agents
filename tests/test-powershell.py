"""Exercise the PowerShell entry point's native argument and exit-code boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell 7 is required for entry-point tests')
class PowerShellTests(unittest.TestCase):
    def test_arguments_and_native_failure_survive_the_entry_point(self):
        with tempfile.TemporaryDirectory(prefix='sandbox powershell ') as directory:
            root = Path(directory)
            shutil.copy2(PROJECT / 'sandbox.ps1', root / 'sandbox.ps1')
            host = root / 'src/host'
            host.mkdir(parents=True)
            (host / 'windows_cli.py').write_text(
                'import json, sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(23)\n')
            arguments = ['build', '--build-arg', 'VALUE=a b"c', '', 'dollar$semicolon;literal',
                         'C:\\directory with spaces\\']
            result = subprocess.run(
                ['pwsh', '-NoProfile', '-File', str(root / 'sandbox.ps1'), *arguments],
                env={**os.environ, 'SANDBOX_PYTHON': sys.executable},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(json.loads(result.stdout), arguments)

    def test_relative_paths_follow_powershell_set_location(self):
        with tempfile.TemporaryDirectory(prefix='sandbox location ') as directory:
            root = Path(directory)
            shutil.copy2(PROJECT / 'sandbox.ps1', root / 'sandbox.ps1')
            host = root / 'src/host'
            host.mkdir(parents=True)
            (host / 'windows_cli.py').write_text('import os\nprint(os.getcwd())\n')
            working = root / 'selected working directory'
            working.mkdir()
            script = root / 'invoke.ps1'
            script.write_text("Set-Location -LiteralPath $env:TEST_WORKING_DIRECTORY\n"
                              "& (Join-Path $PSScriptRoot 'sandbox.ps1') build\n")
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script)],
                                    env={**os.environ, 'SANDBOX_PYTHON': sys.executable,
                                         'TEST_WORKING_DIRECTORY': str(working)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), working)


if __name__ == '__main__':
    unittest.main()
