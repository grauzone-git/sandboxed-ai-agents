'use strict';
try {
  const binary = require('./verify.cjs')();
  if (process.platform !== 'win32') require('node:fs').chmodSync(binary, 0o755);
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
}
