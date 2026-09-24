"""Shared Podman creation options for new sandboxes and image updates."""
import subprocess
import sys

from capabilities import CAPABILITIES_LABEL, parse_capabilities, seccomp_path

LABEL = 'io.sandboxed-agents.project'


def owned_names(owner, runner):
    """Names of containers labelled as owned by OWNER; callers still verify ownership."""
    return runner('ps', '--all', '--filter', f'label={LABEL}={owner}', '--format', '{{.Names}}',
                  capture=True).stdout.split()


def create_args(name, project, image, workspace, port, memory, cpus,
                pids=2048, shm='1g', operation='run', capabilities='none', seccomp=None):
    capabilities = parse_capabilities(capabilities)
    args = [operation]
    if operation == 'run':
        args.append('--detach')
    security = ['--security-opt=no-new-privileges']
    runtime = []
    if capabilities == 'podman':
        security = ['--device=/dev/fuse', '--device=/dev/net/tun', '--security-opt=label=disable',
                    '--security-opt=apparmor=unconfined', '--security-opt=unmask=ALL',
                    f'--security-opt=seccomp={seccomp if seccomp is not None else seccomp_path(project)}']
        # Both containers/storage's runroot and libpod's temporary state must
        # disappear when the outer sandbox stops, unlike the persisted home.
        runtime = ['--tmpfs', '/run/user/1000:rw,nosuid,nodev,noexec,mode=0700']
    return [*args, '--name', name, '--hostname', name,
            '--label', f'{LABEL}={project}',
            '--label', f'{CAPABILITIES_LABEL}={capabilities}',
            '--userns=keep-id:uid=1000,gid=1000', '--user', '0:0',
            *security, '--network=pasta:--no-map-gw',
            f'--memory={memory}', f'--cpus={cpus}', f'--pids-limit={pids}',
            f'--shm-size={shm}', '--publish', f'127.0.0.1:{port}:2222',
            *runtime,
            '--volume', workspace, '--volume', f'{name}-home:/home/agent',
            '--volume', f'{name}-sshd:/var/lib/agent-sshd', image]


if __name__ == '__main__':
    if len(sys.argv) != 9:
        sys.exit('Usage: containers.py NAME PROJECT IMAGE WORKSPACE_MOUNT PORT MEMORY CPUS CAPABILITIES')
    sys.exit(subprocess.run(['podman', *create_args(*sys.argv[1:8], capabilities=sys.argv[8])]).returncode)
