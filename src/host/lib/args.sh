# Parse host CLI options into action, NAME, selection, and CLI_ARGS.
# Sourced by cli.sh; these globals are shared with the command modules.
usage() {
    cat <<'USAGE'
Windows PowerShell commands: use ./sandbox.ps1; see docs/QUICKGUIDE-WINDOWS.md.

Usage:
  ./sandbox build [additional podman build arguments]
  ./sandbox update NAME...|--all [--no-build] [--capabilities LIST]
                                           # rebuild image and recreate sandboxes
  ./sandbox up [NAME [WORKSPACE [SSH_PORT]]] --agents LIST [--tools LIST] [--ssh-config]
                                           [--ssh-port PORT] [--capabilities LIST]
                                           # defaults: agent01, NAME-workspace volume, 2222
  ./sandbox agents NAME [list|check]
  ./sandbox agents NAME set|enable|disable|update LIST
  ./sandbox agents NAME login codex|claude|opencode|copilot|hermes
  ./sandbox tools NAME [list|check]
  ./sandbox tools NAME set|enable|disable|update LIST
  ./sandbox tools NAME login github
  ./sandbox tools NAME setup t3
  ./sandbox tools NAME setup azdo [--persist|--clear]
  ./sandbox tools NAME setup azure [--interactive] [--cloud AzureCloud|AzureChinaCloud]
      [--tenant TENANT] [--subscription SUBSCRIPTION | --tenant-only]
  ./sandbox tool NAME TOOL [arguments...]
  ./sandbox run NAME AGENT [arguments...]
  ./sandbox azdo NAME --pat-env -- devops COMMAND --organization URL [--project PROJECT]
                                           # PAT from environment, one invocation
  ./sandbox copilot|claude|codex|hermes|opencode|t3|deepseek NAME
                                           # start/reconnect a persistent terminal
  ./sandbox service NAME t3|hermes-dashboard|deepseek-ui|tokentracker [status|start|stop|restart|logs]
  ./sandbox forward NAME t3|hermes-dashboard|deepseek-ui|tokentracker [LOCAL_PORT]
  ./sandbox start NAME [--ssh-config]
  ./sandbox stop|shell|check|check-full|fingerprint NAME
  ./sandbox ssh-config NAME [--install]    # create if missing and install Include
  ./sandbox remove NAME [--volumes]         # always delete local SSH files/Include

Omit WORKSPACE to use the NAME-workspace named volume (no host-directory binds).
Supply WORKSPACE to bind that directory at /workspace instead.
Home and SSH server state always use NAME-home + NAME-sshd named volumes.
Use --ssh-port to choose a port without supplying a workspace directory.
Agents: copilot, claude, codex, hermes, opencode, deepseek; LIST also accepts
all and versions (e.g. codex@X.Y.Z). Up requires at least one explicit agent,
including when recreating a container. Nothing defaults to Copilot.
Existing sandboxes can use 'agents NAME set none' to disable every agent.
Agent login requires an enabled agent: Codex/Copilot device code, Claude browser/code login,
or OpenCode/Hermes interactive provider setup.
GitHub login uses the built-in gh CLI and configures Git HTTPS credentials.
Azure DevOps setup uses native az devops login unless --persist is supplied.
Azure setup uses device code by default. --interactive opens the host browser
through temporary loopback forwarding and requires opted-in managed SSH setup.
With --persist, save the PAT as AZURE_DEVOPS_EXT_PAT for new sandbox sessions,
without either login command. Both modes set the default organization.
Setup --clear removes the saved environment PAT; restart existing sessions.
One-off Azure DevOps commands require explicit --pat-env and a nonempty AZURE_DEVOPS_EXT_PAT.
The PAT travels over stdin, is never saved, and is unavailable to later sessions.
Use devops, boards, repos, pipelines or artifacts commands with --organization URL.
PAT commands use the organization from persistent setup when no URL is supplied.
They ignore native Azure defaults and reject login, configure, debug and verbose.
After login, enter a Git user name and email to save globally in the sandbox home.
T3 starts headless; its terminal command follows logs. DeepSeek opens a CLI shell.
T3 Connect setup requires enabled T3 and restarts its managed server after sign-in.
Select hermes-dashboard or deepseek-ui to start the corresponding agent UI.
Disabling either agent also disables its UI tool; cached installs/data remain.
Forwarding TokenTracker starts its installed dashboard if needed (port 7680).
Tools: t3, hermes-dashboard, deepseek-ui, tokentracker; --tools also accepts all and none.
T3/TokenTracker accept version pins; bundled UI tools follow their agent version.
Service/forward aliases: hermes = hermes-dashboard; deepseek = deepseek-ui.
Omitting --tools reuses the saved tool selection, or none for a fresh home.
Capabilities: podman or none (default). --capabilities podman installs nested
rootless Podman for container builds/tests and enables /dev/fuse + /dev/net/tun. It allows
mapping-helper file capabilities, disables SELinux/AppArmor separation, and unmasks kernel paths.
Its seccomp profile permits hostname changes and setns in inner namespaces.
Nested runtime state uses tmpfs; inner images and volumes persist in the home volume.
Capabilities are chosen at creation and preserved by update unless overridden.
Use 'update NAME --capabilities podman' to enable nested Podman on an existing sandbox.
Update builds once without cache, then recreates selected owned sandboxes.
Use --no-build to apply an image you have already built with custom build options.
The optional Podman layer is built/cached even with --no-build.
Update preserves storage, SSH files, settings, and running/stopped state.
Run as your normal user, never with sudo. Up only creates new containers.
Up/start create local SSH files and install their Include only with --ssh-config.
Add SSH later with 'ssh-config NAME --install' while the sandbox is running.
Remove always deletes this sandbox's local SSH files and its Include.
Removal also deduplicates SSH settings within each scope and redundant Host blocks.
The workspace and named volumes are preserved by default.
With --volumes, also delete NAME-home, NAME-sshd, and NAME-workspace if present.
This deletes saved credentials and any files in the named workspace volume.
An explicitly bound workspace directory is always retained.
Set SANDBOX_IMAGE, SANDBOX_MEMORY (default 8g), SANDBOX_CPUS (default 4) as needed.
USAGE
}

parse_cli_args() {
    action=${1:-help}
    if [[ $action == help || $action == --help || $action == -h ]]; then usage; exit; fi
    shift
    if [[ $action == update ]]; then
        CLI_ARGS=("$@")
        return
    fi
    selection=()
    tool_selection=()
    capabilities=none
    capabilities_option=false
    remove_volumes=false
    setup_ssh=false
    if [[ $action == start ]]; then
        [[ $# -eq 1 || ( $# -eq 2 && $2 == --ssh-config ) ]] || fail 'Usage: ./sandbox start NAME [--ssh-config]'
        [[ ${2:-} != --ssh-config ]] || setup_ssh=true
    fi
    if [[ $action == remove ]]; then
        [[ $# -ge 1 ]] || fail 'Usage: ./sandbox remove NAME [--volumes]'
        local legacy_ssh_option=false
        for option in "${@:2}"; do
            case "$option" in
                --volumes) [[ $remove_volumes == false ]] || fail 'Duplicate --volumes.'; remove_volumes=true ;;
                # Accepted for older callers; SSH cleanup is now unconditional.
                --ssh-config) [[ $legacy_ssh_option == false ]] || fail 'Duplicate --ssh-config.'; legacy_ssh_option=true ;;
                *) fail "Unknown remove option: $option. Use --volumes to also delete named volumes." ;;
            esac
        done
    fi
    if [[ $action == up ]]; then
        positional=()
        port_option=()
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --agents)
                    [[ $# -ge 2 && ${#selection[@]} -eq 0 ]] || fail 'Supply --agents once, followed by a list.'
                    selection=("$(python3 "$ROOT/src/host/agent-selection.py" "$2")")
                    shift 2 ;;
                --tools)
                    [[ $# -ge 2 && ${#tool_selection[@]} -eq 0 ]] || fail 'Supply --tools once, followed by a list.'
                    tool_selection=("$(python3 "$ROOT/src/host/agent-selection.py" "$2" tools)")
                    shift 2 ;;
                --capabilities)
                    [[ $# -ge 2 && $capabilities_option == false ]] || fail 'Supply --capabilities once, followed by a list.'
                    capabilities=$(python3 -B "$ROOT/src/host/capabilities.py" "$2")
                    capabilities_option=true
                    shift 2 ;;
                --ssh-config)
                    [[ $setup_ssh == false ]] || fail 'Supply --ssh-config only once.'
                    setup_ssh=true
                    shift ;;
                --ssh-port)
                    [[ $# -ge 2 && ${#port_option[@]} -eq 0 ]] || fail 'Supply --ssh-port once, followed by a port.'
                    port_option=("$2")
                    shift 2 ;;
                --*) fail "Unknown option: $1" ;;
                *) positional+=("$1"); shift ;;
            esac
        done
        set -- "${positional[@]}"
        [[ $# -le 3 ]] || fail 'Usage: ./sandbox up [NAME [WORKSPACE [SSH_PORT]]] --agents LIST [--tools LIST] [--capabilities LIST] [--ssh-config] [--ssh-port PORT]'
        [[ $# -lt 2 || -n ${2:-} ]] || fail 'Omit WORKSPACE for a named volume, or supply a nonempty directory path.'
        [[ $# -lt 3 || ${#port_option[@]} -eq 0 ]] || fail 'Use either positional SSH_PORT or --ssh-port, not both.'
        PORT=${port_option[0]-${3:-2222}}
        [[ $PORT =~ ^[1-9][0-9]{3,4}$ && $PORT -ge 1024 && $PORT -le 65535 ]] || fail 'Use an SSH port from 1024 to 65535.'
        [[ ${#selection[@]} -eq 1 && ${selection[0]} != none ]] \
            || fail 'Creating a sandbox requires --agents with at least one agent (or all). No agent is installed by default.'
        if [[ ${#tool_selection[@]} -gt 0 ]]; then
            python3 "$ROOT/src/host/agent-selection.py" "${tool_selection[0]}" tools "${selection[0]}" >/dev/null
        fi
    fi
    case "$action" in
        agents|tools)
            if [[ ${2:-} == setup ]]; then
                if [[ $action == tools && ${3:-} == azure ]]; then
                    python3 -B "$ROOT/src/host/azure_auth.py" --validate "${@:4}" || exit $?
                else
                [[ $action == tools && ( ( $# -eq 3 && ( $3 == t3 || $3 == azdo ) ) || ( $# -eq 4 && $3 == azdo && ( $4 == --persist || $4 == --clear ) ) ) ]] \
                    || fail 'Usage: ./sandbox tools NAME setup t3, or setup azdo [--persist|--clear].'
                fi
            fi
            if [[ ${2:-} == login ]]; then
                if [[ $action == tools ]]; then
                    [[ $# -eq 3 && $3 == github ]] || fail 'Usage: ./sandbox tools NAME login github.'
                else
                    [[ $# -eq 3 && ( $3 == codex || $3 == claude || $3 == opencode || $3 == copilot || $3 == hermes ) ]] \
                        || fail 'Usage: ./sandbox agents NAME login codex|claude|opencode|copilot|hermes.'
                fi
            fi
            ;;
        copilot|claude|codex|hermes|opencode|t3|deepseek)
            [[ $# -eq 1 ]] || fail "Usage: ./sandbox $action NAME (for CLI arguments, use ./sandbox run NAME $action ...)" ;;
    esac
    if [[ $action != build ]]; then
        NAME=${1:-agent01}
        [[ $NAME =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Use an alphanumeric container name (plus _, ., -).'
        SSH_ROOT="$HOME/.ssh/sanboxed-agents"
        STATE="$SSH_ROOT/$NAME"
        SSH_CONFIG="$STATE/$NAME.conf"
        # These characters have special meaning in OpenSSH path expansion/config parsing.
        for ssh_path in "$ROOT" "$SSH_ROOT"; do
            [[ $ssh_path != *$'\n'* && $ssh_path != *$'\r'* && $ssh_path != *'"'* && $ssh_path != *'%'* && $ssh_path != *'$'* && $ssh_path != *'\'* && $ssh_path != *'*'* && $ssh_path != *'?'* && $ssh_path != *'['* && $ssh_path != *']'* ]] \
                || fail 'Project and SSH paths must not contain newline, quotes, or SSH expansion characters.'
        done
    fi
    CLI_ARGS=("$@")
}
