"""core/db.py — the migration runner and every table it creates.

The runner gets the most attention because a half-applied migration is the one
failure mode that bricks an install permanently: v1 had no schema versioning at
all, so a column change meant "no such column" on every launch forever.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from companion.core.db import (
    MIGRATIONS,
    SAVE_SNAPSHOT_TTL_S,
    SCHEMA_VERSION,
    Database,
    JobRecord,
    Migration,
    current_version,
    open_state_db,
    run_migrations,
    utc_now,
)
from companion.core.paths import TempAppPaths
from companion.domain.outcome import ApplyOutcome


@pytest.fixture()
def db(tmp_path: Path):
    d = Database(tmp_path / "state.sqlite")
    yield d
    d.close()


# --- migration runner ------------------------------------------------------


def test_fresh_db_is_at_current_schema_version(db: Database) -> None:
    assert db.version == SCHEMA_VERSION
    assert db.applied == [m.version for m in MIGRATIONS]


def test_reopening_applies_nothing(tmp_path: Path) -> None:
    first = Database(tmp_path / "state.sqlite")
    first.close()
    second = Database(tmp_path / "state.sqlite")
    try:
        assert second.applied == [], "migrations must not re-run on an existing file"
        assert second.version == SCHEMA_VERSION
    finally:
        second.close()


def test_wal_is_enabled(db: Database) -> None:
    mode = db.one("PRAGMA journal_mode")
    assert mode is not None and mode[0].lower() == "wal"


def test_migrations_run_in_version_order(tmp_path: Path) -> None:
    order: list[int] = []
    migs = [
        Migration(3, "third", "CREATE TABLE t3(x)", lambda c: order.append(3)),
        Migration(1, "first", "CREATE TABLE t1(x)", lambda c: order.append(1)),
        Migration(2, "second", "CREATE TABLE t2(x)", lambda c: order.append(2)),
    ]
    conn = sqlite3.connect(str(tmp_path / "o.sqlite"), isolation_level=None)
    conn.executescript(MIGRATIONS[0].sql)
    conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','0')")
    applied = run_migrations(conn, migs)
    assert applied == [1, 2, 3]
    assert order == [1, 2, 3]
    conn.close()


def test_failed_migration_rolls_back_entirely(tmp_path: Path) -> None:
    """The whole point: version 2 must not be recorded if its body raised."""
    path = tmp_path / "rb.sqlite"
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row

    def explode(_c: sqlite3.Connection) -> None:
        raise RuntimeError("migration body failed")

    migs = [
        MIGRATIONS[0],
        Migration(2, "adds a table then dies", "CREATE TABLE half(x)", explode),
    ]
    with pytest.raises(RuntimeError):
        run_migrations(conn, migs)

    assert current_version(conn) == 1, "version must stay at the last good migration"
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half" not in names, "DDL from the failed step must be rolled back too"
    assert conn.execute("SELECT COUNT(*) FROM schema_migration WHERE version=2").fetchone()[0] == 0
    conn.close()


def test_failed_migration_is_retried_on_next_open(tmp_path: Path) -> None:
    path = tmp_path / "retry.sqlite"
    conn = sqlite3.connect(str(path), isolation_level=None)
    calls = {"n": 0}

    def flaky(_c: sqlite3.Connection) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")

    migs = [MIGRATIONS[0], Migration(2, "flaky", "CREATE TABLE later(x)", flaky)]
    with pytest.raises(RuntimeError):
        run_migrations(conn, migs)
    assert run_migrations(conn, migs) == [2]
    assert current_version(conn) == 2
    conn.close()


def test_duplicate_versions_are_rejected(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "d.sqlite"), isolation_level=None)
    with pytest.raises(ValueError, match="duplicate migration versions"):
        run_migrations(conn, [Migration(1, "a", ""), Migration(1, "b", "")])
    conn.close()


def test_target_stops_partway(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"), isolation_level=None)
    migs = [MIGRATIONS[0], Migration(2, "two", "CREATE TABLE two(x)"),
            Migration(3, "three", "CREATE TABLE three(x)")]
    assert run_migrations(conn, migs, target=2) == [1, 2]
    assert current_version(conn) == 2
    conn.close()


def test_schema_migration_audit_trail(db: Database) -> None:
    rows = db.query("SELECT * FROM schema_migration ORDER BY version")
    assert [r["version"] for r in rows] == [m.version for m in MIGRATIONS]
    assert all(r["applied_utc"].endswith("Z") for r in rows)


def test_all_expected_tables_exist(db: Database) -> None:
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "meta", "schema_migration", "prefs", "job_record",
        "snapshot", "favorite", "save_snapshot",
    } <= names


# --- transactions ----------------------------------------------------------


def test_tx_rolls_back_on_exception(db: Database) -> None:
    with pytest.raises(RuntimeError):
        with db.tx():
            db.set_pref("a", 1)
            db.set_pref("b", 2)
            raise RuntimeError("nope")
    assert db.get_pref("a") is None and db.get_pref("b") is None


def test_tx_is_reentrant(db: Database) -> None:
    with db.tx():
        db.set_pref("outer", 1)
        with db.tx():
            db.set_pref("inner", 2)
    assert db.get_pref("outer") == 1 and db.get_pref("inner") == 2


def test_writes_from_another_thread(db: Database) -> None:
    """ThreadExecutor runs commands off-thread; the handle must tolerate it."""
    err: list[BaseException] = []

    def work() -> None:
        try:
            db.set_pref("from_thread", True)
        except BaseException as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert not err
    assert db.get_pref("from_thread") is True


# --- prefs -----------------------------------------------------------------


def test_prefs_round_trip_types(db: Database) -> None:
    values = {"b": True, "i": 7, "f": 1.5, "s": "x", "l": [1, 2], "d": {"k": "v"}, "n": None}
    db.set_prefs(values)
    for key, value in values.items():
        assert db.get_pref(key) == value, key


def test_pref_upsert_replaces(db: Database) -> None:
    db.set_pref("k", 1)
    db.set_pref("k", 2)
    assert db.get_pref("k") == 2
    assert len(db.query("SELECT * FROM prefs WHERE key='k'")) == 1


def test_delete_pref(db: Database) -> None:
    db.set_pref("k", 1)
    db.delete_pref("k")
    assert db.get_pref("k", "gone") == "gone"


# --- job records -----------------------------------------------------------


def _submit(db: Database, job_id: str = "01JQ8FAAAAAAAAAAAAAAAAAAAA") -> str:
    db.record_submitted(
        job_id=job_id,
        job={"ops": [{"op": "diag.ping", "id": "p"}], "label": "ping"},
        origin="cli.ping",
        label="ping",
        submitted_utc="2026-07-26T10:00:00.000Z",
        created_utc="2026-07-26T10:00:00.000Z",
    )
    return job_id


def test_submitted_row_is_queued_and_counts_ops(db: Database) -> None:
    jid = _submit(db)
    rec = db.job_record(jid)
    assert rec is not None
    assert rec.outcome is ApplyOutcome.QUEUED
    assert rec.op_count == 1
    assert rec.origin == "cli.ping"


def test_latency_and_exec_are_both_measured(db: Database) -> None:
    """latency_ms - exec_ms IS the event-starvation cost (§7.2)."""
    jid = _submit(db)
    db.record_claimed(jid, claimed_utc="2026-07-26T10:00:01.900Z")
    rec = db.record_finished(
        jid,
        outcome=ApplyOutcome.APPLIED,
        result={"state": "done", "ok": True},
        finished_utc="2026-07-26T10:00:01.961Z",
        applied_count=1,
    )
    assert rec is not None
    assert rec.latency_ms == 1961
    assert rec.exec_ms == 61
    assert rec.wait_ms == 1900


def test_core_reported_exec_ms_wins(db: Database) -> None:
    jid = _submit(db)
    db.record_claimed(jid, claimed_utc="2026-07-26T10:00:01.900Z")
    rec = db.record_finished(
        jid,
        outcome=ApplyOutcome.APPLIED,
        finished_utc="2026-07-26T10:00:01.961Z",
        exec_ms=44,
    )
    assert rec is not None and rec.exec_ms == 44


def test_wait_ms_is_none_without_both_numbers(db: Database) -> None:
    jid = _submit(db)
    rec = db.record_finished(jid, outcome=ApplyOutcome.EXPIRED)
    assert rec is not None and rec.exec_ms is None and rec.wait_ms is None


def test_result_for_an_unknown_job_is_kept_as_an_orphan(db: Database) -> None:
    rec = db.record_finished("01JQ8FZZZZZZZZZZZZZZZZZZZZ", outcome=ApplyOutcome.CRASHED)
    assert rec is not None and rec.outcome is ApplyOutcome.CRASHED


def test_job_json_and_result_json_are_stored_verbatim(db: Database) -> None:
    """§7.2: verbatim storage is what makes `companion replay <id>` possible."""
    job = {"ops": [{"op": "player.write", "id": "w", "playerid": 48940}], "label": "Čech"}
    db.record_submitted(job_id="01JQ8FBBBBBBBBBBBBBBBBBBBB", job=job)
    result = {"state": "done", "ok": True, "counts": {"fields_written": 12}}
    db.record_finished("01JQ8FBBBBBBBBBBBBBBBBBBBB", outcome=ApplyOutcome.APPLIED, result=result)
    rec = db.job_record("01JQ8FBBBBBBBBBBBBBBBBBBBB")
    assert rec is not None
    assert rec.job_json == job
    assert rec.result_json == result


def test_job_records_are_uncapped_and_newest_first(db: Database) -> None:
    """v1 capped job_history.json at 40 rows; there is no cap here."""
    for i in range(120):
        jid = f"01JQ8F{i:020d}".replace("0", "0")[:26].upper()
        db.upsert_job_record(
            JobRecord(job_id=f"J{i:025d}", created_utc=f"2026-07-26T10:{i % 60:02d}:00.000Z",
                      outcome=ApplyOutcome.APPLIED)
        )
    assert db.one("SELECT COUNT(*) c FROM job_record")["c"] == 120
    rows = db.job_records(limit=5)
    assert len(rows) == 5
    assert rows[0].created_utc >= rows[-1].created_utc


def test_job_records_filter_by_save_and_outcome(db: Database) -> None:
    db.upsert_job_record(JobRecord("A", "2026-07-26T10:00:00.000Z", ApplyOutcome.APPLIED,
                                   save_uid="S1"))
    db.upsert_job_record(JobRecord("B", "2026-07-26T10:01:00.000Z", ApplyOutcome.FAILED,
                                   save_uid="S1"))
    db.upsert_job_record(JobRecord("C", "2026-07-26T10:02:00.000Z", ApplyOutcome.APPLIED,
                                   save_uid="S2"))
    assert {r.job_id for r in db.job_records(save_uid="S1")} == {"A", "B"}
    assert {r.job_id for r in db.job_records(outcome=ApplyOutcome.APPLIED)} == {"A", "C"}


def test_job_stats_medians(db: Database) -> None:
    for i, (lat, ex) in enumerate([(1000, 50), (2000, 60), (3000, 70)]):
        db.upsert_job_record(
            JobRecord(f"J{i}", f"2026-07-26T10:0{i}:00.000Z", ApplyOutcome.APPLIED,
                      latency_ms=lat, exec_ms=ex)
        )
    stats = db.job_stats()
    assert stats["total"] == 3
    assert stats["by_outcome"] == {"applied": 3}
    assert stats["median_latency_ms"] == 2000
    assert stats["median_exec_ms"] == 60
    assert stats["median_wait_ms"] == 1940


def test_job_stats_on_empty_db(db: Database) -> None:
    stats = db.job_stats()
    assert stats["total"] == 0 and stats["median_wait_ms"] is None


def test_job_stats_since_filters_both_the_counts_and_the_medians(db: Database) -> None:
    """job_stats builds its SQL by concatenation — pin the `since` branch."""
    db.upsert_job_record(JobRecord("old", "2026-07-25T10:00:00.000Z", ApplyOutcome.FAILED,
                                   latency_ms=9000, exec_ms=10))
    db.upsert_job_record(JobRecord("new", "2026-07-26T10:00:00.000Z", ApplyOutcome.APPLIED,
                                   latency_ms=1000, exec_ms=50))
    stats = db.job_stats(since_utc="2026-07-26T00:00:00.000Z")
    assert stats["total"] == 1
    assert stats["by_outcome"] == {"applied": 1}
    assert stats["median_latency_ms"] == 1000
    assert stats["median_exec_ms"] == 50
    assert stats["median_wait_ms"] == 950


def test_job_records_since_filter(db: Database) -> None:
    db.upsert_job_record(JobRecord("old", "2026-07-25T10:00:00.000Z", ApplyOutcome.APPLIED))
    db.upsert_job_record(JobRecord("new", "2026-07-26T10:00:00.000Z", ApplyOutcome.APPLIED))
    rows = db.job_records(since_utc="2026-07-26T00:00:00.000Z")
    assert [r.job_id for r in rows] == ["new"]


# --- snapshots -------------------------------------------------------------


def test_snapshot_round_trip(db: Database) -> None:
    fields = [{"f": "acceleration", "v": 78}, {"f": "finishing", "v": 13}]
    db.put_snapshot(id="5a530f3a8cd1", ts=1785061976.33, fields=fields,
                    label="Petr Čech", target_id=48940)
    snap = db.snapshot("5a530f3a8cd1")
    assert snap is not None
    assert snap.label == "Petr Čech"
    assert snap.n_fields == 2
    assert list(snap.fields) == fields
    assert snap.taken_utc.endswith("Z")


def test_only_one_snapshot_can_be_the_last_apply(db: Database) -> None:
    """v1 kept last_apply_snapshot.json separately and the two could disagree."""
    db.put_snapshot(id="a", ts=1.0, fields=[], is_last_apply=True)
    db.put_snapshot(id="b", ts=2.0, fields=[], is_last_apply=True)
    flagged = db.query("SELECT id FROM snapshot WHERE is_last_apply=1")
    assert [r["id"] for r in flagged] == ["b"]
    assert db.last_apply_snapshot() is not None
    assert db.last_apply_snapshot().id == "b"  # type: ignore[union-attr]


def test_mark_last_apply_moves_the_flag(db: Database) -> None:
    db.put_snapshot(id="a", ts=1.0, fields=[], is_last_apply=True)
    db.put_snapshot(id="b", ts=2.0, fields=[])
    db.mark_last_apply("b")
    assert db.last_apply_snapshot().id == "b"  # type: ignore[union-attr]


def test_snapshots_by_target_newest_first(db: Database) -> None:
    db.put_snapshot(id="old", ts=1.0, fields=[], target_id=48940)
    db.put_snapshot(id="new", ts=2.0, fields=[], target_id=48940)
    db.put_snapshot(id="other", ts=3.0, fields=[], target_id=1)
    assert [s.id for s in db.snapshots(target_id=48940)] == ["new", "old"]


def test_delete_snapshot(db: Database) -> None:
    db.put_snapshot(id="a", ts=1.0, fields=[])
    db.delete_snapshot("a")
    assert db.snapshot("a") is None


# --- favorites -------------------------------------------------------------


def test_favorite_composite_pk_dedupes_on_rename(db: Database) -> None:
    """v1's key included the name, so a catalog rename orphaned the favorite."""
    db.add_favorite(year="26", playerid="48940", revision="Icon", name="Petr Cech",
                    card={"name": "Petr Cech"})
    db.add_favorite(year="26", playerid="48940", revision="Icon", name="Petr Čech",
                    card={"name": "Petr Čech"})
    favs = db.favorites()
    assert len(favs) == 1
    assert favs[0].name == "Petr Čech"
    assert favs[0].key == ("26", "48940", "Icon")


def test_favorite_revision_is_part_of_the_key(db: Database) -> None:
    db.add_favorite(year="26", playerid="48940", revision="Icon", card={})
    db.add_favorite(year="26", playerid="48940", revision="Base", card={})
    assert len(db.favorites()) == 2


def test_is_favorite_and_remove(db: Database) -> None:
    db.add_favorite(year="26", playerid="48940", card={})
    assert db.is_favorite("26", "48940")
    db.remove_favorite("26", "48940")
    assert not db.is_favorite("26", "48940")


# --- live save state -------------------------------------------------------


def test_save_snapshot_ttl(db: Database) -> None:
    db.put_save_snapshot(save_uid="4a7f", kind="squad", payload={"count": 45}, taken_ts=1000.0)
    assert db.save_snapshot("4a7f", "squad", now_ts=1000.0 + 60) is not None
    assert db.save_snapshot("4a7f", "squad", now_ts=1000.0 + SAVE_SNAPSHOT_TTL_S + 1) is None


def test_save_snapshot_ignoring_ttl_reports_age(db: Database) -> None:
    db.put_save_snapshot(save_uid="4a7f", kind="squad", payload={}, taken_ts=1000.0)
    row = db.save_snapshot("4a7f", "squad", ttl_s=None)
    assert row is not None
    assert row.age_s(1300.0) == 300.0


def test_save_snapshot_is_keyed_by_save(db: Database) -> None:
    """v1's current_squad.json had NO save key and would show save A under save B."""
    db.put_save_snapshot(save_uid="A", kind="squad", payload={"team": "Chelsea"}, taken_ts=1.0)
    db.put_save_snapshot(save_uid="B", kind="squad", payload={"team": "Arsenal"}, taken_ts=1.0)
    assert db.save_snapshot("A", "squad", now_ts=1.0).payload["team"] == "Chelsea"  # type: ignore[union-attr]
    assert db.save_snapshot("B", "squad", now_ts=1.0).payload["team"] == "Arsenal"  # type: ignore[union-attr]


def test_stale_row_is_never_fresh_however_recent(db: Database) -> None:
    db.put_save_snapshot(save_uid="", kind="squad", payload={}, taken_ts=1000.0, stale=True)
    assert db.save_snapshot("", "squad", now_ts=1000.0) is None
    assert db.save_snapshot("", "squad", ttl_s=None) is not None


def test_invalidate_marks_stale_without_deleting(db: Database) -> None:
    db.put_save_snapshot(save_uid="A", kind="squad", payload={"x": 1}, taken_ts=1.0)
    db.put_save_snapshot(save_uid="A", kind="teams", payload={"y": 2}, taken_ts=1.0)
    assert db.invalidate_save_snapshots("A", ["squad"]) == 1
    assert db.save_snapshot("A", "squad", now_ts=1.0) is None
    assert db.save_snapshot("A", "squad", ttl_s=None) is not None, "data kept for the stale badge"
    assert db.save_snapshot("A", "teams", now_ts=1.0) is not None


def test_purge_old_save_snapshots(db: Database) -> None:
    db.put_save_snapshot(save_uid="A", kind="squad", payload={}, taken_ts=100.0)
    db.put_save_snapshot(save_uid="B", kind="squad", payload={}, taken_ts=900.0)
    assert db.purge_save_snapshots(older_than_ts=500.0) == 1
    assert db.save_snapshot("B", "squad", ttl_s=None) is not None


# --- open_state_db ---------------------------------------------------------


def test_open_state_db_uses_paths_and_creates_the_file(tmp_path: Path) -> None:
    paths = TempAppPaths(tmp_path)
    with open_state_db(paths) as d:
        assert d.version == SCHEMA_VERSION
    assert paths.state_db.is_file()
    assert paths.state_db.name == "state.sqlite"


def test_utc_now_matches_clock_stamp_format() -> None:
    from companion.core.clock import SystemClock

    a, b = utc_now(), SystemClock().stamp()
    assert len(a) == len(b) and a.endswith("Z") and a[10] == "T"
