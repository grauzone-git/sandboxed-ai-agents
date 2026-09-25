"""Validate release versions and checksum manifests shared by package builders."""
import re

ARTIFACTS = ('sandboxed-agents-linux-amd64', 'sandboxed-agents-linux-arm64',
             'sandboxed-agents-windows-amd64.exe')


def validate_version(version):
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('Version must be a semantic version without a leading v.')
    if '-' in version:
        for part in version.split('-', 1)[1].split('.'):
            if not part or (part.isdigit() and len(part) > 1 and part.startswith('0')):
                raise ValueError('Version prerelease identifiers must be nonempty and numeric identifiers cannot have leading zeroes.')


def read_checksums(manifest):
    checksums = {}
    for number, line in enumerate(manifest.read_text().splitlines(), 1):
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch('[a-f0-9]{64}', fields[0]):
            raise ValueError(f'Invalid SHA256SUMS line {number}; expected a SHA-256 digest and artifact filename.')
        digest, name = fields
        if name in checksums:
            raise ValueError(f'Duplicate SHA256SUMS entry for {name}; regenerate the release manifest.')
        checksums[name] = digest
    return checksums
