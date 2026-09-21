"""Keep Linux image inputs executable after a Windows-style Git checkout."""
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


class ContainerLineEndingTests(unittest.TestCase):
    def test_windows_checkout_keeps_linux_entrypoint_interpreters_valid(self):
        with tempfile.TemporaryDirectory(prefix='sandbox-eol-') as directory:
            root = Path(directory)
            repository = root / 'repository'
            repository.mkdir()
            subprocess.run(['git', 'init', '-q', str(repository)], check=True)
            shutil.copytree(PROJECT / 'src/container', repository / 'src/container')
            for filename in ('sandbox.ps1', 'README.md', 'src/host/windows_cli.py'):
                target = repository / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT / filename, target)
            attributes = PROJECT / '.gitattributes'
            if attributes.exists():
                shutil.copy2(attributes, repository / '.gitattributes')
            # Older checkouts may supply CRLF fixtures; their normalization is
            # intentional here, not a warning about the user's working tree.
            subprocess.run(['git', '-C', str(repository), '-c', 'core.autocrlf=false',
                            '-c', 'core.safecrlf=false', 'add', '.'], check=True)
            checkout = root / 'windows-checkout'
            subprocess.run(['git', '-C', str(repository), '-c', 'core.autocrlf=true',
                            'checkout-index', '--all', '--force', f'--prefix={checkout.as_posix()}/'], check=True)
            for filename in ('entrypoint.sh', 'nested-podman-entrypoint.sh', 'sandbox-agents',
                             'smoke.sh', 'podman-smoke.sh', 'git-identity.sh'):
                content = (checkout / 'src/container' / filename).read_bytes()
                with self.subTest(file=filename):
                    self.assertFalse(b'\r\n' in content, f'{filename}: Windows checkout corrupts the Linux script interpreter')
            for filename in ('sandbox.ps1', 'README.md', 'src/host/windows_cli.py'):
                with self.subTest(file=filename):
                    self.assertNotIn(b'\r\n', (checkout / filename).read_bytes())
            if os.name != 'nt':
                # Run only the checked-out interpreter line, never the privileged
                # entrypoint body. This exercises Linux exec's missing-interpreter failure.
                probe = root / 'entrypoint-probe'
                first_line = (checkout / 'src/container/entrypoint.sh').read_bytes().split(b'\n')[0]
                probe.write_bytes(first_line + b'\nprintf "entrypoint interpreter OK\\n"\n')
                probe.chmod(0o755)
                result = subprocess.run([str(probe)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, 'entrypoint interpreter OK\n')

    @unittest.skipUnless(os.name != 'nt' and shutil.which('sed') and shutil.which('bash'),
                         'Image normalization test runs with Linux sed and Bash')
    def test_image_build_normalizes_already_crlf_sources(self):
        for recipe_name in ('Containerfile', 'Containerfile.podman'):
            with self.subTest(recipe=recipe_name), tempfile.TemporaryDirectory(prefix='sandbox-image-eol-') as directory:
                root = Path(directory)
                recipe = (PROJECT / 'src/container' / recipe_name).read_text()
                lines = recipe.replace('\\\n', ' ').splitlines()
                normalization = next((line[4:].split('&&', 1)[0].strip() for line in lines
                                      if line.startswith('RUN sed -i ')), None)
                self.assertIsNotNone(normalization, 'Image build must repair existing CRLF checkouts')
                scripts = []
                for line in lines:
                    if not line.startswith('COPY '):
                        continue
                    fields = shlex.split(line)
                    if len(fields) != 3 or fields[0] != 'COPY' or fields[1].startswith('--'):
                        continue
                    source = PROJECT / 'src/container' / fields[1]
                    content = source.read_bytes()
                    target = root / fields[2].lstrip('/')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
                    if content.startswith(b'#!') or source.suffix == '.sh':
                        scripts.append(target)
                command = shlex.split(normalization)
                command = [str(root / arg.lstrip('/')) if arg.startswith('/') else arg for arg in command]
                subprocess.run(command, check=True)
                for script in scripts:
                    self.assertFalse(b'\r\n' in script.read_bytes(), f'{script.name} still has CRLF')
                    if os.name != 'nt':
                        if script.read_bytes().startswith(b'#!/bin/bash'):
                            subprocess.run(['bash', '-n', str(script)], check=True)


if __name__ == '__main__':
    unittest.main()
