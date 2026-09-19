#!/bin/bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Refuse a persistent runtime directory: stale libpod PID/mount state breaks
# nested Podman after an outer restart. The launcher provides this tmpfs.
if [[ $(findmnt --noheadings --output FSTYPE --mountpoint /run/user/1000) != tmpfs ]]; then
    echo 'Nested Podman requires a tmpfs at /run/user/1000; recreate this sandbox with the current launcher.' >&2
    exit 1
fi
/usr/local/bin/node /usr/local/lib/sandbox-agents/nested-podman.cjs
install -d -m 0755 /run/user
install -d -m 0700 -o 1000 -g 1000 /run/user/1000
exec /usr/local/sbin/agent-entrypoint "$@"
