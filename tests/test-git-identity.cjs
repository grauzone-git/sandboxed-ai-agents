// Exercise interactive Git identity setup without credentials or network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-git-identity-'));
const config = path.join(fixture, '.gitconfig');
const env = {
  ...process.env,
  HOME: fixture,
  XDG_CONFIG_HOME: path.join(fixture, '.config'),
  GIT_CONFIG_GLOBAL: config,
  GIT_CONFIG_NOSYSTEM: '1',
  GIT_CONFIG_COUNT: '0',
};
const script = path.resolve(__dirname, '../src/container/git-identity.sh');
const setup = input => spawnSync('/bin/bash', [script], { env, cwd: fixture, input, encoding: 'utf8' });
const git = args => {
  const result = spawnSync('git', args, { env, cwd: fixture, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
};

try {
  assert.notEqual(setup('Example User\n').status, 0);
  assert.equal(fs.existsSync(config), false, 'EOF partially wrote identity');

  const name = 'Example "User" $(false)';
  const email = '123+example@users.noreply.github.com';
  const result = setup(`\n   \n${name}\n\n${email}\n`);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /Enter a nonempty value/);
  assert.equal(git(['config', '--global', '--get', 'user.name']), name);
  assert.equal(git(['config', '--global', '--get', 'user.email']), email);

  const saved = fs.readFileSync(config, 'utf8');
  assert.notEqual(setup('Replacement\n').status, 0);
  assert.equal(fs.readFileSync(config, 'utf8'), saved, 'Cancelled setup changed existing identity');
  assert.equal(setup('Replacement\nreplacement@example.com\n').status, 0);
  assert.equal(git(['config', '--global', '--get', 'user.name']), 'Replacement');

  git(['init', '--quiet']);
  assert.equal(git(['config', '--get', 'user.email']), 'replacement@example.com');
  git(['config', '--local', 'user.email', 'local@example.com']);
  assert.equal(git(['config', '--get', 'user.email']), 'local@example.com');
  assert.equal(git(['config', '--global', '--get', 'user.email']), 'replacement@example.com');
  console.log('Git identity prompts, global persistence, cancellation, and local overrides: OK');
} finally {
  fs.rmSync(fixture, { recursive: true, force: true });
}
