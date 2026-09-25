"""Static checks on the image recipe and its smoke test; builds nothing."""
from pathlib import Path
import re
import unittest

CONTAINER = Path(__file__).resolve().parents[1] / 'src/container'


def recipe():
    return (CONTAINER / 'Containerfile').read_text().replace('\\\n', ' ')


class PythonImageTests(unittest.TestCase):
    def test_default_build_installs_python_with_venv_and_pip(self):
        base = re.search(r'apt-get install -y --no-install-recommends ([^&]*?) && case "\$WITH_NATIVE_BUILD_TOOLS"', recipe())
        self.assertIsNotNone(base, 'base package layer not found')
        packages = base.group(1).split()
        for package in ('python3', 'python3-venv', 'python3-pip'):
            with self.subTest(package=package):
                self.assertIn(package, packages)

    def test_native_build_tools_add_compilers_and_python_headers(self):
        native = re.search(r'1\) DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ([^;]*);', recipe())
        self.assertIsNotNone(native, 'WITH_NATIVE_BUILD_TOOLS=1 branch not found')
        self.assertEqual(native.group(1).split(), ['build-essential', 'pkg-config', 'python3-dev'])

    def test_build_versions_record_python(self):
        self.assertRegex(recipe(), r'python3 --version;[^}]*\}\s*> /opt/agent-tools/build-versions\.txt')

    def test_smoke_checks_python(self):
        smoke = (CONTAINER / 'smoke.sh').read_text()
        quick, full = smoke.split('--full ]]', 1)
        self.assertIn('test "$(command -v python3)" = /usr/bin/python3', quick)
        self.assertRegex(quick, r'(?m)^python3 --version$')
        self.assertIn('python3 -m venv', full)


if __name__ == '__main__':
    unittest.main()
