"""Thin, testable API facade for the web UI.

Wraps existing product/backend modules only — no tkinter, no CustomTkinter.
Request/response handlers return plain dicts/lists for JSON serialization.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional

from . import apply_service
from . import card_catalog
from . import companion_config
from . import editor_presets
from . import health_check
from . import job_history
from . import le_apply
from . import paths
from . import product
from . import profiles as profiles_mod
from . import target_players


def _ok_from_result(r: apply_service.ApplyResult) -> bool:
    """True only when the job actually applied or is genuinely still in flight.

    `queued` alone is not success: apply_service returns queued=True with
    outcome="error" for a job the worker ran and reported as failed, and
    outcome="timeout"/"blocked" for a job nothing ever collected. Reporting
    ok=True there is how a failed apply reached the web client as a success.
    """
    outcome = str(getattr(r, "outcome", "") or "")
    if outcome in apply_service.FAILED_OUTCOMES:
        return False
    if bool(getattr(r, "applied", False)):
        return True
    return outcome in ("queued_live", "applied") or bool(getattr(r, "queued", False))


def _result_dict(r: apply_service.ApplyResult) -> Dict[str, Any]:
    """Serialize ApplyResult to a JSON-friendly dict."""
    if is_dataclass(r):
        d = asdict(r)
    else:
        d = {
            "applied": bool(getattr(r, "applied", False)),
            "queued": bool(getattr(r, "queued", False)),
            "live": bool(getattr(r, "live", False)),
            "reason": str(getattr(r, "reason", "") or ""),
            "queue_file": getattr(r, "queue_file", None),
            "last_result": str(getattr(r, "last_result", "") or ""),
            "outcome": str(getattr(r, "outcome", "") or ""),
            "detail": str(getattr(r, "detail", "") or ""),
            "meta": dict(getattr(r, "meta", None) or {}),
        }
    # Keep meta small for API consumers
    meta = d.get("meta") or {}
    if isinstance(meta, dict) and len(str(meta)) > 2000:
        d["meta"] = {
            k: meta[k]
            for k in ("job_status", "job_status_parsed", "queue_file")
            if k in meta
        }
    return d


def _card_summary(card: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    """Compact card row for lists + enough fields to re-apply."""
    keys = (
        "name",
        "playerid",
        "overallrating",
        "potential",
        "year",
        "source",
        "revision",
        "origin",
        "uid",
        "preferredposition1",
        "skillmoves",
        "weakfootabilitytypecode",
        "club",
        "nation",
        "league",
        "rarity",
        "acceleration",
        "sprintspeed",
        "finishing",
        "shortpassing",
        "dribbling",
        "defensiveawareness",
        "standingtackle",
        "gkdiving",
        "eaId",
        "basePlayerEaId",
        "base_id",
        "nationality",
        "height",
        "weight",
        "firstName",
        "lastName",
        "nickname",
    )
    out: Dict[str, Any] = {"index": index, "line": card_catalog.format_card_line(card, index)}
    for k in keys:
        if k in card and card[k] not in (None, ""):
            out[k] = card[k]
    # Pass through extra attr fields when present (editor / apply fidelity)
    for k, v in card.items():
        if k.startswith("_"):
            continue
        if k not in out and isinstance(v, (str, int, float, bool)):
            out[k] = v
    return out


# ── Status / home ────────────────────────────────────────────────────


def get_status() -> Dict[str, Any]:
    """Combined LIVE/worker + health + apply line for chrome/dock."""
    arm = product.arm_status()
    health = health_check.snapshot()
    cfg = companion_config.load_config()
    return {
        "arm": arm,
        "health": health,
        "status_line": le_apply.apply_status_line(short=False),
        "apply_status_short": le_apply.apply_status_line(short=True),
        "target_playerid": cfg.get("default_target_playerid"),
        "product_name": "LE Companion",
        "version": "web-1.0",
    }


def get_home() -> Dict[str, Any]:
    st = get_status()
    return {
        **st,
        "goals": [
            "Start FC26 with LE Launcher",
            "Install worker + Go LIVE (paste bridge once)",
            "Export squad → search cards → Apply",
        ],
        "quick_packs": [
            p for p in product.list_packs() if p.get("category") in ("boost", "workflow", "squad")
        ][:8],
    }


def install_worker() -> Dict[str, Any]:
    return product.install_and_arm_assets()


def go_live() -> Dict[str, Any]:
    return product.go_live(prefer_autoarm=False)


def copy_bridge() -> Dict[str, Any]:
    ok = apply_service.copy_bridge_to_clipboard()
    return {"ok": bool(ok), "clipboard": bool(ok)}


# ── Cards ────────────────────────────────────────────────────────────


def search_cards(
    query: str = "",
    *,
    year: Optional[str] = None,
    ovr: Optional[int] = None,
    limit: int = 40,
) -> Dict[str, Any]:
    """Real catalog search — same path as CLI/GUI."""
    q = str(query or "").strip()
    lim = max(1, min(int(limit or 40), 100))
    hits = card_catalog.search_cards(
        q,
        year=year if year and str(year).strip() else None,
        ovr=ovr,
        limit=lim,
    )
    rows = [_card_summary(dict(c), i) for i, c in enumerate(hits)]
    return {
        "query": q,
        "year": year,
        "ovr": ovr,
        "count": len(rows),
        "hits": rows,
    }


def set_target(playerid: Optional[int]) -> Dict[str, Any]:
    if playerid is None:
        return {"ok": False, "reason": "playerid required", "target_playerid": None}
    tid = int(playerid)
    companion_config.update_config(default_target_playerid=tid)
    return {"ok": True, "target_playerid": tid}


def get_target() -> Dict[str, Any]:
    cfg = companion_config.load_config()
    tid = cfg.get("default_target_playerid")
    return {"target_playerid": int(tid) if tid is not None else None}


def apply_card(
    card: Dict[str, Any],
    *,
    target_playerid: Optional[int] = None,
    wait: bool = False,
) -> Dict[str, Any]:
    """Apply card onto target via product.apply_card_to_target (real queue path).

    wait defaults False for responsive web UI; still writes queue + returns outcome.
    """
    if not card or not isinstance(card, dict):
        return {
            "ok": False,
            "outcome": "error",
            "reason": "card object required",
            "applied": False,
            "queued": False,
        }
    tid = target_playerid
    if tid is None:
        cfg = companion_config.load_config()
        tid = cfg.get("default_target_playerid")
    if tid is None:
        return {
            "ok": False,
            "outcome": "error",
            "reason": "target_playerid required (set target or pass it)",
            "applied": False,
            "queued": False,
        }
    r = product.apply_card_to_target(dict(card), int(tid), wait=bool(wait))
    out = _result_dict(r)
    out["ok"] = _ok_from_result(r)
    out["target_playerid"] = int(tid)
    out["card_name"] = str(card.get("name") or "")
    return out


def apply_card_from_search(
    query: str,
    *,
    target_playerid: int,
    year: Optional[str] = None,
    index: int = 0,
    wait: bool = False,
) -> Dict[str, Any]:
    """Search + apply first/Nth hit — real backend path used by tests."""
    res = search_cards(query, year=year, limit=max(index + 1, 5))
    hits = res.get("hits") or []
    if not hits:
        return {
            "ok": False,
            "outcome": "error",
            "reason": "No cards matched",
            "applied": False,
            "queued": False,
            "search": res,
        }
    idx = int(index or 0)
    if idx < 0 or idx >= len(hits):
        return {
            "ok": False,
            "outcome": "error",
            "reason": f"index {idx} out of range",
            "applied": False,
            "queued": False,
            "search": res,
        }
    card = dict(hits[idx])
    card.pop("index", None)
    card.pop("line", None)
    out = apply_card(card, target_playerid=int(target_playerid), wait=wait)
    out["search_count"] = res["count"]
    out["selected_index"] = idx
    return out


# ── Boost / packs ────────────────────────────────────────────────────


def list_packs() -> Dict[str, Any]:
    packs = product.list_packs()
    return {"count": len(packs), "packs": packs}


def run_pack(pack_id: str, *, wait: bool = False) -> Dict[str, Any]:
    """Run product pack; wait=False queues without long poll (web-friendly)."""
    try:
        out = product.run_pack(str(pack_id), wait=bool(wait))
    except KeyError as e:
        return {"ok": False, "outcome": "error", "reason": str(e), "pack_id": pack_id}
    # Normalize: run_pack returns pack dict with ok/results
    out = dict(out)
    out.setdefault("pack_id", pack_id)
    return out


def run_profile(profile_id: str, *, wait: bool = False) -> Dict[str, Any]:
    r = product.run_profile_turbo(str(profile_id), wait=bool(wait))
    out = _result_dict(r)
    out["ok"] = _ok_from_result(r)
    out["profile_id"] = profile_id
    return out


def list_profiles() -> Dict[str, Any]:
    rows = []
    for p in profiles_mod.load_profiles():
        rows.append(
            {
                "id": p.id,
                "label": p.label,
                "category": p.category,
                "description": p.description,
            }
        )
    return {"count": len(rows), "profiles": rows}


# ── Squad ────────────────────────────────────────────────────────────


def get_squad() -> Dict[str, Any]:
    squad = target_players.load_squad()
    players = list(squad.get("players") or [])
    return {
        "loaded": bool(squad.get("loaded")),
        "teamid": squad.get("teamid") or 0,
        "teamname": squad.get("teamname") or "",
        "count": int(squad.get("count") or 0),
        "players": players,
        "free_agent_count": len(squad.get("free_agents") or []),
        "path": squad.get("path"),
        "status_line": target_players.squad_status_line(),
    }


def search_targets(query: str = "", *, limit: int = 30) -> Dict[str, Any]:
    hits = target_players.search_target(
        str(query or ""),
        limit=max(1, min(int(limit or 30), 80)),
        include_catalog_fallback=True,
        year="26",
    )
    return {"query": query, "count": len(hits), "hits": hits}


def lock_target_from_squad(playerid: int) -> Dict[str, Any]:
    return set_target(int(playerid))


def queue_export_squad(*, wait: bool = False) -> Dict[str, Any]:
    return run_profile("export_user_squad", wait=wait)


# ── Add team ─────────────────────────────────────────────────────────


def add_to_team(
    card: Dict[str, Any],
    *,
    teamid: Optional[int] = None,
    mode: str = "auto",
    wait: bool = False,
) -> Dict[str, Any]:
    r = product.add_card_to_user_team(
        dict(card),
        teamid=teamid,
        mode=mode or "auto",
        wait=bool(wait),
    )
    out = _result_dict(r)
    out["ok"] = _ok_from_result(r)
    out["card_name"] = str(card.get("name") or "")
    return out


# ── Editor ───────────────────────────────────────────────────────────


def list_editor_presets() -> Dict[str, Any]:
    ids = editor_presets.preset_ids()
    return {
        "presets": [{"id": pid, "label": editor_presets.preset_label(pid)} for pid in ids]
    }


def get_editor_preset(preset_id: str) -> Dict[str, Any]:
    fields = editor_presets.apply_preset(str(preset_id))
    return {"id": preset_id, "label": editor_presets.preset_label(preset_id), "fields": fields}


def apply_editor_fields(
    fields: Dict[str, Any],
    *,
    target_playerid: Optional[int] = None,
    wait: bool = False,
) -> Dict[str, Any]:
    """Apply arbitrary field dict as a card-like update onto target."""
    tid = target_playerid
    if tid is None:
        cfg = companion_config.load_config()
        tid = cfg.get("default_target_playerid")
    if tid is None:
        return {
            "ok": False,
            "outcome": "error",
            "reason": "target_playerid required",
            "applied": False,
            "queued": False,
        }
    card = dict(fields or {})
    if "name" not in card:
        card["name"] = f"editor_{tid}"
    return apply_card(card, target_playerid=int(tid), wait=wait)


# ── Catalog / about / history ────────────────────────────────────────


def get_catalog_info() -> Dict[str, Any]:
    cdb = paths.card_db_dir()
    sqlite = cdb / "catalog.sqlite"
    csvs = list(cdb.glob("*.csv")) if cdb.is_dir() else []
    return {
        "card_db": str(cdb),
        "sqlite_exists": sqlite.is_file(),
        "sqlite_bytes": sqlite.stat().st_size if sqlite.is_file() else 0,
        "csv_count": len(csvs),
        "csv_names": [p.name for p in csvs[:40]],
        "ok": health_check.snapshot().get("card_db_ok", False),
        "hint": "Use Cards search or rebuild catalog from CLI --rebuild-catalog",
    }


def get_about() -> Dict[str, Any]:
    return {
        "product": "LE Companion",
        "ui": "web",
        "description": (
            "Career Studio companion for FC 26 Live Editor. "
            "External control/config; inject-side DLL + Lua bridge; "
            "LE Launcher owns FakeEAAC."
        ),
        "surfaces": [
            "home",
            "cards",
            "boost",
            "add_team",
            "squad",
            "editor",
            "catalog",
            "about",
        ],
        "app_root": str(paths.app_root()),
        "le_root": str(paths.le_root()),
    }


def get_history(*, limit: int = 30) -> Dict[str, Any]:
    rows = job_history.load()
    lim = max(1, min(int(limit or 30), 100))
    return {"count": len(rows), "jobs": rows[:lim]}


def get_queue() -> Dict[str, Any]:
    pending = le_apply.list_pending_lua(force=True)
    return {
        "pending_jobs": len(pending),
        "pending_names": [p.name for p in pending[:40]],
        "status_line": le_apply.apply_status_line(short=False),
        "live": le_apply.bridge_alive(90),
        "busy": le_apply.bridge_busy(),
    }


# ── HTTP router (pure handlers — no socket) ──────────────────────────


def handle(
    method: str,
    path: str,
    *,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch API path to facade. Returns {status, data}."""
    q = query or {}
    b = body or {}
    method = (method or "GET").upper()
    path = (path or "/").split("?")[0]
    if not path.startswith("/"):
        path = "/" + path

    def ok(data: Any, status: int = 200) -> Dict[str, Any]:
        return {"status": status, "data": data}

    def err(msg: str, status: int = 400) -> Dict[str, Any]:
        return {"status": status, "data": {"ok": False, "error": msg, "reason": msg}}

    # Normalize /api prefix
    if path.startswith("/api/"):
        api = path[4:]  # keep leading /
    elif path == "/api":
        api = "/"
    else:
        api = path

    try:
        if api in ("/", "/health") and method == "GET":
            return ok({"ok": True, "service": "le-companion-web", "product": "LE Companion"})

        if api == "/status" and method == "GET":
            return ok(get_status())

        if api == "/home" and method == "GET":
            return ok(get_home())

        if api == "/about" and method == "GET":
            return ok(get_about())

        if api == "/catalog" and method == "GET":
            return ok(get_catalog_info())

        if api == "/queue" and method == "GET":
            return ok(get_queue())

        if api == "/history" and method == "GET":
            return ok(get_history(limit=int(q.get("limit") or 30)))

        if api == "/packs" and method == "GET":
            return ok(list_packs())

        if api == "/packs/run" and method == "POST":
            pid = b.get("pack_id") or b.get("id") or q.get("pack_id")
            if not pid:
                return err("pack_id required")
            return ok(run_pack(str(pid), wait=bool(b.get("wait", False))))

        if api == "/profiles" and method == "GET":
            return ok(list_profiles())

        if api == "/profiles/run" and method == "POST":
            pid = b.get("profile_id") or b.get("id")
            if not pid:
                return err("profile_id required")
            return ok(run_profile(str(pid), wait=bool(b.get("wait", False))))

        if api == "/cards/search" and method in ("GET", "POST"):
            src = b if method == "POST" else q
            ovr_raw = src.get("ovr")
            ovr = int(ovr_raw) if ovr_raw not in (None, "", "null") else None
            return ok(
                search_cards(
                    str(src.get("query") or src.get("q") or ""),
                    year=src.get("year"),
                    ovr=ovr,
                    limit=int(src.get("limit") or 40),
                )
            )

        if api == "/cards/apply" and method == "POST":
            card = b.get("card")
            if not card and b.get("query"):
                return ok(
                    apply_card_from_search(
                        str(b.get("query")),
                        target_playerid=int(b["target_playerid"]),
                        year=b.get("year"),
                        index=int(b.get("index") or 0),
                        wait=bool(b.get("wait", False)),
                    )
                )
            if not card:
                return err("card or query required")
            tid = b.get("target_playerid")
            return ok(
                apply_card(
                    dict(card),
                    target_playerid=int(tid) if tid is not None else None,
                    wait=bool(b.get("wait", False)),
                )
            )

        if api == "/target" and method == "GET":
            return ok(get_target())

        if api == "/target" and method == "POST":
            tid = b.get("playerid") or b.get("target_playerid")
            if tid is None:
                return err("playerid required")
            return ok(set_target(int(tid)))

        if api == "/squad" and method == "GET":
            return ok(get_squad())

        if api == "/squad/search" and method in ("GET", "POST"):
            src = b if method == "POST" else q
            return ok(search_targets(str(src.get("query") or src.get("q") or "")))

        if api == "/squad/export" and method == "POST":
            return ok(queue_export_squad(wait=bool(b.get("wait", False))))

        if api == "/add-team" and method == "POST":
            card = b.get("card")
            if not card:
                return err("card required")
            return ok(
                add_to_team(
                    dict(card),
                    teamid=int(b["teamid"]) if b.get("teamid") is not None else None,
                    mode=str(b.get("mode") or "auto"),
                    wait=bool(b.get("wait", False)),
                )
            )

        if api == "/editor/presets" and method == "GET":
            return ok(list_editor_presets())

        if api == "/editor/preset" and method == "GET":
            pid = q.get("id") or q.get("preset_id")
            if not pid:
                return err("id required")
            return ok(get_editor_preset(str(pid)))

        if api == "/editor/apply" and method == "POST":
            fields = b.get("fields") or b.get("card") or {}
            tid = b.get("target_playerid")
            return ok(
                apply_editor_fields(
                    dict(fields),
                    target_playerid=int(tid) if tid is not None else None,
                    wait=bool(b.get("wait", False)),
                )
            )

        if api == "/worker/install" and method == "POST":
            return ok(install_worker())

        if api == "/worker/go-live" and method == "POST":
            return ok(go_live())

        if api == "/worker/copy-bridge" and method == "POST":
            return ok(copy_bridge())

        return err(f"Unknown API route {method} {api}", 404)
    except Exception as e:  # noqa: BLE001 — surface as JSON for web client
        return {
            "status": 500,
            "data": {"ok": False, "error": str(e), "reason": str(e)},
        }
