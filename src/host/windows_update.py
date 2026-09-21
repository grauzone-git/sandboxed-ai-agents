"""Adapt the shared update transaction to pinned WSL engines and Windows paths."""
import re

import update
from windows_capabilities import prepare_nested
from windows_paths import workspace_path


def validate_workspace(source, project, ssh_root):
    value = str(source).replace('\\', '/')
    if value.startswith('/'):
        match = re.fullmatch(r'/mnt/([a-zA-Z])/(.*)', value)
        if not match:
            raise ValueError('Cannot map the guest workspace to a local Windows drive; update requires a /mnt/DRIVE/ path.')
        value = f'{match[1]}:/{match[2]}'
    return workspace_path(value, project, ssh_root)


def run(runtime, project, owner, options):
    runtime.require_resources()

    def runner(*args, capture=False, check=True):
        return runtime.run(*args, capture=capture, allowed=(0,) if check else tuple(range(256)))

    def prepare(capability, image):
        return prepare_nested(runtime, owner, image) if capability == 'podman' else (image, None)

    update.main(project=project, owner_label=owner, options=options, runner=runner,
                workspace_validator=validate_workspace, prepare=prepare)
