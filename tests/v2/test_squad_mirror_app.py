"""Live squad mirror, app side: parsing, guards, shared ingest, Club indicator."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from companion.app.commands import squad as squad_cmd
from companion.app.commands import squad_mirror as sm
from companion.app.commands.apply import refresh_liveness
from companion.app.commands.squad import (
    ensure_squad_fresh,
    ingest_result,
    invalidate,
    is_stale,
)
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Pill
from companion.domain.outcome import JobResult

ROOT = Path(__file__).resolve().parents[2]
SESSION = "01FAKESESSION0000000000000"
ROWS = [
    {"playerid": 1005, "teamid": 45, "teamname": "Fake FC", "name": "Player 1005",
     "overallrating": 75, "potential": 80, "jerseynumber": 6, "birthdate": 150005},
    {"playerid": 1017, "teamid": 45, "teamname": "Fake FC", "name": "Seventeen",
     "overallrating": 77, "potential": 79, "jerseynumber": 18, "birthdate": 150017},
]


class FakeDB:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    def job_records(self, **_kw: Any) -> list[Any]:
        return []

    def put_save_snapshot(self, **kw: Any) -> None:
        self.snapshots.append(kw)

    def invalidate_save_snapshots(self, **_kw: Any) -> int:
        return 0

    def __getattr__(self, _name: str) -> Any:
        # Job bookkeeping (record_submitted, ...) is irrelevant here.
        return lambda *_a, **_kw: None


def _svc(tmp_path: Path, *, save_uid: str = "SAVE-A", native: bool = True) -> Services:
    svc = Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(start=1_700_000_100.0),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
        db=FakeDB(),
    )
    svc.transport.set_liveness(
        replace(svc.transport.liveness(), save_uid=save_uid, native_instant=native)
    )
    refresh_liveness(svc)
    return svc


def _mirror(**over: Any) -> dict[str, Any]:
    body = {
        "schema": 1, "kind": "squad_mirror", "seq": 1_700_000_000_000,
        "hash": "00000000000000aa", "session_id": SESSION, "save_uid": "SAVE-A",
        "core_version": "2.6.38", "generated_at": 1_700_000_098, "started_at": 1_700_000_090,
        "source": "native", "ticks": 12, "build_ms": 2400,
        "teamid": 45, "teamname": "Fake FC", "count": len(ROWS),
        "players": [dict(r) for r in ROWS], "writes_seen": [],
    }
    body.update(over)
    return body


_MTIME = [1_700_000_000_000_000_000]


def _write_mirror(svc: Services, body: dict[str, Any]) -> Path:
    path = sm.mirror_path(svc.paths)
    path.write_text(json.dumps(body), encoding="utf-8")
    _MTIME[0] += 1_000_000
    os.utime(path, ns=(_MTIME[0], _MTIME[0]))
    return path


def _export_result(jid: str, rows: list[dict[str, Any]], **extra: Any) -> JobResult:
    data = {"teamid": 45, "players": rows, **extra}
    return JobResult.from_wire({
        "job_id": jid, "state": "done", "ok": True, "session_id": SESSION,
        "counts": {"ops_total": 1, "ops_ok": 1}, "env": {"save_uid": "SAVE-A"},
        "ops": [{"id": "squad", "op": "export_squad", "ok": True,
                 "counts": {"found": len(rows)}, "data": data}],
    })


# ---- layout & parsing ------------------------------------------------------


def test_layout_creates_the_mirror_dir_the_core_cannot(tmp_path):
    paths = TempAppPaths(tmp_path)
    assert paths.mirror == paths.queue / "mirror"
    assert paths.mirror.is_dir()
    assert sm.mirror_path(paths) == paths.queue / "mirror" / "squad.json"


def test_parse_mirror_accepts_the_core_shape():
    m = sm.parse_mirror(_mirror(team_shirts=[{"playerid": 1005, "jerseynumber": 6}]))
    assert m is not None
    assert m.seq == 1_700_000_000_000 and m.save_uid == "SAVE-A" and m.teamid == 45
    assert [p["playerid"] for p in m.players] == [1005, 1017]
    assert m.build_ms == 2400 and m.writes_seen == frozenset()
    # data is export_squad's data shape, extra export fields included.
    assert m.data["players"] == ROWS and m.data["teamname"] == "Fake FC"
    assert m.data["team_shirts"] == [{"playerid": 1005, "jerseynumber": 6}]
    assert "seq" not in m.data and "hash" not in m.data and "save_uid" not in m.data


@pytest.mark.parametrize("bad", [
    None, [], "x",
    _mirror(schema=2),
    _mirror(kind="something_else"),
    _mirror(seq=0), _mirror(seq="7"), _mirror(seq=True),
    _mirror(hash=""),
    _mirror(players={"1005": {}}),
    _mirror(players=[{"name": "no id"}]),
    _mirror(writes_seen="01J"),
])
def test_parse_mirror_rejects_anything_else(bad):
    assert sm.parse_mirror(bad) is None


# ---- poll & guards ---------------------------------------------------------


def test_new_mirror_is_ingested_through_the_export_path(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    sq = svc.store.snapshot().squad
    assert [p["playerid"] for p in sq.players] == [1005, 1017]
    assert sq.save_uid == "SAVE-A" and sq.session_id == SESSION and sq.teamid == 45
    assert sq.stale is False
    # Same derived fields as an export ingest (age from birthdate).
    assert all("age" in p or p.get("birthdate") for p in sq.players)
    snap = svc.db.snapshots[-1]
    assert snap["save_uid"] == "SAVE-A" and snap["kind"] == "squad"
    assert [p["playerid"] for p in snap["payload"]["players"]] == [1005, 1017]
    memory = svc.squad_sync.mirror
    assert memory.ingested and memory.seq == 1_700_000_000_000
    assert memory.updated_ts == 1_700_000_098


def test_mirror_and_export_ingest_produce_the_same_squad(tmp_path):
    a = _svc(tmp_path / "a")
    ingest_result(a, _export_result("01J00000000000000000000001", [dict(r) for r in ROWS]))
    b = _svc(tmp_path / "b")
    _write_mirror(b, _mirror())
    assert sm.poll_mirror(b) == "ingested"
    sa, sb = a.store.snapshot().squad, b.store.snapshot().squad
    assert sa.players == sb.players
    assert (sa.save_uid, sa.session_id, sa.teamid) == (sb.save_uid, sb.session_id, sb.teamid)


def test_mirror_ingest_reuses_ingest_squad_data(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    seen: list[tuple[dict, dict]] = []
    real = squad_cmd.ingest_squad_data

    def spy(svc_, data, **kw):
        seen.append((dict(data), kw))
        return real(svc_, data, **kw)

    monkeypatch.setattr(squad_cmd, "ingest_squad_data", spy)
    _write_mirror(svc, _mirror(team_shirts=[{"playerid": 1005, "jerseynumber": 6}]))
    assert sm.poll_mirror(svc) == "ingested"
    data, kw = seen[0]
    assert kw["source"] == "mirror" and kw["save_uid"] == "SAVE-A" and kw["session_id"] == SESSION
    assert data["team_shirts"] == [{"playerid": 1005, "jerseynumber": 6}]


def test_mirror_keeps_the_free_agent_probe_of_the_same_save(tmp_path):
    svc = _svc(tmp_path)
    free = [{"playerid": 1300, "teamid": 111592, "overallrating": 60}]
    ingest_result(svc, _export_result("01J00000000000000000000001", [dict(r) for r in ROWS],
                                      free_agents=free))
    _write_mirror(svc, _mirror(generated_at=1_700_000_100))
    assert sm.poll_mirror(svc) == "ingested"
    assert list(svc.store.snapshot().squad.free_agents) == free


def test_unchanged_file_is_one_stat(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    reads = []
    monkeypatch.setattr(sm, "read_json", lambda p: reads.append(p))
    for _ in range(5):
        assert sm.poll_mirror(svc) == "unchanged"
    assert reads == []


def test_absent_file(tmp_path):
    assert sm.poll_mirror(_svc(tmp_path)) == "absent"


def test_mirror_of_another_save_is_never_ingested(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror(save_uid="SAVE-B"))
    assert sm.poll_mirror(svc) == "other_save"
    assert svc.store.snapshot().squad.players == ()
    assert sm.poll_mirror(svc) == "unchanged", "a rejected file is examined once"


def test_mirror_waits_while_the_live_save_is_unknown(tmp_path):
    svc = _svc(tmp_path, save_uid="")
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "save_unknown"
    svc.transport.set_liveness(replace(svc.transport.liveness(), save_uid="SAVE-A"))
    refresh_liveness(svc)
    assert sm.poll_mirror(svc) == "ingested", "transient: retried next poll"


def test_mirror_from_another_core_session_is_ignored(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror(session_id="OLDSESSION"))
    assert sm.poll_mirror(svc) == "other_session"
    assert svc.store.snapshot().squad.players == ()


def test_not_armed_is_transient(tmp_path):
    svc = _svc(tmp_path)
    svc.transport.set_liveness(replace(svc.transport.liveness(), armed=False, pill=Pill.OFF))
    refresh_liveness(svc)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "not_armed"
    assert svc.squad_sync.mirror.file_sig is None


def test_seq_must_increase(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror(seq=50, hash="aa"))
    assert sm.poll_mirror(svc) == "ingested"
    _write_mirror(svc, _mirror(seq=49, hash="bb", players=[dict(ROWS[0])]))
    assert sm.poll_mirror(svc) == "older_seq"
    assert len(svc.store.snapshot().squad.players) == 2
    _write_mirror(svc, _mirror(seq=50, hash="aa"))
    assert sm.poll_mirror(svc) == "seen"


def test_mirror_older_than_the_current_snapshot_is_ignored(tmp_path):
    svc = _svc(tmp_path)
    ingest_result(svc, _export_result("01J00000000000000000000001", [dict(r) for r in ROWS]))
    # Generated well before the export was ingested (clock is 1_700_000_100).
    _write_mirror(svc, _mirror(generated_at=1_700_000_050, players=[dict(ROWS[0])]))
    assert sm.poll_mirror(svc) == "older_than_snapshot"
    assert len(svc.store.snapshot().squad.players) == 2


def test_invalidated_squad_needs_a_mirror_built_after_the_write(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror(seq=10, hash="a1"))
    assert sm.poll_mirror(svc) == "ingested"
    invalidate(svc, reason="boost applied", job_id="01JWRITE00000000000000000A")
    assert svc.store.snapshot().squad.stale is True
    # A mirror that predates the write cannot clear it.
    _write_mirror(svc, _mirror(seq=11, hash="a2", generated_at=1_700_000_101))
    assert sm.poll_mirror(svc) == "awaiting_write"
    assert svc.store.snapshot().squad.stale is True
    # The core names the job once it ran.
    upd = [dict(ROWS[0], overallrating=90), dict(ROWS[1])]
    _write_mirror(svc, _mirror(seq=12, hash="a3", generated_at=1_700_000_102,
                               players=upd, writes_seen=["01JWRITE00000000000000000A"]))
    assert sm.poll_mirror(svc) == "ingested"
    sq = svc.store.snapshot().squad
    assert sq.stale is False and sq.players[0]["overallrating"] == 90
    assert svc.squad_sync.mirror.pending_writes == set()


# ---- tracking: TTL and the export fallback ---------------------------------


def test_tracking_mirror_replaces_the_ttl_export(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    svc.clock.advance(squad_cmd.TTL_SECONDS + 60)
    assert sm.mirror_tracking(svc) is True
    assert is_stale(svc) is False
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []


def test_without_the_native_tick_the_ttl_export_still_runs(tmp_path):
    svc = _svc(tmp_path, native=False)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    svc.clock.advance(squad_cmd.TTL_SECONDS + 60)
    assert sm.mirror_tracking(svc) is False
    assert is_stale(svc) is True
    assert ensure_squad_fresh(svc) is not None
    assert [op.op for op in svc.transport.submitted[0].ops] == ["export_squad"]


def test_write_waits_for_the_mirror_then_falls_back_to_an_export(tmp_path):
    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    invalidate(svc, reason="boost applied", job_id="01JWRITE00000000000000000A")
    assert ensure_squad_fresh(svc) is None, "the mirror is due; no export yet"
    assert svc.transport.submitted == []
    svc.clock.advance(sm.grace_seconds(svc) + 1)
    assert ensure_squad_fresh(svc) is not None
    assert [op.op for op in svc.transport.submitted[0].ops] == ["export_squad"]


def test_grace_scales_with_the_last_build_time(tmp_path):
    svc = _svc(tmp_path)
    assert sm.grace_seconds(svc) == sm.GRACE_MIN_SECONDS
    svc.squad_sync.mirror.build_seconds = 30.0
    assert sm.grace_seconds(svc) == 70.0
    svc.squad_sync.mirror.build_seconds = 500.0
    assert sm.grace_seconds(svc) == sm.GRACE_MAX_SECONDS


def test_export_ingest_settles_pending_writes(tmp_path):
    svc = _svc(tmp_path)
    invalidate(svc, reason="x", job_id="01JWRITE00000000000000000A")
    ingest_result(svc, _export_result("01J00000000000000000000001", [dict(r) for r in ROWS]))
    assert svc.squad_sync.mirror.pending_writes == set()


# ---- Club indicator --------------------------------------------------------


@pytest.mark.parametrize("age, text", [
    (0.0, "Live · updated 0 s ago"),
    (3.4, "Live · updated 3 s ago"),
    (9.9, "Live · updated 9 s ago"),
    (10.0, ""),
    (45.0, ""),
    (None, ""),
])
def test_freshness_text(age, text):
    assert sm.freshness_text(age) == text


def test_live_indicator_follows_the_last_mirror_and_the_session(tmp_path):
    svc = _svc(tmp_path)
    assert sm.live_indicator_text(svc) == ""
    _write_mirror(svc, _mirror(generated_at=1_700_000_097))
    assert sm.poll_mirror(svc) == "ingested"
    assert sm.live_indicator_text(svc) == "Live · updated 3 s ago"
    svc.clock.advance(6.5)
    assert sm.live_indicator_text(svc) == "Live · updated 9 s ago"
    svc.clock.advance(1.0)
    assert sm.live_indicator_text(svc) == ""
    # A disconnected bridge never shows Live.
    svc.clock.advance(-7.0)
    svc.transport.set_liveness(replace(svc.transport.liveness(), pill=Pill.STALLED))
    refresh_liveness(svc)
    assert sm.live_indicator_text(svc) == ""


def test_club_drops_only_the_ttl_flag_while_tracking(tmp_path):
    from companion.ui.surfaces.club import club_squad_view

    svc = _svc(tmp_path)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    svc.clock.advance(300)
    view = club_squad_view(svc, svc.store.snapshot(), svc.clock.now())
    assert view["stale"] is False and "aged" not in view["caption"]
    invalidate(svc, reason="x")
    view = club_squad_view(svc, svc.store.snapshot(), svc.clock.now())
    assert view["stale"] is True, "a write still shows as stale"


def test_helpers_tolerate_a_minimal_services_stand_in():
    from types import SimpleNamespace

    stub = SimpleNamespace(store=Store())
    assert sm.mirror_tracking(stub) is False
    assert sm.live_indicator_text(stub) == ""


def test_shell_polls_the_mirror_before_the_export_coordinator_and_ages_the_indicator():
    src = (ROOT / "companion" / "ui" / "shell.py").read_text(encoding="utf-8")
    assert src.index('("mirror", lambda: poll_mirror(self.svc))') < src.index(
        '("squad", lambda: ensure_squad_fresh(self.svc))'
    )
    assert "refresh_live_indicator" in src
    club = (ROOT / "companion" / "ui" / "surfaces" / "club.py").read_text(encoding="utf-8")
    assert "root.refresh_live_indicator = refresh_live_indicator" in club
    assert "after(" not in club.split("def refresh_live_indicator", 1)[1].split("refresh_is_primary", 1)[0]


def test_core_that_rebuilds_after_writes_holds_the_export_without_native(tmp_path):
    """Core 2.6.41 publishes the mirror in the write's own hub frame, so a
    follow-up export (which needs a second hub event) is only a fallback."""
    svc = _svc(tmp_path, native=False)
    live = svc.transport.liveness()
    caps = dict(live.capabilities or {}, hub_ready=True, mirror_after_write=True)
    svc.transport.set_liveness(replace(live, capabilities=caps))
    refresh_liveness(svc)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    invalidate(svc, reason="signing applied", job_id="01JWRITE00000000000000000A")
    assert sm.awaiting_mirror(svc) is True
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []
    svc.clock.advance(sm.grace_seconds(svc) + 1)
    assert ensure_squad_fresh(svc) is not None


def test_older_core_without_native_falls_back_to_the_export_at_once(tmp_path):
    svc = _svc(tmp_path, native=False)
    _write_mirror(svc, _mirror())
    assert sm.poll_mirror(svc) == "ingested"
    invalidate(svc, reason="signing applied", job_id="01JWRITE00000000000000000A")
    assert sm.awaiting_mirror(svc) is False
