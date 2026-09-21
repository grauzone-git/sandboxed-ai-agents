"""Prepare nested Podman using the selected machine's devices and seccomp policy."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from capabilities import nested_seccomp


def prepare_nested(runtime, project, image):
    runtime.guest('sh', '-c', 'test -c /dev/fuse && test -r /dev/fuse && test -w /dev/fuse && '
                  'test -c /dev/net/tun && test -r /dev/net/tun && test -w /dev/net/tun')
    for kind in ('uid', 'gid'):
        mapping = runtime.guest('podman', 'unshare', 'cat', f'/proc/self/{kind}_map').stdout
        rows = [line.split() for line in mapping.splitlines()]
        if not rows or any(len(row) != 3 or any(not field.isdecimal() for field in row) for row in rows):
            raise ValueError('The machine returned an invalid namespace ID mapping.')
        if sum(int(row[2]) for row in rows) < 65537:
            raise ValueError('Nested Podman requires at least 65536 subordinate UIDs and GIDs in the machine.')
    source = runtime.info.get('host', {}).get('security', {}).get('seccompProfilePath', '')
    if not PurePosixPath(source).is_absolute() or any(char in source for char in ('\n', '\r', '\0')):
        raise ValueError('The engine must report an absolute seccomp profile path for nested Podman.')
    profile = json.loads(runtime.guest('cat', '--', source).stdout)
    policy = json.dumps(nested_seccomp(profile)) + '\n'
    base = runtime.run('image', 'inspect', '--format', '{{.Id}}', image).stdout.strip()
    if not re.fullmatch(r'(sha256:)?[a-f0-9]{64}', base):
        raise ValueError('Podman returned an invalid base image ID.')
    context = Path(__file__).resolve().parents[1] / 'container'
    result = runtime.run('build', '--quiet', '--pull=never', '--build-arg', f'BASE_IMAGE={base}',
                         '-f', context / 'Containerfile.podman', context)
    identity = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ''
    if not re.fullmatch(r'(sha256:)?[a-f0-9]{64}', identity):
        raise ValueError('Podman returned an invalid capability image ID.')
    checkout = hashlib.sha256(project.encode('utf-8')).hexdigest()
    digest = hashlib.sha256(policy.encode('utf-8')).hexdigest()
    # Immutable content-addressed profiles must outlive the client process and
    # machine restarts. They are private guest data, not machine configuration.
    script = '''set -eu
umask 077
for directory in "$HOME" "$HOME/.local" "$HOME/.local/share" "$HOME/.local/share/sandboxed-agents"; do
    test ! -L "$directory" || { echo 'Refusing symlinked profile directory' >&2; exit 1; }
done
directory="$HOME/.local/share/sandboxed-agents/$1"
test ! -L "$directory"
mkdir -p "$directory"
test -O "$directory"
chmod 700 "$directory"
temporary=$(mktemp "$directory/.seccomp.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
cat > "$temporary"
mv -f "$temporary" "$directory/$2.json"
printf '%s\\n' "$directory/$2.json"
'''
    target = runtime.guest('sh', '-c', script, 'sandbox-seccomp', checkout, digest, input=policy).stdout.strip()
    if not PurePosixPath(target).is_absolute() or any(char in target for char in ('\n', '\r', '\0')):
        raise ValueError('The machine returned an invalid staged seccomp path.')
    return identity, target
