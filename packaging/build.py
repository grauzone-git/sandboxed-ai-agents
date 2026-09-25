"""Build every release target twice and require matching SHA-256 digests."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from metadata import ARTIFACTS, validate_version

REPOSITORY = Path(__file__).resolve().parents[1]
GO_VERSION = 'go1.27.1'


def build_release():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    validate_version(args.version)
    if not re.fullmatch('[a-f0-9]{40,64}', args.commit):
        parser.error('commit must be a full Git object hash')
    build_env = {**os.environ, 'GOENV': 'off', 'GOTOOLCHAIN': 'local',
                 'GOEXPERIMENT': '', 'GOFLAGS': ''}
    version = subprocess.run(['go', 'env', 'GOVERSION'], env=build_env, cwd=REPOSITORY,
                             check=True, capture_output=True, text=True).stdout.strip()
    if version != GO_VERSION:
        raise ValueError(f'Release builds require {GO_VERSION}; found {version or "unknown Go version"}.')
    args.output.mkdir(parents=True, exist_ok=True)
    checksums = []
    for name in ARTIFACTS:
        platform, architecture = name.removeprefix('sandboxed-agents-').removesuffix('.exe').split('-')
        binary = (args.output / name).resolve()
        env = {**build_env, 'CGO_ENABLED': '0', 'GOOS': platform, 'GOARCH': architecture,
               'GOAMD64': 'v1', 'GOARM64': 'v8.0'}
        build_command = ['go', 'build', '-trimpath', '-buildvcs=false', '-ldflags',
                 f'-buildid= -X main.version={args.version} -X main.commit={args.commit}']
        subprocess.run([*build_command, '-o', str(binary), './cmd/sandboxed-agents'], cwd=REPOSITORY, env=env, check=True)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='sandbox-rebuild-') as temporary:
            rebuilt = Path(temporary) / name
            subprocess.run([*build_command, '-o', str(rebuilt), './cmd/sandboxed-agents'], cwd=REPOSITORY, env=env, check=True)
            if hashlib.sha256(rebuilt.read_bytes()).hexdigest() != digest:
                raise ValueError(f'Repeated build differs for {name}')
        checksums.append(f'{digest}  {name}\n')
    (args.output / 'SHA256SUMS').write_text(''.join(checksums), encoding='ascii')


def main():
    try:
        build_release()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'Error: {error}')


if __name__ == '__main__':
    main()
