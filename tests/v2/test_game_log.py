"""Game log: Live Editor log discovery, incremental tail, parsing, doctor and CLI.

Fixtures are a few hand-written lines in LE v26.3.6's real format
(``HH:MM:SS.ffffff<TAB>LEVEL<TAB>message``, CRLF, UTF-8) — not copies of the
owner's logs.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.app.commands import game_log as cmd
from companion.core.clock import FakeClock
from companion.domain.game_log import (
    FILTER_ALL,
    FILTER_ERRORS,
    FILTER_WORKER,
    GameLogBuffer,
    SOURCE_LAUNCHER,
    SOURCE_LE,
    SOURCE_LUA,
    SOURCE_WORKER,
    extract_job_id,
    format_row,
    parse_line,
    short_reason,
    summarize,
)
from companion.platform import le_logs
from companion.platform.le_logs import LogTail, LogsDir

JOB_A = "01M4YMC6TJAQYVNMP661T7JM5W"
JOB_B = "01M4YMC7KVVDBAV4SRWWZQP6RY"

#: One squad-full incident: LE's two error lines carry no job id, the worker's
#: job line 230 ms later does.
SQUAD_FULL = [
    f"01:56:49.396690\tINFO\t[LUA API] [LEC] job {JOB_A} -> done ok=true",
    "01:56:51.705423\tERROR\tTottenham Hotspur Squad is full. Player in squad: 42/52, Loaned Out: 10, Presigned: 0",
    "01:56:51.706073\tERROR\t[LUA API] TransferPlayer Failed Target Team Squad is Full @ LE::LUA::ScriptsManager::lua_TransferPlayer",
    f"01:56:51.937413\tINFO\t[LUA API] [LEC] job {JOB_B} -> partial ok=false",
    "01:56:51.954257\tINFO\t[LUA API] [LE_Companion] drain skipped during ENUM_CM_EVENT_MSG_DATA_READY",
    "01:56:51.954567\tINFO\t[LUA API] [LE_Companion] drain skipped during ENUM_CM_EVENT_MSG_DATA_READY",
    "01:56:51.955258\tINFO\t[LUA API] [LE_Companion] drain skipped during ENUM_CM_EVENT_MSG_DATA_READY",
]

EVENT_ERROR = [
    "09:35:57.087179\tERROR\t[LE::LUA::EventsManager::HandleEventError] event pre__CareerModeEvent (ID: 5), error",
    "09:35:57.087394\tERROR\t[LE::LUA::EventsManager::HandleEventError] event post__CareerModeEvent (ID: 6), error: LUA_ERRRUN",
    "09:35:57.087515\tERROR\t[LE::LUA::EventsManager::HandleEventError] C:/FC 26 Live Editor/lua/scripts/le_companion\\json.lua:240: bad argument #2 to 'for iterator' (number expected, got string)",
]

HEADER = [
    "01:37:55.910563\tINFO\tFC 26 Live Editor - v26.3.6",
    "01:37:55.922429\tINFO\tLive Editor DLL Path:    C:\\LE",
    "01:37:56.134497\tINFO\t[LUA API] [LE_Companion] SAFE auto-arm ON (handlers only · no drain at Load) queue=C:/q",
]


def _crlf(lines: list[str]) -> bytes:
    return ("".join(line + "\r\n" for line in lines)).encode("utf-8")


def _name(day: date, *, launcher: bool = False) -> str:
    return f"live_editor_{'launcher_' if launcher else ''}{day:%d-%m-%Y}.log"


def _append(path: Path, data: bytes) -> None:
    with open(path, "ab") as fh:
        fh.write(data)


def _lines(chunks: list[le_logs.TailChunk]) -> list[str]:
    return [line for chunk in chunks for line in chunk.lines]


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


DAY = date(2026, 10, 15)


# --- parsing --------------------------------------------------------------


def test_parse_real_format_error_and_worker_lines() -> None:
    err = parse_line(SQUAD_FULL[2] + "\r", file_date=DAY)
    assert err is not None
    assert (err.clock, err.level, err.source, err.error) == ("01:56:51.706073", "ERROR", SOURCE_LUA, True)
    assert err.stamp == datetime(2026, 10, 15, 1, 56, 51, 706073)
    assert err.job_id == ""

    job = parse_line(SQUAD_FULL[3], file_date=DAY)
    assert job is not None and job.source == SOURCE_WORKER and job.job_id == JOB_B
    assert job.warn and not job.error  # a failed/partial job is highlighted, not an LE error

    le = parse_line(SQUAD_FULL[1])
    assert le is not None and le.source == SOURCE_LE and le.error and le.stamp is None

    arm = parse_line(HEADER[2])
    assert arm is not None and arm.source == SOURCE_WORKER and "·" in arm.message and not arm.error


def test_parse_event_errors_and_unprefixed_lines() -> None:
    entries = [parse_line(line) for line in EVENT_ERROR]
    assert all(e is not None and e.error and e.source in (SOURCE_LUA, SOURCE_WORKER) for e in entries)
    assert entries[2].source == SOURCE_WORKER  # the failing file is the Companion core
    # "event ... error" and LUA_ERR* are errors whatever the level says.
    assert parse_line("10:00:00.000001\tINFO\t[LUA] event post__X (ID: 1), error").error  # type: ignore[union-attr]
    assert parse_line("10:00:00.000001\tINFO\tLUA_ERRRUN somewhere").error  # type: ignore[union-attr]
    # A continuation line without the prefix is kept, with no clock.
    loose = parse_line("    stack traceback:")
    assert loose is not None and loose.clock == "" and loose.error
    assert parse_line("   \r") is None
    assert parse_line("02:19:56.070747\tINFO\tConfig Load Done", launcher=True).source == SOURCE_LAUNCHER  # type: ignore[union-attr]


def test_job_id_extraction() -> None:
    assert extract_job_id(f"[LEC] job {JOB_A} yielded at step 2") == JOB_A
    # An MD5 (32 hex chars) and lowercase session ids are not job ids.
    assert extract_job_id("GAME MD5: 65F97CB9FA1D4542A7F9AD82C7DB421D") == ""
    assert extract_job_id("armed le_companion 2.6.38 (contract 3) session=f3f9a2") == ""
    # ULIDs exclude I, L, O, U.
    assert extract_job_id("job 01M4YMC6TJAQYVNMP661T7JM5I done") == ""
    assert extract_job_id("restored snapshot squad-plan-42 ok", known_ids=("squad-plan-42",)) == "squad-plan-42"


def test_short_reason_and_summary_text() -> None:
    assert short_reason(line.split("\t", 2)[2] for line in SQUAD_FULL[1:3]) == "TransferPlayer squad full"
    assert short_reason(["[LUA API] TransferPlayer Failed Can't transfer player from 1 to 1 @ LE::LUA::X::y"]) == (
        "TransferPlayer failed: Can't transfer player from 1 to 1"
    )
    assert short_reason(line.split("\t", 2)[2] for line in EVENT_ERROR).startswith("json.lua:240: bad argument #2")

    buf = GameLogBuffer()
    buf.begin_file(_name(DAY), DAY)
    assert summarize(buf.tally) == "Game log: no worker errors today"
    for minute in range(6):
        buf.ingest_lines([
            f"09:0{minute}:10.000001\tERROR\tFC Barcelona Squad is full. Player in squad: 45/52, Loaned Out: 7, Presigned: 0",
            f"09:0{minute}:10.000200\tERROR\t[LUA API] TransferPlayer Failed Target Team Squad is Full @ LE::LUA::ScriptsManager::lua_TransferPlayer",
        ])
    assert buf.tally.worker == 6 and buf.tally.total == 6
    assert summarize(buf.tally) == "Game log: 6 worker errors today, last: TransferPlayer squad full (09:05)"
    assert summarize(buf.tally, file_name="live_editor_16-10-2026.log", is_today=False).startswith(
        "Game log: 6 worker errors in live_editor_16-10-2026.log"
    )
    buf.ingest_lines(["10:00:00.000000\tERROR\tDirectX12 - device removed"])
    assert buf.tally.other == 1
    assert summarize(buf.tally).endswith("; 1 other LE error")


# --- ring buffer ---------------------------------------------------------------


def test_incident_job_id_is_inferred_from_the_following_job_line() -> None:
    buf = GameLogBuffer()
    buf.begin_file(_name(DAY), DAY)
    buf.ingest_lines(SQUAD_FULL)
    errors = buf.filtered(FILTER_ERRORS, newest_first=False)
    assert len(errors) == 2
    assert all(e.job_id == JOB_B and e.job_inferred for e in errors)
    assert buf.tally.worker == 1 and buf.tally.last_worker.job_id == JOB_B  # type: ignore[union-attr]
    # The job finished before the error is not blamed.
    assert [e.message for e in buf.for_job(JOB_A)] == [SQUAD_FULL[0].split("\t", 2)[2]]
    assert len(buf.for_job(JOB_B)) == 3
    assert "[job " + JOB_B + "]" in format_row(errors[0])


def test_unrelated_job_line_long_after_an_error_is_not_linked() -> None:
    buf = GameLogBuffer()
    buf.begin_file(_name(DAY), DAY)
    buf.ingest_lines([
        "10:00:00.000000\tERROR\t[LUA API] TransferPlayer Failed Target Team Squad is Full @ LE::LUA::X::y",
        f"10:00:09.000000\tINFO\t[LUA API] [LEC] job {JOB_B} -> done ok=true",
    ])
    assert buf.filtered(FILTER_ERRORS)[0].job_id == ""


def test_ring_buffer_cap_repeat_collapse_and_filters() -> None:
    buf = GameLogBuffer(capacity=5)
    buf.begin_file(_name(DAY), DAY)
    buf.ingest_lines(SQUAD_FULL)
    # Three identical "drain skipped" lines collapse into one entry.
    newest = buf.filtered(FILTER_ALL)[0]
    assert newest.repeat == 3 and newest.clock == "01:56:51.955258"
    assert "×3" in format_row(newest)
    assert len(buf.entries) == 5
    buf.ingest_lines(f"02:00:{i:02d}.000000\tINFO\tDirectX12 - Present {i}" for i in range(20))
    assert len(buf.entries) == 5
    assert [e.message for e in buf.filtered(FILTER_ALL)][0] == "DirectX12 - Present 19"
    assert buf.filtered(FILTER_WORKER) == []          # worker lines rolled out of the cap
    seqs = [e.seq for e in buf.entries]
    assert seqs == sorted(seqs)


def test_worker_filter_keeps_untagged_le_errors() -> None:
    buf = GameLogBuffer()
    buf.begin_file(_name(DAY), DAY)
    buf.ingest_lines(HEADER + SQUAD_FULL)
    worker = buf.filtered(FILTER_WORKER, newest_first=False)
    assert any(e.source == SOURCE_LE and e.error for e in worker)  # "Squad is full" has no tag
    assert not any(e.message.startswith("FC 26 Live Editor") for e in worker)
    assert len(buf.filtered(FILTER_ALL)) > len(worker)


def test_clock_wrap_past_midnight_rolls_the_date() -> None:
    buf = GameLogBuffer()
    buf.begin_file(_name(DAY), DAY)
    buf.ingest_lines(["23:59:58.000000\tINFO\ta", "00:00:03.000000\tINFO\tb", "00:00:04.000000\tINFO\tc"])
    stamps = [e.stamp for e in buf.entries]
    assert stamps[0].date() == DAY and stamps[1].date() == DAY + timedelta(days=1)  # type: ignore[union-attr]
    assert stamps[2].date() == DAY + timedelta(days=1)  # type: ignore[union-attr]


# --- platform: discovery ------------------------------------------------------


def test_log_names_and_current_file_choice(tmp_path: Path) -> None:
    assert le_logs.parse_log_name("live_editor_15-10-2026.log") == ("editor", DAY)
    assert le_logs.parse_log_name("live_editor_launcher_15-10-2026.log") == ("launcher", DAY)
    assert le_logs.parse_log_name("live_editor_31-02-2026.log") is None
    assert le_logs.parse_log_name("notes.txt") is None
    for day in (date(2026, 10, 13), DAY, date(2026, 10, 30)):
        (tmp_path / _name(day)).write_bytes(b"")
    (tmp_path / _name(date(2026, 10, 16), launcher=True)).write_bytes(b"")
    # Newest not after tomorrow; the far-future misnamed file is ignored.
    assert le_logs.current_log(tmp_path, today=date(2026, 10, 16)) == tmp_path / _name(DAY)
    assert le_logs.current_log(tmp_path, today=date(2026, 10, 16), kind="launcher").name.startswith(
        "live_editor_launcher_16"
    )
    assert le_logs.current_log(tmp_path / "missing", today=DAY) is None


def test_resolve_logs_dir_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from companion.platform import registry

    install = tmp_path / "Fc26 LE"
    (install / "Logs").mkdir(parents=True)
    (install / "Logs" / _name(DAY)).write_bytes(b"")
    override = tmp_path / "custom"
    override.mkdir()
    monkeypatch.delenv(le_logs.ENV_LOGS_DIR, raising=False)
    monkeypatch.setattr(registry, "resolve_install_root",
                        lambda hint=None: registry.Resolution(install, registry.Source.HKLM, "Dir"))
    monkeypatch.setattr(registry, "le_data_dir", lambda: None)

    found = le_logs.resolve_logs_dir()
    assert found.path == install / "Logs" and found.source == "registry:HKLM"
    assert le_logs.resolve_logs_dir(override=str(override)).path == override
    assert le_logs.resolve_logs_dir(override=str(install)).path == install / "Logs"
    bad = le_logs.resolve_logs_dir(override=str(tmp_path / "nope"))
    assert bad.path is None and bad.source == "pref"
    monkeypatch.setenv(le_logs.ENV_LOGS_DIR, str(override))
    assert le_logs.resolve_logs_dir().source == "env"
    monkeypatch.delenv(le_logs.ENV_LOGS_DIR)
    monkeypatch.setattr(registry, "resolve_install_root",
                        lambda hint=None: registry.Resolution(None, registry.Source.UNSET, ""))
    monkeypatch.setattr(registry, "le_data_dir", lambda: install)  # data dir holding Logs
    assert le_logs.resolve_logs_dir().source == "data-dir"
    monkeypatch.setattr(registry, "le_data_dir", lambda: None)
    assert not le_logs.resolve_logs_dir().found


# --- platform: tail ------------------------------------------------------------


def test_incremental_tail_with_appends_and_partial_lines(tmp_path: Path) -> None:
    path = tmp_path / _name(DAY)
    path.write_bytes(_crlf(HEADER))
    before = _snapshot(tmp_path)
    tail = LogTail(start="start")
    first = tail.poll(tmp_path, today=DAY)
    assert first[0].reset == "attached" and _lines(first) == HEADER
    assert tail.poll(tmp_path, today=DAY) == []

    # Half a line: held back until its newline arrives.
    _append(path, SQUAD_FULL[0].encode("utf-8"))
    assert tail.poll(tmp_path, today=DAY) == []
    _append(path, b"\r\n" + _crlf(SQUAD_FULL[1:3]))
    got = _lines(tail.poll(tmp_path, today=DAY))
    assert got == SQUAD_FULL[0:3]
    assert tail.offset == path.stat().st_size
    # Invalid UTF-8 is replaced, never fatal.
    _append(path, b"02:00:00.000000\tINFO\tbad \xff byte\r\n")
    assert "\ufffd" in _lines(tail.poll(tmp_path, today=DAY))[0]
    # The reader never wrote to the log folder.
    after = _snapshot(tmp_path)
    assert set(after) == set(before) and after[path.name][0] > before[path.name][0]


def test_tail_read_cap_per_poll(tmp_path: Path) -> None:
    path = tmp_path / _name(DAY)
    lines = [f"03:00:{i % 60:02d}.{i:06d}\tINFO\tline {i} " + "x" * 80 for i in range(200)]
    path.write_bytes(_crlf(lines))
    tail = LogTail(start="start", max_bytes=4096)
    seen: list[str] = []
    polls = 0
    while True:
        chunks = tail.poll(tmp_path, today=DAY)
        if not chunks:
            break
        polls += 1
        assert sum(c.nbytes for c in chunks) <= 4096
        seen += _lines(chunks)
    assert seen == lines and polls >= 5


def test_tail_start_end_skips_old_bulk_and_partial_first_line(tmp_path: Path) -> None:
    path = tmp_path / _name(DAY)
    old = [f"01:00:00.{i:06d}\tINFO\told {i} " + "y" * 60 for i in range(100)]
    path.write_bytes(_crlf(old))
    tail = LogTail(start="end", backlog_bytes=1000)
    got = _lines(tail.poll(tmp_path, today=DAY))
    assert got and len(got) < 20
    assert all(line in old for line in got)  # no half line at the cut
    assert got[-1] == old[-1]


def test_tail_truncation_and_rotation(tmp_path: Path) -> None:
    path = tmp_path / _name(DAY)
    path.write_bytes(_crlf(HEADER + SQUAD_FULL))
    tail = LogTail(start="start")
    tail.poll(tmp_path, today=DAY)

    path.write_bytes(_crlf(["05:00:00.000000\tINFO\tfresh session"]))  # shorter -> truncated
    chunk = tail.poll(tmp_path, today=DAY)[0]
    assert chunk.reset == "truncated" and chunk.lines == ["05:00:00.000000\tINFO\tfresh session"]

    size = path.stat().st_size
    replacement = "06:00:00.000000\tINFO\tother session"  # same byte length, new content
    payload = _crlf([replacement])
    assert len(payload) == size
    path.write_bytes(payload)
    _append(path, _crlf(["06:00:01.000000\tINFO\tnext"]))
    chunk = tail.poll(tmp_path, today=DAY)[0]
    assert chunk.reset == "rotated"
    assert chunk.lines == [replacement, "06:00:01.000000\tINFO\tnext"]


def test_midnight_rollover_drains_old_file_then_switches(tmp_path: Path) -> None:
    yesterday, today = DAY, DAY + timedelta(days=1)
    old = tmp_path / _name(yesterday)
    old.write_bytes(_crlf(["23:59:00.000000\tINFO\tbefore midnight"]))
    tail = LogTail(start="start")
    tail.poll(tmp_path, today=yesterday)

    # The running session keeps writing to yesterday's file past midnight.
    _append(old, _crlf(["00:00:05.000000\tINFO\tstill old session"]))
    got = tail.poll(tmp_path, today=today)
    assert _lines(got) == ["00:00:05.000000\tINFO\tstill old session"] and tail.path == old

    # A new session starts: today's file appears. The old tail (and a trailing
    # line without newline) is flushed first, then the new file is read from 0.
    _append(old, b"00:10:00.000000\tINFO\tlast old line")
    new = tmp_path / _name(today)
    new.write_bytes(_crlf(HEADER))
    chunks = tail.poll(tmp_path, today=today)
    assert [c.path for c in chunks] == [old, new]
    assert chunks[0].lines == ["00:10:00.000000\tINFO\tlast old line"]
    assert chunks[1].reset == "switched" and chunks[1].lines == HEADER
    assert tail.path == new


def test_missing_directory_and_file_are_quiet(tmp_path: Path) -> None:
    tail = LogTail()
    assert tail.poll(None, today=DAY) == []
    assert tail.poll(tmp_path, today=DAY) == []
    monitor = cmd.GameLogMonitor()
    monitor.poll(LogsDir(tmp_path, "pref"), today=DAY)
    assert "No live_editor_<date>.log" in monitor.view().message
    monitor.poll(LogsDir(None, "unset", "registry unset"), today=DAY)
    assert "not found" in monitor.view().message


# --- app layer: monitor, doctor, CLI -------------------------------------------


def _clock_for(day: date) -> FakeClock:
    return FakeClock(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())


def _svc(tmp_path: Path, logs: Path, day: date) -> SimpleNamespace:
    from companion.app.state import AppState, Prefs

    state = AppState(prefs=Prefs(values={le_logs.PREF_LOGS_DIR: str(logs)}))
    store = SimpleNamespace(snapshot=lambda: state)
    return SimpleNamespace(
        paths=SimpleNamespace(root=tmp_path), clock=_clock_for(day), store=store,
        game_log=cmd.GameLogMonitor(),
    )


def _six_squad_full(day_minutes: int = 6) -> list[str]:
    out: list[str] = list(HEADER)
    for i in range(day_minutes):
        job = f"01M4YMC7KVVDBAV4SRWWZQP6R{'ABCDEFGH'[i]}"
        out += [
            f"09:0{i}:51.705423\tERROR\tTottenham Hotspur Squad is full. Player in squad: 42/52, Loaned Out: 10, Presigned: 0",
            f"09:0{i}:51.706073\tERROR\t[LUA API] TransferPlayer Failed Target Team Squad is Full @ LE::LUA::ScriptsManager::lua_TransferPlayer",
            f"09:0{i}:51.937413\tINFO\t[LUA API] [LEC] job {job} -> partial ok=false",
        ]
    return out


def test_monitor_poll_view_and_job_entries(tmp_path: Path) -> None:
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / _name(DAY)).write_bytes(_crlf(HEADER + SQUAD_FULL))
    svc = _svc(tmp_path, logs, DAY)
    assert cmd.poll(svc) is True
    view = cmd.view(svc, FILTER_ERRORS)
    assert view.file_name == _name(DAY) and view.source == "pref" and len(view.entries) == 2
    assert [e.message for e in cmd.entries_for_job(svc, JOB_B)][-1].endswith("partial ok=false")
    assert cmd.poll(svc) is False  # nothing new
    _append(logs / _name(DAY), _crlf(["02:00:00.000000\tINFO\t[LUA API] [LEC] career hub settled — companion resumed"]))
    assert cmd.poll(svc) is True
    assert cmd.view(svc, FILTER_WORKER).entries[0].message.endswith("companion resumed")


def test_doctor_summary_counts_worker_errors_today(tmp_path: Path) -> None:
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / _name(DAY)).write_bytes(_crlf(_six_squad_full()))
    svc = _svc(tmp_path, logs, DAY)
    summ = cmd.summary(svc)
    assert summ.detail == "Game log: 6 worker errors today, last: TransferPlayer squad full (09:05)"
    assert summ.worker_errors == 6 and not summ.ok
    # Incremental: a new incident is picked up on the next call.
    _append(logs / _name(DAY), _crlf(EVENT_ERROR))
    assert cmd.summary(svc).worker_errors == 7

    from companion.app.commands import doctor

    check = doctor.game_log_check(svc)  # type: ignore[arg-type]
    assert check.name == "Game log" and check.severity == "info" and not check.ok
    assert "7 worker errors today" in check.detail
    assert doctor.Report(checks=(check,)).healthy  # info-only: never unhealthy


def test_doctor_run_includes_game_log_check_and_stays_healthy(tmp_path: Path) -> None:
    from companion.app.commands import doctor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport

    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / _name(DAY)).write_bytes(_crlf(_six_squad_full()))
    base = _svc(tmp_path, logs, DAY)
    svc = SimpleNamespace(paths=TempAppPaths(tmp_path / "app"), transport=FakeTransport(),
                          clock=base.clock, store=base.store, game_log=base.game_log)
    report = doctor.run(svc)  # type: ignore[arg-type]
    check = next(c for c in report.checks if c.name == "Game log")
    assert check.severity == "info" and "6 worker errors" in check.detail
    without = doctor.Report(checks=tuple(c for c in report.checks if c.name != "Game log"))
    assert report.healthy == without.healthy


def test_doctor_summary_for_a_quiet_or_old_log(tmp_path: Path) -> None:
    logs = tmp_path / "Logs"
    logs.mkdir()
    old = logs / _name(DAY)
    old.write_bytes(_crlf(HEADER))
    stamp = datetime(2026, 10, 15, 12).timestamp()
    os.utime(old, (stamp, stamp))
    later = DAY + timedelta(days=2)
    summ = cmd.summary(_svc(tmp_path, logs, later))
    assert summ.ok and summ.detail == f"Game log: no worker errors in {_name(DAY)}"


def test_cli_gamelog_text_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture) -> None:
    from companion.cli import build_parser, main

    today = date.today()
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / _name(today)).write_bytes(_crlf(_six_squad_full() + SQUAD_FULL[4:]))
    monkeypatch.setenv(le_logs.ENV_LOGS_DIR, str(logs))
    root = tmp_path / "app"

    args = build_parser().parse_args(["--root", str(root), "gamelog", "--errors", "--lines", "4"])
    assert args.func.__name__ == "cmd_gamelog" and args.errors and args.lines == 4

    assert main(["--root", str(root), "gamelog", "--errors", "--lines", "4"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("# ") and "filter=errors" in out[0]
    rows = out[1:-1]
    assert len(rows) == 4 and all(" ERR " in row for row in rows)
    assert rows[-1].startswith("09:05:51 ERR LUA [LUA API] TransferPlayer Failed")  # oldest first
    assert out[-1] == "# Game log: 6 worker errors today, last: TransferPlayer squad full (09:05)"

    assert main(["--root", str(root), "gamelog", "--json", "--lines", "3"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["filter"] == "worker" and body["file"] == _name(today)
    assert len(body["entries"]) == 3 and body["entries"][-1]["repeat"] == 3
    assert body["summary"]["worker_errors"] == 6

    monkeypatch.setenv(le_logs.ENV_LOGS_DIR, str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    assert main(["--root", str(root), "gamelog"]) == 3


# --- UI helpers (no window) ---------------------------------------------------


def test_ui_helpers_are_pure() -> None:
    from companion.ui.surfaces.activity import selected_job_id
    from companion.ui.surfaces.game_log import row_tags, status_line

    view = cmd.GameLogView((), 7, _name(DAY), "C:/LE/Logs", "registry:HKLM", "", 0)
    assert status_line(view, shown=12, now=datetime(2026, 10, 15, 9, 3, 4)) == (
        f"{_name(DAY)} · 12 shown · updated 09:03:04"
    )
    missing = cmd.GameLogView((), 0, "", "", "unset", "Live Editor Logs folder not found (x).", 0)
    assert status_line(missing, shown=0) == missing.message
    err = parse_line(SQUAD_FULL[2])
    assert row_tags(err) == ("error",)  # type: ignore[arg-type]
    assert selected_job_id(({"_key": JOB_B},)) == JOB_B
    assert selected_job_id(({"_key": "3"},)) == "" and selected_job_id(()) == ""


def test_services_carry_a_game_log_monitor(tmp_path: Path) -> None:
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport

    svc = Services(paths=TempAppPaths(tmp_path), clock=FakeClock(), executor=InlineExecutor(),
                   transport=FakeTransport(), store=Store())
    assert isinstance(svc.game_log, cmd.GameLogMonitor)
    assert cmd.monitor_for(svc) is svc.game_log


def test_tail_reads_while_the_writer_keeps_the_file_open(tmp_path: Path) -> None:
    path = tmp_path / _name(DAY)
    with open(path, "ab", buffering=0) as writer:  # LE keeps its log open
        writer.write(_crlf(HEADER))
        tail = LogTail(start="start")
        assert _lines(tail.poll(tmp_path, today=DAY)) == HEADER
        writer.write(_crlf(SQUAD_FULL[:1]))
        assert _lines(tail.poll(tmp_path, today=DAY)) == SQUAD_FULL[:1]
    with le_logs.open_shared(path) as fh:
        assert fh.read(8) == b"01:37:55"
