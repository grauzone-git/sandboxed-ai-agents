// Exercise both setup modes, using dummy credentials and a fake Azure CLI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-azdo-setup-'));
const source = path.resolve(__dirname, '../src/container');
const settings = path.join(fixture, '.config/sandbox-azdo/environment');
const log = path.join(fixture, 'az-calls');
const pat = 'dummy-$();`literal`" token';
const organization = 'https://dev.azure.com/contoso';
const env = { ...process.env, HOME: fixture, PATH: `${fixture}:${process.env.PATH}` };
delete env.AZURE_DEVOPS_EXT_PAT;
const setup = (args, input, extra = {}) => spawnSync('bash', [path.join(source, 'setup-azdo.sh'), ...args], {
  env: { ...env, ...extra }, input, encoding: 'utf8',
});
const calls = () => fs.existsSync(log) ? fs.readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse) : [];
try {
  fs.writeFileSync(path.join(fixture, 'sandbox-azdo'), `#!/usr/bin/env node\nrequire(${JSON.stringify(path.join(source, 'azdo.cjs'))});\n`, { mode: 0o755 });
  fs.writeFileSync(path.join(fixture, 'az'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify(args) + '\\n');
if (args[0] !== 'devops' || !['login', 'configure'].includes(args[1])) process.exit(99);
if (process.env.TEST_FAIL === args[1]) process.exit(23);
`, { mode: 0o755 });
  const saved = setup(['--persist'], `${organization}\n${pat}\n`);
  assert.equal(saved.status, 0, saved.stderr);
  assert.equal((saved.stdout + saved.stderr).includes(pat), false);
  assert.deepEqual(calls(), [['devops', 'configure', '--defaults', `organization=${organization}`]]);
  assert.equal(fs.readFileSync(settings, 'utf8'), `${pat}\n${organization}\n`);

  for (const input of ['', `${organization}\n`, `${organization}\n\n`, 'invalid-url\nreplacement\n']) {
    assert.notEqual(setup(['--persist'], input).status, 0);
    assert.equal(fs.readFileSync(settings, 'utf8'), `${pat}\n${organization}\n`);
  }
  const failedConfig = setup(['--persist'], `${organization}\nreplacement\n`, { TEST_FAIL: 'configure' });
  assert.equal(failedConfig.status, 23);
  assert.equal(fs.readFileSync(settings, 'utf8'), `${pat}\n${organization}\n`);
  assert.equal(setup(['--persist'], `${organization}\nreplacement\n`).status, 0);
  assert.equal(fs.readFileSync(settings, 'utf8'), `replacement\n${organization}\n`);
  assert.equal(setup([], `${organization}\n`, { TEST_FAIL: 'login' }).status, 23);
  assert.equal(fs.existsSync(settings), true, 'Failed login removed saved environment');

  fs.unlinkSync(log);
  const native = setup([], `${organization}\n`);
  assert.equal(native.status, 0, native.stderr);
  assert.deepEqual(calls(), [
    ['devops', 'login', '--organization', organization],
    ['devops', 'configure', '--defaults', `organization=${organization}`],
  ]);
  assert.equal(fs.existsSync(settings), false);
  assert.equal(setup(['--clear'], '').status, 0);
  fs.rmSync(path.dirname(settings), { recursive: true, force: true });
  const outsideHome = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-azdo-outside-'));
  try {
    fs.symlinkSync(outsideHome, path.dirname(settings));
    assert.notEqual(setup(['--persist'], `${organization}\n${pat}\n`).status, 0);
    assert.equal(fs.existsSync(path.join(outsideHome, 'environment')), false, 'PAT escaped the sandbox home through a symlink');
  } finally {
    fs.rmSync(outsideHome, { recursive: true, force: true });
  }
  console.log('Azure DevOps setup: native login, opt-in environment persistence, replacement and cleanup: OK');
} finally {
  fs.rmSync(fixture, { recursive: true, force: true });
}
