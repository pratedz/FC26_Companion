"""Live squad command: automatic and late ingestion with honest errors."""

from __future__ import annotations

import inspect
from dataclasses import replace

from companion.app import events as E
from companion.app.commands.apply import collect_finished, refresh_liveness
from companion.app.commands.squad import (
    ensure_squad_fresh,
    ingest_result,
    sync_squad,
)
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.outcome import JobResult


def _svc(tmp_path) -> Services:
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
    )


def _export_wire(
    jid: str,
    *,
    players=None,
    state="done",
    ok=True,
    save_uid="SAVE-A",
    teamid=241,
    session_id="",
):
    rows = players or []
    wire = {
        "job_id": jid,
        "state": state,
        "ok": ok,
        "counts": {"ops_total": 1, "ops_ok": 1 if ok else 0},
        "env": {"save_uid": save_uid},
        "ops": [
            {
                "id": "squad",
                "op": "export_squad",
                "ok": ok,
                "counts": {"targets": len(rows), "found": len(rows)},
                "data": {"teamid": teamid, "players": rows},
            }
        ],
    }
    if session_id:
        wire["session_id"] = session_id
    return wire


def test_auto_sync_waits_until_career_mode(tmp_path):
    svc = _svc(tmp_path)
    svc.transport._liveness = replace(svc.transport._liveness, capabilities={})
    refresh_liveness(svc)
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []


def test_auto_sync_ignores_a_career_flag_without_a_loaded_save(tmp_path):
    """IsInCM on the career menus must not queue a squad read."""
    svc = _svc(tmp_path)
    svc.transport._liveness = replace(
        svc.transport._liveness,
        capabilities={"in_career": True, "save_ready": True},
    )
    refresh_liveness(svc)
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []


def test_ensure_does_not_stack_a_second_sync_while_one_is_active(tmp_path):
    svc = _svc(tmp_path)
    refresh_liveness(svc)

    first = ensure_squad_fresh(svc)
    second = ensure_squad_fresh(svc)

    assert first is not None
    assert second is None
    assert len(svc.transport.submitted) == 1
    assert svc.transport.submitted[0].budget_ms == 5000


def test_late_export_is_ingested_by_general_collector(tmp_path):
    svc = _svc(tmp_path)
    jid = sync_squad(svc).job_id
    players = [{"playerid": 10, "name": "Ten", "teamid": 241}]
    svc.transport.complete(jid, _export_wire(jid, players=players))

    done = collect_finished(svc)

    assert len(done) == 1
    assert svc.store.snapshot().squad.players == tuple(players)
    assert "1 live player" in svc.store.snapshot().status


def test_empty_export_does_not_replace_cached_squad(tmp_path):
    svc = _svc(tmp_path)
    cached = ({"playerid": 7, "name": "Cached"},)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-A",
            teamid=241,
            players=cached,
            taken=svc.clock.now(),
        )
    )
    result = JobResult.from_wire(_export_wire("J", players=[]))

    assert ingest_result(svc, result)
    assert svc.store.snapshot().squad.players == cached
    assert "no squad players" in svc.store.snapshot().status.lower()


def test_failed_export_explains_career_mode_action(tmp_path):
    svc = _svc(tmp_path)
    wire = _export_wire("J", players=[], state="rejected", ok=False)
    wire["failures"] = [
        {
            "op": "export_squad",
            "reason": "career_required",
            "detail": "Career Mode save is not loaded",
        }
    ]
    result = JobResult.from_wire(wire)

    assert ingest_result(svc, result)
    status = svc.store.snapshot().status
    assert "Career Mode save is not loaded" in status
    assert "Refresh squad" in status


def test_live_refresh_reconciles_or_clears_squad_target(tmp_path):
    svc = _svc(tmp_path)
    svc.store.dispatch(E.TargetLocked(10, "Old name", "squad", 99))
    ingest_result(
        svc,
        JobResult.from_wire(
            _export_wire("J1", players=[{"playerid": 10, "name": "Live name"}])
        ),
    )
    target = svc.store.snapshot().target
    assert target.name == "Live name" and target.teamid == 241

    ingest_result(
        svc,
        JobResult.from_wire(
            _export_wire("J2", players=[{"playerid": 11, "name": "Other"}])
        ),
    )
    assert not svc.store.snapshot().target.locked


def test_player_picker_does_not_offer_rows_from_stale_team_cache(
    tmp_path, monkeypatch
):
    """Chelsea from yesterday must not look selectable in today's Barcelona save."""
    from companion.ui.surfaces import player as player_surface

    svc = _svc(tmp_path)
    chelsea = ({"playerid": 1, "name": "Chelsea player", "teamid": 5},)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="CHELSEA-SAVE",
            teamid=5,
            players=chelsea,
            taken=svc.clock.now(),
        )
    )
    svc.store.dispatch(E.SquadInvalidated(reason="startup cache"))

    live = svc.store.snapshot().bridge.liveness
    squad = svc.store.snapshot().squad
    assert not player_surface._has_verified_live_squad(squad, live)
    source = inspect.getsource(player_surface._build_target_picker)
    assert "Old squad caches are deliberately unavailable" in source


def test_old_session_export_is_ignored_then_current_live_team_replaces_cache(tmp_path):
    """A late result from an earlier LE session cannot win a save/team switch."""
    svc = _svc(tmp_path)
    chelsea = ({"playerid": 1, "name": "Chelsea player", "teamid": 5},)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="CHELSEA-SAVE",
            teamid=5,
            players=chelsea,
            taken=svc.clock.now(),
        )
    )
    svc.store.dispatch(E.SquadInvalidated(reason="startup cache"))
    refresh_liveness(svc)
    current_session = svc.store.snapshot().bridge.liveness.session_id

    late_old = JobResult.from_wire(
        _export_wire(
            "OLD-JOB",
            players=[{"playerid": 2, "name": "Old session player"}],
            save_uid="OLD-SAVE",
            teamid=10,
            session_id="PREVIOUS-SESSION",
        )
    )
    assert ingest_result(svc, late_old)
    after_old = svc.store.snapshot().squad
    # Connecting to a different worker session clears yesterday's team rather
    # than preserving it as an actionable fallback.
    assert after_old.players == ()
    assert after_old.stale

    barcelona = [
        {"playerid": 3, "name": "Pedri", "teamid": 241},
        {"playerid": 4, "name": "Lamine Yamal", "teamid": 241},
    ]
    fresh = JobResult.from_wire(
        _export_wire(
            "NEW-JOB",
            players=barcelona,
            save_uid="BARCELONA-SAVE",
            teamid=241,
            session_id=current_session,
        )
    )
    assert ingest_result(svc, fresh)
    squad = svc.store.snapshot().squad
    assert squad.save_uid == "BARCELONA-SAVE"
    assert squad.teamid == 241
    assert squad.players == tuple(barcelona)
    assert not squad.stale
