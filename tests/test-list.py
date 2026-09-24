"""List only checkout-owned sandboxes through the Linux CLI and a fake Podman."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
LABEL = 'io.sandboxed-agents.project'

FAKE_PODMAN = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TEST_PODMAN_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
containers = json.load(open(os.environ['TEST_CONTAINERS']))
if args[:1] == ['info']:
    print('true')
elif args[:1] == ['ps']:
    wanted = args[args.index('--filter') + 1].removeprefix('label=')
    for item in containers:
        labels = item['Config']['Labels']
        if '{}={}'.format(*next(iter(labels.items()))) == wanted:
            print(item['Name'])
elif args[:2] == ['container', 'inspect']:
    print(json.dumps([item for item in containers if item['Name'] == args[-1]]))
elif args[:1] == ['exec']:
    item = next(item for item in containers if item['Name'] in args)
    if 'agents' not in item:
        sys.exit(1)
    print(json.dumps({'enabled': item['agents']}))
else:
    sys.exit(1)
'''


def container(name, owner, *, running=True, port=2222, workspace=None, agents=None):
    mount = ({'Type': 'bind', 'Source': workspace, 'Destination': '/workspace'} if workspace
             else {'Type': 'volume', 'Name': f'{name}-workspace', 'Destination': '/workspace'})
    item = {'Name': name,
            'Config': {'Labels': {LABEL: owner}},
            'State': {'Status': 'running' if running else 'exited', 'Running': running},
            'Mounts': [{'Type': 'volume', 'Name': f'{name}-home', 'Destination': '/home/agent'}, mount],
            'HostConfig': {'PortBindings': {'2222/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]}}}
    if agents is not None:
        item['agents'] = agents
    return item


class ListTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sandbox list ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        shutil.copy2(PROJECT / 'sandbox', self.checkout / 'sandbox')
        shutil.copytree(PROJECT / 'src', self.checkout / 'src')
        self.checkout = self.checkout.resolve()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'podman').write_text(FAKE_PODMAN)
        (bin_dir / 'podman').chmod(0o755)
        (bin_dir / 'id').write_text('#!/bin/sh\nprintf "1000\\n"\n')
        (bin_dir / 'id').chmod(0o755)
        self.log = self.root / 'podman.jsonl'
        self.containers = self.root / 'containers.json'
        self.env = {**os.environ, 'HOME': str(self.root / 'home'), 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                    'TEST_PODMAN_LOG': str(self.log), 'TEST_CONTAINERS': str(self.containers)}

    def cli(self, *args, containers=()):
        self.containers.write_text(json.dumps(list(containers)))
        return subprocess.run([str(self.checkout / 'sandbox'), *args], env=self.env,
                              text=True, capture_output=True)

    def podman_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_lists_owned_sandboxes_with_state_workspace_port_and_agents(self):
        owner = str(self.checkout)
        result = self.cli('list', containers=[
            container('agent02', owner, running=False, port=2223, workspace='/work/agent02'),
            container('agent01', owner, agents={'codex': 'latest', 'claude': 'latest'}),
            container('foreign', '/another/checkout', agents={'codex': 'latest'}),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0].split(), ['NAME', 'STATE', 'SSH', 'PORT', 'AGENTS', 'WORKSPACE'])
        self.assertEqual([line.split() for line in lines[1:]], [
            ['agent01', 'running', '2222', 'claude,codex', 'agent01-workspace'],
            ['agent02', 'stopped', '2223', '-', '/work/agent02'],
        ])
        self.assertNotIn('foreign', result.stdout)

    def test_stopped_sandboxes_are_not_started_or_modified(self):
        self.cli('list', containers=[container('agent02', str(self.checkout), running=False)])
        commands = {call[0] if call[0] != 'container' else ' '.join(call[:2]) for call in self.podman_calls()}
        self.assertLessEqual(commands, {'info', 'ps', 'container inspect'})

    def test_empty_result_exits_successfully(self):
        result = self.cli('list', containers=[container('foreign', '/another/checkout')])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'No sandboxes owned by this checkout.')

    def test_unreadable_agent_selection_is_reported_as_unknown(self):
        result = self.cli('list', containers=[container('agent01', str(self.checkout))])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1].split()[3], 'unknown')

    def test_unexpected_arguments_are_rejected_before_podman(self):
        result = self.cli('list', 'agent01')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Usage: ./sandbox list', result.stderr)
        self.assertEqual(self.podman_calls(), [])


if __name__ == '__main__':
    unittest.main()
