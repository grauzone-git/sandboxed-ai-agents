"""User-run Podman check of PowerShell argument transport in an isolated container."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src/host'))
from windows_paths import checkout_identity
from windows_runtime import Runtime

ARGUMENTS = ['', 'two words', 'double"quote', "single'quote", 'dollar$semicolon;literal',
             'C:\\directory with spaces\\', 'backslash\\"quote', 'Gr\u00fc\u00dfe', '--flag=value with spaces',
             '$(echo must-stay-literal)', 'a&b|c<d>e']

# Only the disposable container's /tmp home is populated. The real in-image
# managers run these probes instead of installing or contacting a provider.
BOOTSTRAP = r'''
const fs = require('node:fs');
const path = require('node:path');
const home = process.argv[1];
const expected = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const [kind, name] of [['agents', 'copilot'], ['tools', 't3']]) {
  const state = path.join(home, '.local/state', 'sandbox-' + kind);
  fs.mkdirSync(state, { recursive: true });
  fs.writeFileSync(path.join(state, 'config.json'), JSON.stringify({ enabled: { [name]: 'latest' } }), 'utf8');
  const executable = path.join(home, '.local/share', 'sandbox-' + kind, name, 'bin', name);
  fs.mkdirSync(path.dirname(executable), { recursive: true });
  const source = '#!/usr/local/bin/node\n' +
    'require("node:assert/strict").deepEqual(process.argv.slice(2), ' + JSON.stringify(expected) + ');\n' +
    'console.log("PASS: ' + kind + ' argument boundaries");\n';
  fs.writeFileSync(executable, source, { encoding: 'utf8', mode: 0o755 });
}
'''

INVOKE = r'''
$ErrorActionPreference = 'Stop'
$probeArgs = @(Get-Content -Raw -LiteralPath $env:SANDBOX_ARGUMENT_FIXTURE | ConvertFrom-Json)
foreach ($action in @('run', 'tool')) {
    $id = if ($action -eq 'run') { 'copilot' } else { 't3' }
    & (Join-Path $env:SANDBOX_TEST_PROJECT 'sandbox.ps1') $action $env:SANDBOX_TEST_NAME $id @probeArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=os.environ.get('SANDBOX_IMAGE', 'localhost/agent-sandbox:dev'))
    options = parser.parse_args(argv)
    if os.name != 'nt':
        raise ValueError('Run this live check from the Windows checkout.')
    powershell = shutil.which('pwsh')
    if not powershell:
        raise ValueError('PowerShell 7 must be on PATH.')
    runtime = Runtime()
    runtime.preflight()
    name = 'windows-arguments-' + uuid.uuid4().hex[:12]
    print(f'Test container: {name}', flush=True)
    try:
        runtime.run('run', '--detach', '--pull=never', '--name', name,
                    '--label', f'io.sandboxed-agents.project={checkout_identity(PROJECT)}',
                    '--network', 'none', '--user', '1000:1000', '--env', 'HOME=/tmp/sandbox-argument-probe',
                    '--entrypoint', '/bin/sleep', options.image, 'infinity')
        # Use the manager's required Node runtime; Python is optional in the image.
        runtime.run('exec', '-i', '--user', '1000:1000', name, '/usr/local/bin/node', '-e', BOOTSTRAP,
                    '/tmp/sandbox-argument-probe',
                    input=json.dumps(ARGUMENTS))
        with tempfile.TemporaryDirectory(prefix='sandbox argument check ') as directory:
            root = Path(directory)
            (root / 'arguments.json').write_text(json.dumps(ARGUMENTS), encoding='utf-8')
            script = root / 'invoke.ps1'
            script.write_text(INVOKE, encoding='utf-8')
            subprocess.run([powershell, '-NoProfile', '-File', str(script)], check=True, timeout=60,
                           env={**os.environ, 'SANDBOX_PYTHON': sys.executable,
                                'CONTAINER_CONNECTION': runtime.connection,
                                'SANDBOX_ARGUMENT_FIXTURE': str(root / 'arguments.json'),
                                'SANDBOX_TEST_PROJECT': str(PROJECT), 'SANDBOX_TEST_NAME': name})
    except BaseException:
        print(f'Check failed; inspect and retain {name} if it was created.', file=sys.stderr)
        raise
    runtime.run('rm', '--force', name)
    print('PASS: live Windows run/tool argument boundaries; test container removed')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
