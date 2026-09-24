// Supervise loopback-only tool servers. Installation and selection stay in the manager.
const net = require('node:net');
const http = require('node:http');

function createServices(context) {
  const { entry, quiet, run, fail, requireEnabled, isTools, catalog, managerPath,
    tmuxArgs } = context;

  function sessionName(id) { return `service-${id}`; }
  function serviceAlive(id) { return quiet('tmux', [...tmuxArgs, 'has-session', '-t', `=${sessionName(id)}`]); }
  async function stopService(id, session = sessionName(id), socket = tmuxArgs, port = entry(id).port) {
    if (!quiet('tmux', [...socket, 'has-session', '-t', `=${session}`])) return;
    run('tmux', [...socket, 'kill-session', '-t', `=${session}`]);
    // Wait for the child to release its socket before a restart checks the port.
    for (let attempt = 0; attempt < 100; attempt++) {
      if (!(await canConnect(port))) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    fail(`${id} did not release its port after stopping.`);
  }
  async function serviceReady(id) {
    const info = entry(id);
    if (!info.healthPath) return canConnect(info.port);
    return new Promise(resolve => {
      const request = http.get({ host: '127.0.0.1', port: info.port, path: info.healthPath, timeout: 1000 }, response => {
        response.resume();
        resolve((info.healthStatuses ?? [200]).includes(response.statusCode));
      });
      request.on('timeout', () => request.destroy());
      request.on('error', () => resolve(false));
    });
  }
  async function canConnect(port) {
    return new Promise(resolve => {
      const socket = net.createConnection({ host: '127.0.0.1', port });
      const finish = success => { socket.destroy(); resolve(success); };
      socket.setTimeout(300);
      socket.once('connect', () => finish(true));
      socket.once('error', () => finish(false));
      socket.once('timeout', () => finish(false));
    });
  }
  async function startService(id, wait = true) {
    const info = entry(id);
    if (!info.service) fail(`${id} is not an automatically managed server.`);
    requireEnabled(id);
    if (!serviceAlive(id)) {
      if (await canConnect(info.port)) fail(`Port ${info.port} is already in use; refusing to adopt an unmanaged server.`);
      run('tmux', [...tmuxArgs, 'new-session', '-d', '-s', sessionName(id), '-c', '/workspace', managerPath, 'service-run', id]);
    }
    if (!wait) return;
    const timeout = info.readyTimeout ?? 30;
    const deadline = Date.now() + timeout * 1000;
    while (Date.now() < deadline) {
      if (!serviceAlive(id)) fail(`${id} server exited. Inspect its private log with sandbox NAME service ${id} logs.`);
      if (await serviceReady(id)) {
        console.log(`${id} server ready on container loopback:${info.port} (use SSH forwarding).`);
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    fail(`${id} server did not become ready within ${timeout} seconds; inspect its private log.`);
  }
  async function reconcileServices(enabled, restart = false, wait = true) {
    if (!isTools) await stopService('hermes'); // Retire the old agent-owned dashboard.
    for (const [id, info] of Object.entries(catalog)) {
      if (!info.service) continue;
      if (restart || !Object.hasOwn(enabled, id)) await stopService(id);
      if (Object.hasOwn(enabled, id)) await startService(id, wait);
    }
  }

  return { serviceAlive, serviceReady, startService, stopService, reconcileServices };
}

module.exports = { createServices };
