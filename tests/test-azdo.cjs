// Exercise the container command using a fake Azure CLI and dummy PATs only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-azdo-'));
const helper = path.resolve(__dirname, '../src/container/azdo.cjs');
const token = 'dummy-$();`literal`" token\nnext';
try {
  fs.writeFileSync(path.join(fixture, 'az'), `#!/usr/bin/env node
const assert = require('node:assert/strict');
const fs = require('node:fs');
assert.equal(process.env.AZURE_DEVOPS_EXT_PAT, ${JSON.stringify(token)});
assert.deepEqual(process.argv.slice(2), ['repos', 'list', '--organization', 'https://dev.azure.com/contoso', '--project', 'Project with spaces']);
assert.notEqual(process.env.AZURE_CONFIG_DIR, process.env.HOME + '/.azure');
assert.equal(process.env.AZURE_LOGGING_ENABLE_LOG_FILE, 'false');
assert.equal(process.env.AZURE_CORE_COLLECT_TELEMETRY, 'false');
fs.writeFileSync(process.env.HOME + '/config-path', process.env.AZURE_CONFIG_DIR);
console.log('projects: []');
if (process.env.TEST_AZ_ERROR) {
  console.error('TF400813: invalid, expired or insufficient-permission PAT ' + process.env.AZURE_DEVOPS_EXT_PAT);
  console.error(Buffer.from(':' + process.env.AZURE_DEVOPS_EXT_PAT).toString('base64'));
  process.exit(23);
}
`, { mode: 0o755 });
  const args = ['repos', 'list', '--organization', 'https://dev.azure.com/contoso', '--project', 'Project with spaces'];
  const run = (options = {}) => spawnSync(process.execPath, [helper, ...args], {
    encoding: 'utf8', env: { ...process.env, HOME: fixture, PATH: `${fixture}:${process.env.PATH}`, AZURE_DEVOPS_EXT_PAT: token }, ...options,
  });
  const result = run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'projects: []\n');
  assert.equal(fs.existsSync(fs.readFileSync(path.join(fixture, 'config-path'), 'utf8')), false);
  assert.equal(fs.existsSync(path.join(fixture, '.azure')), false);
  const failure = run({ env: { ...process.env, HOME: fixture, PATH: `${fixture}:${process.env.PATH}`, AZURE_DEVOPS_EXT_PAT: token, TEST_AZ_ERROR: '1' } });
  assert.equal(failure.status, 23);
  assert.match(failure.stderr, /TF400813/);
  assert.equal(failure.stderr.includes(token), false);
  assert.equal(failure.stderr.includes(Buffer.from(':' + token).toString('base64')), false);
  const transport = spawnSync(process.execPath, [helper, '--pat-stdin', ...args], {
    encoding: 'utf8', input: JSON.stringify(token),
    env: { ...process.env, HOME: fixture, PATH: `${fixture}:${process.env.PATH}`, AZURE_DEVOPS_EXT_PAT: 'replaced-dummy' },
  });
  assert.equal(transport.status, 0, transport.stderr);
  for (const value of ['', undefined]) {
    const missing = run({ env: { ...process.env, HOME: fixture, PATH: `${fixture}:${process.env.PATH}`, AZURE_DEVOPS_EXT_PAT: value } });
    assert.notEqual(missing.status, 0);
    assert.match(missing.stderr, /nonempty AZURE_DEVOPS_EXT_PAT/);
    assert.equal(missing.stdout, '');
  }
  const malformed = spawnSync(process.execPath, [helper, '--pat-stdin', ...args], {
    encoding: 'utf8', input: token, env: { ...process.env, HOME: fixture },
  });
  assert.notEqual(malformed.status, 0);
  assert.equal(malformed.stderr.includes('dummy-'), false);
  for (const rejected of [
    ['login'], ['devops', 'login'], ['devops', 'logout'], ['devops', 'configure'],
    [...args, '--debug'], [...args, '--debug=true'], [...args, '--verbose'],
    [...args, '--query', token], ['devops', 'project', 'list'],
  ]) {
    const denied = spawnSync(process.execPath, [helper, ...rejected], {
      encoding: 'utf8', env: { ...process.env, HOME: fixture, PATH: `${fixture}:${process.env.PATH}`, AZURE_DEVOPS_EXT_PAT: token },
    });
    assert.notEqual(denied.status, 0);
    assert.match(denied.stderr, /Error:/);
    assert.equal(denied.stderr.includes('dummy-'), false);
  }
  console.log('Azure DevOps PAT command: OK');
} finally {
  fs.rmSync(fixture, { recursive: true, force: true });
}
