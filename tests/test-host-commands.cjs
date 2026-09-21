// No Podman daemon, downloads, provider credentials, or model calls needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const project = path.resolve(__dirname, '..');
const catalog = require('../src/container/agents.json');
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-command-test-'));
function write(file, value, mode = 0o600) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, value, { mode });
}

async function test() {
  // Mock the host transport, recording whether rejected commands touch Podman.
  const checkout = path.join(fixture, 'project');
  const hostHome = path.join(fixture, 'host-home');
  const sshConfig = path.join(hostHome, '.ssh/sanboxed-agents/demo/demo.conf');
  const mockBin = path.join(fixture, 'bin');
  const transportLog = path.join(fixture, 'transport.jsonl');
  write(path.join(checkout, 'sandbox'), fs.readFileSync(path.join(project, 'sandbox')), 0o755);
  fs.cpSync(path.join(project, 'src'), path.join(checkout, 'src'), { recursive: true });
  write(sshConfig, '# Test SSH configuration\n');
  write(path.join(mockBin, 'id'), '#!/bin/sh\nprintf "1000\\n"\n', 0o755);
  const dummyPat = 'dummy-$();`literal`\" token\nnext';
  const recorder = `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.TEST_TRANSPORT_LOG, JSON.stringify({tool: require('node:path').basename(process.argv[1]), args}) + '\\n');
`;
  write(path.join(mockBin, 'podman'), recorder + `
if (args[0] === 'info') console.log('true');
else if (args[0] === 'inspect') console.log(process.env.TEST_OWNER || process.env.TEST_PROJECT);
else if (args[0] === 'exec' && args.includes('/usr/local/bin/sandbox-azdo')) {
  const token = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (token !== ${JSON.stringify(dummyPat)}) process.exit(91);
  if ('AZURE_DEVOPS_EXT_PAT' in process.env) process.exit(92);
  console.log('projects: []');
  process.exit(Number(process.env.TEST_LOGIN_EXIT || 0));
}
else if (args[0] === 'exec' && (args.includes('login') || args.includes('setup'))) process.exit(Number(process.env.TEST_LOGIN_EXIT || 0));
else if (args[0] === 'exec' && args.includes('service') && args.at(-1) === 'start') process.exit(0);
else if (process.env.TEST_REMOVE && ['stop', 'rm'].includes(args[0])) process.exit(args[0] === process.env.TEST_REMOVE_FAIL ? 1 : 0);
else if (process.env.TEST_REMOVE && args[0] === 'volume' && args[1] === 'inspect') console.log(process.env.TEST_FOREIGN_VOLUME && args.at(-1).endsWith('-sshd') ? '/foreign' : process.env.TEST_PROJECT);
else if (process.env.TEST_REMOVE && args[0] === 'volume' && args[1] === 'exists' && args.at(-1).endsWith('-workspace')) process.exit(1);
else if (process.env.TEST_REMOVE && args[0] === 'volume' && ['exists', 'rm'].includes(args[1])) process.exit(args[1] === 'rm' && process.env.TEST_VOLUME_REMOVE_FAIL ? 1 : 0);
else process.exit(1); // No image/container: stop before creation in positive parsing tests.
`, 0o755);
  write(path.join(mockBin, 'ssh'), recorder + '\nif (process.env.TEST_START_FAILED && args.at(-1) === "-s") process.exit(1);\n', 0o755);
  const cli = (args, extraEnv = {}) => spawnSync(path.join(checkout, 'sandbox'), args, {
    cwd: checkout, encoding: 'utf8', env: { ...process.env, HOME: hostHome, PATH: `${mockBin}:${process.env.PATH}`, TEST_PROJECT: checkout, TEST_TRANSPORT_LOG: transportLog, ...extraEnv },
  });
  for (const args of [
    ['up', 'demo'], ['up', 'demo', '--agents', 'none'], ['up', 'demo', '--agents', ''],
    ['up', 'demo', '--agents', 'codex,unknown'], ['up', 'demo', '--agents'],
    ['up', 'demo', '--tools', 'tokentracker'],
    ['up', 'demo', '--agents', 't3'],
    ['up', 'demo', '--agents', 'codex', '--tools', 'hermes-dashboard'],
    ['up', 'demo', '--agents', 'codex', '--tools', 'deepseek-ui'],
    ['up', 'demo', '--agents', 'deepseek', '--tools', 'deepseek-ui@latest'],
    ['up', 'demo', '--agents', 'hermes', '--tools', 'hermes-dashboard@latest'],
    ['up', 'demo', '--agents', 'codex', '--tools', 'codex'],
    ['up', 'demo', '--agents', 'codex', '--tools'],
    ['up', 'demo', '--agents', 'codex', '--tools', 'tokentracker', '--tools', 'none'],
    ['agents', 'demo', 'login'], ['agents', 'demo', 'login', 'codex', 'extra'],
    ['agents', 'demo', 'login', 'all'], ['agents', 'demo', 'login', 'codex@latest'],
    ['agents', 'demo', 'login', 'deepseek'], ['agents', 'demo', 'login', 'claude', '--console'], ['tools', 'demo', 'login', 'claude'], ['tools', 'demo', 'login', 'codex'],
    ['agents', 'demo', 'login', 'opencode', '--provider', 'openai'], ['tools', 'demo', 'login', 'opencode'],
    ['agents', 'demo', 'login', 'copilot', '--web-flow'], ['tools', 'demo', 'login', 'copilot'],
    ['agents', 'demo', 'login', 'hermes', 'extra'], ['tools', 'demo', 'login', 'hermes'],
    ['tools', 'demo', 'login'], ['tools', 'demo', 'login', 'github', 'extra'],
    ['tools', 'demo', 'login', 'gh'], ['agents', 'demo', 'login', 'github'],
    ['tools', 'demo', 'setup'], ['tools', 'demo', 'setup', 'github'],
    ['tools', 'demo', 'setup', 't3', 'extra'], ['agents', 'demo', 'setup', 't3'],
    ['remove'], ['remove', 'demo', '--unknown'], ['remove', 'demo', '--volumes', '--volumes'], ['remove', 'demo', '--ssh-config', '--ssh-config'],
    ...Object.keys(catalog).map(id => [id]),
  ]) {
    assert.notEqual(cli(args).status, 0, JSON.stringify(args));
    assert.equal(fs.existsSync(transportLog), false, 'Rejected input reached Podman/SSH');
  }
  const azdoArgs = ['azdo', 'demo', '--pat-env', '--', 'devops', 'project', 'list', '--organization', 'https://dev.azure.com/contoso'];
  for (const args of [azdoArgs.filter(arg => arg !== '--pat-env'), ['azdo'], ['azdo', 'demo', '--pat-env']]) {
    const result = cli(args, { AZURE_DEVOPS_EXT_PAT: dummyPat });
    assert.notEqual(result.status, 0);
    assert.equal(fs.existsSync(transportLog), false, 'Missing opt-in reached Podman');
    assert.equal((result.stdout + result.stderr).includes('dummy-'), false);
  }
  for (const value of ['', undefined]) {
    const result = cli(azdoArgs, { AZURE_DEVOPS_EXT_PAT: value });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /nonempty AZURE_DEVOPS_EXT_PAT/);
    assert.equal(fs.existsSync(transportLog), false, 'Missing PAT reached Podman');
  }
  const foreign = cli(azdoArgs, { AZURE_DEVOPS_EXT_PAT: dummyPat, TEST_OWNER: '/foreign' });
  assert.notEqual(foreign.status, 0);
  assert.equal(fs.readFileSync(transportLog, 'utf8').includes('"exec"'), false);
  fs.unlinkSync(transportLog);
  for (const exitCode of [0, 7]) {
    const result = cli(azdoArgs, { AZURE_DEVOPS_EXT_PAT: dummyPat, TEST_LOGIN_EXIT: String(exitCode) });
    assert.equal(result.status, exitCode, result.stderr);
    const logged = fs.readFileSync(transportLog, 'utf8');
    const calls = logged.trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.at(-1).args, ['exec', '-i', '--user', '1000:1000', '--workdir', '/workspace', 'demo', '/usr/local/bin/sandbox-azdo', '--pat-stdin', ...azdoArgs.slice(4)]);
    assert.equal(logged.includes('dummy-'), false);
    assert.equal((result.stdout + result.stderr).includes('dummy-'), false);
    fs.unlinkSync(transportLog);
  }
  const guarded = cli(['up', 'demo', path.join(checkout, 'src/host/azdo.py'), '--agents', 'codex']);
  assert.notEqual(guarded.status, 0);
  assert.match(guarded.stderr, /host SSH\/controller files/);
  fs.unlinkSync(transportLog);
  for (const id of Object.keys(catalog)) {
    assert.equal(cli([id, 'demo']).status, 0, id);
    const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.at(-1), { tool: 'ssh', args: ['-F', sshConfig, '-t', 'demo', `/usr/local/bin/sandbox-agents session ${id}`] });
    assert.equal(calls.some(call => call.args[0] === 'exec'), false, 'Shortcut attempted an installation');
    fs.unlinkSync(transportLog);
  }
  for (const id of ['codex', 'claude', 'opencode', 'copilot', 'hermes', 'github']) {
    const kind = id === 'github' ? 'tools' : 'agents';
    for (const exitCode of [0, 7]) {
      const result = cli([kind, 'demo', 'login', id], { TEST_LOGIN_EXIT: String(exitCode) });
      assert.equal(result.status, exitCode, 'Login exit code was lost');
      const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
      assert.deepEqual(calls.at(-1), { tool: 'podman', args: ['exec', '-i', '--user', '1000:1000', '--workdir', '/workspace', 'demo', `/usr/local/bin/sandbox-${kind}`, 'login', id] });
      assert.equal(calls.some(call => call.tool === 'ssh' || call.args.includes('init')), false);
      fs.unlinkSync(transportLog);
    }
  }
  for (const exitCode of [0, 7]) {
    assert.equal(cli(['tools', 'demo', 'setup', 't3'], { TEST_LOGIN_EXIT: String(exitCode) }).status, exitCode);
    const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.at(-1), { tool: 'podman', args: ['exec', '-i', '--user', '1000:1000', '--workdir', '/workspace', 'demo', '/usr/local/bin/sandbox-tools', 'setup', 't3'] });
    assert.equal(calls.some(call => call.tool === 'ssh' || call.args.includes('init')), false);
    fs.unlinkSync(transportLog);
  }
  for (const spec of ['codex', 'all', 'claude,codex']) {
    const result = cli(['up', 'demo', '--agents', spec]);
    assert.match(result.stderr, /Build the image first/, 'Explicit agent selection was rejected');
    fs.unlinkSync(transportLog);
  }
  for (const spec of ['t3', 'hermes-dashboard', 'deepseek-ui', 'tokentracker', 'tokentracker@0.97.2', 'all', 'none']) {
    const result = cli(['up', 'demo', '--agents', 'hermes,deepseek', '--tools', spec]);
    assert.match(result.stderr, /Build the image first/);
    fs.unlinkSync(transportLog);
  }
  for (const args of [
    ['tools', 'demo', 'enable', 'tokentracker'], ['tools', 'demo', 'set', 'none'],
    ['tool', 'demo', 'tokentracker', '--version'], ['service', 'demo', 'tokentracker', 'status'],
  ]) {
    cli(args); // Mock Podman records the call then reports an unavailable exec.
    const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
    const expected = args[0] === 'service' ? ['service', 'tokentracker', 'status']
      : args[0] === 'tool' ? ['run', 'tokentracker', '--version'] : args.slice(2);
    const last = calls.at(-1).args;
    assert.equal(last[0], 'exec');
    assert.deepEqual(last.slice(last.indexOf('/usr/local/bin/sandbox-tools')), ['/usr/local/bin/sandbox-tools', ...expected]);
    fs.unlinkSync(transportLog);
  }
  for (const workspace of [hostHome, path.dirname(sshConfig), path.join(path.dirname(sshConfig), 'demo')]) {
    const result = cli(['up', 'demo', workspace, '--agents', 'codex']);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Workspace must not (contain|be inside) host SSH/);
    fs.unlinkSync(transportLog);
  }
  // A fresh checkout must never become its own writable agent workspace.
  // The guard must run before image/volume operations or directory creation.
  assert.equal(fs.existsSync(path.join(checkout, '.local')), false);
  const checkoutLink = path.join(fixture, 'controller-link');
  const scriptsLink = path.join(fixture, 'scripts-link');
  fs.symlinkSync(checkout, checkoutLink);
  fs.symlinkSync(path.join(checkout, 'src/host'), scriptsLink);
  const assertProtectedWorkspace = workspace => {
    const result = cli(['up', 'demo', workspace, '--agents', 'codex']);
    assert.notEqual(result.status, 0, workspace);
    assert.match(result.stderr, /Workspace must not (contain|be inside) host SSH\/controller files or state/, workspace);
    const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.map(call => call.args.slice(0, 2)), [['info', '--format'], ['container', 'exists']], 'Unsafe workspace reached provisioning');
    fs.unlinkSync(transportLog);
  };
  for (const workspace of [
    '.', '..', path.join(checkout, 'src'), path.join(checkout, 'tests/new-suite'),
    path.join(checkout, 'Makefile'), checkout, path.dirname(checkout), path.join(checkout, 'workspaces/..'),
    path.join(checkout, 'sandbox'), path.join(checkout, 'src/host'),
    path.join(checkout, 'src/host/new-workspace'), path.join(checkout, 'src/container'),
    path.join(checkout, '.git'), path.join(checkout, '.local/new-workspace'),
    checkoutLink, path.join(checkoutLink, 'src/host'), scriptsLink,
  ]) assertProtectedWorkspace(workspace);
  assert.equal(fs.existsSync(path.join(checkout, 'src/host/new-workspace')), false);
  assert.equal(fs.existsSync(path.join(checkout, '.local')), false);

  // An individually symlinked helper is also host-executed controller code.
  const externalHelpers = path.join(fixture, 'external-helpers');
  const helper = path.join(checkout, 'src/host/check-mounts.py');
  write(path.join(externalHelpers, 'check-mounts.py'), '# fixture helper\n');
  fs.renameSync(helper, helper + '.fixture');
  fs.symlinkSync(path.join(externalHelpers, 'check-mounts.py'), helper);
  assertProtectedWorkspace(externalHelpers);
  fs.unlinkSync(helper);
  fs.renameSync(helper + '.fixture', helper);

  const externalWorkspace = path.join(fixture, 'external-workspace');
  fs.mkdirSync(externalWorkspace);
  fs.symlinkSync(externalWorkspace, path.join(checkout, 'workspace-link'));
  for (const workspace of [
    path.join(checkout, 'workspaces/demo'), path.join(checkoutLink, 'workspaces/demo'),
    externalWorkspace, path.join(checkout, 'workspace-link'),
    path.join(checkout, 'scripts-project'), // Compare path components, not prefixes.
  ]) {
    const result = cli(['up', 'demo', workspace, '--agents', 'codex']);
    assert.match(result.stderr, /Build the image first/, `Safe workspace rejected: ${workspace}`);
    fs.unlinkSync(transportLog);
  }
  for (const [target, remotePort] of [['hermes', 9119], ['hermes-dashboard', 9119], ['deepseek', 3080], ['deepseek-ui', 3080], ['t3', 3773], ['tokentracker', 7680]]) {
    for (const localPort of [undefined, '9120']) {
      const result = cli(['forward', 'demo', target, ...(localPort ? [localPort] : [])]);
      assert.equal(result.status, 0, result.stderr);
      const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
      assert.deepEqual(calls.at(-1), { tool: 'ssh', args: ['-F', sshConfig, '-o', 'ExitOnForwardFailure=yes', '-N', '-L', `127.0.0.1:${localPort ?? remotePort}:127.0.0.1:${remotePort}`, 'demo'] });
      if (target === 'tokentracker') {
        assert.deepEqual(calls.at(-2), { tool: 'ssh', args: ['-F', sshConfig, '-T', '-o', 'BatchMode=yes', 'demo', '/bin/bash', '-s'] });
      }
      fs.unlinkSync(transportLog);
    }
  }
  assert.notEqual(cli(['forward', 'demo', 'tokentracker'], { TEST_START_FAILED: '1' }).status, 0);
  const failedStartCalls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(failedStartCalls.some(call => call.tool === 'ssh' && call.args.includes('-L')), false, 'Opened a tunnel after dashboard startup failed');
  fs.unlinkSync(transportLog);
  const shown = cli(['ssh-config', 'demo']);
  assert.equal(shown.status, 0);
  assert.match(shown.stdout, /Test SSH configuration/);
  assert.equal(fs.existsSync(transportLog), false, 'Local SSH config display contacted Podman');
  assert.notEqual(cli(['ssh-config', 'demo', '--invalid']).status, 0);
  assert.equal(fs.existsSync(transportLog), false, 'Invalid SSH config option contacted Podman');
  const userConfig = path.join(hostHome, '.ssh/config');
  write(userConfig, 'ServerAliveInterval 42\n');
  const installConfig = cli(['ssh-config', 'demo', '--install']);
  assert.equal(installConfig.status, 0, installConfig.stderr);
  assert.equal(fs.existsSync(transportLog), false, 'SSH Include installation contacted Podman');
  assert.equal(fs.readFileSync(userConfig, 'utf8'), `Include "${sshConfig}"\nHost *\n\nServerAliveInterval 42\n`);

  // Exercise destructive option combinations only against mocked Podman and
  // this temporary home. Unrelated SSH/workspace files must survive.
  const otherSSH = path.join(hostHome, '.ssh/sanboxed-agents/other/other.conf');
  const workspaceMarker = path.join(checkout, 'workspaces/demo/keep.txt');
  write(otherSSH, 'Host other\n    Port 2223\n');
  write(workspaceMarker, 'keep workspace');
  const seedRemoval = () => {
    write(sshConfig, 'Host demo\n    Port 2222\n');
    for (const item of ['id_ed25519', 'id_ed25519.pub', 'known_hosts']) {
      write(path.join(path.dirname(sshConfig), item), 'fixture');
    }
    write(userConfig, `Include "${sshConfig}"\nHost *\nInclude "${otherSSH}"\nHost *\nServerAliveInterval 42\n`);
  };
  for (const flags of [[], ['--volumes'], ['--ssh-config'], ['--volumes', '--ssh-config'], ['--ssh-config', '--volumes']]) {
    seedRemoval();
    const result = cli(['remove', 'demo', ...flags], { TEST_REMOVE: '1' });
    assert.equal(result.status, 0, result.stderr);
    const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.filter(call => ['stop', 'rm'].includes(call.args[0])).map(call => call.args), [['stop', 'demo'], ['rm', 'demo']]);
    assert.equal(calls.filter(call => call.args[0] === 'volume' && call.args[1] === 'rm').length, flags.includes('--volumes') ? 2 : 0);
    assert.equal(fs.existsSync(path.dirname(sshConfig)), false, 'Generated keys/config were retained');
    assert.equal(fs.readFileSync(userConfig, 'utf8').includes(sshConfig), false);
    assert.equal(fs.readFileSync(userConfig, 'utf8').includes(otherSSH), true);
    assert.match(result.stdout, /Local sandbox SSH files and Include entries deleted/);
    assert.equal(fs.existsSync(otherSSH), true);
    assert.equal(fs.readFileSync(workspaceMarker, 'utf8'), 'keep workspace');
    fs.unlinkSync(transportLog);
  }
  for (const failure of [{ TEST_REMOVE_FAIL: 'stop' }, { TEST_REMOVE_FAIL: 'rm' }, { TEST_OWNER: '/foreign' }, { TEST_FOREIGN_VOLUME: '1' }]) {
    seedRemoval();
    const before = fs.readFileSync(userConfig, 'utf8');
    assert.notEqual(cli(['remove', 'demo', '--volumes'], { TEST_REMOVE: '1', ...failure }).status, 0);
    assert.equal(fs.existsSync(sshConfig), true, 'Failed container removal deleted SSH files');
    assert.equal(fs.readFileSync(userConfig, 'utf8'), before);
    if (!failure.TEST_REMOVE_FAIL) {
      const calls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
      assert.equal(calls.some(call => call.args[0] === 'stop'), false, 'Ownership failure stopped the container');
    }
    fs.unlinkSync(transportLog);
  }

  // Validate SSH cleanup paths before touching the container, even without flags.
  const savedState = path.join(fixture, 'saved-ssh-state');
  fs.renameSync(path.dirname(sshConfig), savedState);
  fs.symlinkSync(savedState, path.dirname(sshConfig));
  const invalidState = cli(['remove', 'demo'], { TEST_REMOVE: '1' });
  assert.notEqual(invalidState.status, 0);
  assert.match(invalidState.stderr, /unexpected SSH state path/);
  const invalidCalls = fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(invalidCalls.some(call => ['stop', 'rm'].includes(call.args[0])), false);
  fs.unlinkSync(path.dirname(sshConfig));
  fs.renameSync(savedState, path.dirname(sshConfig));
  fs.unlinkSync(transportLog);

  // A failed volume deletion must not retain a connection to a removed container.
  const volumeFailure = cli(['remove', 'demo', '--volumes'], { TEST_REMOVE: '1', TEST_VOLUME_REMOVE_FAIL: '1' });
  assert.notEqual(volumeFailure.status, 0);
  assert.equal(fs.existsSync(path.dirname(sshConfig)), false);
  assert.equal(fs.readFileSync(userConfig, 'utf8').includes(sshConfig), false);
  fs.unlinkSync(transportLog);

  // Sandboxes created without SSH setup can be removed without creating SSH files.
  const noSSHHome = path.join(fixture, 'home-without-ssh');
  fs.mkdirSync(noSSHHome);
  const withoutSSH = cli(['remove', 'demo'], { TEST_REMOVE: '1', HOME: noSSHHome });
  assert.equal(withoutSSH.status, 0, withoutSSH.stderr);
  assert.equal(fs.existsSync(path.join(noSSHHome, '.ssh')), false);

  console.log('Host commands, login transport, workspace protection, forwarding, and removal: OK');
}
test().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(() => fs.rmSync(fixture, { recursive: true, force: true }));
