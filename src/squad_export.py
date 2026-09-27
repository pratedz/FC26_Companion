"""Queue export_user_squad.lua with absolute OUT_PATH for the LE bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import actions
from . import paths
from . import target_players


def export_lua_template() -> Path:
    return paths.bridge_dir() / "export_user_squad.lua"


def prepare_export_lua() -> str:
    src = export_lua_template()
    if not src.is_file():
        raise FileNotFoundError(f"Missing {src}")
    text = src.read_text(encoding="utf-8")
    out = target_players.current_squad_path().resolve()
    out_s = str(out).replace("\\", "/")
    # Ensure parent exists for LE write
    out.parent.mkdir(parents=True, exist_ok=True)
    import re

    text2, n = re.subn(
        r'^local OUT_PATH\s*=\s*.*$',
        f'local OUT_PATH = "{out_s}"',
        text,
        count=1,
        flags=re.M,
    )
    if n == 0:
        text2 = f'local OUT_PATH = "{out_s}"\n' + text
    return text2


def queue_export_squad() -> Dict[str, Any]:
    """Write export job to queue for the silent bridge."""
    lua = prepare_export_lua()
    out = actions.write_lua(lua, stem="export_user_squad", clipboard=False)
    return {
        "queue_file": out.get("queue_file"),
        "squad_path": str(target_players.current_squad_path()),
        "status": "queued",
    }
