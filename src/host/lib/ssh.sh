# Host SSH keys, pinned connections, and Include installation.
handle_local_ssh_config() {
    # Local SSH configuration remains usable without a running Podman service.
    if [[ $action == ssh-config ]]; then
        [[ $# -eq 1 || ( $# -eq 2 && $2 == --install ) ]] || fail 'Usage: ./sandbox ssh-config NAME [--install]'
        if [[ -f $SSH_CONFIG ]]; then
            if [[ ${2:-} == --install ]]; then
                exec python3 "$ROOT/src/host/install-ssh-config.py" "$SSH_CONFIG"
            fi
            cat "$SSH_CONFIG"
            exit
        fi
        [[ ${2:-} == --install ]] || fail "No SSH configuration for $NAME. Run ./sandbox ssh-config $NAME --install while the sandbox is running."
        # Creating missing files needs the running container's public host key.
        # Existing files above can still be displayed/included entirely offline.
    fi
}

wait_for_ssh() {
    local attempt
    for ((attempt=0; attempt<30; attempt++)); do
        if podman exec --user 0 "$NAME" /usr/bin/test -f /var/lib/agent-sshd/ssh_host_ed25519_key.pub 2>/dev/null; then
            return
        fi
        sleep 0.5
    done
    podman logs "$NAME"
    fail 'SSH initialization did not complete.'
}
install_ssh_include() {
    python3 "$ROOT/src/host/install-ssh-config.py" "$SSH_CONFIG"
}
configure_ssh() {
    local mapping port hostkey kind keydata
    command -v ssh-keygen >/dev/null || fail 'Install the OpenSSH client on the host.'
    mkdir -p -m 0700 -- "$HOME/.ssh"
    install -d -m 0700 "$SSH_ROOT" "$STATE"
    if [[ ! -f $STATE/id_ed25519 ]]; then
        ssh-keygen -q -t ed25519 -N '' -C "$NAME sandbox access" -f "$STATE/id_ed25519"
    fi
    # Copy only a PUBLIC key over stdin. No SSH files/sockets are bind-mounted.
    podman exec -i --user 0 "$NAME" /bin/sh -c \
        '/bin/cat > /var/lib/agent-sshd/authorized_keys; /bin/chmod 0644 /var/lib/agent-sshd/authorized_keys' \
        < "$STATE/id_ed25519.pub"
    mapping=$(podman port "$NAME" 2222/tcp)
    [[ $mapping == 127.0.0.1:* && $mapping != *$'\n'* ]] || fail 'Unexpected SSH port mapping.'
    port=${mapping##*:}
    hostkey=$(podman exec --user 0 "$NAME" /bin/cat /var/lib/agent-sshd/ssh_host_ed25519_key.pub)
    read -r kind keydata _ <<< "$hostkey"
    # Trust the host key read directly from our container, not an unverified keyscan.
    printf '[127.0.0.1]:%s %s %s\n' "$port" "$kind" "$keydata" > "$STATE/known_hosts"
    cat > "$SSH_CONFIG" <<EOF
Host $NAME
    HostName 127.0.0.1
    Port $port
    User agent
    IdentityFile "$STATE/id_ed25519"
    IdentitiesOnly yes
    IdentityAgent none
    ForwardAgent no
    ForwardX11 no
    UserKnownHostsFile "$STATE/known_hosts"
    StrictHostKeyChecking yes
    ServerAliveInterval 30
EOF
    chmod 0600 "$STATE/id_ed25519" "$SSH_CONFIG" "$STATE/known_hosts"
    ssh-keygen -lf "$STATE/known_hosts"
    printf 'SSH configuration: %s\nWorkspace in container: /workspace\n' "$SSH_CONFIG"
}
connect() {
    [[ -f $SSH_CONFIG ]] || fail "Run ./sandbox ssh-config $NAME --install to prepare SSH access."
    ssh -F "$SSH_CONFIG" "$@"
}
