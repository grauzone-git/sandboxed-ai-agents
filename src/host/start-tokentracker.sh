#!/usr/bin/env bash
# Sent over SSH by the host launcher; no image upgrade is required.
set -euo pipefail
# Managed selections take precedence even if a cached/legacy executable exists.
if command -v sandbox-tools >/dev/null; then
    exec sandbox-tools service tokentracker start
fi
ready() { curl --max-time 2 --fail --silent --output /dev/null http://127.0.0.1:7680/; }
ready && exit 0
command -v tokentracker >/dev/null || {
    echo 'TokenTracker is not installed. Inside the sandbox run: npm install --global tokentracker-cli' >&2
    exit 1
}
umask 077
state="$HOME/.local/state/sandbox-agents"
mkdir -p "$state"
exec 9>"$state/tokentracker.lock"
flock -w 90 9
ready && exit 0
if ! tmux -L sandbox-tools has-session -t '=tokentracker' 2>/dev/null; then
    # TokenTracker can kill an existing listener when --port is explicit.
    # Refuse startup if that port is occupied by an unready/unrelated service.
    if node -e 'const s=require("node:net").connect(7680,"127.0.0.1"); s.setTimeout(1000); s.on("connect",()=>{s.destroy();process.exit(0)}); s.on("error",()=>process.exit(1)); s.on("timeout",()=>process.exit(0));'; then
        echo 'Port 7680 is occupied but its HTTP server is not ready; refusing to replace it.' >&2
        exit 1
    fi
    echo 'Starting TokenTracker in a persistent sandbox tmux session…'
    tmux -L sandbox-tools new-session -d -s tokentracker -c /workspace \
        /bin/bash -c 'umask 077; exec tokentracker serve --port 7680 --no-open >> "$HOME/.local/state/sandbox-agents/tokentracker.log" 2>&1' 9>&-
fi
deadline=$((SECONDS + 90))
while (( SECONDS < deadline )); do
    ready && exit 0
    if ! tmux -L sandbox-tools has-session -t '=tokentracker' 2>/dev/null; then
        echo "TokenTracker exited. Inspect $state/tokentracker.log inside the sandbox." >&2
        exit 1
    fi
    sleep 1
done
echo "TokenTracker did not become ready. Inspect $state/tokentracker.log inside the sandbox." >&2
exit 1
