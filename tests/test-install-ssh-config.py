"""Exercise config edits in temporary directories; never touch the real SSH config."""
import importlib.util
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("install_ssh_config", Path(__file__).resolve().parents[1] / "src/host/install-ssh-config.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallIncludeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sandbox-ssh-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "generated home with spaces/.ssh/sanboxed-agents/demo/demo.conf"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("Host demo\n    HostName 127.0.0.1\n    Port 2222\n    User agent\n")
        self.config = self.root / "user/.ssh/config"

    def resolved(self, host):
        result = subprocess.run(["ssh", "-G", "-F", str(self.config), host],
                                text=True, capture_output=True, check=True)
        return dict(line.split(" ", 1) for line in result.stdout.splitlines())

    def test_create_permissions_and_idempotence(self):
        self.assertTrue(installer.install_include(self.source, self.config))
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertTrue(self.config.read_text().startswith(f'Include "{self.source}"\n'))
        before = self.config.stat()
        self.assertFalse(installer.install_include(self.source, self.config))
        self.assertEqual(self.config.stat().st_ino, before.st_ino)
        self.assertEqual(self.resolved("demo")["port"], "2222")

    def test_preserve_existing_defaults_and_move_standalone_include(self):
        self.config.parent.mkdir(parents=True)
        original = "# My SSH settings\nServerAliveInterval 73\nHost elsewhere\n    HostName elsewhere.example\n"
        self.config.write_text(original + f'  include = "{self.source}"\n')
        self.config.chmod(0o640)
        installer.install_include(self.source, self.config)
        self.assertTrue(self.config.read_text().endswith(original))
        self.assertEqual(self.config.read_text().count(str(self.source)), 1)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(self.resolved("elsewhere")["serveraliveinterval"], "73")
        self.assertEqual(self.resolved("elsewhere")["hostname"], "elsewhere.example")

    def test_multiple_sandboxes_and_reinstall(self):
        second = self.root / "second_config"
        second.write_text("Host second\n    Port 2223\n")
        installer.install_include(self.source, self.config)
        installer.install_include(second, self.config)
        installer.install_include(self.source, self.config)
        self.assertEqual(self.config.read_text().count(str(self.source)), 1)
        self.assertEqual(self.config.read_text().count(str(second)), 1)
        self.assertEqual(self.config.read_text().count("Host *"), 2)
        self.assertEqual(self.resolved("demo")["port"], "2222")
        self.assertEqual(self.resolved("second")["port"], "2223")

    def test_preserve_config_symlink_and_crlf(self):
        self.config.parent.mkdir(parents=True)
        target = self.root / "dotfile"
        original = b"# Existing\r\nHost elsewhere\r\n    Port 2200\r\n"
        target.write_bytes(original)
        target.chmod(0o600)
        self.config.symlink_to(target)
        installer.install_include(self.source, self.config)
        self.assertTrue(self.config.is_symlink())
        self.assertTrue(target.read_bytes().endswith(original))
        self.assertIn(b"\r\nHost *\r\n", target.read_bytes())
        self.assertFalse(installer.install_include(self.source, self.config))

    def test_missing_source_creates_nothing(self):
        with self.assertRaises(ValueError):
            installer.install_include(self.root / "missing", self.config)
        self.assertFalse(self.config.parent.exists())


if __name__ == "__main__":
    unittest.main()
