#!/bin/bash
# Run explicitly inside a disposable sandbox with the podman capability.
set -euo pipefail
[[ $(id -u) == 1000 ]] || { echo 'Run as the agent user.' >&2; exit 1; }
podman info
check_dir=$(mktemp -d /tmp/podman-smoke.XXXXXXXX)
image_id=
cleanup() {
    if [[ -n $image_id ]]; then podman image rm "$image_id" >/dev/null || true; fi
    rm -rf -- "$check_dir"
}
trap cleanup EXIT
printf '%s\n' "$check_dir" > "$check_dir/marker"
cat > "$check_dir/Containerfile" <<'EOF'
FROM docker.io/library/alpine:latest
COPY marker /marker
RUN cp /marker /built && chmod 0644 /built
USER 65534:65534
CMD ["cat", "/built"]
EOF
podman build --iidfile "$check_dir/image-id" "$check_dir"
image_id=$(cat "$check_dir/image-id")
[[ $(podman run --rm "$image_id") == "$check_dir" ]]
printf 'Nested Podman: image pull, build RUN, and container run as UID/GID 65534: OK\n'
