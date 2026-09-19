// No Podman daemon, downloads, provider credentials, or model calls needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const catalog = require('../src/container/agents.json');
const { createManager } = require('../src/container/agent-manager.cjs');
const { selection, initialSelection } = createManager();
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-command-test-'));
function write(file, value, mode = 0o600) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, value, { mode });
}

async function test() {
  for (const spec of [undefined, '', 'none', 'codex,codex', 'unknown']) {
    assert.throws(() => initialSelection(spec));
  }
  assert.deepEqual(initialSelection('codex'), { codex: 'latest' });
  assert.deepEqual(initialSelection('claude@2.1.276'), { claude: '2.1.276' });
  assert.equal(Object.keys(initialSelection('all')).length, 6);
  assert.deepEqual(selection('none'), {}); // Existing sandboxes can disable all.

  // Exercise manager dispatch with an isolated filesystem and mocked processes.
  const agentHome = path.join(fixture, 'agent');
  const config = path.join(agentHome, '.local/state/sandbox-agents/config.json');
  write(config, JSON.stringify({ enabled: selection('all') }));
  for (const [id, info] of Object.entries(catalog)) {
    const relative = id === 'hermes' ? 'repo/venv/bin/hermes' : `bin/${info.command}`;
    write(path.join(agentHome, '.local/share/sandbox-agents', id, relative), 'test executable');
  }
  const calls = [];
  const processes = {
    spawn: (command, args, options) => {
      calls.push({ command, args, options });
      const child = new (require('node:events').EventEmitter)();
      process.nextTick(() => child.emit('exit', 0, null));
      return child;
    },
    spawnSync: (command, args) => {
      calls.push({ command, args });
      if (command === 'node' && args[1] === '--tools' && args[2] === 'disable') {
        const file = path.join(agentHome, '.local/state/sandbox-tools/config.json');
        const state = JSON.parse(fs.readFileSync(file));
        for (const id of args[3].split(',')) delete state.enabled[id];
        write(file, JSON.stringify(state));
      }
      return { status: 0, stdout: '' };
    },
  };
  const agents = createManager({ home: agentHome, ...processes });
  for (const id of Object.keys(catalog)) {
    calls.length = 0;
    await agents.terminalSession(id);
    const last = calls.at(-1);
    assert.equal(last.command, 'tmux');
    assert.deepEqual(Array.from(last.args.slice(0, 8)), ['-L', 'sandbox-agent-terminals', 'new-session', '-A', '-s', id === 'deepseek' ? 'deepseek-cli' : id, '-c', '/workspace']);
    const terminalCommand = Array.from(last.args.slice(8));
    assert.equal(calls.length, 1);
    assert.deepEqual(terminalCommand, id === 'deepseek'
      ? ['/bin/bash', '-lc', 'dsh --help; exec /bin/bash -l']
      : ['/usr/local/bin/sandbox-agents', 'run', id]);
  }
  const loginCases = [
    ['codex', ['login', '--device-auth']],
    ['claude', ['auth', 'login']],
    ['opencode', ['auth', 'login']],
    ['copilot', ['login', '--device-code']],
    ['hermes', ['model']],
  ];
  for (const [id, expectedArgs] of loginCases) {
    calls.length = 0;
    await agents.login(id);
    assert.equal(calls.length, 1);
    const relative = id === 'hermes' ? 'repo/venv/bin/hermes' : `bin/${id}`;
    assert.equal(calls[0].command, path.join(agentHome, '.local/share/sandbox-agents', id, relative));
    assert.deepEqual(Array.from(calls[0].args), expectedArgs);
    assert.equal(calls[0].options.stdio, 'inherit', 'Login captured authentication output');
  }
  calls.length = 0;
  for (const args of [[], ['deepseek'], ['hermes', 'extra'], ['claude', '--console'], ['codex', '--with-api-key'], ['opencode', '--provider', 'openai'], ['copilot', '--web-flow'], ['unknown']]) {
    await assert.rejects(agents.login(...args));
  }
  assert.equal(calls.length, 0);
  write(config, JSON.stringify({ enabled: {} }));
  calls.length = 0;
  for (const id of Object.keys(catalog)) {
    await assert.rejects(agents.terminalSession(id), /disabled/);
  }
  for (const [id] of loginCases) await assert.rejects(agents.login(id), /disabled/);
  assert.equal(calls.length, 0, 'Disabled agent started or attached a process');
  // Hermes alone never provisions its dashboard, including cached installs.
  const hermesDir = path.join(agentHome, '.local/share/sandbox-agents/hermes');
  write(path.join(hermesDir, 'installed.json'), JSON.stringify({ source: 'NousResearch/hermes-agent', requested: 'main', resolved: 'test-revision' }));
  calls.length = 0;
  agents.install('hermes', 'main');
  assert.equal(calls.length, 0);
  assert.equal(fs.existsSync(path.join(hermesDir, 'dashboard.json')), false);
  const tools = createManager({ isTools: true, home: agentHome, ...processes });
  await assert.rejects(tools.login('codex'), /Usage: sandbox-tools login github/);
  await assert.rejects(tools.login('opencode'), /Usage: sandbox-tools login github/);
  await assert.rejects(tools.login('copilot'), /Usage: sandbox-tools login github/);
  await assert.rejects(tools.login('hermes'), /Usage: sandbox-tools login github/);
  calls.length = 0;
  await tools.login('github');
  assert.deepEqual(calls.map(({ command, args }) => [command, args]), [
    ['gh', ['auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web']],
    ['gh', ['auth', 'setup-git', '--hostname', 'github.com']],
    ['/bin/bash', ['/usr/local/lib/sandbox-agents/git-identity.sh']],
  ]);
  assert.ok(calls.every(call => call.options.stdio === 'inherit'));
  calls.length = 0;
  for (const args of [[], ['github', 'extra'], ['gh'], ['all']]) await assert.rejects(tools.login(...args), /Usage/);
  await assert.rejects(agents.login('github'), /Unknown agent/);
  assert.equal(calls.length, 0);
  for (const outcomes of [[7], [null], [0, 9], [0, 0, 1], [0, 0, null]]) {
    let started = 0;
    const failing = createManager({ isTools: true, home: agentHome, ...processes,
      spawn: () => {
        const code = outcomes[started++];
        const child = new (require('node:events').EventEmitter)();
        process.nextTick(() => child.emit('exit', code, code === null ? 'SIGINT' : null));
        return child;
      },
    });
    await failing.login('github');
    assert.equal(started, outcomes.length, 'Failed login continued to Git setup');
    assert.equal(process.exitCode, outcomes.at(-1) ?? 1);
    process.exitCode = 0;
  }
  const missing = createManager({ isTools: true, home: agentHome, ...processes,
    spawn: () => {
      const child = new (require('node:events').EventEmitter)();
      process.nextTick(() => child.emit('error', new Error('spawn gh ENOENT')));
      return child;
    },
  });
  await assert.rejects(missing.login('github'), /ENOENT/);
  assert.throws(() => tools.install('hermes-dashboard', 'bundled'), /requires the enabled hermes agent/);
  write(config, JSON.stringify({ enabled: { hermes: 'main' } }));
  assert.throws(() => tools.install('hermes-dashboard', 'bundled'), /did not produce index.html/);
  assert.equal(fs.existsSync(path.join(hermesDir, 'dashboard.json')), false);
  write(path.join(hermesDir, 'repo/hermes_cli/web_dist/index.html'), '<html>dashboard</html>');
  tools.install('hermes-dashboard', 'bundled');
  calls.length = 0;
  tools.install('hermes-dashboard', 'bundled');
  assert.equal(calls.length, 0, 'Prepared dashboard rebuilt on re-enable');
  const toolConfig = path.join(agentHome, '.local/state/sandbox-tools/config.json');
  write(toolConfig, JSON.stringify({ enabled: { 'hermes-dashboard': 'bundled', t3: 'latest' } }));
  await agents.apply({});
  assert.deepEqual(JSON.parse(fs.readFileSync(toolConfig)).enabled, { t3: 'latest' }, 'Hermes disable must retain unrelated tools');
  assert.deepEqual(JSON.parse(fs.readFileSync(config)).enabled, {});
  assert.equal(fs.existsSync(path.join(hermesDir, 'dashboard.json')), true, 'Hermes disable removed cached dashboard');
  write(path.join(agentHome, '.local/share/sandbox-tools/t3/bin/t3'), 'test executable');
  calls.length = 0;
  for (const args of [[], ['github'], ['t3', 'extra']]) await assert.rejects(tools.setup(...args), /Usage/);
  await assert.rejects(agents.setup('t3'), /Usage/);
  assert.equal(calls.length, 0);
  await tools.setup('t3');
  assert.deepEqual(calls.map(({ command, args }) => [command, args]), [
    [path.join(agentHome, '.local/share/sandbox-tools/t3/bin/t3'), ['connect', 'link', '--headless']],
    ['/usr/local/bin/sandbox-tools', ['service', 't3', 'restart']],
  ]);
  assert.ok(calls.every(call => call.options.stdio === 'inherit'));
  for (const outcomes of [[7], [null], [0, 9]]) {
    let started = 0;
    const failing = createManager({ isTools: true, home: agentHome, ...processes,
      spawn: () => {
        const code = outcomes[started++];
        const child = new (require('node:events').EventEmitter)();
        process.nextTick(() => child.emit('exit', code, code === null ? 'SIGINT' : null));
        return child;
      },
    });
    await failing.setup('t3');
    assert.equal(started, outcomes.length);
    assert.equal(process.exitCode, outcomes.at(-1) ?? 1);
    process.exitCode = 0;
  }
  const savedTools = fs.readFileSync(toolConfig, 'utf8');
  write(toolConfig, JSON.stringify({ enabled: {} }));
  calls.length = 0;
  await assert.rejects(tools.setup('t3'), /disabled/);
  assert.equal(calls.length, 0, 'Disabled T3 setup started a process');
  write(toolConfig, savedTools);
  calls.length = 0;
  await tools.terminalSession('t3');
  assert.equal(calls[0].command, '/usr/local/bin/sandbox-tools');
  assert.deepEqual(Array.from(calls[0].args), ['service', 't3', 'start']);
  assert.equal(calls.at(-1).args.at(-1), path.join(agentHome, '.local/state/sandbox-tools/t3.log'));
  // Bundled DeepSeek UI shares the agent binary and revision, without npm/builds.
  assert.throws(() => tools.install('deepseek-ui', 'bundled'), /requires the enabled deepseek agent/);
  const deepseekDir = path.join(agentHome, '.local/share/sandbox-agents/deepseek');
  write(config, JSON.stringify({ enabled: { deepseek: 'latest' } }));
  assert.throws(() => tools.install('deepseek-ui', 'bundled'), /Missing installation record/);
  calls.length = 0;
  for (const revision of ['0.1.0', '0.2.0']) {
    write(path.join(deepseekDir, 'installed.json'), JSON.stringify({ requested: 'latest', resolved: revision, package: catalog.deepseek.package }));
    tools.install('deepseek-ui', 'bundled');
    assert.equal(JSON.parse(fs.readFileSync(path.join(agentHome, '.local/share/sandbox-tools/deepseek-ui/installed.json'))).resolved, revision);
  }
  assert.equal(calls.length, 0, 'Bundled UI installed extra packages or built Hermes assets');
  assert.equal(fs.existsSync(path.join(agentHome, '.local/share/sandbox-tools/deepseek-ui/bin')), false);
  write(toolConfig, JSON.stringify({ enabled: { 'deepseek-ui': 'bundled' } }));
  const conflictingLauncher = path.join(agentHome, '.local/bin/claude');
  write(conflictingLauncher, '#!/bin/sh\necho unmanaged\n', 0o755);
  await assert.rejects(agents.apply({ claude: 'latest' }), /unmanaged command/);
  assert.deepEqual(JSON.parse(fs.readFileSync(toolConfig)).enabled, { 'deepseek-ui': 'bundled' }, 'Failed replacement disabled the UI');
  assert.deepEqual(JSON.parse(fs.readFileSync(config)).enabled, { deepseek: 'latest' });
  assert.equal(calls.length, 0, 'Failed replacement changed running services');
  fs.unlinkSync(conflictingLauncher);
  await agents.apply({});
  assert.deepEqual(JSON.parse(fs.readFileSync(toolConfig)).enabled, {});
  assert.deepEqual(JSON.parse(fs.readFileSync(config)).enabled, {});
  assert.equal(calls.filter(call => call.command === 'node' && call.args[2] === 'disable' && call.args[3] === 'deepseek-ui').length, 1);
  assert.equal(fs.existsSync(path.join(deepseekDir, 'installed.json')), true, 'Cascade removed cached agent');
  assert.equal(fs.existsSync(path.join(agentHome, '.local/share/sandbox-tools/deepseek-ui/installed.json')), true, 'Cascade removed cached UI');
  assert.throws(() => tools.selection('deepseek-ui@latest'), /without a version pin/);
  console.log('Agent/tool selection, login, installers, UI dependencies, and cached state: OK');
}
test().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(() => fs.rmSync(fixture, { recursive: true, force: true }));
