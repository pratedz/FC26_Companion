"""Live squad mirror — follow ``queue/mirror/squad.json`` written by the core.

Since core 2.6.38 the native plugin ticks the core on a settled Career hub.
The core keeps a copy of the user's senior squad in ``queue/mirror/squad.json``
(``ingame/le_companion/mirror.lua``): rebuilt in small slices when something
may have changed, written atomically and only when its content changed. The
rows are exactly export_squad's rows, so this module only decides *whether*
a mirror may be ingested and then hands it to ``squad.ingest_squad_data`` —
the same path a Refresh result takes.

Guards, in order (a rejected mirror is examined once per new seq/hash):

* the bridge is armed and the mirror comes from the live core session;
* its save is the live save — never a mirror of another save;
* it is newer than what the squad view already shows (seq, generation time);
* when a write job invalidated the squad, the mirror was built after that job
  ran (``writes_seen`` names it) — an older mirror cannot clear the flag.

The poll is one ``stat`` when nothing changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ...core.transport.jobfile import read_json
from ..services import Services

MIRROR_SCHEMA = 1
MIRROR_KIND = "squad_mirror"
MIRROR_FILE = "squad.json"
#: The Club page calls the squad "Live" this long after a mirror update.
FRESH_SECONDS = 10.0
#: Wait at least this long for a post-write mirror before falling back to an
#: export_squad job; longer when the last rebuild itself took longer.
GRACE_MIN_SECONDS = 20.0
GRACE_MAX_SECONDS = 120.0
#: Slack between the core's whole-second stamp and the app's clock.
CLOCK_SLACK_SECONDS = 1.0

_META_KEYS = frozenset({
    "schema", "kind", "seq", "hash", "session_id", "core_version",
    "generated_at", "started_at", "source", "ticks", "build_ms",
    "writes_seen", "save_uid", "count",
})


@dataclass(frozen=True, slots=True)
class SquadMirror:
    schema: int
    seq: int
    hash: str
    session_id: str
    save_uid: str
    teamid: int | None
    generated_at: float
    build_ms: int
    core_version: str
    players: tuple[Mapping[str, Any], ...]
    writes_seen: frozenset[str]
    #: export_squad ``data`` shape: players, teamid, teamname and any extra
    #: export fields the core carried along (e.g. team_shirts).
    data: Mapping[str, Any] = field(default_factory=dict)


def mirror_path(paths: Any) -> Path:
    base = getattr(paths, "mirror", None)
    if base is None:
        base = Path(paths.queue) / "mirror"
    return Path(base) / MIRROR_FILE


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def parse_mirror(raw: Any) -> SquadMirror | None:
    """A validated mirror, or None for anything this app does not understand."""
    if not isinstance(raw, Mapping):
        return None
    if _int(raw.get("schema")) != MIRROR_SCHEMA:
        return None
    if raw.get("kind", MIRROR_KIND) != MIRROR_KIND:
        return None
    seq = _int(raw.get("seq"))
    digest = raw.get("hash")
    if seq is None or seq <= 0 or not isinstance(digest, str) or not digest:
        return None
    players = raw.get("players")
    if not isinstance(players, list):
        return None
    rows: list[Mapping[str, Any]] = []
    for row in players:
        if not isinstance(row, Mapping) or _int(row.get("playerid")) is None:
            return None
        rows.append(row)
    writes = raw.get("writes_seen", [])
    if not isinstance(writes, list):
        return None
    generated = raw.get("generated_at")
    generated_at = float(generated) if isinstance(generated, (int, float)) and not isinstance(generated, bool) else 0.0
    build_ms = _int(raw.get("build_ms")) or 0
    data = {k: v for k, v in raw.items() if k not in _META_KEYS}
    data["players"] = rows
    return SquadMirror(
        schema=MIRROR_SCHEMA,
        seq=seq,
        hash=digest,
        session_id=str(raw.get("session_id") or ""),
        save_uid=str(raw.get("save_uid") or ""),
        teamid=_int(raw.get("teamid")),
        generated_at=generated_at,
        build_ms=max(0, build_ms),
        core_version=str(raw.get("core_version") or ""),
        players=tuple(rows),
        writes_seen=frozenset(str(w) for w in writes if isinstance(w, str)),
        data=data,
    )


def note_pending_write(svc: Services, job_id: str) -> None:
    """A write job invalidated the squad; only a later mirror may clear it."""
    memory = svc.squad_sync.mirror
    memory.pending_writes.add(job_id)
    if memory.waiting_since_ts is None:
        memory.waiting_since_ts = svc.clock.now_ts()


def _verdict(svc: Services, mirror: SquadMirror) -> tuple[str, bool]:
    """("", _) to ingest, else (reason, transient). Transient reasons retry."""
    state = svc.store.snapshot()
    live = state.bridge.liveness
    memory = svc.squad_sync.mirror
    if not live.armed or not live.session_id:
        return "not_armed", True
    if mirror.session_id != live.session_id:
        return "other_session", False
    live_save = live.save_uid or ""
    if live_save != mirror.save_uid:
        # No live save id yet while the core has one: session.json lags.
        return ("save_unknown", True) if not live_save else ("other_save", False)
    if not mirror.players:
        return "empty", False
    if memory.ingested and memory.session_id == mirror.session_id and mirror.seq <= memory.seq:
        return "older_seq", False
    sq = state.squad
    if (
        sq.taken is not None
        and sq.save_uid == mirror.save_uid
        and mirror.generated_at > 0
        and mirror.generated_at + CLOCK_SLACK_SECONDS < sq.taken.timestamp()
    ):
        return "older_than_snapshot", False
    if memory.pending_writes and not memory.pending_writes <= mirror.writes_seen:
        return "awaiting_write", False
    return "", False


def poll_mirror(svc: Services) -> str:
    """Ingest a new mirror when it passes every guard. Returns what happened."""
    path = mirror_path(svc.paths)
    try:
        stat = path.stat()
    except OSError:
        return "absent"
    memory = svc.squad_sync.mirror
    sig = (int(stat.st_mtime_ns), int(stat.st_size))
    if sig == memory.file_sig:
        return "unchanged"
    mirror = parse_mirror(read_json(path))
    if mirror is None:
        memory.file_sig = sig
        return "invalid"
    if (
        mirror.session_id == memory.session_id
        and mirror.seq == memory.seq
        and mirror.hash == memory.hash
    ):
        memory.file_sig = sig
        return "seen"
    reason, transient = _verdict(svc, mirror)
    if reason:
        memory.last_skip = reason
        if not transient:
            # Final for this file; the core writes a new one when it changes.
            memory.file_sig = sig
        _trace(svc, f"skip reason={reason} seq={mirror.seq}")
        return reason
    from .squad import ingest_squad_data

    if not ingest_squad_data(
        svc,
        mirror.data,
        save_uid=mirror.save_uid,
        session_id=mirror.session_id,
        source="mirror",
    ):
        memory.file_sig = sig
        return "empty"
    memory.file_sig = sig
    memory.session_id = mirror.session_id
    memory.save_uid = mirror.save_uid
    memory.seq = mirror.seq
    memory.hash = mirror.hash
    memory.ingested = True
    now = svc.clock.now_ts()
    generated = mirror.generated_at or now
    memory.updated_ts = min(generated, now)
    memory.build_seconds = mirror.build_ms / 1000.0
    memory.last_skip = ""
    memory.pending_writes -= mirror.writes_seen
    if not memory.pending_writes:
        memory.waiting_since_ts = None
    _trace(svc, f"ingest seq={mirror.seq} players={len(mirror.players)}")
    return "ingested"


def _trace(svc: Services, text: str) -> None:
    from .sync_trace import sync_log

    sync_log("MIRROR", text)


def _memory(svc: Any) -> Any:
    """The mirror memory, or None on a minimal services stand-in."""
    return getattr(getattr(svc, "squad_sync", None), "mirror", None)


def mirror_tracking(svc: Services) -> bool:
    """True while the core is keeping the mirror current for the shown squad.

    Needs the native tick (without it the core rebuilds only on hub events),
    a settled hub, and a squad view that came from this session and save.
    """
    memory = _memory(svc)
    if memory is None:
        return False
    state = svc.store.snapshot()
    live = state.bridge.liveness
    if not memory.ingested or not live.armed or not live.session_id:
        return False
    if memory.session_id != live.session_id or not getattr(live, "native_instant", False):
        return False
    if not (live.capabilities or {}).get("hub_ready"):
        return False
    if (live.save_uid or "") != memory.save_uid:
        return False
    sq = state.squad
    return sq.session_id == live.session_id and (sq.save_uid or "") == memory.save_uid


def grace_seconds(svc: Services) -> float:
    build = svc.squad_sync.mirror.build_seconds
    return max(GRACE_MIN_SECONDS, min(GRACE_MAX_SECONDS, 2.0 * build + 10.0))


def awaiting_mirror(svc: Services) -> bool:
    """Hold the export fallback while a post-write mirror is due."""
    memory = svc.squad_sync.mirror
    if not memory.pending_writes or memory.waiting_since_ts is None:
        return False
    if not mirror_tracking(svc):
        return False
    return (svc.clock.now_ts() - memory.waiting_since_ts) < grace_seconds(svc)


def freshness_text(age_seconds: float | None) -> str:
    """The Club indicator: "Live · updated 3 s ago" while fresh, else ""."""
    if age_seconds is None or age_seconds >= FRESH_SECONDS:
        return ""
    return f"Live · updated {max(0, int(age_seconds))} s ago"


def live_indicator_text(svc: Services) -> str:
    """Indicator text for the current session, "" when not fresh."""
    from ...core.transport.v3 import Pill

    memory = _memory(svc)
    clock = getattr(svc, "clock", None)
    if memory is None or clock is None:
        return ""
    live = svc.store.snapshot().bridge.liveness
    if not memory.ingested or memory.updated_ts is None:
        return ""
    if live.pill not in (Pill.LIVE, Pill.ARMED) or live.session_id != memory.session_id:
        return ""
    return freshness_text(max(0.0, clock.now_ts() - memory.updated_ts))


def apply_mirror_tracking(view: dict[str, Any], svc: Services) -> dict[str, Any]:
    """Club's squad view: no "aged" warning while the mirror is tracking.

    Staleness from a write or a session change still shows; only the
    TTL-based "aged" flag is dropped, because an unchanged squad produces no
    new mirror file by design.
    """
    if not view.get("ttl_stale") or view.get("flag_stale") or view.get("session_stale"):
        return view
    if not mirror_tracking(svc):
        return view
    out = dict(view)
    out["ttl_stale"] = False
    out["stale"] = False
    out["needs_refresh"] = not out.get("players")
    out["caption"] = str(out.get("caption", "")).replace(" · aged", "")
    return out
