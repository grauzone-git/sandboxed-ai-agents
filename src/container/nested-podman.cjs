// Allocate subordinate IDs from this sandbox's actual namespace, never host IDs.
'use strict';
const fs = require('node:fs');

function subordinateRanges(mapping) {
  const ranges = [];
  let remaining = 65535;
  const entries = mapping.trim().split('\n').map(line => {
    const fields = line.trim().split(/\s+/).map(Number);
    if (fields.length !== 3 || fields.some(n => !Number.isSafeInteger(n) || n < 0) || fields[2] === 0) {
      throw new Error('Invalid outer user namespace mapping');
    }
    return fields;
  }).sort((a, b) => a[0] - b[0]);
  for (const [start, , count] of entries) {
    // Keep container root and the agent itself out of subordinate ranges.
    // Splitting around UID/GID 1000 supports a host with the usual 65536 IDs.
    for (const [low, high] of [[1, 1000], [1001, 4294967295]]) {
      const first = Math.max(start, low);
      const length = Math.min(start + count, high) - first;
      const take = Math.min(remaining, Math.max(0, length));
      if (take) ranges.push(`agent:${first}:${take}`);
      remaining -= take;
    }
  }
  if (remaining) {
    throw new Error('Nested Podman needs at least 65535 subordinate IDs inside the sandbox. '
      + 'Allocate at least 65536 IDs to the host user in both /etc/subuid and /etc/subgid, '
      + 'then recreate the sandbox with the updated mapping.');
  }
  return ranges.join('\n') + '\n';
}

function setup() {
  for (const device of ['/dev/fuse', '/dev/net/tun']) {
    if (!fs.statSync(device).isCharacterDevice()) throw new Error(`Nested Podman requires ${device}`);
  }
  // Validate both maps before changing either file. These paths are root-owned;
  // no root writes follow paths in the persisted, agent-controlled home volume.
  const uid = subordinateRanges(fs.readFileSync('/proc/self/uid_map', 'utf8'));
  const gid = subordinateRanges(fs.readFileSync('/proc/self/gid_map', 'utf8'));
  fs.writeFileSync('/etc/subuid', uid, { mode: 0o644 });
  fs.writeFileSync('/etc/subgid', gid, { mode: 0o644 });
}

module.exports = { subordinateRanges };
if (require.main === module) {
  try { setup(); }
  catch (error) { console.error(`Nested Podman setup failed: ${error.message}`); process.exitCode = 1; }
}
