"""Dependency-free syntax and local documentation-link checks; writes no caches."""
import ast
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def main():
    files = [path for directory in ("src", "tests")
             for path in (ROOT / directory).rglob("*") if path.is_file()]
    for file in files:
        if file.suffix == ".py":
            ast.parse(file.read_text(), filename=str(file))
        elif file.suffix == ".json":
            json.loads(file.read_text())
        elif file.suffix == ".cjs":
            subprocess.run(["node", "--check", str(file)], check=True)
    shell_files = [ROOT / "sandbox", ROOT / "tests/run", ROOT / "src/container/sandbox-agents"]
    shell_files.extend(file for file in files if file.suffix == ".sh")
    for file in shell_files:
        subprocess.run(["bash", "-n", str(file)], check=True)

    for document in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", document.read_text()):
            link = urlsplit(target)
            if link.scheme or link.netloc or not link.path:
                continue
            path = document.parent / unquote(link.path)
            if not path.exists():
                raise ValueError(f"Broken local link in {document.relative_to(ROOT)}: {target}")
    print("Bash/Python/JavaScript syntax, JSON catalogs, and local documentation links: OK")


if __name__ == "__main__":
    main()
