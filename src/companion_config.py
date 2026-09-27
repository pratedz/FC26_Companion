"""External control configuration for LE Companion dual-mode.

Read/write companion_config.json used for customization defaults.
Does not touch anticheat or inject paths.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Union

from . import paths

CONFIG_NAME = "companion_config.json"
CONFIG_VERSION = 1

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": CONFIG_VERSION,
    "default_target_playerid": None,
    "apply_categories": None,
    "auto_clear_stale": True,
    "queue_rel": "queue",
    "inject_dll_name": "LECompanionInject.dll",
    "notes": "",
    # Performance / product turbo defaults
    "timeout_live": 55.0,
    "timeout_offline": 3.0,
    "poll_sec": 0.06,
    "apply_wait": True,
    "batch_wait_each": False,
    "sticky_year": "26",
    # SAFE LE Load auto-arm (set by Inject arm button)
    "inject_autoarm": False,
}

PathLike = Union[str, Path]


def config_path() -> Path:
    """Production config path (next to app)."""
    return paths.app_root() / CONFIG_NAME


def load_config(path: Optional[PathLike] = None) -> Dict[str, Any]:
    p = Path(path) if path is not None else config_path()
    data = dict(DEFAULT_CONFIG)
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: raw[k] for k in raw if k in DEFAULT_CONFIG or k == "notes"})
                data["version"] = int(raw.get("version") or CONFIG_VERSION)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return data


def save_config(cfg: Dict[str, Any], path: Optional[PathLike] = None) -> Path:
    p = Path(path) if path is not None else config_path()
    out = dict(DEFAULT_CONFIG)
    out.update(cfg or {})
    out["version"] = CONFIG_VERSION
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def update_config(**kwargs: Any) -> Dict[str, Any]:
    """Update production companion_config.json (intentional user/config write)."""
    cfg = load_config()
    for k, v in kwargs.items():
        if k in DEFAULT_CONFIG or k == "notes":
            cfg[k] = v
    save_config(cfg)
    return cfg


def config_round_trip(
    *,
    default_target_playerid: Optional[int] = 158023,
    notes: str = "round-trip-test",
    path: Optional[PathLike] = None,
) -> Dict[str, Any]:
    """Write then load on an isolated config file — never clobbers production.

    If ``path`` is omitted, uses a temporary directory that is not app_root.
    Production ``companion_config.json`` is left untouched.
    """
    if path is not None:
        cfg_path = Path(path)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="le_companion_cfg_rt_"))
        cfg_path = tmp / CONFIG_NAME

    prod = config_path().resolve()
    if cfg_path.resolve() == prod:
        raise ValueError(
            "config_round_trip refuses production companion_config.json; "
            "pass an isolated path or omit path for a temp file"
        )

    before = {
        "default_target_playerid": default_target_playerid,
        "auto_clear_stale": True,
        "notes": notes,
    }
    save_config({**DEFAULT_CONFIG, **before}, path=cfg_path)
    after = load_config(path=cfg_path)
    ok = (
        after.get("default_target_playerid") == default_target_playerid
        and after.get("notes") == notes
        and after.get("auto_clear_stale") is True
    )
    return {
        "ok": ok,
        "path": str(cfg_path),
        "written": before,
        "loaded": after,
        "production_untouched": True,
        "production_path": str(prod),
    }
