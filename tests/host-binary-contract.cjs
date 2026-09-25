// Shared host scenarios with executable state paths and Podman session transport.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');

module.exports = async function testExecutable({fixture, checkout, hostHome, transportLog, env, launcher, cli, invalidArguments, grammarCases}) {
  const state = path.join(process.platform === 'win32' ? env.LOCALAPPDATA : env.XDG_STATE_HOME, 'sandboxed-agents');
  const sshRoot = path.join(state, 'ssh');
  const sshDirectory = path.join(sshRoot, 'demo');
  const sshConfig = path.join(sshRoot, 'demo.conf');
  const userConfig = path.join(hostHome, '.ssh/config');
  const include = `Include "${sshRoot.replaceAll('\\', '/')}/*.conf"`;
  const write = (file, value) => {
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, value, {mode: 0o600});
  };
  const calls = () => fs.existsSync(transportLog) ? fs.readFileSync(transportLog, 'utf8').trim().split('\n').map(JSON.parse) : [];
  const reset = () => fs.rmSync(transportLog, {force: true});
  const manager = (kind, args, interactive = false) => ['exec', ...(interactive ? ['-i'] : []), '--user', '1000:1000', '--workdir', '/workspace', 'demo', `/usr/local/bin/sandbox-${kind}`, ...args];
  const ssh = args => ['-F', sshConfig, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'IdentityAgent=none', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=10', ...args];
  const seedSSH = (keys = true) => {
    write(sshConfig, '# Test SSH configuration\nHost demo\n    HostName 127.0.0.1\n    Port 2222\n');
    write(path.join(sshDirectory, 'owner.json'), JSON.stringify({Controller: launcher.owner, Name: 'demo'}) + '\n');
    if (keys) for (const file of ['id_ed25519', 'id_ed25519.pub', 'known_hosts']) write(path.join(sshDirectory, file), 'fixture');
  };

  for (const args of invalidArguments) {
    const result = cli(args);
    assert.notEqual(result.status, 0, JSON.stringify(args));
    assert.match(result.stderr, /Error:/, JSON.stringify(args));
    assert.deepEqual(calls(), [], 'Rejected input reached Podman/SSH');
  }
  for (const [args] of grammarCases) {
    const result = cli(args);
    assert.notEqual(result.status, 0, JSON.stringify(args));
    assert.match(result.stderr, /command|name|unknown|usage|update|alphanumeric|arguments/i, JSON.stringify(args));
    assert.deepEqual(calls(), [], 'Rejected grammar reached Podman/SSH');
  }
  for (const args of [['--help'], ['help'], [], ['demo', '--help']]) {
    const result = cli(args);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /sandboxed-agents NAME agents login/);
  }
  for (const args of [['demo', 'update', '--help'], ['update', '--help']]) {
    const result = cli(args);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /update/);
    assert.deepEqual(calls(), []);
  }
  const restarted = cli(['demo', 'restart']);
  assert.equal(restarted.status, 0, restarted.stderr);
  assert.deepEqual(calls().at(-1), {tool: 'podman', args: ['restart', 'demo']});
  assert.equal(fs.existsSync(userConfig), false, 'Plain restart touched host SSH');
  reset();

  require('./host-binary-management.cjs')({hostHome, transportLog, env, cli});
  for (const target of [['t3'], ['azdo'], ['azdo', '--persist'], ['azdo', '--clear'], ['azure'], ['azure', '--tenant', 'tenant-1', '--tenant-only']]) {
    for (const exitCode of [0, 7]) {
      const result = cli(['demo', 'tools', 'setup', ...target], {TEST_LOGIN_EXIT: String(exitCode)});
      assert.equal(result.status, exitCode, result.stderr);
      assert.deepEqual(calls().at(-1), {tool: 'podman', args: manager('tools', ['setup', ...target], true)});
      assert.equal(calls().some(call => call.tool === 'ssh' || call.args.includes('init')), false);
      reset();
    }
  }
  for (const args of [
    ['demo', 'tools', 'enable', 'tokentracker'], ['demo', 'tools', 'set', 'none'],
    ['demo', 'tool', 'tokentracker', '--version'],
  ]) {
    cli(args);
    const expected = args[1] === 'tool' ? ['run', 'tokentracker', '--version'] : args.slice(2);
    assert.deepEqual(calls().at(-1), {tool: 'podman', args: manager('tools', expected, args[1] === 'tool')});
    reset();
  }
  for (const selection of ['codex', 'all', 'claude,codex']) {
    const result = cli(['demo', 'up', '--agents', selection]);
    assert.match(result.stderr, /build the image first/i);
    reset();
  }
  for (const tools of ['t3', 'hermes-dashboard', 'deepseek-ui', 'tokentracker', 'tokentracker@0.97.2', 'all', 'none']) {
    const result = cli(['demo', 'up', '--agents', 'hermes,deepseek', '--tools', tools]);
    assert.match(result.stderr, /build the image first/i);
    reset();
  }

  for (const workspace of [hostHome, state, path.join(state, 'new-workspace'), path.join(hostHome, '.ssh/new')]) {
    const result = cli(['demo', 'up', workspace, '--agents', 'codex']);
    assert.notEqual(result.status, 0, workspace);
    assert.match(result.stderr, /workspace.*(conflict|expose)/i);
    assert.deepEqual(calls(), [], 'Unsafe workspace reached provisioning');
  }
  for (const workspace of [path.join(checkout, 'workspaces/demo'), path.join(fixture, 'external-workspace'), path.join(checkout, 'scripts-project')]) {
    const result = cli(['demo', 'up', workspace, '--agents', 'codex']);
    assert.match(result.stderr, /build the image first/i, workspace);
    reset();
  }

  const browserArgs = ['demo', 'tools', 'setup', 'azure', '--tenant', 'tenant-1', '--interactive'];
  const missingSSH = cli(browserArgs);
  assert.notEqual(missingSSH.status, 0);
  assert.match(missingSSH.stderr, /ssh-config.*--install/i);
  assert.equal(calls().some(call => call.tool === 'ssh'), false);
  reset();
  seedSSH();
  for (const operation of ['status', 'start', 'stop', 'restart', 'logs']) {
    const result = cli(['demo', 'service', 'tokentracker', operation]);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(calls().at(-1), {tool: 'ssh', args: ssh(['-T', 'demo', `/usr/local/bin/sandbox-tools service 'tokentracker' '${operation}'`])});
    assert.equal(calls().some(call => call.tool === 'podman' && call.args[0] === 'exec'), false);
    reset();
  }
  const browserSetup = cli(browserArgs);
  assert.notEqual(browserSetup.status, 0, 'Early SSH exit must fail browser login');
  assert.match(browserSetup.stderr, /ended|protocol|completion/i);
  assert.equal(calls().some(call => call.tool === 'podman' && call.args[0] === 'exec'), false);
  assert.ok(calls().some(call => call.tool === 'ssh' && call.args.includes('demo')
    && call.args.at(-1).includes('azure_setup.py')));
  reset();

  const availablePort = await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = String(server.address().port);
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
  for (const [target, remotePort] of [['hermes', 9119], ['hermes-dashboard', 9119], ['deepseek', 3080], ['deepseek-ui', 3080], ['t3', 3773], ['tokentracker', 7680]]) {
    for (const localPort of [undefined, availablePort]) {
      const result = cli(['demo', 'forward', target, ...(localPort ? [localPort] : [])]);
      if (!localPort && result.status !== 0 && /local port .* is unavailable/.test(result.stderr)) {
        assert.equal(calls().some(call => call.tool === 'ssh'), false, 'Occupied port reached SSH');
        reset();
        continue;
      }
      assert.equal(result.status, 0, result.stderr);
      const id = target === 'hermes' ? 'hermes-dashboard' : target === 'deepseek' ? 'deepseek-ui' : target;
      assert.deepEqual(calls().at(-2), {tool: 'ssh', args: ssh(['-T', 'demo', `/usr/local/bin/sandbox-tools service '${id}' 'start'`])});
      assert.deepEqual(calls().at(-1), {tool: 'ssh', args: ssh(['-o', 'ExitOnForwardFailure=yes', '-N', '-L', `127.0.0.1:${localPort ?? remotePort}:127.0.0.1:${remotePort}`, 'demo'])});
      reset();
    }
  }
  const failedStart = cli(['demo', 'forward', 'tokentracker', availablePort], {TEST_START_FAILED: '1'});
  assert.notEqual(failedStart.status, 0);
  assert.deepEqual(calls().at(-1), {tool: 'ssh', args: ssh(['-T', 'demo', "/usr/local/bin/sandbox-tools service 'tokentracker' 'start'"])});
  assert.equal(calls().some(call => call.tool === 'ssh' && call.args.includes('-L')), false);
  reset();

  assert.match(cli(['demo', 'ssh-config']).stdout, /Test SSH configuration/);
  assert.deepEqual(calls(), []);
  assert.notEqual(cli(['demo', 'ssh-config', '--invalid']).status, 0);
  write(userConfig, 'ServerAliveInterval 42\n');
  const installed = cli(['demo', 'ssh-config', '--install']);
  assert.equal(installed.status, 0, installed.stderr);
  assert.deepEqual(calls(), [], 'Complete SSH setup should reinstall offline');
  assert.equal(fs.readFileSync(userConfig, 'utf8'), `${include}\nServerAliveInterval 42\n`);

  const otherConfig = path.join(sshRoot, 'other.conf');
  write(otherConfig, 'Host other\n    Port 2223\n');
  const marker = path.join(checkout, 'workspaces/demo/keep.txt');
  write(marker, 'keep workspace');
  const seedRemoval = () => {
    seedSSH();
    write(userConfig, `${include}\nServerAliveInterval 42\n`);
  };
  for (const flags of [[], ['--volumes'], ['--ssh-config'], ['--volumes', '--ssh-config'], ['--ssh-config', '--volumes']]) {
    seedRemoval();
    const result = cli(['demo', 'remove', ...flags], {TEST_REMOVE: '1'});
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(calls().filter(call => ['stop', 'rm'].includes(call.args[0])).map(call => call.args), [['stop', 'demo'], ['rm', 'demo']]);
    assert.equal(calls().filter(call => call.args[0] === 'volume' && call.args[1] === 'rm').length, flags.includes('--volumes') ? 2 : 0);
    assert.equal(fs.existsSync(sshDirectory), false);
    assert.equal(fs.existsSync(sshConfig), false);
    assert.equal(fs.existsSync(otherConfig), true);
    assert.equal(fs.readFileSync(userConfig, 'utf8'), `${include}\nServerAliveInterval 42\n`);
    assert.equal(fs.readFileSync(marker, 'utf8'), 'keep workspace');
    reset();
  }
  for (const failure of [{TEST_REMOVE_FAIL: 'stop'}, {TEST_REMOVE_FAIL: 'rm'}, {TEST_FOREIGN_OWNER: '/foreign'}, {TEST_FOREIGN_VOLUME: '1'}]) {
    seedRemoval();
    assert.notEqual(cli(['demo', 'remove', '--volumes'], {TEST_REMOVE: '1', ...failure}).status, 0);
    assert.equal(fs.existsSync(sshConfig), true);
    assert.equal(fs.readFileSync(userConfig, 'utf8'), `${include}\nServerAliveInterval 42\n`);
    if (!failure.TEST_REMOVE_FAIL) assert.equal(calls().some(call => call.args[0] === 'stop'), false);
    reset();
  }
  const saved = path.join(fixture, 'saved-state');
  fs.renameSync(sshDirectory, saved);
  fs.symlinkSync(saved, sshDirectory, process.platform === 'win32' ? 'junction' : 'dir');
  const unsafeRemoval = cli(['demo', 'remove'], {TEST_REMOVE: '1'});
  assert.notEqual(unsafeRemoval.status, 0);
  assert.match(unsafeRemoval.stderr, /symlink|reparse|redirect/i);
  assert.equal(calls().some(call => ['stop', 'rm'].includes(call.args[0])), false);
  fs.unlinkSync(sshDirectory);
  fs.renameSync(saved, sshDirectory);
  reset();
  const volumeFailure = cli(['demo', 'remove', '--volumes'], {TEST_REMOVE: '1', TEST_VOLUME_REMOVE_FAIL: '1'});
  assert.notEqual(volumeFailure.status, 0);
  assert.equal(fs.existsSync(sshDirectory), false);
  assert.equal(fs.existsSync(sshConfig), false);
  reset();
  fs.unlinkSync(otherConfig);
  seedRemoval();
  assert.equal(cli(['demo', 'remove'], {TEST_REMOVE: '1'}).status, 0);
  assert.equal(fs.readFileSync(userConfig, 'utf8'), 'ServerAliveInterval 42\n');
  reset();
  const emptyHome = path.join(fixture, 'home-without-ssh');
  fs.mkdirSync(emptyHome);
  const empty = cli(['demo', 'remove', '--ssh-config'], {TEST_REMOVE: '1', HOME: emptyHome, USERPROFILE: emptyHome,
    XDG_STATE_HOME: path.join(fixture, 'empty-state'), LOCALAPPDATA: path.join(fixture, 'empty-local')});
  assert.equal(empty.status, 0, empty.stderr);
  assert.equal(fs.existsSync(path.join(emptyHome, '.ssh')), false);
  console.log('Executable host commands, login transport, workspace protection, forwarding, and removal: OK');
};
