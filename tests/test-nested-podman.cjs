'use strict';
const assert = require('node:assert/strict');
const { subordinateRanges } = require('../src/container/nested-podman.cjs');

function checkMap(mapping) {
  const entries = mapping.trim().split('\n').map(line => line.trim().split(/\s+/).map(Number));
  const allocated = new Set();
  for (const line of subordinateRanges(mapping).trim().split('\n')) {
    const [user, start, length] = line.split(':');
    assert.equal(user, 'agent');
    for (let id = Number(start); id < Number(start) + Number(length); id++) {
      assert.notEqual(id, 0, 'Must not delegate container root');
      assert.notEqual(id, 1000, 'Must not delegate the agent identity');
      assert.equal(allocated.has(id), false, 'Overlapping ranges');
      assert.ok(entries.some(([first, , count]) => id >= first && id < first + count), 'Unmapped ID');
      allocated.add(id);
    }
  }
  assert.equal(allocated.size, 65535, 'Must support image UID/GID 65534 (nobody)');
}

// keep-id with 65536 host subordinate IDs; host user and subordinate IDs are
// deliberately different from container IDs. Never copy the parent column.
checkMap('0 1 1000\n1000 0 1\n1001 1001 64536\n');
checkMap('0 200000 1000\n1000 1234 1\n1001 201000 200000\n');
// Sparse mappings and distinct UID/GID maps must work independently.
checkMap('0 300000 500\n1000 300500 1\n2000 300501 70000\n');
checkMap('0 1 1000\n1000 0 1\n1001 1001 30000\n40000 31001 35536\n');
assert.throws(() => subordinateRanges('0 1 1000\n1000 0 1\n1001 1001 64535\n'), /at least 65535/);
assert.throws(() => subordinateRanges('0 0 1'), /at least 65535/);
for (const bad of ['', 'x y z', '0 1', '0 1 -2']) {
  assert.throws(() => subordinateRanges(bad), /Invalid/);
}
console.log('Nested Podman subordinate IDs: mapped, disjoint, sufficient, and root excluded: OK');
