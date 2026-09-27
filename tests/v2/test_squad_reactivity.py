"""Squad sync self-heals. Navigation is not the recovery mechanism."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from companion.app import events as E
from companion.app.commands.apply import collect_finished, refresh_liveness
from companion.app.commands.liveness_hold import stabilize_liveness
from companion.app.commands.squad import (
    ensure_squad_fresh,
    ingest_result,
    sync_squad,
    sync_status_text,
)
from companion.app.services import Services
from companion.app.state import AppState, JobsState, JobView, SquadState
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.outcome import ApplyOutcome, JobResult
from companion.ui.shell import PollGate, _surface_needs_refresh
from companion.ui.surfaces.club import club_phase


def _svc(tmp_path: Path) -> Services:
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
    )


def _live(**kwargs) -> Liveness:
    base = dict(
        pill=Pill.ARMED,
        message="ready",
        armed=True,
        session_id="ABC",
        capabilities={"hub_ready": True, "in_career": True, "save_ready": True},
    )
    base.update(kwargs)
    return Liveness(**base)


def _arm(svc: Services, **kwargs) -> None:
    svc.transport._liveness = _live(**kwargs)
    refresh_liveness(svc)


def _export(jid: str, *, players=None, state="done", ok=True, session_id="ABC", save_uid="SAVE-A"):
    rows = list(players or [])
    return {
        "job_id": jid,
        "state": state,
        "ok": ok,
        "session_id": session_id,
        "env": {"save_uid": save_uid},
        "ops": [
            {
                "id": "squad",
                "op": "export_squad",
                "ok": ok,
                "data": {"teamid": 241, "players": rows},
            }
        ],
    }


def _finish_failure(svc: Services, job_id: str) -> None:
    svc.transport.complete(
        job_id,
        _export(job_id, state="rejected", ok=False, players=[]),
    )
    collect_finished(svc)


def test_a_sync_waits_for_armed_then_hub_then_queues(tmp_path):
    svc = _svc(tmp_path)
    svc.transport._liveness = Liveness(
        pill=Pill.OFF, message="off", armed=False, session_id=""
    )
    refresh_liveness(svc)
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []

    _arm(svc, capabilities={"in_career": True, "save_ready": True, "hub_ready": False})
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []

    _arm(svc)
    job_id = ensure_squad_fresh(svc)
    assert job_id
    assert len(svc.transport.submitted) == 1
    assert svc.transport.submitted[0].label == "Sync squad"


def test_b_failed_sync_retries_after_cooldown_same_session(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    first = ensure_squad_fresh(svc)
    assert first
    _finish_failure(svc, first)
    assert ensure_squad_fresh(svc) is None
    svc.clock.advance(2)
    second = ensure_squad_fresh(svc)
    assert second and second != first
    assert len(svc.transport.submitted) == 2


def test_c_zero_players_can_retry_same_session(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    job_id = ensure_squad_fresh(svc)
    svc.transport.complete(job_id, _export(job_id, players=[]))
    collect_finished(svc)
    assert svc.store.snapshot().squad.players == ()
    assert ensure_squad_fresh(svc) is None
    svc.clock.advance(2)
    assert ensure_squad_fresh(svc) is not None


def test_d_one_missing_session_file_does_not_clear_squad(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    players = ({"playerid": 9, "name": "Kept"},)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-A",
            session_id="ABC",
            teamid=241,
            players=players,
            taken=svc.clock.now(),
        )
    )
    svc.store.dispatch(E.TargetLocked(9, "Kept", "squad", 241))
    svc.transport._liveness = Liveness(
        pill=Pill.OFF, message="Start LE — the companion arms itself.", armed=False
    )
    refresh_liveness(svc)
    squad = svc.store.snapshot().squad
    assert squad.players == players
    assert svc.store.snapshot().bridge.liveness.session_id == "ABC"
    assert svc.store.snapshot().target.locked

    _arm(svc)
    assert svc.store.snapshot().squad.players == players
    assert svc.store.snapshot().target.name == "Kept"


def test_e_real_session_change_clears_squad(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-A",
            session_id="ABC",
            teamid=241,
            players=({"playerid": 1, "name": "A"},),
            taken=svc.clock.now(),
        )
    )
    svc.store.dispatch(E.TargetLocked(1, "A", "squad", 241))
    _arm(svc, session_id="XYZ")
    assert svc.store.snapshot().squad.players == ()
    assert not svc.store.snapshot().target.locked


def test_f_invalidated_squad_refreshes_in_the_same_session(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-A",
            session_id="ABC",
            teamid=241,
            players=({"playerid": 1, "name": "A"},),
            taken=svc.clock.now(),
        )
    )
    assert ensure_squad_fresh(svc) is None
    svc.store.dispatch(E.SquadInvalidated(reason="write applied"))
    job_id = ensure_squad_fresh(svc)
    assert job_id
    assert svc.transport.submitted[-1].label == "Sync squad"


def test_g_and_h_club_phase_follows_sync_then_players():
    assert club_phase(has_players=False, sync_pending=False) == "setup"
    assert club_phase(has_players=False, sync_pending=True) == "reading"
    assert club_phase(has_players=True, sync_pending=False) == "board"

    before = AppState()
    reading = replace(
        before,
        jobs=JobsState(
            active={
                "J": JobView(
                    job_id="J",
                    label="Sync squad",
                    outcome=ApplyOutcome.QUEUED,
                    submitted=datetime.now(timezone.utc),
                )
            }
        ),
    )
    assert _surface_needs_refresh("club", before, reading)
    other_job = replace(
        before,
        jobs=JobsState(
            active={
                "J": JobView(
                    job_id="J",
                    label="Prepare squad",
                    outcome=ApplyOutcome.QUEUED,
                    submitted=datetime.now(timezone.utc),
                )
            }
        ),
    )
    assert not _surface_needs_refresh("club", before, other_job)
    boarded = replace(
        before,
        squad=SquadState(
            players=({"playerid": 1},),
            taken=datetime.now(timezone.utc),
            stale=False,
            session_id="ABC",
        ),
    )
    assert _surface_needs_refresh("club", before, boarded)


def test_i_refresh_board_source_has_no_missing_prompt_call():
    source = Path(__file__).resolve().parents[2].joinpath(
        "companion", "ui", "surfaces", "club.py"
    ).read_text(encoding="utf-8")
    assert "_prompt_text" not in source
    assert "def refresh_board" in source


@pytest.mark.gui
def test_i_refresh_board_updates_rows_without_raising(tmp_path):
    import customtkinter as ctk

    from companion.ui.surfaces.club import build

    svc = _svc(tmp_path)
    _arm(svc)
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-A",
            session_id="ABC",
            teamid=241,
            players=(
                {"playerid": 1, "name": "One", "overallrating": 80, "preferredposition1": "ST"},
                {"playerid": 2, "name": "Two", "overallrating": 70, "preferredposition1": "CM"},
            ),
            taken=svc.clock.now(),
        )
    )
    root = ctk.CTk()
    root.withdraw()
    try:
        page = build(root, svc, vm={})
        assert callable(page.refresh_board)
        svc.store.dispatch(
            E.SquadSynced(
                save_uid="SAVE-A",
                session_id="ABC",
                teamid=241,
                players=(
                    {"playerid": 3, "name": "Three", "overallrating": 88, "preferredposition1": "LW"},
                ),
                taken=svc.clock.now(),
            )
        )
        assert page.refresh_board() is True
    finally:
        root.destroy()


def test_j_manual_refresh_reports_an_in_progress_sync(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    first = sync_squad(svc)
    second = sync_squad(svc)
    assert first.created is True
    assert second.created is False
    assert second.job_id == first.job_id
    assert sync_status_text(second) == "Squad sync already in progress."
    assert len(svc.transport.submitted) == 1


def test_manual_refresh_bypasses_retry_cooldown(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    svc.squad_sync.retry_after_ts = svc.clock.now_ts() + 30
    assert ensure_squad_fresh(svc) is None
    ticket = sync_squad(svc)
    assert ticket.created is True
    assert "queued" in sync_status_text(ticket).lower()


def test_k_poll_gate_rejects_overlap():
    gate = PollGate()
    assert gate.enter() is True
    assert gate.enter() is False
    gate.leave()
    assert gate.enter() is True


def test_l_save_uid_change_drops_previous_squad_and_can_reload(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc, save_uid="SAVE-A")
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-A",
            session_id="ABC",
            teamid=241,
            players=({"playerid": 1, "name": "Old save"},),
            taken=svc.clock.now(),
        )
    )
    _arm(svc, save_uid="SAVE-B")
    assert svc.store.snapshot().squad.players == ()
    assert ensure_squad_fresh(svc) is not None


def test_hub_exit_without_save_uid_marks_squad_stale(tmp_path):
    svc = _svc(tmp_path)
    svc.store.dispatch(
        E.LivenessChanged(
            _live(save_uid="", capabilities={"hub_ready": True})
        )
    )
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="",
            session_id="ABC",
            teamid=241,
            players=({"playerid": 1, "name": "A"},),
            taken=svc.clock.now(),
        )
    )
    svc.store.dispatch(
        E.LivenessChanged(
            _live(save_uid="", capabilities={"hub_ready": False})
        )
    )
    squad = svc.store.snapshot().squad
    assert squad.players
    assert squad.stale is True
    svc.store.dispatch(E.LivenessChanged(_live(save_uid="")))
    assert ensure_squad_fresh(svc) is not None


def test_rejected_old_session_does_not_block_current_retry(tmp_path):
    svc = _svc(tmp_path)
    _arm(svc)
    stale = JobResult.from_wire(
        _export("OLD", players=[{"playerid": 2}], session_id="PREVIOUS", state="done", ok=True)
    )
    assert ingest_result(svc, stale)
    assert svc.squad_sync.failures == 0
    assert svc.squad_sync.retry_after_ts is None
    assert ensure_squad_fresh(svc) is not None


def test_stabilize_keeps_session_across_two_misses_then_stays_bound():
    hold_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    from companion.app.commands.liveness_hold import LivenessHold

    hold = LivenessHold()
    good = _live()
    assert stabilize_liveness(hold, good, hold_now).session_id == "ABC"
    missing = Liveness(pill=Pill.OFF, message="missing", armed=False)
    kept = stabilize_liveness(hold, missing, hold_now)
    assert kept.session_id == "ABC" and kept.armed is True
    kept2 = stabilize_liveness(hold, missing, hold_now)
    assert kept2.session_id == "ABC" and kept2.armed is True
    gone = stabilize_liveness(hold, missing, hold_now)
    assert gone.session_id == "ABC"
    assert gone.armed is False
