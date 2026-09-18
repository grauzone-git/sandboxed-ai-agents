# Podman preflight and ownership/manager boundaries.
require_podman() {
    command -v podman >/dev/null || fail 'Podman is not installed/on PATH. Install Podman 5+, then retry.'
    [[ $(id -u) != 0 ]] || fail 'Use rootless Podman as your normal user.'
    [[ $(podman info --format '{{.Host.Security.Rootless}}') == true ]] || fail 'Podman must be rootless.'
}

owned() {
    [[ $(podman inspect --format "{{index .Config.Labels \"$LABEL\"}}" "$NAME") == "$ROOT" ]] \
        || fail "Container $NAME is not owned by this configuration."
}
agent_manager() {
    podman exec --user 1000:1000 --workdir /workspace "$NAME" /usr/local/bin/sandbox-agents "$@"
}
tool_manager() {
    podman exec --user 1000:1000 --workdir /workspace "$NAME" /usr/local/bin/sandbox-tools "$@"
}
