"""Removal checks use isolated homes; never delete a real sandbox or SSH key."""
import importlib.util
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('remove_ssh', Path(__file__).resolve().parents[1] / 'src/host/remove-ssh-config.py')
remover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remover)


class RemoveSSHTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sandbox remove test ')
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / 'home'
        self.root = self.home / '.ssh/sanboxed-agents'
        self.state = self.root / 'demo'
        self.config = self.home / '.ssh/config'
        self.generated = self.state / 'demo.conf'
        self.state.mkdir(parents=True)
        for name in ('id_ed25519', 'id_ed25519.pub', 'known_hosts'):
            (self.state / name).write_text('fixture')
        self.generated.write_text('Host demo\n    Port 2222\n')
        self.other = self.root / 'other/other.conf'
        self.other.parent.mkdir()
        self.other.write_text('Host other\n    Port 2223\n')
        self.config.write_text(f'Include "{self.generated}"\nHost *\n\nInclude "{self.other}"\nHost *\nServerAliveInterval 47\n')

    def cleanup(self, check=False):
        remover.cleanup('demo', self.home, check)

    def test_cleanup_preserves_other_hosts_and_is_idempotent(self):
        before = self.config.read_bytes()
        self.cleanup(check=True)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.generated.exists())
        self.cleanup()
        self.assertFalse(self.state.exists())
        self.assertTrue(self.other.exists())
        self.assertNotIn(str(self.generated), self.config.read_text())
        resolved = subprocess.run(['ssh', '-G', '-F', str(self.config), 'other'], capture_output=True, text=True, check=True).stdout
        self.assertIn('port 2223\n', resolved)
        self.assertIn('serveraliveinterval 47\n', resolved)
        after = self.config.read_bytes()
        self.cleanup()
        self.assertEqual(self.config.read_bytes(), after)

    def test_multi_file_includes_symlink_crlf_and_permissions(self):
        original = f'  Include "{self.generated}" "{self.other}"\r\nHost *\r\n# Keep me\r\n'
        self.config.unlink()
        target = self.home / 'ssh-dotfile'
        target.write_bytes(original.encode())
        target.chmod(0o640)
        self.config.symlink_to(target)
        self.cleanup()
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertIn(b'# Keep me\r\n', target.read_bytes())
        resolved = subprocess.run(['ssh', '-G', '-F', str(self.config), 'other'], capture_output=True, text=True, check=True).stdout
        self.assertIn('port 2223\n', resolved)

    def test_preserves_unknown_files_and_file_symlink_targets(self):
        outside = self.home / 'unrelated-key'
        outside.write_text('keep')
        key = self.state / 'id_ed25519'
        key.unlink()
        key.symlink_to(outside)
        (self.state / 'notes.txt').write_text('keep notes')
        self.cleanup()
        self.assertEqual(outside.read_text(), 'keep')
        self.assertEqual((self.state / 'notes.txt').read_text(), 'keep notes')
        self.assertFalse(key.is_symlink())

    def test_rejects_directory_symlink_before_any_changes(self):
        moved = self.root / 'saved-demo'
        self.state.rename(moved)
        self.state.symlink_to(moved, target_is_directory=True)
        before = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unexpected SSH state path'):
            self.cleanup(check=True)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue((moved / 'id_ed25519').exists())

    def test_missing_home_creates_nothing_and_names_are_validated(self):
        missing = self.home / 'missing'
        remover.cleanup('absent', missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            remover.cleanup('../demo', self.home)


if __name__ == '__main__':
    unittest.main()
