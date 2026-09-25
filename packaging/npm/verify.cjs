'use strict';
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

module.exports = function verifiedBinary() {
  const targets = {'linux/x64': 'linux-amd64', 'linux/arm64': 'linux-arm64',
    'win32/x64': 'windows-amd64.exe'};
  const target = targets[`${process.platform}/${process.arch}`];
  if (!target) throw new Error(`Unsupported platform: ${process.platform}/${process.arch}`);
  const name = `sandboxed-agents-${target}`;
  const directory = path.join(__dirname, 'artifacts');
  const entries = fs.readFileSync(path.join(directory, 'SHA256SUMS'), 'utf8').trim().split(/\r?\n/);
  const matches = entries.map(line => line.split(/\s+/)).filter(fields => fields[1] === name);
  if (matches.length !== 1 || !/^[a-f0-9]{64}$/.test(matches[0][0])) {
    throw new Error(`Missing or invalid checksum for ${name}`);
  }
  const binary = path.join(directory, name);
  const digest = crypto.createHash('sha256').update(fs.readFileSync(binary)).digest('hex');
  if (digest !== matches[0][0]) throw new Error(`Checksum mismatch for ${name}`);
  return binary;
};
