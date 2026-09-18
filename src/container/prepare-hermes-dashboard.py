"""Provision the dashboard using the selected Hermes revision's build helper."""
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))

# These are supplied by the upstream installer's Python dependency stage.
import fastapi  # noqa: F401
import uvicorn  # noqa: F401
import ptyprocess  # noqa: F401

try:
    from hermes_cli.main_web_build import _build_web_ui, _web_ui_build_needed
except ModuleNotFoundError as error:
    if error.name != "hermes_cli.main_web_build":
        raise
    from hermes_cli.main import _build_web_ui, _web_ui_build_needed

if not (repo / "web/package.json").is_file():
    sys.exit("This Hermes revision has no dashboard frontend; select a newer revision.")
if not _build_web_ui(repo / "web", fatal=True) or _web_ui_build_needed(repo / "web"):
    sys.exit("Hermes dashboard frontend build failed.")
