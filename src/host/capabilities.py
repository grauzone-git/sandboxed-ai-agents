"""Validate sandbox capabilities and build their optional image layers."""
from pathlib import Path
import copy
import json
import os
import re
import subprocess
import sys
import tempfile

CAPABILITIES_LABEL = 'io.sandboxed-agents.capabilities'
NESTED_SYSCALLS = ('sethostname', 'setdomainname', 'setns')


def seccomp_path(project):
    return Path(project) / '.local/nested-podman-seccomp.json'


def nested_seccomp(profile):
    """Allow inner namespace setup while retaining every other host rule."""
    if not isinstance(profile, dict) or not isinstance(profile.get('syscalls'), list):
        raise ValueError('Invalid host seccomp profile.')
    result = copy.deepcopy(profile)
    rules = []
    for rule in result['syscalls']:
        rule['names'] = [name for name in rule['names'] if name not in NESTED_SYSCALLS]
        if rule['names']:
            rules.append(rule)
    # An ERRNO rule wins over ALLOW, so remove these names from conditional
    # deny rules too. Kernel namespace capability checks continue to apply.
    rules.append({'names': list(NESTED_SYSCALLS), 'action': 'SCMP_ACT_ALLOW'})
    result['syscalls'] = rules
    return result


def prepare_seccomp(project, runner):
    security = json.loads(runner('info', '--format', '{{json .Host.Security}}', capture=True).stdout)
    source = security.get('seccompProfilePath', '')
    if not source or not Path(source).is_absolute():
        raise ValueError('Podman must report an absolute host seccomp profile path for nested containers.')
    policy = nested_seccomp(json.loads(Path(source).read_text()))
    target = seccomp_path(project)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.nested-seccomp-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(policy, stream)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_capabilities(value):
    if value == 'none':
        return 'none'
    items = value.split(',')
    if not items or any(item != 'podman' for item in items) or len(set(items)) != len(items):
        raise ValueError('Capabilities must be podman or none (default); duplicates are not allowed.')
    return ','.join(sorted(items))


def podman(*args, capture=False):
    return subprocess.run(['podman', *map(str, args)], check=True, text=True,
                          stdout=subprocess.PIPE if capture else sys.stderr)


def prepare_image(project, image, capabilities, *, runner=podman):
    capabilities = parse_capabilities(capabilities)
    if capabilities == 'none':
        return image
    base = runner('image', 'inspect', '--format', '{{.Id}}', image, capture=True).stdout.strip()
    if not re.fullmatch(r'(sha256:)?[a-f0-9]{64}', base):
        raise ValueError('Podman returned an invalid base image ID.')
    prepare_seccomp(project, runner)
    # Use the immutable base ID, so concurrent builds cannot change our parent.
    # Podman's layer cache reuses the packages until the base or recipe changes.
    context = Path(project) / 'src/container'
    result = runner('build', '--quiet', '--pull=never', '--build-arg', f'BASE_IMAGE={base}',
                    '-f', context / 'Containerfile.podman', context, capture=True)
    identity = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ''
    if not re.fullmatch(r'(sha256:)?[a-f0-9]{64}', identity):
        raise ValueError('Podman returned an invalid capability image ID.')
    return identity


if __name__ == '__main__':
    try:
        if len(sys.argv) == 2:
            print(parse_capabilities(sys.argv[1]))
        elif len(sys.argv) == 4:
            print(prepare_image(*sys.argv[1:]))
        else:
            raise ValueError('Usage: capabilities.py LIST | PROJECT IMAGE LIST')
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'Error: {error}')
