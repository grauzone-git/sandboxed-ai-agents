// Verify enabled tool restoration without Podman, systemd, or installed tools.
const assert = require('node:assert/strict');
const { createServices } = require('../src/container/services.cjs');
const tools = require('../src/container/tools.json');

async function test() {
  const calls = [];
  const sessions = new Set();
  // Port zero cannot be an existing server; no real workspace ports are probed.
  const catalog = { t3: { ...tools.t3, port: 0 } };
  let enabled = { t3: 'latest' };
  const services = createServices({
    isTools: true,
    catalog,
    entry: id => catalog[id],
    managerPath: '/usr/local/bin/sandbox-tools',
    tmuxArgs: ['-L', 'sandbox-tools'],
    fail: message => { throw new Error(message); },
    requireEnabled: id => assert.ok(Object.hasOwn(enabled, id)),
    quiet: (command, args) => {
      assert.equal(command, 'tmux');
      return sessions.has(args.at(-1).replace(/^=/, ''));
    },
    run: (command, args) => {
      calls.push([command, args]);
      if (args.includes('new-session')) sessions.add(args[args.indexOf('-s') + 1]);
      if (args.includes('kill-session')) sessions.delete(args.at(-1).replace(/^=/, ''));
    },
  });

  // Same restoration path as the manager's boot command.
  await services.reconcileServices(enabled, false, false);
  assert.deepEqual(calls, [['tmux', ['-L', 'sandbox-tools', 'new-session', '-d', '-s', 'service-t3', '-c', '/workspace', '/usr/local/bin/sandbox-tools', 'service-run', 't3']]]);
  await services.reconcileServices(enabled, false, false);
  assert.equal(calls.length, 1, 'Repeated boot started a duplicate server');

  sessions.clear(); // Container stop discards the tmux server.
  await services.reconcileServices(enabled, false, false);
  assert.equal(calls.length, 2, 'Container restart did not restore T3');
  assert.deepEqual(calls[1], calls[0]);

  enabled = {};
  await services.reconcileServices(enabled, false, false);
  assert.equal(sessions.size, 0);
  calls.length = 0;
  await services.reconcileServices(enabled, false, false);
  assert.equal(calls.length, 0, 'Disabled T3 started on boot');
  assert.deepEqual(tools.t3.service, ['serve', '--host', '127.0.0.1', '--port', '3773']);
  console.log('T3 service restoration, restart, idempotence, and disabled selection: OK');
}

test().catch(error => { console.error(error); process.exitCode = 1; });
