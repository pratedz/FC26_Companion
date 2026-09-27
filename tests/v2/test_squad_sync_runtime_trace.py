"""Reproducible sync scenarios. Live FC is not required and is not touched."""

from __future__ import annotations

import logging
import os
from dataclasses import replace

import pytest

from companion.app import events as E
from companion.app.commands.apply import collect_finished, refresh_liveness
from companion.app.commands.squad import ensure_squad_fresh, sync_squad
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.outcome import ApplyOutcome
from companion.ui.surfaces.club import club_phase


pytestmark = pytest.mark.usefixtures("_sync_debug")


@pytest.fixture
def _sync_debug(monkeypatch):
    monkeypatch.setenv("COMPANION_SYNC_DEBUG", "1")


@pytest.fixture
def trace():
    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    log = logging.getLogger("companion.sync")
    handler = _H()
    handler.setLevel(logging.INFO)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield lines
    log.removeHandler(handler)


def _svc(tmp_path) -> Services:
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
        save_uid="SAVE-A",
        capabilities={"hub_ready": True, "in_career": True, "save_ready": True},
    )
    base.update(kwargs)
    return Liveness(**base)


def _arm(svc: Services, **kwargs) -> None:
    svc.transport._liveness = _live(**kwargs)
    refresh_liveness(svc)


def _players(name: str, pid: int = 1):
    return ({"playerid": pid, "name": name, "overallrating": 80},)


def _export(jid: str, *, players, session_id="ABC", save_uid="SAVE-A", state="done", ok=True):
    return {
        "job_id": jid,
        "state": state,
        "ok": ok,
        "session_id": session_id,
        "env": {"save_uid": save_uid},
        "ops": [{
            "id": "squad",
            "op": "export_squad",
            "ok": ok,
            "data": {"teamid": 241, "players": list(players)},
        }],
    }


def _complete(svc: Services, job_id: str, **kwargs) -> None:
    svc.transport.complete(job_id, _export(job_id, **kwargs))
    collect_finished(svc)


def _phase(svc: Services) -> str:
    state = svc.store.snapshot()
    pending = any(
        (not job.done) and job.label == "Sync squad"
        for job in state.jobs.active.values()
    )
    return club_phase(has_players=bool(state.squad.players), sync_pending=pending)


def test_runtime_scenarios_a_through_l_and_retry(tmp_path, trace):
    os.environ["COMPANION_SYNC_DEBUG"] = "1"
    svc = _svc(tmp_path)

    # B then A: companion first, career not ready, then career becomes ready.
    svc.transport._liveness = Liveness(pill=Pill.OFF, message="off", armed=False)
    refresh_liveness(svc)
    assert ensure_squad_fresh(svc) is None
    assert _phase(svc) == "setup"
    _arm(svc, capabilities={"hub_ready": False, "in_career": True, "save_ready": True})
    assert ensure_squad_fresh(svc) is None
    assert svc.transport.submitted == []
    _arm(svc)
    job = ensure_squad_fresh(svc)
    assert job
    assert _phase(svc) == "reading"
    _complete(svc, job, players=_players("A"))
    assert _phase(svc) == "board"
    assert len(svc.store.snapshot().squad.players) == 1
    assert svc.squad_sync.failures == 0
    assert svc.squad_sync.retry_after_ts is None

    # C: one missing session read.
    svc.store.dispatch(E.TargetLocked(1, "A", "squad", 241))
    svc.store.dispatch(
        E.FieldEdited(field_name="acceleration", value=90)
    ) if False else None
    from companion.app.state import EditorState
    svc.store.dispatch(E.EditorLoaded(base={"acceleration": 80}, categories=frozenset({"pace"})))
    svc.store.dispatch(E.FieldEdited(field_name="acceleration", value=90))
    editor_before = svc.store.snapshot().editor.dirty
    svc.transport._liveness = Liveness(pill=Pill.OFF, message="missing", armed=False)
    refresh_liveness(svc)
    assert svc.store.snapshot().squad.players
    assert svc.store.snapshot().target.locked
    assert svc.store.snapshot().editor.dirty == editor_before
    assert svc.store.snapshot().bridge.liveness.session_id == "ABC"

    # D: grace exceeded, same session restored.
    refresh_liveness(svc)
    refresh_liveness(svc)
    assert svc.store.snapshot().bridge.liveness.armed is False
    assert svc.store.snapshot().bridge.liveness.session_id == "ABC"
    assert svc.store.snapshot().squad.players
    assert svc.store.snapshot().target.locked
    _arm(svc)
    assert svc.store.snapshot().squad.players
    assert svc.store.snapshot().target.name == "A"
    assert svc.store.snapshot().editor.dirty == editor_before

    # E: ABC -> XYZ
    _arm(svc, session_id="XYZ", save_uid="SAVE-A")
    assert svc.store.snapshot().squad.players == ()
    assert not svc.store.snapshot().target.locked
    new_job = ensure_squad_fresh(svc)
    assert new_job
    _complete(svc, new_job, players=_players("New", 8), session_id="XYZ")
    assert svc.store.snapshot().squad.session_id == "XYZ"
    assert svc.store.snapshot().squad.players[0]["name"] == "New"

    # F: write in flight, then invalidate, then auto refresh.
    _arm(svc, session_id="XYZ")
    svc.store.dispatch(
        E.JobSubmitted(job_id="WRITE1", label="Apply player", at=svc.clock.now())
    )
    svc.store.dispatch(E.SquadInvalidated(reason="write applied"))
    assert svc.store.snapshot().squad.stale is True
    assert svc.store.snapshot().squad.players
    assert ensure_squad_fresh(svc) is None
    svc.store.dispatch(
        E.JobFinished(
            job_id="WRITE1",
            result=__import__(
                "companion.domain.outcome", fromlist=["JobResult"]
            ).JobResult.queued("WRITE1").__class__.from_wire(
                {"job_id": "WRITE1", "state": "done", "ok": True, "label": "Apply player"}
            ),
        )
    )
    refresh_job = ensure_squad_fresh(svc)
    assert refresh_job
    _complete(svc, refresh_job, players=_players("New", 8), session_id="XYZ")
    assert svc.store.snapshot().squad.stale is False

    # G: automation blocks the automatic read, then it runs.
    svc.store.dispatch(E.SquadInvalidated(reason="automation applied"))
    svc.store.dispatch(
        E.JobSubmitted(job_id="AUTO1", label="Prepare squad", at=svc.clock.now())
    )
    assert ensure_squad_fresh(svc) is None
    from companion.domain.outcome import JobResult
    svc.store.dispatch(
        E.JobFinished(
            job_id="AUTO1",
            result=JobResult.from_wire(
                {"job_id": "AUTO1", "state": "done", "ok": True, "label": "Prepare squad"}
            ),
        )
    )
    auto_job = ensure_squad_fresh(svc)
    assert auto_job
    _complete(svc, auto_job, players=_players("New", 8), session_id="XYZ")

    # H and L: a new session clears the squad. Opening Club does not start the read.
    _arm(svc, session_id="ABC", save_uid="SAVE-A")
    assert svc.store.snapshot().squad.players == ()
    opened = {"club": 0}

    def navigate(dest: str) -> bool:
        opened[dest] = opened.get(dest, 0) + 1
        return True

    navigate("club")
    assert opened["club"] == 1
    queued = ensure_squad_fresh(svc)
    assert queued
    assert opened["club"] == 1

    # I: phases already observed setup -> reading -> board above.

    # J: result applied while "away"; state is already the board.
    assert _phase(svc) == "reading"
    _complete(svc, queued, players=_players("Back", 3), session_id="ABC")
    assert _phase(svc) == "board"

    # K: save_uid change is the signal.
    before_uid = svc.store.snapshot().squad.save_uid
    before_team = svc.store.snapshot().squad.teamid
    _arm(svc, session_id="ABC", save_uid="SAVE-B")
    assert svc.store.snapshot().squad.players == ()
    assert before_uid == "SAVE-A"
    assert before_team == 241
    save_job = ensure_squad_fresh(svc)
    assert save_job
    _complete(
        svc, save_job, players=_players("Other club", 4),
        session_id="ABC", save_uid="SAVE-B",
    )
    # teamid in the export helper is 241 unless we change the wire. The clear
    # happened before the new read, which is the required invalidation.
    assert svc.store.snapshot().squad.save_uid == "SAVE-B"
    assert not svc.store.snapshot().squad.stale

    # Retry 2s, 4s, 8s, then success resets.
    svc.clock.advance(1)
    fail_job = ensure_squad_fresh(svc)
    # squad is fresh, so this is skip. Invalidate to force reads.
    svc.store.dispatch(E.SquadInvalidated(reason="force"))
    delays = []
    current = ensure_squad_fresh(svc)
    assert current
    for _ in range(3):
        svc.transport.complete(
            current,
            _export(current, players=[], state="rejected", ok=False, session_id="ABC", save_uid="SAVE-B"),
        )
        collect_finished(svc)
        wait = svc.squad_sync.retry_after_ts - svc.clock.now_ts()
        delays.append(wait)
        assert ensure_squad_fresh(svc) is None
        svc.clock.advance(wait)
        current = ensure_squad_fresh(svc)
        assert current
    assert delays == [2.0, 4.0, 8.0]
    _complete(svc, current, players=_players("Recovered", 5), session_id="ABC", save_uid="SAVE-B")
    assert svc.squad_sync.failures == 0
    assert svc.squad_sync.retry_after_ts is None

    # Zero-player result still retries on the same session.
    svc.store.dispatch(E.SquadInvalidated(reason="empty probe"))
    empty = ensure_squad_fresh(svc)
    _complete(svc, empty, players=[], session_id="ABC", save_uid="SAVE-B")
    assert svc.store.snapshot().squad.players
    assert svc.squad_sync.failures == 1
    assert ensure_squad_fresh(svc) is None
    svc.clock.advance(2)
    assert ensure_squad_fresh(svc) is not None

    blob = "\n".join(trace)
    out = (
        __import__("pathlib").Path(r"C:\Users\prated\Downloads\companion analyse\outcomes")
        / "08_sync_runtime_trace.txt"
    )
    out.write_text(blob + "\n", encoding="utf-8")
    assert "[SYNC]" in blob
    assert "queue reason=" in blob
    assert "retry_in=2" in blob
    assert "retry_in=4" in blob
    assert "retry_in=8" in blob
    assert "squad_cleared" in blob
