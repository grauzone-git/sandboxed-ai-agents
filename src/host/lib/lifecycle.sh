# Container lifecycle. Each operation receives NAME and its original arguments.

create_sandbox() {
    podman container exists "$NAME" && fail "Container exists. Use ./sandbox start $NAME."
    local workspace_path= workspace_mount volume sandbox_image
    local volumes=("$NAME-home" "$NAME-sshd") missing_volumes=()
    if [[ $# -ge 2 ]]; then
        workspace_path=$(python3 "$ROOT/src/host/workspace.py" "$2" "$ROOT" "$SSH_ROOT")
        workspace_mount="$workspace_path:/workspace:Z"
    else
        volumes+=("$NAME-workspace")
        workspace_mount="$NAME-workspace:/workspace"
    fi
    podman image exists "$IMAGE" || fail 'Build the image first with ./sandbox build.'
    # Check every retained volume before creating anything new.
    for volume in "${volumes[@]}"; do
        if podman volume exists "$volume"; then
            [[ $(podman volume inspect --format "{{index .Labels \"$LABEL\"}}" "$volume") == "$ROOT" ]] \
                || fail "Volume $volume belongs to another configuration."
        else
            missing_volumes+=("$volume")
        fi
    done
    sandbox_image=$(python3 -B "$ROOT/src/host/capabilities.py" "$ROOT" "$IMAGE" "$capabilities")
    for volume in "${missing_volumes[@]}"; do
        podman volume create --label "$LABEL=$ROOT" "$volume" >/dev/null
    done
    if [[ -n $workspace_path ]]; then mkdir -p -- "$workspace_path"; fi
    python3 -B "$ROOT/src/host/containers.py" "$NAME" "$ROOT" "$sandbox_image" "$workspace_mount" "$PORT" \
        "${SANDBOX_MEMORY:-8g}" "${SANDBOX_CPUS:-4}" "$capabilities"
    wait_for_ssh
    if [[ -z $workspace_path ]]; then
        # Fix only the named volume's mount root, never recurse through project
        # data or change ownership of a host bind.
        podman exec --user 0 "$NAME" /bin/chown 1000:1000 /workspace
    fi
    if [[ $setup_ssh == true ]]; then
        configure_ssh
        install_ssh_include
    fi
    # Clear retained tool dependencies before replacing the agent selection.
    # The explicit requested tool set is installed after the agents below.
    if [[ ${#tool_selection[@]} -gt 0 ]]; then
        tool_manager set none
    fi
    agent_manager init "${selection[@]}"
    tool_manager init "${tool_selection[@]}"
}

start_sandbox() {
    owned
    podman start "$NAME"
    if [[ $setup_ssh == true ]]; then
        wait_for_ssh
        configure_ssh
        install_ssh_include
    fi
}

remove_sandbox() {
    owned
    python3 "$ROOT/src/host/remove-ssh-config.py" --check "$NAME"
    local volume
    local volumes=()
    if [[ $remove_volumes == true ]]; then
        # Validate all targets before stopping/removing anything. Never force
        # volume removal: Podman must refuse a volume used by another container.
        for volume in "$NAME-home" "$NAME-sshd" "$NAME-workspace"; do
            if podman volume exists "$volume"; then
                [[ $(podman volume inspect --format "{{index .Labels \"$LABEL\"}}" "$volume") == "$ROOT" ]] \
                    || fail "Volume $volume belongs to another configuration."
                volumes+=("$volume")
            fi
        done
    fi
    podman stop "$NAME"
    podman rm "$NAME"
    # The connection is obsolete once the container is gone, even if a later
    # volume deletion fails. Failed container removal must retain SSH access.
    python3 "$ROOT/src/host/remove-ssh-config.py" "$NAME"
    if [[ $remove_volumes == true ]]; then
        for volume in "${volumes[@]}"; do podman volume rm "$volume"; done
    fi
    printf 'Removed container %s. Host workspace directories retained.\n' "$NAME"
    [[ $remove_volumes == true ]] && echo 'Sandbox named volumes deleted.' || echo 'Sandbox named volumes retained.'
    echo 'Local sandbox SSH files and Include entries deleted.'
}
