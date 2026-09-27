"""Resolve target player name → precise in-game playerid.

Primary source: current_squad.json (exported from Live Editor via
bridge/export_user_squad.lua while in Career Mode).

Fallback: card_db name hits (base/special ids) when no squad file exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import card_catalog
from . import paths

SquadPlayer = Dict[str, Any]


def current_squad_path() -> Path:
    return paths.app_root() / "current_squad.json"


def load_squad(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Load squad JSON. Returns empty structure if missing/invalid."""
    p = Path(path) if path is not None else current_squad_path()
    empty: Dict[str, Any] = {
        "mode": None,
        "teamid": 0,
        "teamname": "",
        "count": 0,
        "players": [],
        "free_agents": [],
        "dummy_pool": [],
        "path": str(p),
        "loaded": False,
    }
    if not p.is_file():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    players = data.get("players") or []
    if not isinstance(players, list):
        players = []
    normed: List[SquadPlayer] = []
    for raw in players:
        if not isinstance(raw, dict):
            continue
        try:
            pid = int(raw.get("playerid"))
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        normed.append(
            {
                "playerid": pid,
                "name": str(raw.get("name") or "").strip(),
                "position": str(raw.get("position") or "").strip(),
                "overallrating": _safe_int(raw.get("overallrating")),
                "potential": _safe_int(raw.get("potential")),
                "jerseynumber": _safe_int(raw.get("jerseynumber")),
                "source": "squad",
            }
        )
    # SAFE add-to-team pool (export_user_squad.lua free_agents / dummy_pool)
    free_agents = _normalize_free_agent_pool(
        data.get("free_agents") or data.get("dummy_pool") or []
    )
    return {
        "mode": data.get("mode") or "career",
        "teamid": _safe_int(data.get("teamid")) or 0,
        "teamname": str(data.get("teamname") or ""),
        "count": len(normed),
        "players": normed,
        "free_agents": free_agents,
        "dummy_pool": free_agents,
        "path": str(p),
        "loaded": True,
    }


def _normalize_free_agent_pool(raw_pool: Any) -> List[Dict[str, Any]]:
    """Normalize free-agent / dummy pool rows from squad export JSON."""
    if not isinstance(raw_pool, list):
        return []
    out: List[Dict[str, Any]] = []
    for raw in raw_pool:
        if isinstance(raw, (int, float)) or (
            isinstance(raw, str) and str(raw).strip().isdigit()
        ):
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                out.append(
                    {
                        "playerid": pid,
                        "overallrating": 50,
                        "teamid": 111592,
                        "free_agent": True,
                        "source": "free_agent",
                    }
                )
            continue
        if not isinstance(raw, dict):
            continue
        try:
            pid = int(raw.get("playerid") or raw.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        out.append(
            {
                "playerid": pid,
                "overallrating": _safe_int(raw.get("overallrating")) or 50,
                "teamid": _safe_int(raw.get("teamid")) or 111592,
                "free_agent": True,
                "source": str(raw.get("source") or "free_agent"),
            }
        )
    return out


def free_agent_pool(
    squad: Optional[Dict[str, Any]] = None,
    *,
    path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Return free-agent dummy candidates for SAFE add-to-team.

    Always prefers keys already on the squad dict (from load_squad). If the
    free_agents/dummy_pool keys are missing entirely, re-reads current_squad.json
    so a stale in-memory shape cannot silently wipe the pool (FA · 0 regression).

    Explicit empty lists mean “no FA” (do not fall back to disk).
    """
    sq = squad if squad is not None else load_squad(path)
    has_fa_key = "free_agents" in sq or "dummy_pool" in sq
    pool = _normalize_free_agent_pool(
        sq.get("free_agents") or sq.get("dummy_pool") or []
    )
    if pool:
        return pool
    # Explicit empty pool on a fully-loaded squad → trust it
    if has_fa_key and squad is not None:
        return []
    # Keys missing (old load_squad shape) → re-read file
    p = Path(path) if path is not None else Path(str(sq.get("path") or current_squad_path()))
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    return _normalize_free_agent_pool(
        data.get("free_agents") or data.get("dummy_pool") or []
    )


def free_agent_count(
    squad: Optional[Dict[str, Any]] = None,
    *,
    path: Optional[Union[str, Path]] = None,
) -> int:
    return len(free_agent_pool(squad, path=path))


def _safe_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    s = (s or "").casefold().strip()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> List[str]:
    n = _norm(s)
    return [t for t in n.split(" ") if t]


def _score_name(query: str, name: str) -> float:
    """Higher = better match. 0 = no match."""
    q = _norm(query)
    n = _norm(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 100.0
    if n.startswith(q) or q.startswith(n):
        return 90.0
    if q in n:
        return 80.0 + min(10.0, 10.0 * len(q) / max(len(n), 1))
    # all query tokens present as substrings / word starts
    qtoks = _tokens(query)
    ntoks = _tokens(name)
    if not qtoks:
        return 0.0
    hits = 0
    for qt in qtoks:
        for nt in ntoks:
            if nt == qt or nt.startswith(qt) or qt in nt:
                hits += 1
                break
    if hits == len(qtoks):
        return 60.0 + 20.0 * (hits / max(len(ntoks), 1))
    if hits > 0:
        return 20.0 * hits / len(qtoks)
    return 0.0


def search_squad(
    query: str,
    *,
    limit: int = 40,
    path: Optional[Union[str, Path]] = None,
) -> List[SquadPlayer]:
    """Search current squad by player name. Empty if no squad / no matches."""
    squad = load_squad(path)
    q = (query or "").strip()
    if not q:
        # return full squad (capped) for browsing
        players = list(squad.get("players") or [])
        players.sort(key=lambda p: (_norm(str(p.get("name") or "")), p.get("playerid") or 0))
        return players[:limit]

    scored: List[tuple[float, SquadPlayer]] = []
    for p in squad.get("players") or []:
        sc = _score_name(q, str(p.get("name") or ""))
        # also allow exact playerid query
        if sc <= 0 and q.isdigit():
            try:
                if int(q) == int(p.get("playerid") or 0):
                    sc = 100.0
            except (TypeError, ValueError):
                pass
        if sc > 0:
            scored.append((sc, p))
    scored.sort(key=lambda t: (-t[0], -(_safe_int(t[1].get("overallrating")) or 0), _norm(str(t[1].get("name") or ""))))
    return [p for _, p in scored[:limit]]


def search_target(
    query: str,
    *,
    limit: int = 40,
    include_catalog_fallback: bool = True,
    year: Optional[str] = "26",
    squad_path: Optional[Union[str, Path]] = None,
) -> List[SquadPlayer]:
    """Prefer squad matches; optionally fall back to card_db name → playerid."""
    hits = search_squad(query, limit=limit, path=squad_path)
    if hits or not include_catalog_fallback:
        return hits

    q = (query or "").strip()
    if not q or q.isdigit():
        return hits

    try:
        cards = card_catalog.search_cards(q, year=year, limit=limit)
    except Exception:
        return hits

    seen: set[int] = set()
    out: List[SquadPlayer] = []
    for c in cards:
        pid = _safe_int(c.get("playerid"))
        if pid is None or pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        out.append(
            {
                "playerid": pid,
                "name": str(c.get("name") or "").strip(),
                "position": str(c.get("position") or c.get("preferredposition1") or "").strip(),
                "overallrating": _safe_int(c.get("overallrating")),
                "potential": None,
                "jerseynumber": None,
                "source": "catalog",
                "year": c.get("year"),
                "revision": c.get("revision"),
            }
        )
        if len(out) >= limit:
            break
    return out


def format_target_line(p: SquadPlayer, index: int = 0) -> str:
    name = p.get("name") or "?"
    pid = p.get("playerid", "?")
    ovr = p.get("overallrating")
    ovr_s = f"OVR {ovr}" if ovr is not None else "OVR —"
    pos = p.get("position") or ""
    jersey = p.get("jerseynumber")
    bits = [f"[{index}] {name}", ovr_s]
    if pos:
        bits.append(str(pos))
    if jersey not in (None, "", 0, "0"):
        bits.append(f"#{jersey}")
    bits.append(f"id={pid}")
    src = p.get("source")
    if src and src != "squad":
        bits.append(f"({src})")
    return " | ".join(bits)


def squad_status_line(path: Optional[Union[str, Path]] = None) -> str:
    s = load_squad(path)
    if not s.get("loaded"):
        return "No squad export yet — run Export squad (Career Mode + LE)"
    team = s.get("teamname") or "?"
    tid = s.get("teamid") or 0
    n = s.get("count") or 0
    mode = s.get("mode") or "career"
    fa = len(s.get("free_agents") or s.get("dummy_pool") or [])
    if fa:
        return f"Squad: {n} players · {team} ({tid}) · {mode} · FA pool {fa}"
    return f"Squad: {n} players · {team} ({tid}) · {mode} · FA pool 0 (re-export)"
