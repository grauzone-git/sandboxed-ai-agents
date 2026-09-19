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

    def test_removal_collapses_empty_resets_and_preserves_ssh_behavior(self):
        self.config.write_text(f'Include "{self.other}"\nHost *\n\n'
                               f'Include "{self.generated}"\nHost *\n\n'
                               '# Existing defaults\nHost *\nServerAliveInterval 47\n'
                               'Host private\n    Port 2204\n'
                               'Host *\n    ConnectTimeout 12\n')
        def resolved(host):
            return subprocess.run(['ssh', '-G', '-F', str(self.config), host],
                                  capture_output=True, text=True, check=True).stdout
        before = {host: resolved(host) for host in ('other', 'private', 'unrelated')}
        self.cleanup()
        self.assertEqual(self.config.read_text().count('Host *'), 2)
        self.assertIn('# Existing defaults\n', self.config.read_text())
        self.assertEqual({host: resolved(host) for host in before}, before)
        after = self.config.read_bytes()
        self.cleanup()
        self.assertEqual(self.config.read_bytes(), after)

    def test_duplicate_resets_preserve_comments_crlf_symlink_and_mode(self):
        original = (f'Include "{self.generated}"\r\nHost *\r\n\r\n'
                    '# Keep this comment\r\n  hOsT = "*" # Also keep this\r\n'
                    '\tHost=*\r\nServerAliveInterval 47\r\n')
        self.config.unlink()
        target = self.home / 'ssh-dotfile'
        target.write_bytes(original.encode())
        target.chmod(0o640)
        self.config.symlink_to(target)
        self.cleanup(check=True)
        self.assertEqual(target.read_bytes(), original.encode())
        self.cleanup()
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(target.read_bytes(), b'Host *\r\n\r\n# Keep this comment\r\n'
                                             b'  # Also keep this\r\nServerAliveInterval 47\r\n')

    def test_resets_are_not_merged_across_scope_boundaries(self):
        for separator in [f'Include "{self.other}"\n', 'Host private\n',
                          'Host * !private\n', 'Match host private\n',
                          'Host "unterminated\n']:
            with self.subTest(separator=separator):
                content = ('Host *\n' + separator + 'Host *\n').encode()
                self.assertEqual(remover.remove_includes(content, self.generated), content)

    def test_existing_duplicates_cleaned_without_matching_include(self):
        self.config.write_bytes(b'Host *\n\nHost *\n# defaults\nHost *')
        self.cleanup()
        self.assertEqual(self.config.read_bytes(), b'Host *\n\n# defaults\n')

    def test_all_setting_types_deduplicated_within_each_host_scope(self):
        original = ('Host first\n    User alice\n    Port 2200\n'
                    '    User alice # retain explanation\n    Port 2200\n'
                    '    IdentityFile "/tmp/key#one"\n'
                    '    IdentityFile "/tmp/key#one" # key comment\n'
                    '    LocalForward 8080 localhost:80\n'
                    '    LocalForward 8080 localhost:80\n'
                    'Host first\n    User alice\n    ConnectTimeout 12\n'
                    'Host second\n    User alice\n    Port 2200\n'
                    'Match host second\n    ServerAliveInterval 47\n'
                    '    ServerAliveInterval 47\n')
        cleaned = remover.remove_includes(original.encode(), self.generated).decode()
        self.assertEqual(cleaned.count('Host first\n'), 1)
        self.assertEqual(cleaned.count('User alice'), 2)
        self.assertEqual(cleaned.count('Port 2200'), 2)
        self.assertEqual(cleaned.count('IdentityFile'), 1)
        self.assertEqual(cleaned.count('LocalForward'), 1)
        self.assertEqual(cleaned.count('ServerAliveInterval'), 1)
        self.assertIn('    # retain explanation\n', cleaned)
        self.assertIn('    # key comment\n', cleaned)
        self.assertEqual(remover.remove_includes(cleaned.encode(), self.generated).decode(), cleaned)

    def test_deduplicating_options_preserves_resolved_connections(self):
        self.config.write_text('Host first\n User alice\n Port 2200\n User alice\n'
                               'Host first\n Port 2200\n ConnectTimeout 12\n'
                               'Host second\n User alice\n Port 2200\n'
                               'Match host second\n ServerAliveInterval 47\n ServerAliveInterval 47\n'
                               'Host *\n Compression yes\n Compression yes\n')
        def resolved(host):
            return subprocess.run(['ssh', '-G', '-F', str(self.config), host],
                                  capture_output=True, text=True, check=True).stdout
        before = {host: resolved(host) for host in ('first', 'second', 'unrelated')}
        self.cleanup()
        self.assertEqual({host: resolved(host) for host in before}, before)

    def test_include_duplicates_and_scope_boundaries(self):
        include = f'Include "{self.other}"\n'
        content = (include + '# comment\n' + include + 'Host *\n User alice\n'
                   + include + 'Host *\n User alice\n')
        cleaned = remover.remove_includes(content.encode(), self.generated).decode()
        self.assertEqual(cleaned.count(include), 2)
        self.assertEqual(cleaned.count('Host *'), 2)
        self.assertEqual(cleaned.count('User alice'), 2)
        self.assertIn('# comment\n', cleaned)

    def test_command_quoting_and_order_dependent_settings_are_preserved(self):
        content = (b'Host *\n RemoteCommand echo "$HOME"\n RemoteCommand echo \'$HOME\'\n'
                   b' SendEnv LANG\n SendEnv -LANG\n SendEnv LANG\n'
                   b'Match exec "true"\nMatch exec "true"\n')
        self.assertEqual(remover.remove_includes(content, self.generated), content)

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
