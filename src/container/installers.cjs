// Installation and cache policy. The manager supplies state and process helpers.
const fs = require('node:fs');
const path = require('node:path');

function createInstaller(context) {
  const { entry, installed, requireDependency, home, readJson, writeJson,
    prefix, executable, run, env, fail } = context;

  function install(id, requested, force = false) {
    const info = entry(id);
    const record = installed(id);
    if (info.agent) {
      requireDependency(id);
      const dependency = readJson(path.join(home, '.local/share/sandbox-agents', info.agent, 'installed.json'), null);
      if (!dependency?.resolved) fail(`Missing installation record for ${info.agent}. Re-enable that agent first.`);
      if (id === 'hermes-dashboard') prepareHermesDashboard(dependency.resolved, force);
      if (force || record?.resolved !== dependency.resolved || record?.requested !== requested) {
        writeJson(path.join(prefix(id), 'installed.json'), { requested, resolved: dependency.resolved, source: `agent:${info.agent}` });
      }
      return;
    }
    const sameInstaller = info.package ? record?.package === info.package : record?.source === (id === 't3' ? 't3.codes' : 'NousResearch/hermes-agent');
    if (!force && sameInstaller && record?.requested === requested && fs.existsSync(executable(id))) {
      return;
    }
    console.log(`Installing ${id}@${requested} into the sandbox home…`);
    const destination = prefix(id);
    fs.mkdirSync(destination, { recursive: true, mode: 0o700 });
    if (info.package) {
      const scripts = [info.package, ...(info.allowScripts ?? [])].join(',');
      run('npm', ['install', '--global', '--prefix', destination, '--no-audit', '--no-fund', `--allow-scripts=${scripts}`, `${info.package}@${requested}`], { cwd: home });
      const meta = readJson(path.join(destination, 'lib/node_modules', info.package, 'package.json'));
      if (!fs.existsSync(executable(id))) fail(`${id} installation did not produce ${info.command}.`);
      writeJson(path.join(destination, 'installed.json'), { requested, resolved: meta.version, package: info.package });
    } else if (id === 't3') {
      const installer = path.join(destination, 'install.sh');
      run('curl', ['-fsSL', info.installer, '-o', installer], { cwd: home });
      const installEnv = { ...env, T3CODE_HOME: path.join(home, '.t3'), T3CODE_INSTALL_BIN_DIR: path.join(destination, 'bin'), T3CODE_CHANNEL: 'stable' };
      if (requested === 'latest') delete installEnv.T3CODE_VERSION;
      else installEnv.T3CODE_VERSION = requested;
      run('/bin/sh', [installer], { cwd: home, env: installEnv });
      const resolved = run(executable(id), ['--version'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
      writeJson(path.join(destination, 'installed.json'), { requested, resolved, source: 't3.codes' });
    } else {
      // A failed update must not leave a successful dashboard stamp for a
      // checkout whose Python/frontend files may already have changed.
      fs.rmSync(path.join(destination, 'dashboard.json'), { force: true });
      const installer = path.join(destination, 'install.sh');
      run('curl', ['-fsSL', info.installer, '-o', installer], { cwd: home });
      const args = [installer, '--dir', path.join(destination, 'repo'), '--skip-setup', '--non-interactive', '--skip-browser', '--skip-computer-use'];
      if (/^[a-fA-F0-9]{40}$/.test(requested)) args.push('--commit', requested, '--force-commit');
      else args.push('--branch', requested);
      // Bootstrap through upstream's stage API. Its monolithic install
      // requires a C++ compiler for optional Node/desktop components. The venv
      // supplies the official CLI entrypoint without publishing extra launchers.
      for (const stage of ['repository', 'venv', 'python-deps', 'config', 'complete']) {
        run('/bin/bash', [...args, '--stage', stage], { cwd: home, stdio: ['ignore', 'inherit', 'inherit'] });
      }
      if (!fs.existsSync(executable(id))) fail('Hermes installation did not create its CLI entrypoint.');
      const commit = run('git', ['-C', path.join(destination, 'repo'), 'rev-parse', 'HEAD'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
      writeJson(path.join(destination, 'installed.json'), { requested, resolved: commit, source: 'NousResearch/hermes-agent' });
    }
  }
  function prepareHermesDashboard(revision, force = false) {
    const hermesRoot = path.join(home, '.local/share/sandbox-agents/hermes');
    const repo = path.join(hermesRoot, 'repo');
    const stamp = path.join(hermesRoot, 'dashboard.json');
    const index = path.join(repo, 'hermes_cli/web_dist/index.html');
    if (!force && readJson(stamp, null)?.revision === revision && fs.existsSync(index)) return;
    console.log('Preparing the Hermes dashboard in the sandbox home…');
    // Upstream scopes npm to the web/TUI workspaces, excluding Electron and
    // desktop native modules. Build during provisioning, not service startup.
    run(path.join(repo, 'venv/bin/python'), [path.join(__dirname, 'prepare-hermes-dashboard.py'), repo], { cwd: repo });
    if (!fs.existsSync(index)) fail('Hermes dashboard build did not produce index.html.');
    writeJson(stamp, { revision });
  }

  return { install };
}

module.exports = { createInstaller };
