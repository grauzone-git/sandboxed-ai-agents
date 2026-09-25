#!/usr/bin/env node
'use strict';
try {
  const binary = require('./verify.cjs')();
  const fs = require('node:fs');
  const path = require('node:path');
  const modules = path.dirname(__dirname);
  const prefix = path.dirname(modules);
  const directories = [path.join(modules, '.bin'), prefix];
  if (path.basename(prefix) === 'lib') directories.push(path.join(path.dirname(prefix), 'bin'));
  const launches = [path.resolve(process.argv[1]), __filename];
  for (const directory of directories) {
    for (const suffix of ['', '.cmd', '.ps1']) {
      const shim = path.join(directory, 'sandboxed-agents' + suffix);
      if (fs.existsSync(shim)) launches.push(shim);
    }
  }
  const result = require('node:child_process').spawnSync(binary, process.argv.slice(2), {
    stdio: 'inherit', env: {...process.env, SANDBOX_LAUNCH_PATHS: JSON.stringify(launches)},
  });
  if (result.error) throw result.error;
  if (result.signal) process.kill(process.pid, result.signal);
  else process.exitCode = result.status;
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
}
