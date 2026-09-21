"""Resolve Windows local paths without exposing controller files or network shares."""
import ctypes
import os
from pathlib import Path
import re

from workspace import protected_paths


def local_path(value):
    if not value or any(char in str(value) for char in ('\n', '\r', '\0')):
        raise ValueError('Supply a nonempty local directory path without control characters.')
    path = Path(value).absolute()
    for candidate in (path, path.resolve()):
        if os.name == 'nt':
            if not re.fullmatch(r'[A-Za-z]:', candidate.drive) or ':' in str(candidate)[2:]:
                raise ValueError('Use a local drive directory; UNC, device, and network paths are unsupported.')
            if ctypes.windll.kernel32.GetDriveTypeW(str(candidate.anchor)) not in (2, 3):
                raise ValueError('Use a local drive directory; network drives are unsupported.')
        elif str(value).startswith(('\\\\', '//')):
            raise ValueError('UNC and network paths are unsupported.')
    return path.resolve()


def checkout_identity(project):
    return os.path.normcase(str(local_path(project)))


def workspace_path(value, project, ssh_root):
    candidate = local_path(value)
    if candidate == Path(candidate.anchor):
        raise ValueError('A drive root cannot be a workspace.')
    if candidate.exists() and not candidate.is_dir():
        raise ValueError('The workspace must be a directory.')
    for protected in protected_paths(project, ssh_root):
        if candidate.is_relative_to(protected) or protected.is_relative_to(candidate):
            raise ValueError('Workspace must not expose host SSH/controller files or state.')
    return candidate
