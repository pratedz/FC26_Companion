"""High-performance apply snapshot history (multi-entry undo)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths
from . import undo_apply

MAX_SNAPSHOTS = 80


def _dir() -> Path:
    d = paths.app_root() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _dir() / "index.json"


def _confined_snapshot_path(path_str: str, *, sid: str = "") -> Optional[Path]:
    """Resolve path only if it is a .json file under snapshots/."""
    base = _dir().resolve()
    raw = (path_str or "").strip()
    candidates: List[Path] = []
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            candidates.append(base / p.name)
        else:
            candidates.append(p)
        # Prefer basename under snapshots even if absolute path was stored
        candidates.append(base / Path(raw).name)
    if sid:
        candidates.append(base / f"{sid}.json")
    seen = set()
    for cand in candidates:
        try:
            rp = cand.resolve()
        except OSError:
            continue
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        try:
            rp.relative_to(base)
        except ValueError:
            continue
        if rp.suffix.lower() != ".json":
            continue
        if rp.name in ("index.json",):
            continue
        return rp
    return None


def _load_index() -> List[Dict[str, Any]]:
    p = _index_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_index(rows: List[Dict[str, Any]]) -> None:
    rows = rows[:MAX_SNAPSHOTS]
    # Always store relative basenames only (never absolute escape paths)
    cleaned: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        sid = str(r.get("id") or "")
        rel = f"{sid}.json" if sid else Path(str(r.get("path") or "")).name
        if not rel.endswith(".json"):
            rel = f"{sid or 'snap'}.json"
        r["path"] = rel
        cleaned.append(r)
    _index_path().write_text(json.dumps(cleaned, indent=2), encoding="utf-8")


def save_fields_snapshot(
    *,
    target_id: int,
    fields: List[Tuple[str, int]],
    label: str = "",
    kind: str = "apply",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist snapshot to history + last_apply_snapshot for instant undo."""
    sid = uuid.uuid4().hex[:12]
    payload = {
        "id": sid,
        "ts": time.time(),
        "target_id": int(target_id),
        "label": label or kind,
        "kind": kind,
        "fields": [{"f": f, "v": int(v)} for f, v in fields],
        "extra": extra or {},
    }
    # Always update single-slot undo path
    undo_apply.save_snapshot(
        target_id=int(target_id),
        fields=fields,
        label=label or kind,
    )
    # History file (basename only under snapshots/)
    rel = f"{sid}.json"
    fpath = _dir() / rel
    fpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    idx = _load_index()
    idx.insert(
        0,
        {
            "id": sid,
            "ts": payload["ts"],
            "target_id": payload["target_id"],
            "label": payload["label"],
            "kind": kind,
            "path": rel,
            "n_fields": len(payload["fields"]),
        },
    )
    _save_index(idx)
    return payload


def save_from_card(card: Dict[str, Any], target_id: int, label: str = "") -> Dict[str, Any]:
    from . import player_schema

    ed = player_schema.normalize_player_card(dict(card or {}))
    fields = player_schema.card_to_field_updates(
        ed, enabled_categories=player_schema.default_enabled_categories()
    )
    return save_fields_snapshot(
        target_id=target_id,
        fields=fields,
        label=label or str(card.get("name") or "card"),
        kind="card",
        extra={"name": card.get("name"), "ovr": card.get("overallrating")},
    )


def list_snapshots(limit: int = 40) -> List[Dict[str, Any]]:
    return _load_index()[:limit]


def get_snapshot(sid: str) -> Optional[Dict[str, Any]]:
    sid = str(sid or "").strip()
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        return None
    for row in _load_index():
        if row.get("id") == sid:
            p = _confined_snapshot_path(str(row.get("path") or ""), sid=sid)
            if p is not None and p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return None
    p = _confined_snapshot_path(f"{sid}.json", sid=sid)
    if p is not None and p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def undo_lua_for(sid: Optional[str] = None) -> str:
    if sid:
        snap = get_snapshot(sid)
        if not snap:
            raise ValueError(f"snapshot not found: {sid}")
        return undo_apply.generate_undo_lua(snap)
    return undo_apply.generate_undo_lua()


def format_snapshot_line(row: Dict[str, Any], idx: int = 0) -> str:
    import datetime as _dt

    ts = row.get("ts") or 0
    try:
        when = _dt.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        when = "??"
    return (
        f"[{idx}] {when}  id={row.get('target_id')}  "
        f"{row.get('kind')}  {row.get('label')}  "
        f"fields={row.get('n_fields')}  sid={row.get('id')}"
    )
