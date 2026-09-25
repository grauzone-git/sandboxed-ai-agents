"""Stage npm and NuGet packages from checksum-verified release artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from metadata import ARTIFACTS, read_checksums, validate_version

PACKAGING = Path(__file__).resolve().parent


def prepare(version, artifacts, output):
    validate_version(version)
    checksums = read_checksums(artifacts / 'SHA256SUMS')
    for name in ARTIFACTS:
        if name not in checksums:
            raise ValueError(f'SHA256SUMS has no entry for {name}; regenerate it with packaging/build.py.')
        if hashlib.sha256((artifacts / name).read_bytes()).hexdigest() != checksums[name]:
            raise ValueError(f'Checksum mismatch for {name}')
    npm = output / 'npm'
    shutil.copytree(PACKAGING / 'npm', npm)
    shutil.copy2(PACKAGING.parent / 'LICENSE', npm / 'LICENSE')
    (npm / 'artifacts').mkdir()
    for name in (*ARTIFACTS, 'SHA256SUMS'):
        shutil.copy2(artifacts / name, npm / 'artifacts' / name)
    metadata = json.loads((npm / 'package.json').read_text())
    metadata['version'] = version
    (npm / 'package.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (npm / 'README.md').write_text('Install globally with npm. Requires Podman and OpenSSH.\n'
                                  'Package installation does not create sandboxes or configure SSH.\n')
    nuget = output / 'nuget'
    nuget.mkdir()
    shutil.copytree(PACKAGING / 'nuget', nuget / 'tools')
    shutil.copy2(PACKAGING.parent / 'LICENSE', nuget / 'tools' / 'LICENSE')
    for name in (*(name for name in ARTIFACTS if '-windows-' in name), 'SHA256SUMS'):
        shutil.copy2(artifacts / name, nuget / 'tools' / name)
    (nuget / 'sandboxed-agents.nuspec').write_text(f'''<?xml version="1.0"?>
<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd">
  <metadata>
    <id>SandboxedAgents</id><version>{version}</version>
    <authors>grauzone-git</authors>
    <description>Standalone sandboxed-agents for Windows. Run tools/install-command.ps1 after NuGet extraction.</description>
    <projectUrl>https://github.com/grauzone-git/sandboxed-ai-agents</projectUrl>
    <license type="file">tools/LICENSE</license>
    <readme>tools/README.md</readme>
    <requireLicenseAcceptance>false</requireLicenseAcceptance>
  </metadata>
  <files><file src="tools\\**" target="tools" /></files>
</package>
''')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.version, args.artifacts, args.output)
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(f'Error: {error}')


if __name__ == '__main__':
    main()
