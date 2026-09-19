#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
IMAGE=${SANDBOX_IMAGE:-localhost/agent-sandbox:dev}
LABEL=io.sandboxed-agents.project
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
for module in args podman ssh lifecycle; do
    source "$ROOT/src/host/lib/$module.sh"
done
parse_cli_args "$@"
set -- "${CLI_ARGS[@]}"
if [[ $action == update ]]; then
    # Help and missing-argument errors do not need a running Podman service.
    if [[ $# -gt 0 && $1 != --help && $1 != -h ]]; then require_podman; fi
    exec python3 -B "$ROOT/src/host/update.py" "$@"
fi
handle_local_ssh_config "$@"
require_podman
if [[ $action == build ]]; then
    exec podman build --pull=always -t "$IMAGE" -f "$ROOT/src/container/Containerfile" "$@" "$ROOT/src/container"
fi

case "$action" in
    up) create_sandbox "$@" ;;
    start) start_sandbox "$@" ;;
    ssh-config)
        owned
        wait_for_ssh
        configure_ssh
        install_ssh_include
        ;;
    stop) owned; podman stop "$NAME" ;;
    remove) remove_sandbox "$@" ;;
    shell) owned; connect -t "$NAME" 'cd /workspace && exec bash -l' ;;
    copilot|claude|codex|hermes|opencode|t3|deepseek)
        owned
        manager_command=/usr/local/bin/sandbox-agents
        [[ $action != t3 ]] || manager_command=/usr/local/bin/sandbox-tools
        connect -t "$NAME" "$manager_command session $action"
        ;;
    agents|tools)
        [[ $# -ge 1 ]] || fail "Usage: ./sandbox $action NAME [list|check|set|enable|disable|update LIST|login TARGET]"
        manager_function=agent_manager
        [[ $action != tools ]] || manager_function=tool_manager
        operation=${2:-list}
        case "$operation" in
            login) ;; # Validated before contacting Podman.
            list|check) [[ $# -le 2 ]] || fail 'Unexpected argument.' ;;
            set|enable|disable|update)
                [[ $# -eq 3 ]] || fail "Usage: ./sandbox $action NAME $operation LIST"
                spec=$(python3 "$ROOT/src/host/agent-selection.py" "$3" "$action")
                # Preserve all for update: it means all ENABLED agents.
                [[ $3 != all || $operation != update ]] || spec=all
                ;;
            *) fail "Unknown $action operation: $operation" ;;
        esac
        owned
        case "$operation" in
            login)
                interactive=(-i)
                [[ ! -t 0 || ! -t 1 ]] || interactive+=(-t)
                exec podman exec "${interactive[@]}" --user 1000:1000 --workdir /workspace "$NAME" "/usr/local/bin/sandbox-$action" login "$3"
                ;;
            list|check) "$manager_function" "$operation" ;;
            *) "$manager_function" "$operation" "$spec" ;;
        esac
        ;;
    run|tool)
        [[ $# -ge 2 ]] || fail "Usage: ./sandbox $action NAME COMMAND [arguments...]"
        manager_command=/usr/local/bin/sandbox-agents
        [[ $action != tool ]] || manager_command=/usr/local/bin/sandbox-tools
        owned
        shift
        interactive=(-i)
        [[ ! -t 0 || ! -t 1 ]] || interactive+=(-t)
        exec podman exec "${interactive[@]}" --user 1000:1000 --workdir /workspace "$NAME" "$manager_command" run "$@"
        ;;
    service)
        [[ $# -ge 2 && $# -le 3 ]] || fail 'Usage: ./sandbox service NAME t3|hermes-dashboard|deepseek-ui|tokentracker [status|start|stop|restart|logs]'
        owned
        if [[ $2 == tokentracker || $2 == t3 || $2 == hermes || $2 == hermes-dashboard || $2 == deepseek || $2 == deepseek-ui ]]; then
            service_id=$2
            [[ $service_id != hermes ]] || service_id=hermes-dashboard
            [[ $service_id != deepseek ]] || service_id=deepseek-ui
            tool_manager service "$service_id" "${3:-status}"
        else
            agent_manager service "$2" "${3:-status}"
        fi
        ;;
    forward)
        [[ $# -ge 2 && $# -le 3 ]] || fail 'Usage: ./sandbox forward NAME t3|hermes-dashboard|deepseek-ui|tokentracker [LOCAL_PORT]'
        case "$2" in
            t3) remote_port=3773 ;;
            hermes|hermes-dashboard) remote_port=9119 ;;
            deepseek|deepseek-ui) remote_port=3080 ;;
            tokentracker) remote_port=7680 ;;
            *) fail 'Choose t3, hermes-dashboard, deepseek-ui, or tokentracker.' ;;
        esac
        local_port=${3:-$remote_port}
        [[ $local_port =~ ^[1-9][0-9]{3,4}$ && $local_port -ge 1024 && $local_port -le 65535 ]] || fail 'Use a local port from 1024 to 65535.'
        owned
        if [[ $2 == tokentracker ]]; then
            connect -T -o BatchMode=yes "$NAME" /bin/bash -s < "$ROOT/src/host/start-tokentracker.sh"
        elif [[ $2 == hermes || $2 == hermes-dashboard || $2 == t3 || $2 == deepseek || $2 == deepseek-ui ]]; then
            service_id=$2
            [[ $service_id != hermes ]] || service_id=hermes-dashboard
            [[ $service_id != deepseek ]] || service_id=deepseek-ui
            tool_manager service "$service_id" start
        fi
        printf 'Forwarding http://127.0.0.1:%s to %s:%s; leave this terminal running.\n' "$local_port" "$NAME" "$remote_port"
        connect -o ExitOnForwardFailure=yes -N -L "127.0.0.1:$local_port:127.0.0.1:$remote_port" "$NAME"
        ;;
    check|check-full)
        owned
        podman inspect "$NAME" --format '{{json .Mounts}}' | python3 "$ROOT/src/host/check-mounts.py" "$NAME"
        args=()
        [[ $action == check-full ]] && args+=(--full)
        connect -o BatchMode=yes "$NAME" agent-smoke "${args[@]}"
        ;;
    fingerprint) ssh-keygen -lf "$STATE/known_hosts" ;;
    *) usage; fail "Unknown command: $action" ;;
esac
