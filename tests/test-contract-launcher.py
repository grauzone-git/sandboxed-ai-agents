"""Check launcher selection and owner discovery through the contract helper CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


class ContractLauncherTests(unittest.TestCase):
    def test_selected_launcher_supplies_owner_without_a_checkout(self):
        with tempfile.TemporaryDirectory(prefix='contract launcher ') as temporary:
            root = Path(temporary)
            launcher = root / 'custom launcher.py'
            launcher.write_text('''import subprocess, sys
assert sys.argv[1:] == ['argument with spaces', 'list']
subprocess.run(['podman', 'ps', '--filter',
                'label=io.sandboxed-agents.project=custom-group'], check=True)
''')
            command = [sys.executable, str(launcher), 'argument with spaces']
            result = subprocess.run(
                [sys.executable, '-B', str(PROJECT / 'tests/contract_launcher.py'), str(root)],
                env={**os.environ, 'SANDBOX_TEST_LAUNCHER': json.dumps(command)},
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'command': command, 'owner': 'custom-group'})

    def test_list_suite_invokes_selected_wrapper_with_literal_arguments(self):
        with tempfile.TemporaryDirectory(prefix='contract wrapper ') as temporary:
            root = Path(temporary)
            marker = root / 'calls.jsonl'
            wrapper = root / 'wrapper.py'
            wrapper.write_text('''import json, os, sys
from pathlib import Path
with Path(sys.argv[1]).open('a') as stream:
    stream.write(json.dumps(sys.argv[3:]) + '\\n')
os.execv(sys.argv[2], sys.argv[2:])
''')
            command = [sys.executable, str(wrapper), str(marker), '{checkout}/sandbox']
            result = subprocess.run(
                [sys.executable, '-B', str(PROJECT / 'tests/test-list.py')],
                env={**os.environ, 'SANDBOX_TEST_LAUNCHER': json.dumps(command)},
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in marker.read_text().splitlines()]
            self.assertIn(['list', 'agent01'], calls)
            self.assertIn(['list'], calls)

    def test_owner_discovery_isolates_home_and_state(self):
        with tempfile.TemporaryDirectory(prefix='contract state ') as temporary:
            root = Path(temporary)
            launcher = root / 'launcher.py'
            launcher.write_text('''import os, pathlib, subprocess
for name in ('HOME', 'XDG_STATE_HOME', 'LOCALAPPDATA', 'USERPROFILE'):
    assert os.environ[name] != 'must-not-use', name
    pathlib.Path(os.environ[name]).mkdir(parents=True, exist_ok=True)
subprocess.run(['podman', 'ps', '--filter',
                'label=io.sandboxed-agents.project=default'], check=True)
''')
            env = {**os.environ, **dict.fromkeys(
                ('HOME', 'XDG_STATE_HOME', 'LOCALAPPDATA', 'USERPROFILE'), 'must-not-use'),
                'SANDBOX_TEST_LAUNCHER': json.dumps([sys.executable, str(launcher)])}
            result = subprocess.run(
                [sys.executable, '-B', str(PROJECT / 'tests/contract_launcher.py'), str(root)],
                env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_without_owner_query_fails_instead_of_assuming_checkout(self):
        with tempfile.TemporaryDirectory(prefix='contract no owner ') as temporary:
            result = subprocess.run(
                [sys.executable, '-B', str(PROJECT / 'tests/contract_launcher.py'), temporary],
                env={**os.environ, 'SANDBOX_TEST_LAUNCHER': json.dumps([sys.executable, '-c', 'pass'])},
                text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('one nonempty owner label filter', result.stderr)

    def test_invalid_launcher_configuration_fails_clearly(self):
        for configured in ('[]', '"shell command"', '[1]', '{invalid'):
            with self.subTest(configured=configured):
                result = subprocess.run(
                    [sys.executable, '-B', str(PROJECT / 'tests/contract_launcher.py'), str(PROJECT)],
                    env={**os.environ, 'SANDBOX_TEST_LAUNCHER': configured}, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Error:', result.stderr)


if __name__ == '__main__':
    unittest.main()
