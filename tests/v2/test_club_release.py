"""Club release queues only ticked players who are in the current squad."""

from __future__ import annotations

import inspect

import pytest

from companion.app import events as E
from companion.app.commands.release import build_release_job, queue_club_release
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.job import JobValidationError


@pytest.fixture
def svc(tmp_path):
    store = Store()
    services = Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=store,
    )
    services.store.dispatch(
        E.LivenessChanged(
            Liveness(pill=Pill.ARMED, message="ready", armed=True, session_id="session-1")
        )
    )
    services.store.dispatch(
        E.SquadSynced(
            save_uid="save-1",
            session_id="session-1",
            teamid=2,
            players=(
                {"playerid": 73669, "name": "Carlos Alberto"},
                {"playerid": 85308, "name": "Pelé"},
            ),
            taken=services.clock.now(),
        )
    )
    return services


def test_release_job_uses_host_release_for_ticked_squad_players_only(svc):
    job = build_release_job(
        svc,
        (
            {"id": 73669, "name": "Carlos Alberto"},
            {"id": 99999, "name": "Not in squad"},
            {"id": 73669, "name": "Carlos Alberto"},
            {"playerid": 85308, "name": "Pelé"},
        ),
    )
    wire = job.to_wire(now=1)
    assert wire["origin"] == "ui.club.release"
    assert wire["requires"]["save_uid"] == "save-1"
    assert wire["max_attempts"] == 1
    assert [op["playerid"] for op in wire["ops"]] == [73669, 85308]
    assert {op["action"] for op in wire["ops"]} == {"release"}
    assert all(op["op"] == "transfer" and op["verify"] is True and op["on_error"] == "continue" for op in wire["ops"])


def test_release_refuses_an_empty_tick_or_a_stale_squad(svc):
    with pytest.raises(JobValidationError, match="Tick the players"):
        build_release_job(svc, ())
    svc.store.dispatch(E.SquadInvalidated(reason="test"))
    with pytest.raises(JobValidationError, match="stale"):
        build_release_job(svc, ({"id": 73669, "name": "Carlos Alberto"},))


def test_queue_submits_the_release_job(svc, monkeypatch):
    captured = {}

    def submit(_svc, job):
        captured["job"] = job
        return job.job_id

    monkeypatch.setattr("companion.app.commands.release.submit_job", submit)
    job_id = queue_club_release(svc, ({"id": 73669, "name": "Carlos Alberto"},))
    assert job_id == captured["job"].job_id
    assert captured["job"].ops[0].body["action"] == "release"


def test_club_surface_exposes_release_for_the_ticked_selection():
    from companion.ui.surfaces import club

    source = inspect.getsource(club._build_squad)
    assert "Release from club" in source
    assert "queue_club_release" in source
    assert "release_selected" in source
    assert 'foot.pack(side="bottom", fill="x"' in source
    assert "fill_available=True" in source
    assert "pack_propagate(False)" in source
