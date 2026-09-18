#!/bin/bash
set -euo pipefail
# Never resolve root commands through the persisted, agent-writable npm bin path.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
install -d -m 0755 /run/sshd
# Only fix mount roots, never recursively chown the workspace or persisted home.
chown 1000:1000 /home/agent
chmod 0750 /home/agent
# Persisted contents are agent-controlled: create subdirectories without root rights.
setpriv --reuid=1000 --regid=1000 --clear-groups mkdir -p /home/agent/.local/bin
install -d -m 0755 /var/lib/agent-sshd
if [[ ! -f /var/lib/agent-sshd/ssh_host_ed25519_key ]]; then
    ssh-keygen -q -t ed25519 -N '' -f /var/lib/agent-sshd/ssh_host_ed25519_key
fi
if [[ ! -f /var/lib/agent-sshd/authorized_keys ]]; then
    install -m 0644 /dev/null /var/lib/agent-sshd/authorized_keys
fi
/usr/sbin/sshd -t
# The launcher applies the requested selection on first creation. Do not start
# a retained home's old services before an explicit --agents selection is applied.
# /run is in this container's writable layer: retained on start, new on recreate.
if [[ -e /run/agent-booted ]]; then
    # Keep SSH available for repairs if persisted agent configuration is broken.
    setpriv --reuid=1000 --regid=1000 --clear-groups /usr/local/bin/sandbox-agents boot \
        || printf 'Agent service restore failed; connect over SSH to repair it.\n' >&2
    setpriv --reuid=1000 --regid=1000 --clear-groups /usr/local/bin/sandbox-tools boot \
        || printf 'Tool service restore failed; connect over SSH to repair it.\n' >&2
else
    touch /run/agent-booted
fi
exec /usr/sbin/sshd -D -e
