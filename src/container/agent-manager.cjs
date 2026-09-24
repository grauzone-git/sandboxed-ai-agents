// Runs exclusively as the unprivileged container user. No provider credentials
// are read, copied from the host, or supplied on installer command lines.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const childProcess = require('node:child_process');
const { readSettings } = require('./azdo-settings.cjs');

function createManager({
  isTools = false,
  home = os.homedir(),
  spawnSync = childProcess.spawnSync,
  spawn = childProcess.spawn,
} = {}) {
  const kind = isTools ? 'tool' : 'agent';
  const manager = isTools ? 'sandbox-tools' : 'sandbox-agents';
  const managerPath = `/usr/local/bin/${manager}`;
  const agentsCatalog = require('./agents.json');
  const toolsCatalog = require('./tools.json');
  const catalog = isTools ? toolsCatalog : agentsCatalog;
  const root = path.join(home, '.local/share', manager);
  const stateDir = path.join(home, '.local/state', manager);
  const stateFile = path.join(stateDir, 'config.json');
  const binDir = path.join(home, '.local/bin');
  const marker = `# Managed by ${manager}; use the sandbox ${kind} selection commands.`;
  const env = {
    ...process.env,
    PATH: `${binDir}:${home}/.dotnet/tools:/opt/agent-tools/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`,
    NPM_CONFIG_PREFIX: path.join(home, '.local'),
    NPM_CONFIG_CACHE: path.join(home, '.npm'),
    PLAYWRIGHT_BROWSERS_PATH: path.join(home, '.cache/ms-playwright'),
    DISABLE_AUTOUPDATER: '1',
  };
  const savedPat = readSettings(home).pat;
  if (env.AZURE_DEVOPS_EXT_PAT === undefined && savedPat) env.AZURE_DEVOPS_EXT_PAT = savedPat;
  const tmuxArgs = ['-L', manager];
  const fail = message => { throw new Error(message); };
  function run(command, args = [], options = {}) {
    const result = spawnSync(command, args, { env, stdio: 'inherit', ...options });
    if (result.error || result.status !== 0) {
      fail(`${path.basename(command)} failed (${result.error?.message || result.status || result.signal}).`);
    }
    return result.stdout?.toString().trim();
  }
  function quiet(command, args) {
    return spawnSync(command, args, { env, stdio: 'ignore' }).status === 0;
  }
  function readJson(file, fallback) {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
  }
  function writeJson(file, value) {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const temp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
    fs.renameSync(temp, file);
  }
  function selection(spec) {
    if (spec === 'all') spec = Object.keys(catalog).join(',');
    if (spec === 'none') return {};
    if (typeof spec !== 'string' || !spec) fail(`Provide a comma-separated ${kind} list, all, or none.`);
    const result = {};
    for (const part of spec.split(',')) {
      const [id, requested, ...rest] = part.trim().split('@');
      if (!Object.hasOwn(catalog, id)) fail(`Unknown ${kind}: ${id}`);
      if (Object.hasOwn(result, id)) fail(`Duplicate ${kind}: ${id}`);
      if (rest.length || (requested !== undefined && !/^[A-Za-z0-9][A-Za-z0-9._+/-]*$/.test(requested))) fail(`Invalid version/ref: ${part}`);
      if (catalog[id].agent && requested !== undefined && requested !== 'bundled') fail(`${id} uses its agent's version; select ${id} without a version pin.`);
      result[id] = requested ?? catalog[id].version;
    }
    return result;
  }
  function initialSelection(spec) {
    const enabled = selection(spec);
    if (!Object.keys(enabled).length) fail('Creating a sandbox requires at least one explicitly selected agent.');
    return enabled;
  }
  function current() {
    const state = readJson(stateFile, { enabled: {} });
    // Revalidate persisted state rather than trusting it as shell input.
    state.enabled = Object.keys(state.enabled).length
      ? selection(Object.entries(state.enabled).map(([id, version]) => `${id}@${version}`).join(',')) : {};
    return state;
  }
  function entry(id) {
    if (!Object.hasOwn(catalog, id)) fail(`Unknown ${kind}: ${id}`);
    return catalog[id];
  }
  function prefix(id) { entry(id); return path.join(root, id); }
  function executable(id) {
    const dependency = entry(id).agent;
    if (dependency) return path.join(home, '.local/share/sandbox-agents', dependency,
      dependency === 'hermes' ? 'repo/venv/bin/hermes' : `bin/${agentsCatalog[dependency].command}`);
    return path.join(prefix(id), id === 'hermes' ? 'repo/venv/bin/hermes' : `bin/${entry(id).command}`);
  }
  function installed(id) { return readJson(path.join(prefix(id), 'installed.json'), null); }
  function launcherPath(id) { return path.join(binDir, entry(id).command); }
  function exists(file) { try { fs.lstatSync(file); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } }
  function managed(file) {
    return exists(file) && !fs.lstatSync(file).isSymbolicLink() && fs.readFileSync(file, 'utf8').includes(marker);
  }
  function legacyGlobalInstall(id) {
    if (!isTools || !entry(id).package) return false;
    const file = launcherPath(id);
    if (!exists(file) || !fs.lstatSync(file).isSymbolicLink()) return false;
    const packageDir = path.join(home, '.local/lib/node_modules', entry(id).package);
    const meta = readJson(path.join(packageDir, 'package.json'), null);
    const bin = typeof meta?.bin === 'string' ? meta.bin : meta?.bin?.[entry(id).command];
    return meta?.name === entry(id).package && typeof bin === 'string'
      && fs.existsSync(file) && fs.existsSync(path.join(packageDir, bin))
      && fs.realpathSync(file) === fs.realpathSync(path.join(packageDir, bin));
  }
  function ensureLaunchersAllowed(ids) {
    for (const id of ids) {
      const file = launcherPath(id);
      if (exists(file) && !managed(file) && !legacyGlobalInstall(id)) fail(`${file} is an existing unmanaged command. Move/uninstall it before enabling ${id}.`);
    }
  }
  function syncLaunchers(enabled) {
    fs.mkdirSync(binDir, { recursive: true, mode: 0o755 });
    for (const id of Object.keys(catalog)) {
      const file = launcherPath(id);
      if (Object.hasOwn(enabled, id)) {
        if (exists(file) && !managed(file)) fail(`Refusing to overwrite unmanaged command: ${file}`);
        fs.writeFileSync(file, `#!/bin/sh\n${marker}\nexec ${managerPath} run ${id} "$@"\n`, { mode: 0o755 });
        fs.chmodSync(file, 0o755);
      } else if (managed(file)) {
        fs.unlinkSync(file);
      }
    }
  }
  const { install } = require('./installers.cjs').createInstaller({
    entry, installed, requireDependency, home, readJson, writeJson,
    prefix, executable, run, env, fail,
  });

  const { serviceAlive, serviceReady, startService, stopService, reconcileServices } =
    require('./services.cjs').createServices({
      entry, quiet, run, fail, requireEnabled, isTools, catalog, managerPath, tmuxArgs,
    });

  async function migrateT3() {
    const agentFile = path.join(home, '.local/state/sandbox-agents/config.json');
    const toolFile = path.join(home, '.local/state/sandbox-tools/config.json');
    const agents = readJson(agentFile, { enabled: {} });
    if (!Object.hasOwn(agents.enabled, 't3')) return;
    const tools = readJson(toolFile, { enabled: {} });
    await stopService('t3', 'service-t3', ['-L', 'sandbox-agents'], 3773);
    const oldPrefix = path.join(home, '.local/share/sandbox-agents/t3');
    const newPrefix = path.join(home, '.local/share/sandbox-tools/t3');
    if (fs.existsSync(oldPrefix) && !fs.existsSync(newPrefix)) {
      fs.mkdirSync(path.dirname(newPrefix), { recursive: true, mode: 0o700 });
      fs.renameSync(oldPrefix, newPrefix);
    }
    if (!Object.hasOwn(tools.enabled, 't3')) tools.enabled.t3 = agents.enabled.t3;
    writeJson(toolFile, tools);
    delete agents.enabled.t3;
    writeJson(agentFile, agents);
    const oldWrapper = path.join(binDir, 't3');
    if (exists(oldWrapper) && !fs.lstatSync(oldWrapper).isSymbolicLink()
        && fs.readFileSync(oldWrapper, 'utf8').includes('# Managed by sandbox-agents;')) fs.unlinkSync(oldWrapper);
    console.log('Migrated the saved T3 selection and installation to tools.');
  }
  async function apply(enabled, forceIds = []) {
    const removedTools = [];
    if (isTools) {
      for (const id of Object.keys(enabled)) requireDependency(id);
    } else {
      const dependents = selectedDependentTools();
      for (const id of dependents) {
        if (!Object.hasOwn(enabled, toolsCatalog[id].agent)) {
          if (toolsCatalog[id].disableWithAgent) removedTools.push(id);
          else fail(`Disable the ${id} tool before disabling the ${toolsCatalog[id].agent} agent.`);
        }
      }
      for (const id of dependents) {
        if (removedTools.includes(id)) continue;
        const agent = toolsCatalog[id].agent;
        if (forceIds.includes(agent) || installed(agent)?.requested !== enabled[agent] || !fs.existsSync(executable(agent))) {
          run('node', [__filename, '--tools', 'service', id, 'stop']);
        }
      }
    }
    ensureLaunchersAllowed(Object.keys(enabled));
    for (const [id, requested] of Object.entries(enabled)) install(id, requested, forceIds.includes(id));
    // Provision the replacement agent set first. A failed installation must not
    // disable a working UI. Disable dependent tools before committing the new
    // agent state so no saved tool is left pointing to a disabled agent.
    if (removedTools.length) {
      console.log(`Disabling dependent tools: ${removedTools.join(', ')}`);
      run('node', [__filename, '--tools', 'disable', removedTools.join(',')]);
    }
    if (isTools) {
      // Migrate the old forwarding helper's managed tmux session before starting
      // the tool service. Never stop an arbitrary listener on the same port.
      await stopService('tokentracker', 'tokentracker');
      await stopService('hermes-dashboard', 'service-hermes', ['-L', 'sandbox-agents']);
      for (const id of Object.keys(enabled)) {
        if (legacyGlobalInstall(id)) {
          run('npm', ['uninstall', '--global', '--prefix', path.join(home, '.local'), '--ignore-scripts', '--no-audit', '--no-fund', entry(id).package], { cwd: home });
        }
      }
    }
    // Commit the selection only once all installations succeeded. Failed attempts
    // can leave downloaded packages, but do not enable a partial requested set.
    writeJson(stateFile, { enabled });
    syncLaunchers(enabled);
    // Restart managed services after provisioning so they see current packages.
    await reconcileServices(enabled, true);
    if (!isTools && selectedDependentTools().length) run('node', [__filename, '--tools', 'refresh-dependent-tools']);
    list();
  }
  function selectedDependentTools() {
    return Object.keys(readJson(path.join(home, '.local/state/sandbox-tools/config.json'), { enabled: {} }).enabled)
      .filter(id => toolsCatalog[id]?.agent);
  }
  function requireDependency(id) {
    const dependency = entry(id).agent;
    if (!dependency) return;
    const agents = readJson(path.join(home, '.local/state/sandbox-agents/config.json'), { enabled: {} }).enabled;
    if (!Object.hasOwn(agents, dependency)) fail(`${id} requires the enabled ${dependency} agent. Use: sandbox NAME agents enable ${dependency}`);
    if (!fs.existsSync(executable(id))) fail(`${dependency} is missing. Re-enable that agent first.`);
  }
  function list() {
    const enabled = current().enabled;
    console.log(`${kind.toUpperCase().padEnd(20)}ENABLED  INSTALLED VERSION / REVISION`);
    for (const id of Object.keys(catalog)) {
      const record = installed(id);
      console.log(`${id.padEnd(20)}${(Object.hasOwn(enabled, id) ? 'yes' : 'no').padEnd(9)}${record?.resolved ?? 'not installed'}`);
    }
  }
  function requireEnabled(id) {
    entry(id);
    if (!Object.hasOwn(current().enabled, id)) fail(`${id} is disabled. Enable it with: sandbox NAME ${kind}s enable ${id}`);
    requireDependency(id);
    const command = executable(id);
    if (!fs.existsSync(command)) fail(`${id} is missing. Reapply its agent selection to install it.`);
    return command;
  }
  async function terminalSession(id) {
    requireEnabled(id); // Check even when attaching to a retained tmux session.
    let command = [managerPath, 'run', id];
    // Harness ships headless/SDK profiles, but no interactive terminal UI.
    // Keep the shortcut useful without implicitly starting its optional web UI.
    if (id === 'deepseek') command = ['/bin/bash', '-lc', 'dsh --help; exec /bin/bash -l'];
    if (id === 't3') {
      // Use the locked service command; never start a second T3 server or hold
      // the installation lock throughout an interactive terminal attachment.
      run(managerPath, ['service', 't3', 'start']);
      command = ['tail', '-n', '80', '-F', path.join(stateDir, 't3.log')];
    }
    run('tmux', ['-L', `sandbox-${kind}-terminals`, 'new-session', '-A', '-s', id === 'deepseek' ? 'deepseek-cli' : id, '-c', '/workspace', ...command]);
  }
  async function setup(id, ...extra) {
    if (isTools && id === 'azure') {
      return waitForChild(spawn('/opt/az/bin/python3', ['-B', '/usr/local/lib/sandbox-agents/azure_setup.py', ...extra], { env, stdio: 'inherit' }));
    }
    if (isTools && id === 'azdo' && (extra.length === 0 || (extra.length === 1 && ['--persist', '--clear'].includes(extra[0])))) {
      return waitForChild(spawn('/bin/bash', ['/usr/local/lib/sandbox-agents/setup-azdo.sh', ...extra], { env, stdio: 'inherit' }));
    }
    if (id === 'azdo') fail('Usage: sandbox-tools setup azdo [--persist|--clear]');
    if (!isTools || id !== 't3' || extra.length) fail('Usage: sandbox-tools setup t3');
    requireEnabled(id);
    console.log(entry(id).setupMessage);
    const code = await launch(id, entry(id).setup);
    if (code !== 0) return;
    // Service changes use the wrapper's lock, after interactive authorization ends.
    return waitForChild(spawn(managerPath, ['service', id, 'restart'], { env, stdio: 'inherit' }));
  }
  async function login(id, ...extra) {
    if (isTools) {
      if (id !== 'github' || extra.length) fail('Usage: sandbox-tools login github');
      console.log('Starting GitHub login inside this sandbox. Open the printed URL in your desktop browser and enter the one-time code. Keep this terminal open until login completes.');
      const code = await waitForChild(spawn('gh', ['auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web'], { env, stdio: 'inherit' }));
      if (code !== 0) return;
      const setupCode = await waitForChild(spawn('gh', ['auth', 'setup-git', '--hostname', 'github.com'], { env, stdio: 'inherit' }));
      if (setupCode !== 0) return;
      return waitForChild(spawn('/bin/bash', ['/usr/local/lib/sandbox-agents/git-identity.sh'], { env, stdio: 'inherit' }));
    }
    if (!id || extra.length) fail('Usage: sandbox-agents login codex|claude|opencode|copilot|hermes');
    const info = entry(id);
    if (!info.login) fail(`Managed login is not supported for ${id}. Use sandbox NAME run ${id} with its own authentication command.`);
    requireEnabled(id);
    console.log(info.loginMessage ?? `Starting ${id} login inside this sandbox. Follow the prompts in this terminal.`);
    // Inherit the terminal directly. Do not capture codes/credentials in manager
    // logs or hold the installation lock while waiting for browser authorization.
    return launch(id, info.login);
  }
  async function launch(id, args, asService = false) {
    const info = entry(id);
    const command = requireEnabled(id);
    let log;
    if (asService) {
      if (!info.service) fail(`${id} has no managed server.`);
      fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
      log = fs.openSync(path.join(stateDir, `${id}.log`), 'a', 0o600);
      args = info.service;
    } else args = [...(info.args ?? []), ...args];
    const child = spawn(command, args, { env, stdio: asService ? ['ignore', log, log] : 'inherit' });
    if (log !== undefined) fs.closeSync(log);
    return waitForChild(child);
  }
  async function waitForChild(child) {
    const handlers = new Map(['SIGTERM', 'SIGINT', 'SIGHUP'].map(signal => [signal, () => child.kill(signal)]));
    for (const [signal, handler] of handlers) process.on(signal, handler);
    try {
      return await new Promise((resolve, reject) => {
        child.once('error', reject);
        child.once('exit', (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0); resolve(process.exitCode); });
      });
    } finally {
      for (const [signal, handler] of handlers) process.removeListener(signal, handler);
    }
  }
  async function main() {
    if (process.getuid() === 0) fail('Run as the agent user, not root.');
    const [command = 'list', ...args] = process.argv.slice(isTools ? 3 : 2);
    if (['init', 'set', 'enable', 'disable', 'update', 'boot', 'service'].includes(command)) await migrateT3();
    switch (command) {
      case 'init': return apply(isTools ? (args[0] === undefined ? current().enabled : selection(args[0])) : initialSelection(args[0]));
      case 'set': return apply(selection(args[0]));
      case 'enable': return apply({ ...current().enabled, ...selection(args[0]) });
      case 'disable': {
        const enabled = current().enabled;
        for (const id of Object.keys(selection(args[0]))) delete enabled[id];
        return apply(enabled);
      }
      case 'update': {
        const enabled = current().enabled;
        const ids = args[0] === 'all' ? Object.keys(enabled) : Object.keys(selection(args[0]));
        for (const id of ids) if (!Object.hasOwn(enabled, id)) fail(`${id} is disabled.`);
        return apply(enabled, ids);
      }
      case 'boot': {
        const enabled = current().enabled;
        syncLaunchers(enabled);
        return reconcileServices(enabled, false, false);
      }
      case 'list': return list();
      case 'run': return launch(args[0], args.slice(1));
      case 'login': return login(...args);
      case 'setup': return setup(...args);
      case 'session': return terminalSession(args[0]);
      case 'refresh-dependent-tools': {
        if (!isTools) return;
        for (const id of selectedDependentTools()) {
          install(id, current().enabled[id]);
          await startService(id);
        }
        return;
      }
      case 'service-run': return launch(args[0], [], true);
      case 'check': {
        for (const id of Object.keys(current().enabled)) {
          console.log(`Checking ${id}…`);
          run(executable(id), [id === 'deepseek' || entry(id).agent === 'deepseek' ? '--help' : '--version'], { timeout: 60000, stdio: ['ignore', 'pipe', 'pipe'] });
          if (entry(id).service && (!serviceAlive(id) || !(await serviceReady(id)))) fail(`${id} server is not ready.`);
        }
        console.log(`Enabled ${kind} executables and managed servers: OK`);
        return;
      }
      case 'service': {
        const [id, operation = 'status'] = args;
        if (!entry(id).service) fail(`${id} is not a managed server.`);
        if (operation === 'stop') return stopService(id);
        if (operation === 'restart') { await stopService(id); return startService(id); }
        if (operation === 'start') return startService(id);
        if (operation === 'logs') return run('tail', ['-n', '80', path.join(stateDir, `${id}.log`)]);
        if (operation === 'status') { console.log(`${id}: ${serviceAlive(id) ? 'running' : 'stopped'}; container loopback:${entry(id).port}`); return; }
        fail('Choose status, start, stop, restart, or logs.');
      }
      default: fail(`Unknown manager command: ${command}`);
    }
  }

  return { main, selection, initialSelection, install, apply, terminalSession, login, setup };
}

if (require.main === module) {
  const isTools = process.argv[2] === '--tools';
  createManager({ isTools }).main().catch(error => {
    console.error(`${isTools ? 'tool' : 'agent'} setup failed: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { createManager };
