"""Exercise workspace creation and retention without a Podman daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sandbox-workspace-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "controller"
        self.checkout.mkdir()
        shutil.copy2(PROJECT / "sandbox", self.checkout / "sandbox")
        shutil.copytree(PROJECT / "src", self.checkout / "src")
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.state = self.root / "volumes.json"
        self.state.write_text("{}")
        self.env = {**os.environ, "HOME": str(self.home),
                    "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "TEST_PROJECT": str(self.checkout), "TEST_LOG": str(self.log),
                    "TEST_VOLUMES": str(self.state)}
        (self.bin / "id").write_text("#!/bin/sh\nprintf '1000\\n'\n")
        (self.bin / "podman").write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
state = Path(os.environ['TEST_VOLUMES'])
volumes = json.loads(state.read_text())
if args[0] == 'info': print('true')
elif args[:2] == ['container', 'exists']: sys.exit(1)
elif args[0] == 'inspect': print(os.environ['TEST_PROJECT'])
elif args[0] == 'volume':
    name = args[-1]
    if args[1] == 'exists': sys.exit(0 if name in volumes else 1)
    elif args[1] == 'inspect': print(volumes[name])
    elif args[1] == 'create':
        volumes[name] = args[args.index('--label') + 1].split('=', 1)[1]
    elif args[1] == 'rm': del volumes[name]
    else: sys.exit('Unexpected volume call')
    state.write_text(json.dumps(volumes))
elif args[0] == 'exec' and '/bin/chown' in args and os.environ.get('TEST_CHOWN_FAIL'): sys.exit(1)
elif args[0] in ('exec', 'run', 'image', 'stop', 'start', 'rm'): pass
else: sys.exit('Unexpected call: ' + repr(args))
''')
        for command in self.bin.iterdir():
            command.chmod(0o755)

    def cli(self, *args, success=True, env=None):
        result = subprocess.run([str(self.checkout / "sandbox"), *args],
                                cwd=self.checkout, env={**self.env, **(env or {})},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_omitted_workspace_and_name_use_only_named_volumes(self):
        self.cli("up", "--agents", "codex")
        run = next(call for call in self.calls() if call[0] == "run")
        mounts = [run[i + 1] for i, arg in enumerate(run) if arg == "--volume"]
        self.assertEqual(mounts, ["agent01-workspace:/workspace", "agent01-home:/home/agent",
                                  "agent01-sshd:/var/lib/agent-sshd"])
        self.assertIn("127.0.0.1:2222:2222", run)
        self.assertEqual(set(json.loads(self.state.read_text())),
                         {"agent01-workspace", "agent01-home", "agent01-sshd"})
        self.assertFalse((self.checkout / "workspaces").exists())
        self.assertFalse((self.home / ".ssh").exists())
        chown = next(call for call in self.calls() if "/bin/chown" in call)
        self.assertEqual(chown, ["exec", "--user", "0", "agent01", "/bin/chown", "1000:1000", "/workspace"])
        self.assertLess(self.calls().index(chown), next(i for i, call in enumerate(self.calls()) if "init" in call))

    def test_explicit_directory_stays_a_bind_with_either_port_syntax(self):
        directory = self.root / "workspace with spaces"
        for port_args in [("2231",), ("--ssh-port", "2231")]:
            with self.subTest(port_args=port_args):
                self.log.unlink(missing_ok=True)
                self.cli("up", "demo", str(directory), *port_args, "--agents", "codex")
                run = next(call for call in self.calls() if call[0] == "run")
                self.assertIn(f"{directory}:/workspace:Z", run)
                self.assertIn("127.0.0.1:2231:2222", run)
                self.assertTrue(directory.is_dir())
                self.assertNotIn("demo-workspace", json.loads(self.state.read_text()))
                self.assertFalse(any("/bin/chown" in call for call in self.calls()))

    def test_named_volume_reused_after_removal_then_deleted_on_request(self):
        self.cli("up", "demo", "--ssh-port", "2232", "--agents", "codex")
        run = next(call for call in self.calls() if call[0] == "run")
        self.assertIn("127.0.0.1:2232:2222", run)
        self.cli("remove", "demo")
        self.assertEqual(len(json.loads(self.state.read_text())), 3)
        self.log.unlink()
        self.cli("up", "demo", "--agents", "codex")
        self.assertFalse(any(call[:2] == ["volume", "create"] for call in self.calls()))
        self.cli("remove", "demo", "--volumes")
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_foreign_workspace_volume_rejected_before_mutation(self):
        self.state.write_text(json.dumps({"demo-workspace": "/another-controller"}))
        for args in [("up", "demo", "--agents", "codex"), ("remove", "demo", "--volumes")]:
            with self.subTest(args=args):
                self.log.unlink(missing_ok=True)
                result = self.cli(*args, success=False)
                self.assertIn("belongs to another configuration", result.stderr)
                self.assertFalse(any(call[0] in ("run", "stop", "rm") or
                                     call[:2] in (["volume", "create"], ["volume", "rm"])
                                     for call in self.calls()))
                self.assertFalse((self.checkout / "workspaces").exists())

    def test_invalid_ports_and_empty_directory_fail_before_podman(self):
        options = [("--ssh-port",), ("--ssh-port", ""), ("--ssh-port", "1023"),
                   ("--ssh-port", "65536"), ("--ssh-port", "nope"),
                   ("--ssh-port", "2222", "--ssh-port", "2223"),
                   ("./project", "2222", "--ssh-port", "2223"), ("",)]
        for option in options:
            with self.subTest(option=option):
                self.cli("up", "demo", "--agents", "codex", *option, success=False)
        self.assertEqual(self.calls(), [])

    def test_failed_workspace_initialization_does_not_install_agents(self):
        self.cli("up", "demo", "--agents", "codex", success=False, env={"TEST_CHOWN_FAIL": "1"})
        self.assertFalse(any("init" in call for call in self.calls()))


if __name__ == "__main__":
    unittest.main()
