"""Single diagnostics snapshot for Ops / Home."""

from __future__ import annotations

from typing import Any, Dict, List

from . import apply_service
from . import le_apply
from . import paths
from . import target_players


def _safe(label: str, fn: Any, default: Any, warns: List[str]) -> Any:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        warns.append(f"{label}: {e}")
        return default


def _card_db_has_content(d: Any) -> bool:
    try:
        from pathlib import Path

        p = Path(d)
        if not p.is_dir():
            return False
        if (p / "catalog.sqlite").is_file():
            return True
        if any(p.glob("*.csv")):
            return True
        if (p / "futgg").is_dir() and any((p / "futgg").glob("*.jsonl")):
            return True
    except Exception:
        return False
    return False


def snapshot() -> Dict[str, Any]:
    warns: List[str] = []
    layout = _safe("layout", apply_service.layout_warnings, [], warns)
    if isinstance(layout, list):
        warns.extend(str(x) for x in layout)
    squad = _safe("squad", target_players.load_squad, {}, warns)
    age = _safe("pulse", le_apply.bridge_heartbeat_age_sec, None, warns)
    pending = _safe("queue", le_apply.list_pending_lua, [], warns)
    state = _safe("sync", le_apply.sync_state, "missing", warns)
    cdb = paths.app_root() / "card_db"
    return {
        "app_root": str(paths.app_root()),
        "le_root": str(paths.le_root()),
        "worker_installed": bool(_safe("worker", le_apply.bridge_installed, False, warns)),
        "le_core_clean": not bool(_safe("autoarm", le_apply.autoarm_installed, False, warns)),
        "sync_state": state,
        "pulse_age": age,
        "pulse_label": (
            le_apply.format_age(age) if age is not None else "—"
        ),
        "pending_jobs": len(pending or []),
        "pending_names": [p.name for p in (pending or [])[:12]],
        "squad_loaded": bool((squad or {}).get("loaded")),
        "squad_count": int((squad or {}).get("count") or 0),
        "squad_team": str((squad or {}).get("teamname") or ""),
        "card_db_ok": _card_db_has_content(cdb),
        "warnings": warns,
        "status_line": _safe("status", le_apply.apply_status_line, "", warns),
    }


def format_report(snap: Dict[str, Any] | None = None) -> str:
    s = snap or snapshot()
    lines = [
        f"App: {s['app_root']}",
        f"LE:  {s['le_root']}",
        f"Worker installed: {s['worker_installed']}",
        f"LE core clean: {s['le_core_clean']}",
        f"State: {s['sync_state']} · pulse {s['pulse_label']}",
        f"Queue: {s['pending_jobs']} job(s)",
        f"Squad: {s['squad_count']} · {s['squad_team'] or '(no export)'}",
        f"card_db: {'ok' if s['card_db_ok'] else 'MISSING'}",
    ]
    for w in s.get("warnings") or []:
        lines.append(f"! {w}")
    for n in s.get("pending_names") or []:
        lines.append(f"  · {n}")
    return "\n".join(lines)
