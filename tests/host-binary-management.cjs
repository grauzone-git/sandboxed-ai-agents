// Exercise executable management through public CLI output and fake Podman calls.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = function testManagement({hostHome, transportLog, env, cli}) {
  const calls = () => fs.existsSync(transportLog) ? fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse) : [];
  const reset = () => fs.rmSync(transportLog, {force: true});
  const snapshot = directory => fs.existsSync(directory) ? fs.readdirSync(directory).sort().map(name => {
    const file = path.join(directory, name);
    return [name, fs.statSync(file).isDirectory() ? snapshot(file) : fs.readFileSync(file).toString('base64')];
  }) : null;
  const sshBefore = snapshot(path.join(hostHome, '.ssh'));
  const stateRoot = path.join(process.platform === 'win32' ? env.LOCALAPPDATA : env.XDG_STATE_HOME, 'sandboxed-agents');
  const stateBefore = snapshot(stateRoot);
  const invalid = [
    ['run'], ['run', 'unknown'], ['run', 't3'], ['tool'], ['tool', 'codex'],
    ['codex', '--version'], ['t3', 'extra'],
    ['agents', 'login'], ['agents', 'login', 'deepseek'], ['agents', 'login', 'github'],
    ['agents', 'login', 'codex@latest'], ['agents', 'login', 'codex', 'extra'],
    ['tools', 'login', 'claude'], ['tools', 'login', 'gh'], ['tools', 'login', 'github', 'extra'],
  ];
  for (const kind of ['agents', 'tools']) {
    invalid.push([kind, 'bogus'], [kind, 'list', 'extra'], [kind, 'check', 'extra']);
    for (const operation of ['set', 'enable', 'disable', 'update']) {
      invalid.push([kind, operation], [kind, operation, ''], [kind, operation, 'unknown'],
        [kind, operation, 'codex,'], [kind, operation, 'none,codex'],
        [kind, operation, kind === 'agents' ? 't3' : 'codex']);
    }
  }
  for (const parameters of invalid) {
    const result = cli(['demo', ...parameters]);
    assert.notEqual(result.status, 0, JSON.stringify(parameters));
    assert.match(result.stderr, /Error:/);
    assert.deepEqual(calls(), [], 'Rejected management input reached Podman/SSH');
  }
  const check = (parameters, kind, expected, interactive = false) => {
    for (const exitCode of [0, 7]) {
      const result = cli(['demo', ...parameters], {TEST_MANAGEMENT: '1', TEST_EXEC_EXIT: String(exitCode)});
      assert.equal(result.status, exitCode, `${JSON.stringify(parameters)}: ${result.stderr}`);
      assert.equal(result.stdout, 'manager output\n');
      assert.ok(result.stderr.startsWith('manager diagnostic\n'));
      const recorded = calls();
      assert.deepEqual(recorded.at(-1), {tool: 'podman', args: ['exec', ...(interactive ? ['-i'] : []),
        '--user', '1000:1000', '--workdir', '/workspace', 'demo', `/usr/local/bin/sandbox-${kind}`, ...expected]});
      assert.equal(recorded.filter(call => call.args[0] === 'exec').length, 1);
      assert.equal(recorded.some(call => call.tool === 'ssh' || call.args.includes('init')), false);
      reset();
    }
  };
  for (const id of ['copilot', 'claude', 'codex', 'hermes', 'opencode', 'deepseek', 't3']) {
    check([id], id === 't3' ? 'tools' : 'agents', ['session', id], true);
  }
  for (const id of ['copilot', 'claude', 'codex', 'hermes', 'opencode', 'github']) {
    const kind = id === 'github' ? 'tools' : 'agents';
    check([kind, 'login', id], kind, ['login', id], true);
  }
  for (const kind of ['agents', 'tools']) {
    check([kind], kind, ['list']);
    for (const operation of ['list', 'check']) check([kind, operation], kind, [operation]);
    const selected = kind === 'agents' ? 'codex' : 'tokentracker';
    for (const operation of ['set', 'enable', 'disable', 'update']) {
      check([kind, operation, selected], kind, [operation, selected]);
    }
    check([kind, 'set', 'none'], kind, ['set', 'none']);
    check([kind, 'update', 'all'], kind, ['update', 'all']);
  }
  const literal = ['--', 'two words', "quote'and\"double", '$(touch forbidden)', '; echo nope', '', 'line\nbreak', '--help'];
  check(['run', 'codex', ...literal], 'agents', ['run', 'codex', ...literal], true);
  check(['tool', 'tokentracker', ...literal], 'tools', ['run', 'tokentracker', ...literal], true);
  const foreign = cli(['demo', 'agents', 'list'], {TEST_FOREIGN_OWNER: '/foreign', TEST_MANAGEMENT: '1'});
  assert.notEqual(foreign.status, 0);
  assert.equal(foreign.stderr, 'Error: container demo is not owned by this configuration\n');
  assert.equal(calls().some(call => call.args[0] === 'exec' || call.tool === 'ssh'), false);
  reset();
  assert.deepEqual(snapshot(path.join(hostHome, '.ssh')), sshBefore, 'Management changed host SSH');
  assert.deepEqual(snapshot(stateRoot), stateBefore, 'Management changed controller state');
  console.log('Executable management, sessions, login, literal arguments, and exit status: OK');
};
