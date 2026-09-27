"""Tests for the app layer: store, reducers, presenters.

The presenter tests are the ones that matter — they pin the honesty rules that
v1 broke (NO_OP is not a checkmark; a timeout is not a failure; ARMED+empty is
a success state).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from companion.app import events as E
from companion.app.presenters import (
    TONE_ERROR,
    TONE_OK,
    TONE_WARN,
    editor_view,
    header_view,
    job_report,
    jobs_view,
    liveness_view,
    outcome_view,
    search_view,
    squad_view,
)
from companion.app.state import AppState, SquadState
from companion.app.store import Store
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.ids import new_ulid
from companion.domain.outcome import ApplyOutcome, JobResult

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Store + reducers
# ---------------------------------------------------------------------------


def test_store_notifies_only_on_change():
    store = Store()
    seen: list[AppState] = []
    store.subscribe(seen.append)
    store.dispatch(E.StatusSet("hello"))
    assert len(seen) == 1
    store.dispatch(E.StatusSet("hello"))  # same value -> new object, still notifies
    assert store.snapshot().status == "hello"


def test_status_set_carries_tone_and_monotonic_id():
    store = Store()
    store.dispatch(E.StatusSet("hello", tone="ok"))
    first = store.snapshot()
    assert first.status == "hello"
    assert first.status_tone == "ok"
    assert first.status_id == 1
    store.dispatch(E.StatusSet("hello", tone="error"))
    second = store.snapshot()
    assert second.status_tone == "error"
    assert second.status_id == 2


def test_unsubscribe():
    store = Store()
    seen: list[AppState] = []
    off = store.subscribe(seen.append)
    store.dispatch(E.StatusSet("a"))
    off()
    store.dispatch(E.StatusSet("b"))
    assert len(seen) == 1


def test_bad_subscriber_does_not_break_dispatch():
    store = Store()
    errors: list[BaseException] = []
    store.set_error_handler(errors.append)
    store.subscribe(lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    good: list[AppState] = []
    store.subscribe(good.append)
    store.dispatch(E.StatusSet("x"))
    assert len(errors) == 1 and len(good) == 1


def test_state_is_frozen():
    st = AppState()
    with pytest.raises(Exception):
        st.status = "nope"  # type: ignore[misc]


def test_target_lock_and_clear():
    store = Store()
    store.dispatch(E.TargetLocked(playerid=158023, name="Messi", source="search"))
    assert store.snapshot().target.locked
    assert store.snapshot().target.playerid == 158023
    store.dispatch(E.TargetCleared())
    assert not store.snapshot().target.locked


def test_stale_search_reply_is_dropped():
    """A superseded search must not render over the current one."""
    store = Store()
    store.dispatch(E.SearchStarted(query="messi", generation=1))
    store.dispatch(E.SearchStarted(query="pele", generation=2))
    store.dispatch(E.SearchSucceeded(results=[{"name": "Messi"}], generation=1))
    assert store.snapshot().search.results == ()  # dropped
    store.dispatch(E.SearchSucceeded(results=[{"name": "Pele"}], generation=2))
    assert len(store.snapshot().search.results) == 1
    assert store.snapshot().search.busy is False


def test_editing_back_to_original_clears_dirty():
    store = Store()
    store.dispatch(E.EditorLoaded(base={"overallrating": 91}))
    store.dispatch(E.FieldEdited("overallrating", 94))
    assert store.snapshot().editor.dirty == {"overallrating": 94}
    store.dispatch(E.FieldEdited("overallrating", 91))
    assert store.snapshot().editor.dirty == {}
    assert store.snapshot().editor.has_changes is False


def test_editor_merged():
    store = Store()
    store.dispatch(E.EditorLoaded(base={"a": 1, "b": 2}))
    store.dispatch(E.FieldEdited("b", 9))
    assert store.snapshot().editor.merged() == {"a": 1, "b": 9}


def test_job_lifecycle_moves_active_to_history():
    store = Store()
    jid = new_ulid()
    store.dispatch(E.JobSubmitted(job_id=jid, label="Messi -> 94", at=NOW))
    assert store.snapshot().jobs.in_flight == 1
    res = JobResult.from_wire(
        {"job_id": jid, "state": "done", "ok": True, "counts": {"fields_written": 2}}
    )
    store.dispatch(E.JobFinished(job_id=jid, result=res))
    snap = store.snapshot()
    assert snap.jobs.active == {}
    assert len(snap.jobs.history) == 1
    assert snap.jobs.history[0].outcome is ApplyOutcome.APPLIED
    assert snap.jobs.history[0].label == "Messi -> 94"  # label survives


def test_clear_queue_drops_active_jobs_but_keeps_finished_history():
    store = Store()
    first = new_ulid()
    second = new_ulid()
    store.dispatch(E.JobSubmitted(job_id=first, label="finished", at=NOW))
    store.dispatch(
        E.JobFinished(
            job_id=first,
            result=JobResult.from_wire(
                {"job_id": first, "state": "done", "ok": True}
            ),
        )
    )
    store.dispatch(E.JobSubmitted(job_id=second, label="waiting", at=NOW))

    store.dispatch(E.JobQueueCleared())

    assert store.snapshot().jobs.active == {}
    assert [item.job_id for item in store.snapshot().jobs.history] == [first]


def test_prefs_roundtrip():
    store = Store()
    store.dispatch(E.PrefsLoaded({"growth_mirror": False, "theme": "light"}))
    assert store.snapshot().prefs.growth_mirror is False
    store.dispatch(E.PrefChanged("growth_mirror", True))
    assert store.snapshot().prefs.growth_mirror is True
    assert store.snapshot().prefs.theme == "light"  # other keys preserved


# ---------------------------------------------------------------------------
# Presenters — the honesty rules
# ---------------------------------------------------------------------------


def test_every_outcome_has_a_view():
    """Exhaustiveness: adding an enum member must break here, not in a tab."""
    for oc in ApplyOutcome:
        v = outcome_view(oc)
        assert v["label"] and v["tone"] and v["detail"]


def test_no_op_is_not_a_green_checkmark():
    v = outcome_view(ApplyOutcome.NO_OP)
    assert v["tone"] != TONE_OK
    assert v["tone"] == TONE_WARN
    assert "not found" in v["detail"].lower()


def test_applied_is_the_only_clean_success():
    assert outcome_view(ApplyOutcome.APPLIED)["tone"] == TONE_OK
    for oc in (ApplyOutcome.PARTIAL, ApplyOutcome.FAILED, ApplyOutcome.REJECTED,
               ApplyOutcome.CRASHED, ApplyOutcome.NO_OP):
        assert outcome_view(oc)["tone"] != TONE_OK


def test_armed_and_empty_reads_as_success():
    lv = Liveness(pill=Pill.ARMED, message="Ready.", armed=True, queue_depth=0)
    v = liveness_view(lv)
    assert v["tone"] == TONE_OK
    assert v["pill"] == "ARMED"
    assert v["show_force_drain"] is False


def test_stalled_offers_force_drain():
    lv = Liveness(pill=Pill.STALLED, message="2 jobs queued…", armed=True,
                  queue_depth=2, last_drain_age=600)
    v = liveness_view(lv)
    assert v["tone"] == TONE_ERROR
    assert v["show_force_drain"] is True
    assert v["age_text"] == "10 min ago"


def test_partial_report_names_the_failed_fields():
    jid = new_ulid()
    res = JobResult.from_wire(
        {
            "job_id": jid,
            "state": "done",
            "ok": False,
            "counts": {"fields_requested": 91, "fields_written": 88, "fields_failed": 3},
            "failures": [
                {"op": "stats", "field": "potential", "requested": 99, "readback": 94,
                 "reason": "verify_mismatch", "detail": "wrote 99 read 94"},
            ],
        }
    )
    rep = job_report(res)
    assert rep["outcome"]["label"] == "Partial"
    assert rep["has_failures"] is True
    assert rep["failures"][0]["field"] == "potential"
    assert "88" in rep["summary"] and "3" in rep["summary"]


def test_failed_report_carries_lua_error_text():
    msg = "field.lua:63: attempt to perform bitwise operation on a nil value"
    res = JobResult.from_wire(
        {"job_id": new_ulid(), "state": "failed", "ok": False,
         "error": {"phase": "execute", "message": msg}}
    )
    rep = job_report(res)
    assert msg in rep["summary"]
    assert rep["error"]["message"] == msg


def test_queued_report_is_not_a_failure():
    res = JobResult.queued(new_ulid(), label="pending")
    rep = job_report(res)
    assert rep["outcome"]["label"] == "Queued"
    assert rep["outcome"]["tone"] != TONE_ERROR


def test_apply_blocked_reasons_are_actionable():
    st = AppState()
    assert "player" in editor_view(st)["blocked_reason"].lower()

    st2 = st.with_(target=st.target.__class__(playerid=1, name="X"))
    assert "changes" in editor_view(st2)["blocked_reason"].lower()


def test_header_view_shape():
    st = AppState()
    h = header_view(st)
    assert h["target"]["text"] == "No player selected"
    assert h["bridge"]["pill"] == "OFF"


def test_header_view_hides_internal_id_when_a_player_name_is_available():
    st = AppState()
    st = st.with_(target=st.target.__class__(playerid=123, name="Joe Gomez"))
    assert header_view(st)["target"]["text"] == "Joe Gomez"


def test_squad_view_renders_staleness_not_a_silent_lie():
    st = AppState().with_(
        squad=SquadState(save_uid="abc", teamid=5, players=({"id": 1},),
                         taken=NOW - timedelta(seconds=40), stale=False)
    )
    v = squad_view(st, now=NOW)
    assert v["count"] == 1
    assert "40s ago" in v["caption"]


def test_search_and_jobs_views():
    store = Store()
    store.dispatch(E.SearchStarted(query="messi", generation=1))
    store.dispatch(E.SearchSucceeded(results=[{"name": "Messi"}], generation=1))
    sv = search_view(store.snapshot())
    assert sv["count"] == 1 and sv["busy"] is False and sv["empty"] is False

    jid = new_ulid()
    store.dispatch(E.JobSubmitted(job_id=jid, label="x", at=NOW))
    jv = jobs_view(store.snapshot())
    assert jv["active"][0]["outcome"]["label"] == "Queued"
    assert jv["active"][0]["stage"]["chip"] == "Queued"


def test_in_flight_stage_names_the_next_career_step():
    from companion.app.presenters import in_flight_stage
    from companion.domain.outcome import ApplyOutcome

    queued = in_flight_stage(ApplyOutcome.QUEUED, pill=Pill.ARMED)
    assert queued["chip"] == "Queued"
    waiting = in_flight_stage(ApplyOutcome.QUEUED, pill=Pill.WAITING)
    assert waiting["chip"] == "Open Team Management"
    claimed = in_flight_stage(ApplyOutcome.QUEUED, pill=Pill.WAITING, claimed=True)
    assert claimed["chip"] == "In Live Editor"
    working = in_flight_stage(ApplyOutcome.DEFERRED, pill=Pill.LIVE)
    assert working["chip"] == "In Live Editor"
