"""Policy validators are importable without reading argv or stdin."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


HOST = Path(__file__).resolve().parents[1] / "src/host"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, HOST / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selection = load_module("agent-selection")
mounts = load_module("check-mounts")
workspace = load_module("workspace")


class ValidatorTests(unittest.TestCase):
    def test_selection_preserves_pins_and_checks_bundled_dependencies(self):
        self.assertEqual(selection.validate_selection("codex@1.2.3,claude"), "codex@1.2.3,claude")
        self.assertEqual(selection.validate_selection("hermes-dashboard", "tools", "hermes@main"), "hermes-dashboard")
        for spec, kind, agents in [("codex,codex", "agents", None),
                                   ("deepseek-ui@latest", "tools", "deepseek"),
                                   ("deepseek-ui", "tools", "codex")]:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                selection.validate_selection(spec, kind, agents)

    def test_mounts_reject_extra_duplicate_and_foreign_volumes(self):
        valid = [{"Destination": "/workspace", "Type": "bind", "Source": "/project"},
                 {"Destination": "/home/agent", "Type": "volume", "Name": "demo-home"},
                 {"Destination": "/var/lib/agent-sshd", "Type": "volume", "Name": "demo-sshd"}]
        self.assertEqual(len(mounts.validate_mounts("demo", valid)), 3)
        named = [{"Destination": "/workspace", "Type": "volume", "Name": "demo-workspace"}, *valid[1:]]
        self.assertEqual(len(mounts.validate_mounts("demo", named)), 3)
        invalid = [[{**named[0], "Name": "other-workspace"}, *valid[1:]],
                   [{**named[0], "Type": "tmpfs"}, *valid[1:]],
                   valid + [valid[0]], [valid[0], valid[0], valid[2]],
                   [valid[0], {**valid[1], "Name": "other-home"}, valid[2]],
                   [valid[0], {**valid[1], "Type": "bind"}, valid[2]]]
        for inventory in invalid:
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                mounts.validate_mounts("demo", inventory)

    def test_nested_source_links_and_allowed_workspaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, ssh_root = root / "controller", root / "ssh"
            modules = project / "src/host/lib"
            modules.mkdir(parents=True)
            external = root / "shared-helper.sh"
            external.write_text("# host helper")
            (modules / "helper.sh").symlink_to(external)
            for target in (project, modules, external, project / "tests", project / "Makefile"):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    workspace.validate_workspace(target, project, ssh_root)
            safe = project / "workspaces/demo"
            self.assertEqual(workspace.validate_workspace(safe, project, ssh_root), safe)
            alias = root / "alias"
            alias.symlink_to(project, target_is_directory=True)
            with self.assertRaises(ValueError):
                workspace.validate_workspace(alias, project, ssh_root)
            self.assertEqual(workspace.validate_workspace(alias / "workspaces/demo", project, ssh_root), safe)


if __name__ == "__main__":
    unittest.main()
