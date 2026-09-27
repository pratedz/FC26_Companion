"""Regression guards for the in-game worker's event-driven queue drain.

The worker cannot be loaded in CPython (it runs inside FC Live Editor's Lua
host), so these tests exercise the safety contract at the source boundary:

* DATA_READY is a complete blackout and never reads the Career DB;
* jobs run only on an explicit conservative UI-ready event allow-list; and
* the host callback must forward both the real event id and its phase.

These are deliberately narrow.  They protect the exact regression that left a
valid ``export_squad`` job permanently queued while the UI showed STALLED.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "ingame" / "le_companion" / "runner.lua"
EVENTS = ROOT / "ingame" / "le_companion" / "events.lua"
INIT = ROOT / "ingame" / "le_companion" / "init.lua"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def _quoted_names(table_name: str, source: str) -> set[str]:
    match = re.search(
        rf"events\.{re.escape(table_name)}\s*=\s*\{{(?P<body>.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match, f"{table_name} table is missing"
    return set(re.findall(r'"([A-Z0-9_]+)"', match.group("body")))


def test_loader_aliases_namespaced_modules_before_dependents_load() -> None:
    source = _text(INIT)
    assert 'package.loaded[name] = mod' in source
    assert 'package.loaded["le_companion." .. name] = mod' in source
    assert source.index("package.loaded[name] = mod") < source.index(
        'local version = assert(load_mod("version")'
    )


def test_pump_always_releases_busy_after_an_unexpected_error() -> None:
    source = _text(RUNNER)
    public_pump = source.split("function runner.pump(", 1)[1].split(
        "function runner.force_drain", 1
    )[0]
    assert "util.capture" in public_pump
    assert "runner._busy = false" in public_pump
    assert "pump failed:" in public_pump


def test_data_ready_never_drains_even_read_only_work() -> None:
    source = _text(RUNNER)

    assert '"post_load_read"' not in source
    blackout_branch = source.split('if kind == "blackout" then', 1)[1].split(
        'local cache = events.cache_action', 1
    )[0]
    assert "runner.pump" not in blackout_branch


def test_data_ready_is_still_a_write_blackout_not_a_general_pump() -> None:
    source = _text(EVENTS)
    blackout = _quoted_names("BLACKOUT_NAMES", source)
    pumps = _quoted_names("PUMP_NAMES", source)

    assert "DATA_READY" in blackout
    assert "DATA_READY" not in pumps


def test_bootstrap_filter_allows_reads_and_rejects_writes() -> None:
    source = _text(RUNNER)
    match = re.search(
        r"local function is_bootstrap_read\(job_id\)(?P<body>.*?)\nend",
        source,
        flags=re.DOTALL,
    )
    assert match, "is_bootstrap_read is missing"
    body = match.group("body")

    for operation in ("diag.ping", "db.dump", "export_squad", "snapshot"):
        assert f'name == "{operation}"' in body
    assert 'name == "budget"' in body
    assert '(op.action or "get") == "get"' in body

    # Reads are an explicit allow-list.  Mutations must not creep into it.
    for operation in ("player.update", "team.transfer", "add_to_team"):
        assert f'name == "{operation}"' not in body
    assert "if not safe then return false end" in body


def test_non_blackout_post_events_may_release_only_allowlisted_reads() -> None:
    source = _text(RUNNER)

    assert 'elseif has_bootstrap_read() then' in source
    assert 'runner.pump("safe_read:" .. events.name(event_id), deadline, true)' in source


def test_write_pump_allowlist_contains_only_settled_frontend_events() -> None:
    source = _text(EVENTS)
    pumps = _quoted_names("PUMP_NAMES", source)

    assert pumps == {
        "ENTERED_HUB_FIRST_TIME",
        "SCREEN_HAS_DONE_LOADING",
        "ENTERING_TEAM_MANAGEMENT",
    }
    for unsafe in ("DAY_PASSED", "WEEK_PASSED", "POST_MATCH_REPORTS_DISPLAYED"):
        assert unsafe not in pumps


def test_live_editor_callbacks_forward_event_and_phase() -> None:
    source = _text(INIT)

    assert 'pcall(add, "pre__CareerModeEvent", function(_mgr, event_id)' in source
    assert 'runner.on_career(event_id, "pre")' in source
    assert 'pcall(add, "post__CareerModeEvent", function(_mgr, event_id)' in source
    assert 'runner.on_career(event_id, "post")' in source


def test_arm_does_not_drain_or_start_the_fast_read_timer() -> None:
    source = _text(INIT)
    arm = source.split("function LEC.arm()", 1)[1].split("function LEC.force_drain", 1)[0]
    assert "runner.pump" not in arm
    assert "tick_fast_read" not in arm
    assert "start_fast_read_timer" not in arm


def test_fast_read_timer_starts_after_le_init_and_retries_on_career_post() -> None:
    init = _text(INIT)
    assert 'pcall(add, "post__LEInitDoneEvent"' in init
    assert "runner.on_init_done()" in init

    source = _text(RUNNER)
    init_done = source.split("function runner.on_init_done()", 1)[1].split(
        "function runner.on_career", 1
    )[0]
    assert "start_fast_read_timer" in init_done
    assert "runner.pump" not in init_done

    career_post = source.split('if phase == "post" or phase == nil then', 1)[1].split(
        "function runner.tick_fast_read", 1
    )[0]
    assert "start_fast_read_timer" in career_post


def test_fast_read_timer_drains_reads_only_without_sticky_blackout() -> None:
    source = _text(RUNNER)
    assert "function runner.tick_fast_read" in source
    assert "function runner.start_fast_read_timer" in source
    assert "createTimer" in source
    assert "createTimer(nil" in source
    assert "_G.LEC_FAST_READ_TIMER" in source
    assert "timer.Enabled = true" in source

    tick = source.split("function runner.tick_fast_read()", 1)[1].split(
        "local function timer_is_live", 1
    )[0]
    assert 'runner.pump("fast_read"' in tick
    assert "true)" in tick
    assert "_read_blackout" not in tick
    assert "has_bootstrap_read" in tick
    assert "FAST_READ_WAKE" in tick
    assert 'FAST_READ_WAKE = "_fast_read"' in source

    blackout = source.split('if kind == "blackout" then', 1)[1].split(
        'local cache = events.cache_action', 1
    )[0]
    assert "_read_blackout" not in blackout
    assert "runner.pump" not in blackout
    assert "GetDBTableRows" not in source


def test_add_player_drain_does_not_overlap_transfer_settle() -> None:
    source = _text(RUNNER)
    assert "sign_transfer_busy" in source
    assert "yield_does_not_consume_attempts" in source
    assert "job_first_op(queue[i]) == \"add_to_team\"" in source
    assert "if not resuming then" in source
    assert "dummy_not_free (-1 sentinel)" in source


def test_invalidate_squads_pumps_writes_after_transfer_settle() -> None:
    source = _text(RUNNER)
    career = source.split("function runner.on_career", 1)[1].split(
        "function runner.tick_fast_read", 1
    )[0]
    assert 'kind == "invalidate_squads"' in career
    pump_line = [line for line in career.splitlines() if "runner.pump(" in line]
    assert any("invalidate_squads" in career and 'runner.pump("event:"' in line for line in pump_line)
    assert 'kind == "pump" or kind == "hard_reset" or kind == "invalidate_squads"' in career


def test_add_to_team_batch_picks_all_dummies_before_any_transfer() -> None:
    source = _text(ROOT / "ingame" / "le_companion" / "ops" / "add_to_team.lua")
    batch = source.split("local function step_batch", 1)[1]
    assert "-- pick_all: assign every dummy before any TransferPlayer" in batch
    assert "-- transfer_all: every reserved id, then one awaiting_event" in batch
    pick_at = batch.index("-- pick_all: assign every dummy before any TransferPlayer")
    transfer_at = batch.index("-- transfer_all: every reserved id, then one awaiting_event")
    await_at = batch.index("res.awaiting_event = true")
    assert pick_at < transfer_at < await_at
    assert "try_pick_member" in batch
    assert "TransferPlayer" in batch


def test_snapshot_uses_record_scan_not_full_players_dump() -> None:
    ops = _text(ROOT / "ingame" / "le_companion" / "ops.lua")
    handler = ops.split('ops.handlers["snapshot"]', 1)[1].split("ops.handlers[", 1)[0]
    assert 'GetDBTableRows("players")' not in handler
    assert "db.scan_for" in handler
    assert "h:get" in handler


def test_core_advertises_fast_read_timer() -> None:
    from companion import CORE_VERSION

    version = _text(ROOT / "ingame" / "le_companion" / "version.lua")
    assert "fast_read_timer" in version
    assert "fast_read_timer   = false" in version
    assert f'version.VERSION = "{CORE_VERSION}"' in version
    runner = _text(RUNNER)
    assert "version.CAPABILITIES.fast_read_timer = live" in runner
    status = _text(ROOT / "ingame" / "le_companion" / "status.lua")
    assert 'if k ~= "capabilities" then body[k] = v end' in status
    assert "extra.capabilities" in status
