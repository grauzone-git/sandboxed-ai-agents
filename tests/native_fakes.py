"""Install Python fake commands as native executables on Windows."""
import os
from pathlib import Path
import shutil
import sys


def write_fake(directory, name, source):
    directory = Path(directory)
    if os.name != 'nt':
        command = directory / name
        command.write_text(source, encoding='utf-8')
        command.chmod(0o755)
        return command

    relay = os.environ.get('SANDBOX_TEST_FAKE_COMMAND')
    if not relay:
        raise ValueError('Set SANDBOX_TEST_FAKE_COMMAND to a Go binary built from '
                         './tests/fake-command before running Windows contract tests.')
    (directory / f'{name}.py').write_text(source, encoding='utf-8')
    (directory / f'{name}.python').write_text(sys.executable, encoding='utf-8')
    command = directory / f'{name}.exe'
    shutil.copy2(relay, command)
    return command
