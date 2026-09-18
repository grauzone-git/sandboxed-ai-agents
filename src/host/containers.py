"""Shared Podman creation options for new sandboxes and image updates."""
import subprocess
import sys

LABEL = 'io.sandboxed-agents.project'


def create_args(name, project, image, workspace, port, memory, cpus,
                pids=2048, shm='1g', operation='run'):
    args = [operation]
    if operation == 'run':
        args.append('--detach')
    return [*args, '--name', name, '--hostname', name,
            '--label', f'{LABEL}={project}',
            '--userns=keep-id:uid=1000,gid=1000', '--user', '0:0',
            '--security-opt=no-new-privileges', '--network=pasta:--no-map-gw',
            f'--memory={memory}', f'--cpus={cpus}', f'--pids-limit={pids}',
            f'--shm-size={shm}', '--publish', f'127.0.0.1:{port}:2222',
            '--volume', workspace, '--volume', f'{name}-home:/home/agent',
            '--volume', f'{name}-sshd:/var/lib/agent-sshd', image]


if __name__ == '__main__':
    if len(sys.argv) != 8:
        sys.exit('Usage: containers.py NAME PROJECT IMAGE WORKSPACE_MOUNT PORT MEMORY CPUS')
    sys.exit(subprocess.run(['podman', *create_args(*sys.argv[1:])]).returncode)
