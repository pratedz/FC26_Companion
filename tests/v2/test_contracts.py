"""Contract tests for the frozen v2 layer: ids, outcome, job, transport.

These are the tests the rest of the build is written against. If one of these
has to change, the wire format changed — stop and think.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from companion.core.clock import FakeClock
from companion.core.paths import TempAppPaths, confine
from companion.core.transport.fake import FakeTransport
from companion.core.transport.jobfile import (
    atomic_write_json,
    job_id_from_filename,
    list_job_ids,
    read_json,
)
from companion.core.transport.v3 import FAST_READ_WAKE, FileTransport, Pill
from companion.domain.ids import is_ulid, new_ulid, ulid_time_ms
from companion.domain.job import (
    GENERATED_ID_MAX,
    Grants,
    Job,
    JobValidationError,
    job_apply_player,
    job_ping,
    op_budget,
    op_create_player,
    op_ping,
    op_raw_lua,
    op_set_fields,
    op_set_names,
    op_snapshot,
)
from companion.domain.outcome import (
    ApplyOutcome,
    JobResult,
    TERMINAL,
    outcome_from_wire,
    worst,
)


# ---------------------------------------------------------------------------
# ULIDs
# ---------------------------------------------------------------------------


def test_ulid_shape_and_sortability():
    a = new_ulid(now_ms=1_000_000)
    b = new_ulid(now_ms=2_000_000)
    assert is_ulid(a) and is_ulid(b)
    assert len(a) == 26
    assert a < b  # later time sorts later — the directory listing IS the queue
    assert ulid_time_ms(a) == 1_000_000


def test_ulid_uniqueness():
    seen = {new_ulid() for _ in range(2000)}
    assert len(seen) == 2000


def test_ulid_rejects_lowercase_and_junk():
    assert not is_ulid("")
    assert not is_ulid("not-a-ulid")
    assert not is_ulid(new_ulid().lower())
    assert not is_ulid("I" * 26)  # I is not in the Crockford alphabet


# ---------------------------------------------------------------------------
# ApplyOutcome — the closed enum
# ---------------------------------------------------------------------------


def test_worst_wins_ordering():
    assert worst([ApplyOutcome.APPLIED, ApplyOutcome.FAILED]) is ApplyOutcome.FAILED
    assert worst([ApplyOutcome.APPLIED, ApplyOutcome.NO_OP]) is ApplyOutcome.NO_OP
    assert worst([]) is ApplyOutcome.APPLIED


def test_terminal_set():
    assert ApplyOutcome.QUEUED not in TERMINAL
    assert ApplyOutcome.DEFERRED not in TERMINAL
    assert ApplyOutcome.APPLIED in TERMINAL
    assert ApplyOutcome.CRASHED in TERMINAL


def test_found_false_never_reads_as_ok():
    """The whole point of v3: scanned everything, wrote nothing => NO_OP."""
    oc = outcome_from_wire("done", False, {"found": 0, "fields_written": 0})
    assert oc is ApplyOutcome.NO_OP
    r = JobResult.from_wire(
        {"job_id": new_ulid(), "state": "done", "ok": False,
         "counts": {"found": 0, "fields_written": 0}}
    )
    assert r.outcome is ApplyOutcome.NO_OP
    assert r.outcome is not ApplyOutcome.APPLIED


def test_partial_field_failures_make_job_not_applied():
    r = JobResult.from_wire(
        {
            "job_id": new_ulid(),
            "state": "done",
            "ok": False,
            "counts": {"found": 1, "fields_written": 88, "fields_failed": 3},
        }
    )
    assert r.outcome is ApplyOutcome.PARTIAL


def test_explicit_outcome_name_wins():
    r = JobResult.from_wire(
        {"job_id": new_ulid(), "state": "done", "ok": True, "outcome": "crashed"}
    )
    assert r.outcome is ApplyOutcome.CRASHED


def test_job_outcome_never_better_than_worst_op():
    r = JobResult.from_wire(
        {
            "job_id": new_ulid(),
            "state": "done",
            "ok": True,
            "counts": {"ops_total": 2, "ops_ok": 1},
            "ops": [
                {"id": "a", "op": "set_fields", "ok": True,
                 "counts": {"requested": 4, "written": 4}},
                {"id": "b", "op": "set_fields", "ok": False,
                 "counts": {"requested": 4, "written": 2, "failed": 2}},
            ],
        }
    )
    assert r.outcome < ApplyOutcome.APPLIED


def test_lua_error_text_survives():
    msg = "imports/t3db/field.lua:63: attempt to perform bitwise operation on a nil value"
    r = JobResult.from_wire(
        {"job_id": new_ulid(), "state": "failed", "ok": False,
         "error": {"phase": "execute", "message": msg}}
    )
    assert r.outcome is ApplyOutcome.FAILED
    assert msg in (r.error or {}).get("message", "")


# ---------------------------------------------------------------------------
# Job contract
# ---------------------------------------------------------------------------


def test_job_wire_roundtrip_shape():
    job = job_apply_player(
        158023,
        {"overallrating": 94, "acceleration": 91},
        names={"firstname": "Lionel", "surname": "Messi"},
        label="Messi -> 94",
    )
    wire = job.to_wire(now=1_784_960_972)
    assert wire["schema"] == 3
    assert is_ulid(wire["job_id"])
    assert wire["ops"][0]["op"] == "set_fields"
    assert wire["ops"][0]["table"] == "players"
    assert wire["ops"][0]["growth_mirror"] == "auto"
    assert wire["ops"][1]["table"] == "editedplayernames"
    assert wire["ops"][1]["upsert"] is True
    # grants are deny-by-default and always present
    assert wire["grants"]["allow_create_player"] is False


def test_si5_create_player_needs_grant():
    job = Job(ops=(op_create_player("mk"),))
    with pytest.raises(JobValidationError, match="allow_create_player"):
        job.validate()
    ok = Job(ops=(op_create_player("mk"),), grants=Grants(allow_create_player=True))
    ok.validate()  # no raise


def test_si5_playerid_ceiling_is_hard():
    with pytest.raises(JobValidationError, match="499999"):
        op_create_player("mk", playerid=500_001)
    with pytest.raises(JobValidationError, match="499999"):
        op_create_player("mk", id_range=(460_000, 559_999))
    assert GENERATED_ID_MAX == 499_999


def test_raw_lua_is_not_part_of_the_data_protocol():
    with pytest.raises(JobValidationError, match="removed"):
        op_raw_lua("x", "return 1")


def test_atomic_rejects_non_reversible_ops():
    job = Job(
        ops=(op_create_player("mk"),),
        grants=Grants(allow_create_player=True),
        atomic=True,
    )
    with pytest.raises(JobValidationError, match="rollback"):
        job.validate()


def test_snapshot_to_rejects_ops_it_cannot_capture():
    job = Job(ops=(op_ping(),), snapshot_to="C:/snapshots/ping.json")
    with pytest.raises(JobValidationError, match="set_fields"):
        job.validate()


def test_absolute_expiry_is_serialized():
    wire = Job(
        ops=(op_ping(),), require_cm=False, expires_at=2_000_000_000
    ).to_wire(now=1)
    assert wire["expires_at"] == 2_000_000_000


def test_duplicate_op_ids_rejected():
    job = Job(ops=(op_ping("a"), op_ping("a")))
    with pytest.raises(JobValidationError, match="duplicate"):
        job.validate()


def test_unknown_op_rejected():
    from companion.domain.job import Op

    job = Job(ops=(Op("player.obliterate", "x"),))
    with pytest.raises(JobValidationError, match="unknown op"):
        job.validate()


def test_set_names_never_writes_nameids():
    """Never invent a nameid; zero nameids in a payload freezes CreatePlayer."""
    op = op_set_names("names", 158023, firstname="Marco", surname="van Basten")
    for k in op.body["fields"]:
        assert not k.endswith("nameid")


# ---------------------------------------------------------------------------
# SI-7: confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "../evil.json", "..", "a/b.json", "a\\b.json", "C:evil.json",
     "C:\\evil.json", "/etc/passwd", " "],
)
def test_confine_rejects_traversal(tmp_path, bad):
    assert confine(tmp_path, bad) is None


def test_confine_accepts_bare_names(tmp_path):
    p = confine(tmp_path, "01JQ8F3K2R7XZ4M9QW1YV6HB0.json")
    assert p is not None and p.parent == tmp_path.resolve()


def test_job_filename_gate():
    u = new_ulid()
    assert len(u) == 26
    assert job_id_from_filename(f"{u}.json") == u
    assert job_id_from_filename("evil.json") is None
    assert job_id_from_filename(f"{u}.lua") is None
    assert job_id_from_filename(f"../{u}.json") is None
    assert job_id_from_filename(f"{u[:25]}.json") is None  # 25 chars is not a ULID


# ---------------------------------------------------------------------------
# jobfile atomics
# ---------------------------------------------------------------------------


def test_atomic_write_and_read(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    assert read_json(p) == {"a": 1}
    assert not (tmp_path / "x.json.tmp").exists()


def test_read_json_tolerates_garbage(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_json(p) is None
    assert read_json(tmp_path / "absent.json") is None
    (tmp_path / "list.json").write_text("[1,2]", encoding="utf-8")
    assert read_json(tmp_path / "list.json") is None


def test_list_job_ids_sorted_and_filtered(tmp_path):
    a = new_ulid(now_ms=1000)
    b = new_ulid(now_ms=2000)
    for name in (f"{b}.json", f"{a}.json", "_junk.json", "notulid.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert list_job_ids(tmp_path) == sorted([a, b])


# ---------------------------------------------------------------------------
# FileTransport
# ---------------------------------------------------------------------------


def _transport(tmp_path, alive_pids=frozenset()):
    paths = TempAppPaths(tmp_path)
    clock = FakeClock()
    t = FileTransport(paths, clock=clock, pid_probe=lambda pid: pid in alive_pids)
    return t, paths, clock


def test_submit_writes_valid_job_file(tmp_path):
    t, paths, _ = _transport(tmp_path)
    jid = t.submit(job_ping())
    f = paths.jobs / f"{jid}.json"
    assert f.is_file()
    wire = json.loads(f.read_text(encoding="utf-8"))
    assert wire["schema"] == 3 and wire["job_id"] == jid
    assert t.pending_ids() == [jid]


def test_submit_wakes_fast_read_for_snapshots_not_writes(tmp_path):
    t, paths, _ = _transport(tmp_path)
    wake = paths.queue / FAST_READ_WAKE

    t.submit(job_ping())
    assert wake.is_file()
    wake.unlink()

    t.submit(
        Job(
            ops=(
                op_snapshot(
                    "player.read",
                    playerids=[241],
                    fields=["overallrating"],
                ),
            )
        )
    )
    assert wake.is_file()
    wake.unlink()

    t.submit(
        Job(
            ops=(
                op_set_fields(
                    "stats",
                    key_value=241,
                    fields={"overallrating": 90},
                ),
            )
        )
    )
    assert not wake.exists()

    t.submit(Job(ops=(op_budget("cash", action="set", transfer=1),)))
    assert not wake.exists()


def test_invalid_job_never_reaches_disk(tmp_path):
    t, paths, _ = _transport(tmp_path)
    bad = Job(ops=(op_create_player("mk"),))  # no grant
    with pytest.raises(JobValidationError):
        t.submit(bad)
    assert list(paths.jobs.iterdir()) == []


def test_cancel_only_unclaimed(tmp_path):
    t, paths, _ = _transport(tmp_path)
    jid = t.submit(job_ping())
    assert t.cancel(jid) is True
    assert t.cancel(jid) is False  # already gone


def test_clear_queue_removes_pending_claimed_state_and_partial_but_keeps_history(tmp_path):
    t, paths, _ = _transport(tmp_path)
    pending = t.submit(job_ping())
    claimed = new_ulid()
    atomic_write_json(paths.claimed / f"{claimed}.json", {"job_id": claimed})
    # Reproduce an index-only worker whose claimed index was never created.
    (paths.claimed / "index.json").unlink(missing_ok=True)
    atomic_write_json(paths.state_dir / f"{claimed}.state.json", {"step": 1})
    atomic_write_json(
        paths.results / f"{claimed}.partial.json",
        {"job_id": claimed, "state": "running", "ok": False},
    )
    atomic_write_json(
        paths.results / "finished.json",
        {"job_id": "finished", "state": "done", "ok": True},
    )

    assert t.clear_queue() == 2
    assert not (paths.jobs / f"{pending}.json").exists()
    assert not (paths.claimed / f"{claimed}.json").exists()
    assert not (paths.state_dir / f"{claimed}.state.json").exists()
    assert not (paths.results / f"{claimed}.partial.json").exists()
    assert (paths.results / "finished.json").exists()
    assert json.loads((paths.jobs / "index.json").read_text(encoding="utf-8")) == []
    assert json.loads((paths.claimed / "index.json").read_text(encoding="utf-8")) == []


def test_result_reads_final_then_partial(tmp_path):
    t, paths, _ = _transport(tmp_path)
    jid = t.submit(job_ping())
    assert t.result(jid) is None
    atomic_write_json(
        paths.results / f"{jid}.partial.json",
        {"schema": 3, "job_id": jid, "state": "running", "ok": False},
    )
    r = t.result(jid)
    assert r is not None and not r.outcome.is_terminal
    atomic_write_json(
        paths.results / f"{jid}.json",
        {"schema": 3, "job_id": jid, "state": "done", "ok": True,
         "counts": {"ops_total": 1, "ops_ok": 1, "fields_written": 1}},
    )
    r2 = t.result(jid)
    assert r2 is not None and r2.outcome.is_terminal


def test_await_result_timeout_returns_queued_not_failed(tmp_path):
    t, paths, clock = _transport(tmp_path)
    jid = t.submit(job_ping())
    r = t.await_result(jid, timeout=2.0, poll=0.5)
    assert r.outcome is ApplyOutcome.QUEUED
    assert not r.outcome.is_terminal  # timeout is NOT failure


# ---- liveness matrix (§3.5) — the single most user-visible v1 fix ----------


def test_liveness_off_when_no_session(tmp_path):
    t, *_ = _transport(tmp_path)
    lv = t.liveness()
    assert lv.pill is Pill.OFF and not lv.armed


def test_liveness_off_when_pid_dead(tmp_path):
    t, paths, _ = _transport(tmp_path)  # probe: nothing alive
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    assert t.liveness().pill is Pill.OFF


def test_liveness_pid_zero_uses_confirmed_host_process(tmp_path, monkeypatch):
    """Lua may be unable to expose a PID; that is unknown, not automatically OFF."""
    t, paths, _ = _transport(tmp_path)
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 0})
    monkeypatch.setattr(
        "companion.core.transport.v3._host_process_running", lambda: True
    )
    lv = t.liveness()
    assert lv.pill is Pill.ARMED
    assert lv.armed is True
    assert lv.pid is None
    assert lv.pid_alive is False


def test_liveness_rejects_obsolete_worker_capabilities(tmp_path, monkeypatch):
    t, paths, clock = _transport(tmp_path)
    atomic_write_json(
        paths.session_file,
        {
            "session_id": "OLD",
            "le_pid": 0,
            "armed_at": clock.now_ts(),
            "capabilities": {"atomic_rollback": True},
            "ops": {"raw_lua": 1, "export_squad": 1},
        },
    )
    monkeypatch.setattr(
        "companion.core.transport.v3._host_process_running", lambda: True
    )
    lv = t.liveness()
    assert lv.pill is Pill.OFF
    assert not lv.armed
    assert "restart" in lv.message.lower()


def test_liveness_accepts_compatible_running_worker_during_upgrade(tmp_path):
    """An installed upgrade must not disable established features mid-game."""
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(
        paths.session_file,
        {"session_id": "S", "le_pid": 4242, "core_version": "2.3.1"},
    )
    lv = t.liveness()
    assert lv.pill is Pill.ARMED
    assert lv.armed
    assert "existing features work" in lv.message.lower()
    assert "phase 5" in lv.message.lower()


def test_liveness_accepts_newer_patch_on_the_same_core_line(tmp_path):
    """A script copy one patch ahead must not look like a dead worker."""
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(
        paths.session_file,
        {
            "session_id": "S",
            "le_pid": 4242,
            "core_version": "2.6.99",
            "contract": 3,
        },
    )
    lv = t.liveness()
    assert lv.pill is Pill.ARMED
    assert lv.armed
    assert "older than supported" not in lv.message.lower()
    assert "newer than this companion" not in lv.message.lower()


def test_liveness_rejects_newer_worker_from_another_minor_line(tmp_path):
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(
        paths.session_file,
        {
            "session_id": "S",
            "le_pid": 4242,
            "core_version": "2.7.0",
            "contract": 3,
        },
    )
    lv = t.liveness()
    assert lv.pill is Pill.OFF
    assert not lv.armed
    assert "newer than this companion" in lv.message.lower()


def test_liveness_rejects_worker_older_than_compatible_floor(tmp_path):
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(
        paths.session_file,
        {"session_id": "OLD", "le_pid": 4242, "core_version": "2.2.0"},
    )
    lv = t.liveness()
    assert lv.pill is Pill.OFF
    assert not lv.armed
    assert "older than supported" in lv.message.lower()


def test_liveness_rejects_pidless_marker_older_than_game(tmp_path, monkeypatch):
    t, paths, clock = _transport(tmp_path)
    atomic_write_json(
        paths.session_file,
        {
            "session_id": "OLD",
            "le_pid": 0,
            "armed_at": clock.now_ts() - 60,
        },
    )
    monkeypatch.setattr(
        "companion.core.transport.v3._host_game_started_at",
        lambda: clock.now_ts(),
    )
    monkeypatch.setattr(
        "companion.core.transport.v3._host_process_running", lambda: True
    )
    lv = t.liveness()
    assert lv.pill is Pill.OFF
    assert "predates" in lv.message.lower()


def test_liveness_pid_zero_is_off_without_host_evidence(tmp_path, monkeypatch):
    t, paths, _ = _transport(tmp_path)
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 0})
    monkeypatch.setattr(
        "companion.core.transport.v3._host_process_running", lambda: False
    )
    lv = t.liveness()
    assert lv.pill is Pill.OFF
    assert "session found" in lv.message.lower()


def test_liveness_armed_when_idle_and_empty(tmp_path):
    """Idle-but-armed reads ARMED, never OFF — kills the 90-second bug."""
    t, paths, clock = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    # No drain for an hour: still ARMED because the queue is empty.
    atomic_write_json(paths.drain_file, {"at": clock.now_ts() - 3600})
    lv = t.liveness()
    assert lv.pill is Pill.ARMED and lv.armed


def test_liveness_live_waiting_stalled(tmp_path):
    t, paths, clock = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    jid = t.submit(job_ping())

    atomic_write_json(paths.drain_file, {"at": clock.now_ts() - 2})
    assert t.liveness().pill is Pill.LIVE

    atomic_write_json(paths.drain_file, {"at": clock.now_ts() - 30})
    assert t.liveness().pill is Pill.WAITING

    atomic_write_json(paths.drain_file, {"at": clock.now_ts() - 600})
    lv = t.liveness()
    assert lv.pill is Pill.STALLED
    assert "Force Drain" in lv.message


def test_liveness_ignores_mtime_only_content(tmp_path):
    """A backup tool refreshing mtime must not manufacture a heartbeat."""
    t, paths, clock = _transport(tmp_path, alive_pids={4242})
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    t.submit(job_ping())
    atomic_write_json(paths.drain_file, {"at": clock.now_ts() - 600})
    # "touch" the file — mtime fresh, content stale
    import os

    os.utime(paths.drain_file)
    assert t.liveness().pill is Pill.STALLED


# ---- crash recovery --------------------------------------------------------


def test_sweep_crashed_emits_result(tmp_path):
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    jid = new_ulid()
    atomic_write_json(
        paths.claimed / f"{jid}.json",
        {"schema": 3, "job_id": jid, "claimed_by": "DEADSESSION", "ops": []},
    )
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    swept = t.sweep_crashed()
    assert swept == [jid]
    r = t.result(jid)
    assert r is not None and r.outcome is ApplyOutcome.CRASHED
    # idempotent: a second sweep does not rewrite
    assert t.sweep_crashed() == []


def test_sweep_does_not_fail_a_claim_still_being_stamped(tmp_path):
    """A just-claimed file has no owner yet. That is not a dead session."""
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    jid = new_ulid()
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    atomic_write_json(
        paths.claimed / f"{jid}.json",
        {"schema": 3, "job_id": jid, "ops": []},
    )
    assert t.sweep_crashed() == []
    assert t.result(jid) is None


def test_sweep_leaves_live_sessions_claims(tmp_path):
    t, paths, _ = _transport(tmp_path, alive_pids={4242})
    jid = new_ulid()
    atomic_write_json(paths.session_file, {"session_id": "S", "le_pid": 4242})
    atomic_write_json(
        paths.claimed / f"{jid}.json",
        {"schema": 3, "job_id": jid, "claimed_by": "S", "ops": []},
    )
    assert t.sweep_crashed() == []


# ---------------------------------------------------------------------------
# FakeTransport honours the same contract
# ---------------------------------------------------------------------------


def test_fake_transport_contract():
    ft = FakeTransport()
    job = job_ping()
    jid = ft.submit(job)
    assert ft.result(jid) is None
    assert ft.pending_ids() == [jid]
    ft.complete_ok(jid)
    r = ft.result(jid)
    assert r is not None and r.outcome is ApplyOutcome.APPLIED
    assert ft.pending_ids() == []


def test_fake_transport_validates():
    ft = FakeTransport()
    with pytest.raises(JobValidationError):
        ft.submit(Job(ops=()))
