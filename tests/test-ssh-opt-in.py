"""Exercise SSH setup against fake Podman and real OpenSSH in an isolated home."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from contract_launcher import describe_launcher


PROJECT = Path(__file__).resolve().parents[1]


class SSHOptInTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sandbox-ssh-opt-in-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.checkout = self.root / "controller"
        self.checkout.mkdir()
        shutil.copy2(PROJECT / "sandbox", self.checkout / "sandbox")
        for directory in ("src",):
            shutil.copytree(PROJECT / directory, self.checkout / directory)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "podman.jsonl"
        self.hostkey = self.root / "hostkey"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.hostkey)], check=True)
        self.config = self.home / ".ssh/sanboxed-agents/demo/demo.conf"
        self.user_config = self.home / ".ssh/config"
        self.authorized = self.root / "authorized_keys"
        self.env = {
            **os.environ, "HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}",
            'USERPROFILE': str(self.home), 'LOCALAPPDATA': str(self.root / 'local'),
            'XDG_STATE_HOME': str(self.root / 'state'),
            "TEST_LOG": str(self.log),
            "TEST_HOSTKEY": str(self.hostkey) + ".pub", "TEST_AUTHORIZED": str(self.authorized),
        }
        (self.bin / "id").write_text("#!/bin/sh\nprintf '1000\\n'\n")
        (self.bin / "id").chmod(0o755)
        (self.bin / "podman").write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'info': print('true')
elif args[0] == 'inspect': print(os.environ['TEST_OWNER'])
elif args[:2] in (['container', 'exists'], ['volume', 'exists']): sys.exit(1)
elif args[0] == 'port': print('127.0.0.1:2222')
elif args[0] == 'exec' and args[-1] == '/var/lib/agent-sshd/ssh_host_ed25519_key.pub' and '/bin/cat' in args:
    print(Path(os.environ['TEST_HOSTKEY']).read_text(), end='')
elif args[0] == 'exec' and any('cat > /var/lib/agent-sshd/authorized_keys' in a for a in args):
    Path(os.environ['TEST_AUTHORIZED']).write_text(sys.stdin.read())
elif args[0] in ('exec', 'run', 'start', 'image', 'volume'): pass
else: sys.exit('Unexpected Podman call: ' + repr(args))
''')
        (self.bin / "podman").chmod(0o755)
        launcher = describe_launcher(self.checkout, self.env)
        self.command = launcher['command']
        self.owner = launcher['owner']
        self.env['TEST_OWNER'] = self.owner

    def cli(self, *args, success=True):
        result = subprocess.run([*self.command, *args], cwd=self.checkout,
                                env=self.env, capture_output=True, text=True, timeout=20)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_setup(self):
        for name in ("demo.conf", "id_ed25519", "id_ed25519.pub", "known_hosts"):
            self.assertTrue((self.config.parent / name).is_file(), name)
        header = f'Include "{self.config}"\nHost *\n\n'
        self.assertTrue(self.user_config.read_text().startswith(header))
        self.assertEqual(self.user_config.read_text().count(f'Include "{self.config}"'), 1)
        self.assertEqual(self.authorized.read_text(), (self.config.parent / "id_ed25519.pub").read_text())
        self.assertIn("[127.0.0.1]:2222", (self.config.parent / "known_hosts").read_text())

    def test_default_up_and_start_create_no_local_ssh(self):
        self.cli("demo", "up", "--agents", "codex")
        self.cli("demo", "start")
        self.assertFalse((self.home / ".ssh").exists())
        self.assertFalse(self.authorized.exists())
        self.assertFalse(any(call[0] == "port" for call in self.calls()))
        self.assertTrue(any("init" in call and "codex" in call for call in self.calls()))

    def test_up_flag_creates_files_and_include_preserving_user_config(self):
        self.user_config.parent.mkdir()
        self.user_config.write_text("Host existing\n    HostName example.test\n")
        self.cli("demo", "up", "--ssh-config", "--agents", "codex")
        self.assert_setup()
        self.assertIn("Host existing\n    HostName example.test\n", self.user_config.read_text())
        snapshot = {p: p.read_bytes() for p in (self.user_config, *self.config.parent.iterdir())}
        self.cli("demo", "up", "--agents", "codex")
        self.cli("demo", "start")
        self.assertEqual(snapshot, {p: p.read_bytes() for p in snapshot})
        self.cli("demo", "start", "--ssh-config")
        self.assert_setup()
        self.assertEqual(snapshot, {p: p.read_bytes() for p in snapshot})

    def test_start_flag_creates_missing_files(self):
        self.cli("demo", "start", "--ssh-config")
        self.assert_setup()

    def test_later_install_creates_files_then_works_offline(self):
        missing = self.cli("demo", "ssh-config", success=False)
        self.assertIn("demo ssh-config --install", missing.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.home / ".ssh").exists())
        self.cli("demo", "ssh-config", "--install")
        self.assert_setup()
        calls = self.calls()
        self.cli("demo", "ssh-config", "--install")
        self.assertEqual(self.cli("demo", "ssh-config").stdout, self.config.read_text())
        self.assertEqual(self.calls(), calls, "Existing config operations contacted Podman")
        self.assert_setup()

    def test_invalid_flags_fail_before_podman(self):
        for args in [("demo", "up", "--agents", "codex", "--ssh-config", "--ssh-config"),
                     ("demo", "start", "--unknown"), ("start",),
                     ("demo", "start", "--ssh-config", "--ssh-config")]:
            self.cli(*args, success=False)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.home / ".ssh").exists())


if __name__ == "__main__":
    unittest.main()
