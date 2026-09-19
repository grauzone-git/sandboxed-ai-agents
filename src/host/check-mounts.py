"""Check Podman's explicit mount inventory against the storage boundary."""
import json
import sys


def validate_mounts(name, mounts):
    expected = {
        "/workspace": (("bind", None), ("volume", f"{name}-workspace")),
        "/home/agent": (("volume", f"{name}-home"),),
        "/var/lib/agent-sshd": (("volume", f"{name}-sshd"),),
    }
    # Optional for compatibility with sandboxes created before runtime tmpfs
    # support. Only this exact tmpfs is allowed; never a bind or named volume.
    if any(mount.get('Destination') == '/run/user/1000' for mount in mounts):
        expected['/run/user/1000'] = (("tmpfs", None),)
    if len(mounts) != len(expected):
        raise ValueError(f"Unexpected mounts: {mounts!r}")
    descriptions = []
    for mount in mounts:
        destination = mount["Destination"]
        if destination not in expected:
            raise ValueError(f"Unexpected destination: {destination}")
        allowed = expected.pop(destination)
        kind = mount["Type"]
        volume = mount.get("Name") if kind == "volume" else None
        if (kind, volume) not in allowed:
            raise ValueError(f"Unexpected mount: {mount!r}")
        source = 'ephemeral' if kind == 'tmpfs' else volume or mount['Source']
        descriptions.append(f"{destination}: {kind} {source}")
    return descriptions


def main(args):
    if len(args) != 1:
        raise ValueError("Usage: check-mounts.py NAME < MOUNTS_JSON")
    for description in validate_mounts(args[0], json.load(sys.stdin)):
        print(description)
    print("Mount policy: workspace bind or named volume, named home and SSH volumes, optional runtime tmpfs: OK")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (KeyError, TypeError, ValueError) as error:
        sys.exit(str(error))
