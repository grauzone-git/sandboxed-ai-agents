"""Exercise local workspace protection, including real Windows aliases when available."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/host'))
from windows_paths import checkout_identity, local_path, workspace_path


class WindowsPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox paths ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'controller'
        self.project.mkdir()
        (self.project / 'src').mkdir()
        (self.project / 'sandbox.ps1').write_text('# entry')
        self.ssh = self.root / 'home/.ssh/sanboxed-agents'

    def test_local_spaces_and_protected_paths(self):
        workspace = self.root / 'workspace with spaces'
        self.assertEqual(workspace_path(str(workspace), self.project, self.ssh), workspace.resolve())
        for value in [self.project, self.root, self.project / 'src', self.project / 'sandbox.ps1', self.ssh]:
            with self.subTest(path=value), self.assertRaises(ValueError):
                workspace_path(str(value), self.project, self.ssh)
        with self.assertRaises(ValueError):
            local_path('\\\\server\\share\\workspace')
        with self.assertRaises(ValueError):
            local_path('')

    def test_worktree_git_metadata_is_protected(self):
        metadata = self.root / 'external git/worktrees/controller'
        metadata.mkdir(parents=True)
        (self.project / '.git').write_text(f'gitdir: {metadata}\n')
        common = metadata.parents[1]
        (metadata / 'commondir').write_text('../..\n')
        for target in (metadata, common):
            with self.subTest(target=target), self.assertRaises(ValueError):
                workspace_path(str(target), self.project, self.ssh)

    def test_source_symlink_target_remains_protected(self):
        target = self.root / 'external source'
        target.mkdir()
        link = self.project / 'src/external'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires Windows Developer Mode or appropriate privileges')
        with self.assertRaises(ValueError):
            workspace_path(str(target), self.project, self.ssh)

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows path resolution')
    def test_case_drive_root_and_junction_guards(self):
        self.assertEqual(checkout_identity(self.project), checkout_identity(str(self.project).swapcase()))
        with self.assertRaises(ValueError):
            workspace_path(self.project.anchor, self.project, self.ssh)
        alias = self.root / 'junction'
        subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(self.project)],
                       check=True, capture_output=True)
        self.addCleanup(lambda: alias.rmdir())
        with self.assertRaises(ValueError):
            workspace_path(str(alias), self.project, self.ssh)
        for value in ['C:\\workspace:stream', '\\\\?\\C:\\workspace', '\\\\server\\share']:
            with self.subTest(path=value), self.assertRaises(ValueError):
                local_path(value)


if __name__ == '__main__':
    unittest.main()
