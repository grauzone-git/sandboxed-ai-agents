// Persist the sandbox's explicitly configured PAT and its organization together.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function settingsPath(home = os.homedir()) {
  return path.join(home, '.config/sandbox-azdo/environment');
}

function readSettings(home) {
  try {
    const [pat, organization] = fs.readFileSync(settingsPath(home), 'utf8').split('\n');
    return { pat, organization };
  } catch (error) {
    if (error.code === 'ENOENT') return {};
    throw new Error('Cannot read saved Azure DevOps environment settings.');
  }
}

function saveSettings(pat, organization) {
  const file = settingsPath();
  const directory = path.dirname(file);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const relative = path.relative(fs.realpathSync(os.homedir()), fs.realpathSync(directory));
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('Azure DevOps environment settings must stay inside the sandbox home; remove the external directory symlink.');
  }
  fs.chmodSync(directory, 0o700);
  const temporary = fs.mkdtempSync(path.join(directory, '.setup-'));
  try {
    const pending = path.join(temporary, 'environment');
    fs.writeFileSync(pending, `${pat}\n${organization}\n`, { mode: 0o600, flag: 'wx' });
    fs.renameSync(pending, file);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

function clearSettings() {
  fs.rmSync(settingsPath(), { force: true });
}

module.exports = { readSettings, saveSettings, clearSettings };
