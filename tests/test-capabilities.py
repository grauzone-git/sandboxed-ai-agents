"""Nested seccomp changes preserve host restrictions unrelated to namespace setup."""
import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/host'))
from capabilities import nested_seccomp, prepare_seccomp, seccomp_path


class NestedSeccompTests(unittest.TestCase):
    def test_namespace_allow_removes_conflicting_denials_only(self):
        profile = {
            'defaultAction': 'SCMP_ACT_ERRNO', 'defaultErrnoRet': 1,
            'archMap': [{'architecture': 'SCMP_ARCH_X86_64', 'subArchitectures': ['SCMP_ARCH_X86']}],
            'syscalls': [
                {'names': ['read', 'write', 'setns'], 'action': 'SCMP_ACT_ALLOW'},
                {'names': ['sethostname', 'setdomainname', 'setns', 'quotactl'],
                 'action': 'SCMP_ACT_ERRNO', 'errnoRet': 1, 'excludes': {'caps': ['CAP_SYS_ADMIN']}},
                {'names': ['sethostname', 'setdomainname', 'setns', 'bpf'],
                 'action': 'SCMP_ACT_ALLOW', 'includes': {'caps': ['CAP_SYS_ADMIN']}},
                {'names': ['sethostname'], 'action': 'SCMP_ACT_ERRNO'},
                {'names': ['socket'], 'action': 'SCMP_ACT_ALLOW',
                 'args': [{'index': 0, 'value': 16, 'op': 'SCMP_CMP_NE'}]},
            ],
        }
        before = copy.deepcopy(profile)
        result = nested_seccomp(profile)
        self.assertEqual(profile, before)
        self.assertEqual(result['defaultAction'], profile['defaultAction'])
        self.assertEqual(result['defaultErrnoRet'], profile['defaultErrnoRet'])
        self.assertEqual(result['archMap'], profile['archMap'])
        self.assertEqual(result['syscalls'][:-1], [
            {**profile['syscalls'][0], 'names': ['read', 'write']},
            {**profile['syscalls'][1], 'names': ['quotactl']},
            {**profile['syscalls'][2], 'names': ['bpf']}, profile['syscalls'][4],
        ])
        self.assertEqual(result['syscalls'][-1], {
            'names': ['sethostname', 'setdomainname', 'setns'], 'action': 'SCMP_ACT_ALLOW'})
        self.assertEqual(nested_seccomp(result), result)

    def test_generated_policy_is_private_and_not_a_workspace_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / 'host-seccomp.json'
            source.write_text(json.dumps({'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': []}))
            def runner(*args, **kwargs):
                self.assertEqual(args, ('info', '--format', '{{json .Host.Security}}'))
                return subprocess.CompletedProcess(args, 0, json.dumps({'seccompProfilePath': str(source)}))
            prepare_seccomp(project, runner)
            target = seccomp_path(project)
            self.assertEqual(target.parent, project / '.local')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(json.loads(target.read_text())['defaultAction'], 'SCMP_ACT_ERRNO')
            before = target.read_bytes()
            prepare_seccomp(project, runner)
            self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
