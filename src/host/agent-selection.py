"""Validate agent/tool selections before the host creates containers or volumes."""
import json
from pathlib import Path
import re
import sys


CATALOG_DIRECTORY = Path(__file__).resolve().parents[1] / "container"
VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]*")


def validate_selection(spec, kind="agents", agents=None):
    if kind not in ("agents", "tools"):
        raise ValueError("Choose agents or tools.")
    catalog = json.loads((CATALOG_DIRECTORY / f"{kind}.json").read_text())
    if spec == "all":
        spec = ",".join(catalog)
    if spec == "none":
        return "none"

    selected = {}
    for item in spec.split(","):
        name, separator, version = item.strip().partition("@")
        if name not in catalog:
            raise ValueError(f"Unknown {kind[:-1]} {name!r}. Choose: {', '.join(catalog)}, all, or none.")
        if separator and not VERSION_PATTERN.fullmatch(version):
            raise ValueError(f"Invalid version/ref for {name}: {version!r}")
        if catalog[name].get("agent") and separator and version != "bundled":
            raise ValueError(f"{name} uses its agent's version; omit the tool version pin.")
        if name in selected:
            raise ValueError(f"Duplicate {kind[:-1]}: {name}")
        selected[name] = f"{name}@{version}" if separator else name

    if kind == "tools" and agents is not None:
        enabled_agents = {item.partition("@")[0] for item in agents.split(",")}
        for name in selected:
            dependency = catalog[name].get("agent")
            if dependency and dependency not in enabled_agents:
                raise ValueError(f"Tool {name} requires --agents to include {dependency}.")
    return ",".join(selected.values())


def main(args):
    if not 1 <= len(args) <= 3:
        raise ValueError("Usage: agent-selection.py LIST [agents|tools [AGENT_LIST]]")
    print(validate_selection(*args))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, ValueError) as error:
        sys.exit(str(error))
