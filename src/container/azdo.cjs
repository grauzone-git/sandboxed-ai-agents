#!/usr/bin/env node
// Run Azure DevOps with an invocation-scoped PAT and no saved Azure login state.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function main(args) {
  let token = process.env.AZURE_DEVOPS_EXT_PAT;
  if (args[0] === '--pat-stdin') {
    args = args.slice(1);
    try { token = JSON.parse(fs.readFileSync(0, 'utf8')); }
    catch { throw new Error('Invalid PAT input; use the host azdo --pat-env command.'); }
  }
  if (typeof token !== 'string' || !token || token.includes('\0')) {
    throw new Error('Set a nonempty AZURE_DEVOPS_EXT_PAT in this session, then retry.');
  }
  if (!['devops', 'boards', 'repos', 'pipelines', 'artifacts'].includes(args[0]) || args.length < 2
      || (args[0] === 'devops' && ['login', 'logout', 'configure'].includes(args[1]))) {
    throw new Error('Use an Azure DevOps operation; login, logout and configure are not supported by this PAT-only command.');
  }
  if (args.some(arg => token && arg.includes(token))) {
    throw new Error('Supply the PAT only through the environment, never in arguments.');
  }
  if (args.some(arg => {
    const option = arg.split('=')[0];
    return option.startsWith('--') && option.length > 2
      && ('--debug'.startsWith(option) || '--verbose'.startsWith(option));
  })) {
    throw new Error('Debug and verbose output are disabled for PAT commands.');
  }
  const organization = args.findIndex(arg => arg === '--organization' || arg === '--org');
  if (organization < 0 || !args[organization + 1] || args[organization + 1].startsWith('-')) {
    throw new Error('Supply --organization URL for this invocation; saved Azure defaults are not used.');
  }
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-azdo-'));
  try {
    const result = spawnSync('az', args, {
      env: { ...process.env, AZURE_DEVOPS_EXT_PAT: token, AZURE_CONFIG_DIR: directory,
        AZURE_CORE_COLLECT_TELEMETRY: 'false', AZURE_LOGGING_ENABLE_LOG_FILE: 'false' },
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 64 * 1024 * 1024,
    });
    const secrets = [token, Buffer.from(':' + token).toString('base64'),
      Buffer.from(token).toString('base64'), JSON.stringify(token).slice(1, -1), encodeURIComponent(token)];
    const redact = output => secrets.reduce((text, secret) => text.split(secret).join('[REDACTED]'), output || '');
    process.stdout.write(redact(result.stdout));
    process.stderr.write(redact(result.stderr));
    if (result.error) throw new Error('Unable to run az; check Azure CLI installation and output size.');
    return result.status ?? 1;
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

try { process.exitCode = main(process.argv.slice(2)); }
catch (error) { console.error(`Error: ${error.message}`); process.exitCode = 1; }
