"""Check Podman's explicit mount inventory against the storage boundary."""
import json
import sys


def validate_mounts(name, mounts):
    expected = {
        "/workspace": (("bind", None), ("volume", f"{name}-workspace")),
        "/home/agent": (("volume", f"{name}-home"),),
        "/var/lib/agent-sshd": (("volume", f"{name}-sshd"),),
    }
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
        descriptions.append(f"{destination}: {kind} {volume or mount['Source']}")
    return descriptions


def main(args):
    if len(args) != 1:
        raise ValueError("Usage: check-mounts.py NAME < MOUNTS_JSON")
    for description in validate_mounts(args[0], json.load(sys.stdin)):
        print(description)
    print("Mount policy: workspace bind or named volume, named home and SSH volumes: OK")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (KeyError, TypeError, ValueError) as error:
        sys.exit(str(error))
