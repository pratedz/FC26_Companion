"""lec_native ``native_status.json``: reader, staleness, plain text, surfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from companion.app.presenters import NATIVE_REASON_TEXT, native_reason_text, native_view
from companion.core.clock import FakeClock
from companion.core.paths import TempAppPaths
from companion.core.transport.native_status import (
    KNOWN_REASONS,
    NATIVE_STATUS_FILE,
    NativeStatus,
    read_native_status,
)
from companion.core.transport.v3 import FileTransport, Pill

NOW = 1_784_960_000.0


def _status(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": 1, "plugin": "lec_native", "version": "1.0.0", "le_build": "v26.3.6",
        "pid": 1234, "verified": True, "reason": "ok", "le_base": "0x7ff600000000",
        "le_base_via": "module_list", "pe_header": "match", "disabled": False,
        "error_stop": False, "tick_live": True, "tick_ms": 200, "timer_kind": "window",
        "threads": {"open": 1, "career": 2, "hotkey": 0, "timer": 2},
        "same_thread": True, "same_state": True,
        "counts": {"career_events": 10, "hotkey_events": 0, "ticks": 500, "tick_errors": 0,
                   "skip_thread": 0, "skip_state": 0, "skip_busy": 0},
        "last_tick_age_ms": 120, "last_career_age_ms": 5000, "last_status": 0,
        "uptime_ms": 123456,
    }
    body.update(over)
    return body


def _write(path: Path, payload: Any, *, age: float = 0.5, raw: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
    os.utime(path, (NOW - age, NOW - age))
    return path


# ---- reader ---------------------------------------------------------------


def test_parses_the_documented_example(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, _status())
    ns = read_native_status(p, now_ts=NOW, session_pid=1234)
    assert isinstance(ns, NativeStatus)
    assert ns.live and ns.instant and not ns.stale
    assert ns.tick_ms == 200 and ns.version == "1.0.0" and ns.le_build == "v26.3.6"
    assert ns.threads["career"] == 2 and ns.counts["ticks"] == 500
    assert ns.last_tick_age_ms == 120 and ns.uptime_ms == 123456
    assert ns.timer_kind == "window" and ns.same_thread and ns.same_state


@pytest.mark.parametrize(
    "raw",
    ["", "{", '{"schema": 1, "plugin": "lec_na', "[1, 2]", "null", '"ok"', "{}",
     '{"plugin": "something_else", "reason": "ok"}'],
)
def test_missing_partial_or_foreign_json_is_none(tmp_path: Path, raw: str) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, None, raw=raw)
    assert read_native_status(p, now_ts=NOW) is None


def test_absent_file_is_none(tmp_path: Path) -> None:
    assert read_native_status(tmp_path / NATIVE_STATUS_FILE, now_ts=NOW) is None


def test_wrong_field_types_degrade_instead_of_raising(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, _status(
        tick_ms="fast", tick_live="true", threads=[1], counts={"ticks": "x"},
        pid=None, last_tick_age_ms="soon", reason=None,
    ))
    ns = read_native_status(p, now_ts=NOW, session_pid=1234)
    assert ns is not None
    assert ns.tick_ms == 0 and ns.tick_live is False  # "true" string is not trusted
    assert ns.threads == {} and ns.counts == {"ticks": 0}
    assert ns.pid == 0 and ns.last_tick_age_ms is None
    assert ns.reason == "unknown" and not ns.instant


# ---- staleness ------------------------------------------------------------


def test_old_mtime_is_stale(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, _status(), age=5.0)
    ns = read_native_status(p, now_ts=NOW, session_pid=1234)
    assert ns is not None and ns.stale and ns.stale_reason == "old"
    assert not ns.live and not ns.instant
    assert native_view(ns)["line"] == "Instant mode: not loaded"


def test_fresh_mtime_just_under_the_window_is_live(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, _status(), age=4.5)
    assert read_native_status(p, now_ts=NOW, session_pid=1234).live  # type: ignore[union-attr]


def test_pid_mismatch_is_stale(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, _status(pid=999))
    ns = read_native_status(p, now_ts=NOW, session_pid=1234)
    assert ns is not None and ns.stale and ns.stale_reason == "pid_mismatch"
    view = native_view(ns)
    assert view["state"] == "not_loaded" and "another game session" in view["detail"]


def test_unknown_session_pid_falls_back_to_mtime_only(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, _status(pid=999))
    assert read_native_status(p, now_ts=NOW, session_pid=None).live  # type: ignore[union-attr]
    assert read_native_status(p, now_ts=NOW, session_pid=0).live  # type: ignore[union-attr]


# ---- plain text -----------------------------------------------------------


@pytest.mark.parametrize("reason", KNOWN_REASONS)
def test_every_reason_has_plain_text(reason: str) -> None:
    text = native_reason_text(reason)
    assert text and reason not in text  # plain words, not the code
    assert reason in NATIVE_REASON_TEXT


@pytest.mark.parametrize("reason", [r for r in KNOWN_REASONS if r != "ok"])
def test_off_line_names_the_plain_reason(reason: str) -> None:
    ns = NativeStatus.from_wire(_status(reason=reason, tick_live=False, verified=reason != "le_build_mismatch_code"))
    view = native_view(ns)
    assert view["state"] == "off"
    assert view["line"] == f"Instant mode: off — {native_reason_text(reason)}"


def test_unknown_reason_still_renders() -> None:
    view = native_view(NativeStatus.from_wire(_status(reason="brand_new", tick_live=False)))
    assert view["state"] == "off" and "brand_new" in view["line"] and view["attention"]


def test_on_and_not_loaded_lines() -> None:
    assert native_view(NativeStatus.from_wire(_status()))["line"] == "Instant mode: on (native tick 200 ms)"
    assert native_view(None)["line"] == "Instant mode: not loaded"


def test_disabled_and_error_stop_are_off() -> None:
    disabled = native_view(NativeStatus.from_wire(_status(disabled=True)))
    assert disabled["state"] == "off" and not disabled["attention"]
    stopped = native_view(NativeStatus.from_wire(_status(error_stop=True)))
    assert stopped["line"] == f"Instant mode: off — {native_reason_text('tick_error_stop')}"
    assert stopped["attention"]


def test_waiting_for_career_event_is_not_an_alarm() -> None:
    view = native_view(NativeStatus.from_wire(_status(reason="waiting_for_career_event", tick_live=False)))
    assert view["state"] == "off" and not view["attention"]


# The exact shape lec_native 1.1.0 writes (lec_format_status).
_STATUS_1_1 = """{
  "schema": 1,
  "plugin": "lec_native",
  "version": "1.1.0",
  "le_build": "v26.3.6",
  "pid": 1234,
  "verified": true,
  "reason": "ok",
  "le_base": "0x7ffaae130000",
  "le_base_via": "module_list",
  "pe_header": "match",
  "disabled": false,
  "error_stop": false,
  "tick_live": true,
  "tick_ms": 200,
  "timer_kind": "thread",
  "threads": {"open": 14536, "career": 13704, "hotkey": 0, "timer": 13704},
  "same_thread": true,
  "same_state": true,
  "counts": {"career_events": 3246, "hotkey_events": 0, "ticks": 75, "tick_errors": 0, "skip_thread": 0, "skip_state": 0, "skip_busy": 0, "timer_ticks": 0, "apc_ticks": 75},
  "carrier": "apc",
  "apc": {"enabled": true, "tid": 13704, "queued": 76, "ran": 75, "queue_failed": 0, "pending": true, "pending_ms": 40},
  "last_tick_age_ms": 160,
  "last_career_age_ms": 407891,
  "last_status": 0,
  "uptime_ms": 48289703
}
"""


def test_parses_the_lec_native_1_1_status(tmp_path: Path) -> None:
    p = _write(tmp_path / NATIVE_STATUS_FILE, None, raw=_STATUS_1_1)
    ns = read_native_status(p, now_ts=NOW, session_pid=1234)
    assert ns is not None and ns.instant
    assert ns.carrier == "apc"
    assert ns.apc["queued"] == 76 and ns.apc["ran"] == 75 and ns.apc["pending"] == 1
    assert ns.counts["apc_ticks"] == 75 and ns.counts["timer_ticks"] == 0
    assert native_view(ns)["line"] == "Instant mode: on (native tick 200 ms via APC)"


def test_no_carrier_is_off_and_not_an_alarm() -> None:
    view = native_view(NativeStatus.from_wire(_status(reason="no_carrier", tick_live=False)))
    assert view["state"] == "off" and not view["attention"]
    assert "Team Management" in view["line"]


def test_a_1_0_status_without_carrier_fields_still_reads() -> None:
    ns = NativeStatus.from_wire(_status())
    assert ns.carrier == "" and ns.apc == {}
    assert native_view(ns)["line"] == "Instant mode: on (native tick 200 ms)"


# ---- transport + liveness -------------------------------------------------


def _armed_transport(tmp_path: Path, *, pid: int = 1234) -> tuple[FileTransport, TempAppPaths]:
    paths = TempAppPaths(tmp_path)
    paths.queue.mkdir(parents=True, exist_ok=True)
    paths.session_file.write_text(json.dumps({
        "session_id": "01TESTSESSION0000000000000", "le_pid": pid, "armed_at": NOW - 60,
        "core_version": "", "protocol": 3,
    }), encoding="utf-8")
    t = FileTransport(paths, clock=FakeClock(NOW), pid_probe=lambda p: p == pid)
    return t, paths


def test_transport_native_status_uses_session_pid(tmp_path: Path) -> None:
    t, paths = _armed_transport(tmp_path)
    assert t.native_status() is None
    _write(paths.queue / NATIVE_STATUS_FILE, _status(pid=1234))
    assert t.native_status().instant  # type: ignore[union-attr]
    _write(paths.queue / NATIVE_STATUS_FILE, _status(pid=4321))
    assert t.native_status().stale_reason == "pid_mismatch"  # type: ignore[union-attr]


def test_armed_message_mentions_instant_mode_only_when_live(tmp_path: Path) -> None:
    t, paths = _armed_transport(tmp_path)
    lv = t.liveness()
    assert lv.pill is Pill.ARMED, lv.message
    assert not lv.native_instant and "instantly" not in lv.message
    _write(paths.queue / NATIVE_STATUS_FILE, _status())
    lv = t.liveness()
    assert lv.pill is Pill.ARMED and lv.native_instant
    assert "instantly on a settled Career hub" in lv.message
    _write(paths.queue / NATIVE_STATUS_FILE, _status(), age=30)
    lv = t.liveness()
    assert lv.pill is Pill.ARMED and not lv.native_instant


# ---- surfaces -------------------------------------------------------------


def test_doctor_report_has_native_section_and_check(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from companion.app.commands import doctor
    from companion.core.transport.fake import FakeTransport

    fake = FakeTransport()
    fake.set_native_status(NativeStatus.from_wire(_status()))
    svc = SimpleNamespace(paths=TempAppPaths(tmp_path), transport=fake)
    report = doctor.run(svc)  # type: ignore[arg-type]
    body = report.to_dict()
    assert body["native"]["line"] == "Instant mode: on (native tick 200 ms)"
    check = next(c for c in report.checks if c.name == "Instant mode")
    assert check.ok and check.detail == "on (native tick 200 ms)"


@pytest.mark.parametrize("command", ["status", "doctor"])
def test_cli_output_has_native_section(
    tmp_path: Path, capsys: pytest.CaptureFixture, command: str
) -> None:
    from companion.cli import main

    main(["--root", str(tmp_path), command])
    body = json.loads(capsys.readouterr().out)
    assert body["native"]["state"] == "not_loaded"
    assert body["native"]["line"] == "Instant mode: not loaded"
