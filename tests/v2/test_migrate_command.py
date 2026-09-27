"""app/commands/migrate.py — the v1 import.

The P0 gate (§8.2) is "migrate round-trips the real user directory, verified
field-by-field". `test_real_user_directory_*` does exactly that against a *copy*
of the live install, and is skipped when the real files are absent so the suite
still runs on a clean checkout.

Everything else pins the three properties that make the command safe to ship:
idempotent, non-destructive, and honest in its report.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from companion.app.commands import migrate
from companion.core.db import Database
from companion.core.paths import TempAppPaths
from companion.domain.ids import is_ulid
from companion.domain.outcome import ApplyOutcome
from companion.platform import secrets

#: The live v1 install this project sits in. Present on the dev machine only.
V1_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def paths(tmp_path: Path) -> TempAppPaths:
    return TempAppPaths(tmp_path)


@pytest.fixture()
def db(paths: TempAppPaths):
    d = Database(paths.state_db)
    yield d
    d.close()


def _write(root: Path, name: str, payload: object) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _src(report: migrate.Report, name: str) -> migrate.SourceResult:
    return next(s for s in report.sources if s.source == name)


# --- config ----------------------------------------------------------------


def test_config_becomes_prefs_minus_the_dropped_keys(paths: TempAppPaths, db: Database) -> None:
    _write(
        paths.root,
        "companion_config.json",
        {
            "version": 1,
            "default_target_playerid": 8004,
            "apply_categories": ["attributes", "ratings"],
            "auto_clear_stale": True,
            "sticky_year": "26",
            "timeout_live": 55.0,
            "poll_sec": 0.06,
            "inject_autoarm": False,
        },
    )
    report = migrate.run(paths, db=db, write_report=False)
    res = _src(report, "companion_config.json")

    assert db.get_pref("default_target_playerid") == 8004
    assert db.get_pref("apply_categories") == ["attributes", "ratings"]
    assert db.get_pref("auto_clear_stale") is True, "a bool must survive as a bool"
    assert db.get_pref("sticky_year") == "26"

    for dropped in ("timeout_live", "poll_sec", "version", "inject_autoarm"):
        assert db.get_pref(dropped, "ABSENT") == "ABSENT", dropped
    assert res.dropped == 4
    assert any("timeout_live" in n for n in res.notes)


def test_every_dropped_key_states_a_reason(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "companion_config.json", dict.fromkeys(migrate.DROPPED_PREFS, 1))
    report = migrate.run(paths, db=db, write_report=False)
    notes = _src(report, "companion_config.json").notes
    assert len(notes) == len(migrate.DROPPED_PREFS)
    assert all(": " in n and len(n.split(": ", 1)[1]) > 20 for n in notes)


def test_user_edits_survive_a_second_migrate(paths: TempAppPaths, db: Database) -> None:
    """First import wins. A re-run must not clobber a value changed in v2."""
    _write(paths.root, "companion_config.json", {"sticky_year": "26"})
    migrate.run(paths, db=db, write_report=False)
    db.set_pref("sticky_year", "25")
    report = migrate.run(paths, db=db, write_report=False)
    assert db.get_pref("sticky_year") == "25"
    assert _src(report, "companion_config.json").skipped == 1


def test_corrupt_config_is_reported_not_swallowed(paths: TempAppPaths, db: Database) -> None:
    """v1's readers turned a truncated file into {} with `except: pass`."""
    (paths.root / "companion_config.json").write_text('{"a": 1', encoding="utf-8")
    report = migrate.run(paths, db=db, write_report=False)
    res = _src(report, "companion_config.json")
    assert res.present and res.errors
    assert "unreadable" in res.errors[0]
    assert report.ok is False


# --- first run marker ------------------------------------------------------


def test_first_run_marker_becomes_a_pref(paths: TempAppPaths, db: Database) -> None:
    (paths.root / ".first_run_done").write_text("1", encoding="utf-8")
    migrate.run(paths, db=db, write_report=False)
    assert db.get_pref("first_run_done") is True


def test_absent_first_run_marker_is_not_present(paths: TempAppPaths, db: Database) -> None:
    report = migrate.run(paths, db=db, write_report=False)
    assert _src(report, ".first_run_done").present is False


# --- job history -----------------------------------------------------------

_HISTORY = [
    {
        "ts": 1785061977.9335153,
        "kind": "card",
        "detail": "Petr Čech",
        "outcome": "error",
        "reason": "edit · id=48940 · player NOT FOUND",
        "target_id": 48940,
        "queue_file": "C:\\Users\\x\\queue\\apply_48940.lua",
        "last_result": "OK processed=1 ok=1",
    },
    {
        "ts": 1784961760.3920608,
        "kind": "boost",
        "detail": "Match Day Pack",
        "outcome": "queued_live",
        "reason": "Queued (no wait)",
        "target_id": None,
        "queue_file": "",
        "last_result": "",
    },
]


def test_job_history_maps_v1_outcome_strings(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "job_history.json", _HISTORY)
    migrate.run(paths, db=db, write_report=False)
    outcomes = sorted(r.outcome for r in db.job_records(limit=50))
    assert outcomes == sorted([ApplyOutcome.FAILED, ApplyOutcome.QUEUED])


@pytest.mark.parametrize(
    ("v1", "expected"),
    [
        ("applied", ApplyOutcome.APPLIED),
        ("queued_live", ApplyOutcome.QUEUED),
        ("blocked", ApplyOutcome.REJECTED),
        ("error", ApplyOutcome.FAILED),
        ("timeout", ApplyOutcome.EXPIRED),
        ("who knows", ApplyOutcome.FAILED),
    ],
)
def test_outcome_mapping(paths: TempAppPaths, db: Database, v1: str, expected) -> None:
    _write(paths.root, "job_history.json", [{"ts": 1.0, "outcome": v1, "detail": v1}])
    migrate.run(paths, db=db, write_report=False)
    assert db.job_records(limit=1)[0].outcome is expected


def test_imported_job_ids_are_real_ulids_and_time_sorted(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "job_history.json", _HISTORY)
    migrate.run(paths, db=db, write_report=False)
    recs = db.job_records(limit=50)
    assert all(is_ulid(r.job_id) for r in recs)
    # ULIDs sort by time, and so must these: the 2026-07-26 row is newer.
    by_id = sorted(recs, key=lambda r: r.job_id)
    assert by_id[-1].label == "Petr Čech"


def test_job_history_import_is_idempotent(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "job_history.json", _HISTORY)
    first = migrate.run(paths, db=db, write_report=False)
    second = migrate.run(paths, db=db, write_report=False)
    assert _src(first, "job_history.json").imported == 2
    assert _src(second, "job_history.json").imported == 0
    assert _src(second, "job_history.json").skipped == 2
    assert db.one("SELECT COUNT(*) c FROM job_record")["c"] == 2


def test_machine_absolute_queue_path_is_kept_out_of_the_columns(
    paths: TempAppPaths, db: Database
) -> None:
    """v1 stored a machine-absolute path in every row; it is forensics, not a column."""
    _write(paths.root, "job_history.json", _HISTORY)
    migrate.run(paths, db=db, write_report=False)
    rec = next(r for r in db.job_records(limit=50) if r.label == "Petr Čech")
    assert rec.result_json is not None
    assert rec.result_json["queue_file"].endswith("apply_48940.lua")
    assert rec.result_json["reason"].startswith("edit")
    assert "C:\\" not in rec.origin and "C:\\" not in rec.label


def test_imported_jobs_are_flagged_unreplayable(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "job_history.json", _HISTORY)
    report = migrate.run(paths, db=db, write_report=False)
    rec = db.job_records(limit=1)[0]
    assert rec.job_json == {} and rec.op_count == 0
    assert any("not replayable" in n for n in _src(report, "job_history.json").notes)


def test_bad_history_row_is_reported_and_the_rest_import(
    paths: TempAppPaths, db: Database
) -> None:
    _write(paths.root, "job_history.json", ["not an object", _HISTORY[0]])
    report = migrate.run(paths, db=db, write_report=False)
    res = _src(report, "job_history.json")
    assert res.imported == 1 and res.errors


# --- snapshots -------------------------------------------------------------


def _make_snapshots(root: Path) -> None:
    fields = [{"f": "acceleration", "v": 78}, {"f": "finishing", "v": 13}]
    _write(
        root,
        "snapshots/index.json",
        [
            {"id": "5a530f3a8cd1", "ts": 1785061976.33, "target_id": 48940,
             "label": "Petr Čech", "kind": "card", "path": "5a530f3a8cd1.json", "n_fields": 2},
            {"id": "d7195d08ba6b", "ts": 1785061957.0, "target_id": 262859,
             "label": "Levi Colwill", "kind": "card", "path": "d7195d08ba6b.json", "n_fields": 2},
        ],
    )
    for sid, label, tid in (("5a530f3a8cd1", "Petr Čech", 48940),
                            ("d7195d08ba6b", "Levi Colwill", 262859)):
        _write(root, f"snapshots/{sid}.json",
               {"id": sid, "ts": 1785061976.33, "target_id": tid, "label": label,
                "kind": "card", "fields": fields, "extra": {}})


def test_snapshots_import_with_their_fields(paths: TempAppPaths, db: Database) -> None:
    _make_snapshots(paths.root)
    migrate.run(paths, db=db, write_report=False)
    snap = db.snapshot("5a530f3a8cd1")
    assert snap is not None
    assert snap.label == "Petr Čech"
    assert snap.target_id == 48940
    assert snap.n_fields == 2
    assert snap.save_uid is None, "v1 never recorded which save a snapshot came from"


def test_snapshot_import_is_idempotent(paths: TempAppPaths, db: Database) -> None:
    _make_snapshots(paths.root)
    migrate.run(paths, db=db, write_report=False)
    second = migrate.run(paths, db=db, write_report=False)
    assert _src(second, "snapshots/").skipped == 2
    assert db.one("SELECT COUNT(*) c FROM snapshot")["c"] == 2


def test_index_entry_without_a_sidecar_is_an_error_not_an_empty_undo(
    paths: TempAppPaths, db: Database
) -> None:
    _write(paths.root, "snapshots/index.json",
           [{"id": "ghost", "ts": 1.0, "path": "ghost.json"}])
    report = migrate.run(paths, db=db, write_report=False)
    assert db.snapshot("ghost") is None, "an undo with no fields would silently do nothing"
    assert _src(report, "snapshots/").errors


def test_sidecar_path_traversal_is_confined(paths: TempAppPaths, db: Database) -> None:
    """SI-7: the stored path is untrusted input, so only its basename is used."""
    _make_snapshots(paths.root)
    evil = paths.root / "evil.json"
    _write(paths.root, "evil.json", {"fields": [{"f": "x", "v": 1}]})
    _write(paths.root, "snapshots/index.json",
           [{"id": "esc", "ts": 1.0, "path": "..\\..\\evil.json"}])
    report = migrate.run(paths, db=db, write_report=False)
    assert db.snapshot("esc") is None
    assert _src(report, "snapshots/").errors
    assert evil.is_file(), "and the outside file is untouched"


def test_last_apply_becomes_a_flag_not_a_second_store(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "last_apply_snapshot.json",
           {"ts": 1785072730.37, "target_id": 1, "label": "t",
            "fields": [{"f": "acceleration", "v": 90}]})
    migrate.run(paths, db=db, write_report=False)
    last = db.last_apply_snapshot()
    assert last is not None
    assert last.label == "t" and last.target_id == 1
    assert db.one("SELECT COUNT(*) c FROM snapshot WHERE is_last_apply=1")["c"] == 1


def test_last_apply_flags_the_existing_twin_rather_than_duplicating(
    paths: TempAppPaths, db: Database
) -> None:
    """v1's file duplicated a snapshot row; the two stores could disagree."""
    _make_snapshots(paths.root)
    _write(paths.root, "last_apply_snapshot.json",
           {"ts": 1785061976.33, "target_id": 48940, "label": "Petr Čech",
            "fields": [{"f": "acceleration", "v": 78}]})
    report = migrate.run(paths, db=db, write_report=False)
    assert db.one("SELECT COUNT(*) c FROM snapshot")["c"] == 2, "no twin row"
    assert db.last_apply_snapshot().id == "5a530f3a8cd1"  # type: ignore[union-attr]
    assert _src(report, "last_apply_snapshot.json").imported == 1


def test_last_apply_import_is_idempotent(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "last_apply_snapshot.json",
           {"ts": 5.0, "target_id": 1, "label": "t", "fields": [{"f": "a", "v": 1}]})
    migrate.run(paths, db=db, write_report=False)
    second = migrate.run(paths, db=db, write_report=False)
    assert _src(second, "last_apply_snapshot.json").skipped == 1
    assert db.one("SELECT COUNT(*) c FROM snapshot")["c"] == 1


# --- favorites -------------------------------------------------------------


def test_favorites_import_with_a_real_composite_key(paths: TempAppPaths, db: Database) -> None:
    _write(
        paths.root,
        "favorites.json",
        [
            {"name": "Petr Čech", "playerid": 48940, "year": "26", "revision": "Icon",
             "overallrating": 89, "acceleration": 78},
            {"name": "Zinedine Zidane", "uid": 1179, "year": "26", "origin": "Icon",
             "overallrating": 94},
        ],
    )
    report = migrate.run(paths, db=db, write_report=False)
    favs = {f.playerid: f for f in db.favorites()}
    assert set(favs) == {"48940", "1179"}
    assert favs["48940"].ovr == 89
    assert favs["48940"].card["acceleration"] == 78, "attrs kept for re-apply"
    assert favs["1179"].revision == "Icon", "v1 fell back to `origin` for the revision"
    assert _src(report, "favorites.json").imported == 2


def test_favorite_without_a_year_or_id_is_reported(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "favorites.json", [{"name": "Nobody"}])
    report = migrate.run(paths, db=db, write_report=False)
    assert db.favorites() == []
    assert _src(report, "favorites.json").errors


def test_absent_favorites_is_a_note_not_an_error(paths: TempAppPaths, db: Database) -> None:
    report = migrate.run(paths, db=db, write_report=False)
    res = _src(report, "favorites.json")
    assert not res.present and not res.errors and res.notes


def test_favorites_import_is_idempotent(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "favorites.json",
           [{"name": "A", "playerid": 1, "year": "26", "revision": "Base"}])
    migrate.run(paths, db=db, write_report=False)
    second = migrate.run(paths, db=db, write_report=False)
    assert _src(second, "favorites.json").skipped == 1
    assert len(db.favorites()) == 1


# --- current squad ---------------------------------------------------------


def test_current_squad_lands_stale_and_unkeyed(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "current_squad.json",
           {"mode": "career", "teamid": 5, "teamname": "Chelsea", "count": 45, "players": []})
    migrate.run(paths, db=db, write_report=False)

    assert db.save_snapshot("", "squad") is None, "must never be served as fresh"
    row = db.save_snapshot("", "squad", ttl_s=None)
    assert row is not None
    assert row.stale is True
    assert row.save_uid == ""
    assert row.payload["teamname"] == "Chelsea"


def test_current_squad_import_is_idempotent(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "current_squad.json", {"teamname": "Chelsea"})
    migrate.run(paths, db=db, write_report=False)
    second = migrate.run(paths, db=db, write_report=False)
    assert _src(second, "current_squad.json").skipped == 1


# --- credentials -----------------------------------------------------------


def test_credentials_never_enter_sqlite(paths: TempAppPaths, db: Database) -> None:
    """Plaintext OAuth tokens in a file next to the exe must not become rows."""
    cred = _write(paths.root, "xai_credentials.json",
                  {"access_token": "SECRET-TOKEN", "refresh_token": "SECRET-REFRESH"})
    report = migrate.run(paths, db=db, write_report=False)

    dump = "\n".join(db.conn.iterdump())
    assert "SECRET-TOKEN" not in dump and "SECRET-REFRESH" not in dump
    assert cred.is_file(), "and the file is not deleted by migrate"
    assert "SECRET-TOKEN" in cred.read_text(encoding="utf-8")
    res = _src(report, "xai_credentials.json")
    assert res.present
    if secrets.available():
        # Sealed into DPAPI, never into SQLite; plaintext remains by policy.
        assert res.imported == 1 and res.skipped == 0
        assert any("secrets" in n for n in res.notes)
    else:
        # An unusable DPAPI service must be reported, never papered over with
        # a plaintext "secure" store or destructive cleanup.
        assert res.imported == 0 and res.errors


def test_credentials_are_handed_to_platform_secrets_when_present(
    paths: TempAppPaths, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    cred = _write(paths.root, "xai_credentials.json", {"access_token": "T"})
    seen: list[Path] = []
    pkg = types.ModuleType("companion.platform")
    pkg.__path__ = []  # type: ignore[attr-defined]
    mod = types.ModuleType("companion.platform.secrets")
    def import_credentials(p: Path, *, shred: bool) -> None:
        assert shred is False
        seen.append(p)

    mod.import_v1_credentials = import_credentials  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "companion.platform", pkg)
    monkeypatch.setitem(sys.modules, "companion.platform.secrets", mod)

    report = migrate.run(paths, db=db, write_report=False)
    assert seen == [cred]
    assert _src(report, "xai_credentials.json").imported == 1
    assert cred.is_file(), "shredding belongs to secrets.py, after a verified round-trip"


def test_secrets_failure_is_reported_and_leaves_the_file(
    paths: TempAppPaths, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    cred = _write(paths.root, "xai_credentials.json", {"access_token": "T"})

    def boom(_p: Path, *, shred: bool) -> None:
        assert shred is False
        raise RuntimeError("DPAPI unavailable")

    pkg = types.ModuleType("companion.platform")
    pkg.__path__ = []  # type: ignore[attr-defined]
    mod = types.ModuleType("companion.platform.secrets")
    mod.import_v1_credentials = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "companion.platform", pkg)
    monkeypatch.setitem(sys.modules, "companion.platform.secrets", mod)

    report = migrate.run(paths, db=db, write_report=False)
    assert cred.is_file()
    assert "DPAPI unavailable" in _src(report, "xai_credentials.json").errors[0]


def test_legacy_credential_importer_is_not_retried_without_shred_false(
    paths: TempAppPaths, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    cred = _write(paths.root, "xai_credentials.json", {"access_token": "T"})
    called = False

    def legacy_importer(_p: Path) -> None:
        nonlocal called
        called = True

    pkg = types.ModuleType("companion.platform")
    pkg.__path__ = []  # type: ignore[attr-defined]
    mod = types.ModuleType("companion.platform.secrets")
    mod.import_v1_credentials = legacy_importer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "companion.platform", pkg)
    monkeypatch.setitem(sys.modules, "companion.platform.secrets", mod)

    report = migrate.run(paths, db=db, write_report=False)
    result = _src(report, "xai_credentials.json")
    assert not called
    assert cred.is_file()
    assert result.imported == 0 and result.skipped == 1


# --- report and non-destructiveness ---------------------------------------


def test_report_is_written_atomically_and_is_valid_json(paths: TempAppPaths, db: Database) -> None:
    _write(paths.root, "job_history.json", _HISTORY)
    report = migrate.run(paths, db=db, write_report=True)
    path = Path(report.report_path)
    assert path.name == "migrate-report.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["total_imported"] == 2
    assert body["ok"] is True
    assert not list(paths.logs.glob("*.tmp")), "the temp file is renamed, not left behind"


def test_migrate_records_its_own_run(paths: TempAppPaths, db: Database) -> None:
    migrate.run(paths, db=db, write_report=False)
    assert db.get_pref("migrate.last_run_utc", "").endswith("Z")
    assert db.get_pref("migrate.schema_version") == db.version


def test_no_v1_file_is_modified_or_deleted(paths: TempAppPaths, db: Database) -> None:
    """The prime directive of §8: v1 keeps working the day after v2 installs."""
    _write(paths.root, "companion_config.json", {"sticky_year": "26"})
    _write(paths.root, "job_history.json", _HISTORY)
    _write(paths.root, "favorites.json", [{"playerid": 1, "year": "26"}])
    _write(paths.root, "current_squad.json", {"teamname": "Chelsea"})
    _write(paths.root, "xai_credentials.json", {"access_token": "T"})
    _make_snapshots(paths.root)
    (paths.root / ".first_run_done").write_text("1", encoding="utf-8")

    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in paths.root.rglob("*")
        if p.is_file() and "logs" not in p.parts and p.suffix != ".sqlite"
        and not p.name.startswith("state.sqlite")
    }
    assert before
    migrate.run(paths, db=db, write_report=True)
    for p, (data, mtime) in before.items():
        assert p.is_file(), f"{p.name} was deleted"
        assert p.read_bytes() == data, f"{p.name} was modified"
        assert p.stat().st_mtime_ns == mtime, f"{p.name} was rewritten"


def test_migrate_on_an_empty_root_is_clean(paths: TempAppPaths, db: Database) -> None:
    report = migrate.run(paths, db=db, write_report=False)
    assert report.ok
    assert report.total_imported == 0
    assert all(not s.present for s in report.sources if s.source != "migrate-report.json")


def test_migrate_opens_its_own_db_when_none_is_injected(paths: TempAppPaths) -> None:
    _write(paths.root, "companion_config.json", {"sticky_year": "26"})
    report = migrate.run(paths, write_report=False)
    assert report.total_imported == 1
    with Database(paths.state_db) as d:
        assert d.get_pref("sticky_year") == "26"


# --- CLI wiring ------------------------------------------------------------


def test_cli_registers_the_migrate_subcommand(tmp_path: Path) -> None:
    from companion.cli import build_parser

    args = build_parser().parse_args(["--root", str(tmp_path), "migrate"])
    assert args.func.__name__ == "cmd_migrate"
    assert args.no_report is False


def test_cli_migrate_runs_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from companion.cli import main

    _write(tmp_path, "companion_config.json", {"sticky_year": "26"})
    code = main(["--root", str(tmp_path), "migrate"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["total_imported"] == 1
    assert (tmp_path / "logs" / "migrate-report.json").is_file()


def test_cli_migrate_exits_nonzero_on_a_corrupt_source(tmp_path: Path) -> None:
    from companion.cli import main

    (tmp_path / "job_history.json").write_text("[{", encoding="utf-8")
    assert main(["--root", str(tmp_path), "migrate", "--no-report"]) == 1


# --- the P0 gate: the real user directory ---------------------------------


@pytest.mark.skipif(
    not (V1_ROOT / "job_history.json").is_file(),
    reason="real v1 user data not present on this machine",
)
def test_real_user_directory_round_trips(tmp_path: Path) -> None:
    """§8.2 P0 gate — run against a COPY of the live install and verify counts.

    Never against the live directory itself: migrate does not write there, but a
    test that could is a test that will.
    """
    root = tmp_path / "v1copy"
    root.mkdir()
    for name in (
        "companion_config.json", "job_history.json", "last_apply_snapshot.json",
        "current_squad.json", "favorites.json", ".first_run_done",
    ):
        src = V1_ROOT / name
        if src.is_file():
            shutil.copy2(src, root / name)
    if (V1_ROOT / "snapshots").is_dir():
        shutil.copytree(V1_ROOT / "snapshots", root / "snapshots")

    paths = TempAppPaths(root)
    report = migrate.run(paths, write_report=True)

    with Database(paths.state_db) as db:
        history = json.loads((V1_ROOT / "job_history.json").read_text(encoding="utf-8"))
        assert db.one("SELECT COUNT(*) c FROM job_record")["c"] == len(history)

        index = json.loads((V1_ROOT / "snapshots/index.json").read_text(encoding="utf-8"))
        snaps = _src(report, "snapshots/")
        assert snaps.imported + len(snaps.errors) == len(index)
        # The last-apply payload can intentionally identify one of the indexed
        # snapshots. In that case migration promotes that row instead of
        # duplicating it, so count all imported snapshot rows here.
        assert db.one("SELECT COUNT(*) c FROM snapshot")["c"] >= len(index)
        assert all(db.snapshot(entry["id"]) is not None for entry in index)
        assert db.one("SELECT COUNT(*) c FROM snapshot WHERE is_last_apply=1")["c"] <= 1

        # Field-by-field on the newest history row.
        newest = max(history, key=lambda r: r.get("ts") or 0)
        rec = max(db.job_records(limit=200), key=lambda r: r.created_utc)
        assert rec.label == (newest.get("detail") or "")[:200]
        assert rec.origin == f"v1.{newest.get('kind')}"
        assert rec.result_json is not None
        assert rec.result_json["outcome_v1"] == newest.get("outcome")
        assert rec.result_json["reason"] == newest.get("reason")

        # Field-by-field on one real snapshot, fields list included.
        entry = index[0]
        sidecar = json.loads(
            (V1_ROOT / "snapshots" / f"{entry['id']}.json").read_text(encoding="utf-8")
        )
        snap = db.snapshot(entry["id"])
        assert snap is not None
        assert snap.label == entry["label"]
        assert snap.target_id == entry["target_id"]
        assert list(snap.fields) == sidecar["fields"]
        assert snap.n_fields == entry["n_fields"]

        # Config, minus the drops.
        cfg = json.loads((V1_ROOT / "companion_config.json").read_text(encoding="utf-8"))
        for key, value in cfg.items():
            if key in migrate.DROPPED_PREFS:
                assert db.get_pref(key, "ABSENT") == "ABSENT", key
            else:
                assert db.get_pref(key) == value, key

        # The squad blob, stale and unkeyed.
        if (V1_ROOT / "current_squad.json").is_file():
            squad = json.loads((V1_ROOT / "current_squad.json").read_text(encoding="utf-8"))
            row = db.save_snapshot("", "squad", ttl_s=None)
            assert row is not None and row.stale
            assert row.payload["count"] == squad["count"]
            assert len(row.payload["players"]) == len(squad["players"])

    assert report.ok, [s.errors for s in report.sources if s.errors]


@pytest.mark.skipif(
    not (V1_ROOT / "job_history.json").is_file(),
    reason="real v1 user data not present on this machine",
)
def test_real_user_directory_migrate_twice_is_a_no_op(tmp_path: Path) -> None:
    root = tmp_path / "v1copy"
    root.mkdir()
    for name in ("companion_config.json", "job_history.json", "last_apply_snapshot.json",
                 "current_squad.json"):
        src = V1_ROOT / name
        if src.is_file():
            shutil.copy2(src, root / name)
    if (V1_ROOT / "snapshots").is_dir():
        shutil.copytree(V1_ROOT / "snapshots", root / "snapshots")

    paths = TempAppPaths(root)
    migrate.run(paths, write_report=False)
    with Database(paths.state_db) as db:
        counts = {
            t: db.one(f"SELECT COUNT(*) c FROM {t}")["c"]
            for t in ("job_record", "snapshot", "favorite", "save_snapshot", "prefs")
        }
    second = migrate.run(paths, write_report=False)
    with Database(paths.state_db) as db:
        again = {
            t: db.one(f"SELECT COUNT(*) c FROM {t}")["c"]
            for t in ("job_record", "snapshot", "favorite", "save_snapshot", "prefs")
        }
    assert counts == again
    assert second.total_imported == 0
