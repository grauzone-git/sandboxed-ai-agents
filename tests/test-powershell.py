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
    def test_native_stderr_is_plain_text_without_powershell_error_context(self):
        with tempfile.TemporaryDirectory(prefix='sandbox stderr ') as directory:
            root = Path(directory)
            shutil.copy2(PROJECT / 'sandbox.ps1', root / 'sandbox.ps1')
            host = root / 'src/host'
            host.mkdir(parents=True)
            (host / 'windows_cli.py').write_text(
                'import sys\nprint("Finding your release...\\n\\nInstalled", file=sys.stderr)\n'
                'sys.exit(int(sys.argv[-1]))\n')
            for working in (PROJECT, root):
                for code in (0, 37):
                    with self.subTest(code=code, cwd=working):
                        result = subprocess.run(
                            ['pwsh', '-NoProfile', '-File', str(root / 'sandbox.ps1'), 'test', 'agents', str(code)],
                            env={**os.environ, 'SANDBOX_PYTHON': sys.executable, 'NO_COLOR': '1'},
                            cwd=working, capture_output=True, text=True, timeout=30)
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertEqual(result.stdout, '')
                        # PowerShell can omit empty ErrorRecords while rendering.
                        # Exact stream preservation is checked through 2>&1 below.
                        self.assertIn(result.stderr, ('Finding your release...\n\nInstalled\n',
                                                     'Finding your release...\nInstalled\n'))

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

    def test_manager_output_can_be_assigned_and_errors_redirected(self):
        with tempfile.TemporaryDirectory(prefix='sandbox pipeline ') as directory:
            root = Path(directory)
            shutil.copy2(PROJECT / 'sandbox.ps1', root / 'sandbox.ps1')
            host = root / 'src/host'
            host.mkdir(parents=True)
            (host / 'windows_cli.py').write_text(
                'import sys\nprint("copilot no 1.0.86")\n'
                'print("first diagnostic\\n\\nlast diagnostic", file=sys.stderr)\nsys.exit(37)\n')
            script = root / 'invoke.ps1'
            script.write_text(
                "$lines = @(& (Join-Path $PSScriptRoot 'sandbox.ps1') test agents list 2>&1)\n"
                "$code = $LASTEXITCODE\n"
                "@{ lines = @($lines | ForEach-Object { $_.ToString() }); code = $code } | ConvertTo-Json -Compress\n")
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script)],
                                    env={**os.environ, 'SANDBOX_PYTHON': sys.executable},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertCountEqual(payload['lines'], ['copilot no 1.0.86', 'first diagnostic', '', 'last diagnostic'])
            self.assertEqual([line for line in payload['lines'] if line != 'copilot no 1.0.86'],
                             ['first diagnostic', '', 'last diagnostic'])
            self.assertEqual(payload['code'], 37)
            self.assertEqual(result.stderr, '')

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
            self.assertTrue(Path(result.stdout.strip()).samefile(working))

    def test_output_capture_drains_both_streams_and_preserves_blank_lines(self):
        with tempfile.TemporaryDirectory(prefix='sandbox streams ') as directory:
            root = Path(directory)
            shutil.copy2(PROJECT / 'sandbox.ps1', root / 'sandbox.ps1')
            host = root / 'src/host'
            host.mkdir(parents=True)
            (host / 'windows_cli.py').write_text(
                'import sys\n'
                'print("first\\n\\nlast")\n'
                'for i in range(512): print("x" * 1024, file=sys.stderr)\n'
                'print("", file=sys.stderr)\n'
                'print("done")\n')
            script = root / 'invoke.ps1'
            script.write_text(
                "$lines = @(& (Join-Path $PSScriptRoot 'sandbox.ps1') agents test list 2> $null)\n"
                "$code = $LASTEXITCODE\n"
                "@{ lines = $lines; code = $code } | ConvertTo-Json -Compress\n")
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script)],
                                    env={**os.environ, 'SANDBOX_PYTHON': sys.executable},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['lines'], ['first', '', 'last', 'done'])
            self.assertEqual(payload['code'], 0)
            self.assertEqual(result.stderr, '')

    def test_interactive_commands_keep_console_output_and_stdin(self):
        with tempfile.TemporaryDirectory(prefix='sandbox console ') as directory:
            root = Path(directory)
            shutil.copy2(PROJECT / 'sandbox.ps1', root / 'sandbox.ps1')
            host = root / 'src/host'
            host.mkdir(parents=True)
            (host / 'windows_cli.py').write_text('print(input("prompt: "))\n')
            script = root / 'invoke.ps1'
            script.write_text(
                "$lines = @(& (Join-Path $PSScriptRoot 'sandbox.ps1') @args)\n"
                "Write-Output ('captured: ' + $lines.Count)\n")
            for arguments in [('shell', 'test'), ('run', 'test', 'copilot'),
                              ('tools', 'test', 'setup', 't3'), ('agents', 'test', 'login', 'copilot')]:
                with self.subTest(arguments=arguments):
                    result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script), *arguments],
                                            env={**os.environ, 'SANDBOX_PYTHON': sys.executable},
                                            input='console input\n', capture_output=True,
                                            text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, 'prompt: console input\ncaptured: 0\n')


if __name__ == '__main__':
    unittest.main()
