"""Core 2.6.42: one Team Management visit instead of ten.

The real Lua core runs under a fake Live Editor host (lupa). ``ops.dispatch``
is replaced by a recorder so the tests pin the *runner's* decisions: when a
job may run, which class it is, and that the calendar-freeze rules (no host
call, no disk IO outside a settled hub) still hold.

What is pinned:

* hub readiness is a latch: hub screen/notification events keep it,
  simulation, save, load, match, season and unknown events clear it with zero
  host calls and zero disk IO, and only a hub event sets it;
* job classes: read / light / heavy, and which drains may run which;
* a hub screen event runs read and light jobs (throttled, disk-silent when
  the queue is empty), never heavy ones; a settled hub event runs everything
  and finishes a queue that sits behind a slow job;
* a native tick never starts work while a Career event is open, within
  LIMITS.quiet_ms of the last event, or from inside a job;
* session.json advertises the 2.6.42 capabilities.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parents[2]
INGAME = ROOT / "ingame"

HUB = 26                # ENTERED_HUB_FIRST_TIME
TEAM_MANAGEMENT = 82    # ENTERING_TEAM_MANAGEMENT
LEAVING_TM = 83         # LEAVING_TEAM_MANAGEMENT
DATA_READY = 27
SCREEN_DONE = 68        # SCREEN_HAS_DONE_LOADING
ADDED_TO_TEAM = 110     # PLAYER_ADDED_TO_TEAM
HOURS_PASSED = 14
DAY_PASSED = 15
SEASON_RESET = 23
PREPARE_FOR_SAVE = 28
POST_LOAD_PREPARE = 29
ABOUT_TO_ENTER_A_MATCH = 42
UNKNOWN = 999

ULIDS = [f"01J000000000000000000000{c}{d}" for c in "ABC" for d in "ABC"]
assert all(len(u) == 26 for u in ULIDS)

READ = {"op": "diag.ping"}
LIGHT = {"op": "set_fields", "playerid": 1, "fields": {"overallrating": 80}}
HEAVY = {"op": "transfer", "action": "release", "playerid": 1}


def _runtime():
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover - older lupa builds
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


class Core:
    def __init__(self, lua, queue: Path):
        self.lua = lua
        self.g = lua.globals()
        self.queue = queue

    def run(self, code: str):
        return self.lua.execute(code)

    def eval(self, expr: str):
        return self.lua.eval(expr)

    def at(self, t: float) -> None:
        self.g.CLOCK = t

    def event(self, event_id, phase: str):
        return self.eval(f"LEC.runner.on_career({event_id}, '{phase}')")

    def both(self, event_id):
        self.event(event_id, "pre")
        return self.event(event_id, "post")

    def hub(self, event_id=HUB):
        self.event(event_id, "pre")
        return self.event(event_id, "post")

    def ready(self) -> bool:
        return self.eval("LEC.runner._hub_ready") is True

    def reset_io(self) -> None:
        self.run("io_log = {}; host_calls = 0")

    def io(self) -> list[str]:
        return [self.g.io_log[i] for i in range(1, self.eval("#io_log") + 1)]

    def host_calls(self) -> int:
        return int(self.eval("host_calls"))

    def job(self, job_id: str, *ops: dict, **extra) -> Path:
        body = {"schema": 3, "job_id": job_id, "label": "test", "ops": list(ops),
                "budget_ms": 5000, **extra}
        path = self.queue / "jobs" / f"{job_id}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def done(self, job_id: str) -> bool:
        return (self.queue / "results" / f"{job_id}.json").exists()

    def queued(self, job_id: str) -> bool:
        return (self.queue / "jobs" / f"{job_id}.json").exists()

    def ran(self) -> list[str]:
        return [self.g.ran[i] for i in range(1, self.eval("#ran") + 1)]


@pytest.fixture
def core(tmp_path: Path) -> Core:
    lua = _runtime()
    queue = tmp_path / "queue"
    for sub in ("jobs", "claimed", "results", "state", "poison"):
        (queue / sub).mkdir(parents=True)

    def dir_list(path):
        p = Path(str(path))
        if not p.is_dir():
            return None
        return lua.table_from(sorted(os.listdir(p)))

    g = lua.globals()
    g.ingame_root = _posix(INGAME)
    g.queue_dir = _posix(queue)
    g.py_dir_list = dir_list
    lua.execute(
        """
        CLOCK = 100.0
        _G.__mock_clock = function() return CLOCK end
        _G.__mock_time = function() return 1700000000 end

        host_calls = 0
        in_cm = true
        IsInCM = function() host_calls = host_calls + 1; return in_cm end

        io_log = {}
        local real_open, real_rename, real_remove = io.open, os.rename, os.remove
        io.open = function(p, m)
            io_log[#io_log + 1] = "open:" .. tostring(m or "r") .. ":" .. tostring(p)
            return real_open(p, m)
        end
        os.rename = function(a, b)
            io_log[#io_log + 1] = "rename:" .. tostring(a)
            return real_rename(a, b)
        end
        os.remove = function(p)
            io_log[#io_log + 1] = "remove:" .. tostring(p)
            return real_remove(p)
        end
        LEC_DIR_LIST = function(dir)
            io_log[#io_log + 1] = "list:" .. tostring(dir)
            return py_dir_list(dir)
        end

        package.loadlib = function() return nil, "stubbed in tests", "open" end
        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end

        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir, mirror = false })
        assert(LEC.arm())

        -- Record which ops run and under which drain; every op succeeds.
        ran = {}
        SLOW = {}   -- op name -> seconds of mock clock one dispatch takes
        local ops = package.loaded["ops"]
        ops.capture_envelope_snapshot = function() return true, {} end
        ops.dispatch = function(op, job, ctx)
            ran[#ran + 1] = op.op
            CLOCK = CLOCK + (SLOW[op.op] or 0)
            return { ok = true, counts = { found = 1 }, data = {} }
        end
        """
    )
    return Core(lua, queue)


# ---------------------------------------------------------------------------
# The hub latch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_id", [DATA_READY, SCREEN_DONE, LEAVING_TM, ADDED_TO_TEAM, 86, 137])
def test_benign_events_keep_the_hub_latch(core, event_id):
    core.hub()
    assert core.ready()
    core.event(event_id, "pre")
    assert core.ready()
    core.event(event_id, "post")
    assert core.ready()
    assert core.eval("LEC.runner._simulating") is False


def test_hub_pre_phase_is_a_screen_change_not_a_pause(core):
    core.hub()
    assert core.event(TEAM_MANAGEMENT, "pre") == "hub_pre"
    assert core.ready()


@pytest.mark.parametrize(
    "event_id",
    [HOURS_PASSED, DAY_PASSED, PREPARE_FOR_SAVE, POST_LOAD_PREPARE,
     ABOUT_TO_ENTER_A_MATCH, SEASON_RESET, 5, 30, 37, UNKNOWN],
)
def test_revoking_events_clear_the_latch_with_no_host_call_or_io(core, event_id):
    core.hub()
    core.job(ULIDS[0], LIGHT)  # queued work must not tempt a revoking event
    core.reset_io()
    assert core.event(event_id, "pre") == "paused"
    assert not core.ready()
    assert core.eval("LEC.runner._simulating") is True
    core.event(event_id, "post")
    assert not core.ready()
    assert core.host_calls() == 0
    assert core.io() == []
    assert core.queued(ULIDS[0])


def test_benign_events_never_establish_readiness_and_stay_silent(core):
    # Before any hub event (main menu, loading, or after a day advance).
    core.job(ULIDS[0], READ)
    core.reset_io()
    for event_id in (DATA_READY, SCREEN_DONE, LEAVING_TM, ADDED_TO_TEAM):
        core.both(event_id)
    assert not core.ready()
    assert core.host_calls() == 0
    assert core.io() == []
    assert core.queued(ULIDS[0])

    # A day advance after a hub visit: benign events must not revive it.
    core.hub()
    core.both(DAY_PASSED)
    core.reset_io()
    core.both(DATA_READY)
    assert not core.ready()
    assert core.io() == [] and core.host_calls() == 0


def test_only_a_hub_event_sets_the_latch_again(core):
    core.hub()
    core.both(PREPARE_FOR_SAVE)
    core.both(DATA_READY)
    assert not core.ready()
    core.hub(TEAM_MANAGEMENT)
    assert core.ready()


def test_nested_events_inside_a_job_do_not_revoke(core):
    core.hub()
    core.run("LEC.runner._busy = true")
    assert core.event(DAY_PASSED, "pre") == "nested"
    assert core.ready()
    core.run("LEC.runner._busy = false")


# ---------------------------------------------------------------------------
# Job classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ops, expected",
    [
        ([{"op": "diag.ping"}], "read"),
        ([{"op": "export_squad"}], "read"),
        ([{"op": "snapshot"}], "read"),
        ([{"op": "db.dump"}], "read"),
        ([{"op": "injury.scan"}], "read"),
        ([{"op": "budget"}], "read"),
        ([{"op": "budget", "action": "get"}], "read"),
        ([{"op": "assets.legacy", "action": "check"}], "read"),
        ([{"op": "set_fields"}], "light"),
        ([{"op": "bulk_edit"}], "light"),
        ([{"op": "career.set"}], "light"),
        ([{"op": "growth_sync"}], "light"),
        ([{"op": "injury.cure"}], "light"),
        ([{"op": "budget", "action": "set"}], "light"),
        ([{"op": "diag.ping"}, {"op": "set_fields"}], "light"),
        ([{"op": "add_to_team"}], "heavy"),
        ([{"op": "transfer", "action": "release"}], "heavy"),
        ([{"op": "transfer", "action": "list_player"}], "heavy"),
        ([{"op": "create_player"}], "heavy"),
        ([{"op": "names.sync"}], "heavy"),
        ([{"op": "repair_partial_add"}], "heavy"),
        ([{"op": "restore_partial_add"}], "heavy"),
        ([{"op": "assets.legacy", "action": "export"}], "heavy"),
        ([{"op": "something_new"}], "heavy"),
        ([{"op": "diag.ping"}, {"op": "add_to_team"}], "heavy"),
        ([], "heavy"),
    ],
)
def test_job_class(core, ops, expected):
    core.g._ops_json = json.dumps({"ops": ops})
    assert core.eval("LEC.runner.job_class(require('json').decode(_ops_json))") == expected


def test_malformed_job_is_heavy(core):
    assert core.eval("LEC.runner.job_class(nil)") == "heavy"
    assert core.eval("LEC.runner.job_class({ ops = 'x' })") == "heavy"


# ---------------------------------------------------------------------------
# Drains on hub screen events
# ---------------------------------------------------------------------------


def test_ui_event_runs_read_and_light_jobs_but_never_heavy(core):
    core.hub()
    core.job(ULIDS[0], READ)
    core.job(ULIDS[1], LIGHT)
    core.job(ULIDS[2], HEAVY)
    core.at(101.0)
    core.event(DATA_READY, "pre")
    core.event(DATA_READY, "post")
    assert core.done(ULIDS[0]) and core.done(ULIDS[1])
    assert core.queued(ULIDS[2]), "a heavy job waits for Team Management"
    assert core.ran() == ["diag.ping", "set_fields"]
    drain = json.loads((core.queue / "drain.json").read_text(encoding="utf-8"))
    assert drain["reason"] == "ui:DATA_READY"
    assert drain["jobs_deferred"] == 1

    # The next settled hub event runs it.
    core.at(102.0)
    core.hub(TEAM_MANAGEMENT)
    assert core.done(ULIDS[2])
    assert core.ran()[-1] == "transfer"


@pytest.mark.parametrize("op", [{"op": "export_squad"}, {"op": "db.dump", "table": "players"}])
def test_ui_event_leaves_whole_table_reads_for_the_hub_visit(core, op):
    # 0.2-3.2 s of game-thread time: a freeze on an ordinary hub click.
    core.hub()
    core.job(ULIDS[0], op)
    core.job(ULIDS[1], READ)
    core.at(101.0)
    core.event(DATA_READY, "post")
    assert core.done(ULIDS[1])
    assert core.queued(ULIDS[0])
    # A quiet native tick may take it ...
    core.at(103.0)
    assert core.eval("LEC.runner.on_native_tick()") == "ok"
    assert core.done(ULIDS[0])


def test_ui_event_runs_nothing_while_a_signing_awaits_its_event(core):
    core.hub()
    core.job(ULIDS[0], LIGHT)
    core.run("LEC.runner._awaiting_event = true")
    core.at(101.0)
    core.reset_io()
    assert core.event(DATA_READY, "post") == "awaiting_event"
    assert core.io() == []
    assert core.queued(ULIDS[0])


def test_ui_event_drains_are_throttled(core):
    core.hub()
    core.job(ULIDS[0], READ)
    core.at(101.0)
    core.event(DATA_READY, "post")
    assert core.done(ULIDS[0])
    core.job(ULIDS[1], READ)
    core.reset_io()
    core.at(101.5)  # inside LIMITS.ui_pump_every_ms
    assert core.event(DATA_READY, "post") == "throttled"
    assert core.io() == []
    assert core.queued(ULIDS[1])
    core.at(102.2)
    core.event(DATA_READY, "post")
    assert core.done(ULIDS[1])


def test_ui_event_with_an_empty_queue_reads_two_indexes_and_writes_nothing(core):
    core.hub()
    core.at(110.0)
    core.reset_io()
    assert core.event(DATA_READY, "post") == "idle"
    writes = [e for e in core.io() if e.startswith(("open:w", "rename:", "remove:"))]
    assert writes == []
    assert core.host_calls() == 0


def test_ui_event_jobs_can_be_switched_off(core):
    core.run("LEC.configure({ queue_dir = queue_dir, mirror = false, ui_event_jobs = false })")
    core.hub()
    core.job(ULIDS[0], READ)
    core.at(101.0)
    assert core.event(DATA_READY, "post") == "off"
    assert core.queued(ULIDS[0])


def test_ui_event_is_not_a_drain_before_the_hub_or_after_leaving_career(core):
    core.job(ULIDS[0], READ)
    assert core.event(DATA_READY, "post") == "hub_required"
    core.hub()
    core.job(ULIDS[1], READ)
    core.run("in_cm = false")
    core.at(101.0)
    assert core.event(DATA_READY, "post") == "idle"
    assert core.queued(ULIDS[1])


# ---------------------------------------------------------------------------
# One settled hub visit finishes the queue
# ---------------------------------------------------------------------------


def test_one_hub_visit_finishes_a_queue_behind_a_slow_export(core):
    # A 3 s squad export queued before a signing used to push the signing to
    # the next visit (120 ms drain budget). One visit now runs both.
    core.run("SLOW['export_squad'] = 3.0")
    core.job(ULIDS[0], {"op": "export_squad"})
    core.job(ULIDS[1], HEAVY)
    core.job(ULIDS[2], LIGHT)
    core.hub(TEAM_MANAGEMENT)
    assert core.done(ULIDS[0]) and core.done(ULIDS[1]) and core.done(ULIDS[2])
    assert core.ran() == ["export_squad", "transfer", "set_fields"]


def test_hub_drain_still_stops_starting_jobs_after_its_budget(core):
    core.run("SLOW['export_squad'] = 4.5")
    core.job(ULIDS[0], {"op": "export_squad"})
    core.job(ULIDS[1], LIGHT)
    core.hub(TEAM_MANAGEMENT)
    assert core.done(ULIDS[0])
    assert core.queued(ULIDS[1]), "hub_drain_budget_ms still bounds one event"


# ---------------------------------------------------------------------------
# Native ticks
# ---------------------------------------------------------------------------


def test_native_tick_waits_for_quiet_and_for_open_events_to_close(core):
    core.hub()                      # t = 100
    core.job(ULIDS[0], LIGHT)
    core.at(100.5)
    assert core.eval("LEC.runner.on_native_tick()") == "settling"
    core.at(102.0)
    core.event(DATA_READY, "pre")   # a screen starts loading at 102.0 ...
    core.at(104.0)
    assert core.eval("LEC.runner.on_native_tick()") == "settling", "event still open"
    core.event(DATA_READY, "post")  # ... and finishes at 104.0
    assert core.done(ULIDS[0]), "the post event itself drained the light job"
    core.job(ULIDS[1], LIGHT)
    core.at(104.5)
    assert core.eval("LEC.runner.on_native_tick()") == "settling", "quiet_ms not reached"
    core.at(106.0)
    assert core.eval("LEC.runner.on_native_tick()") == "ok"
    assert core.done(ULIDS[1])


def test_native_tick_runs_light_jobs_and_leaves_heavy_ones_for_the_hub(core):
    core.hub()
    core.job(ULIDS[0], HEAVY)
    core.job(ULIDS[1], LIGHT)
    core.at(103.0)
    assert core.eval("LEC.runner.on_native_tick()") == "ok"
    assert core.done(ULIDS[1])
    assert core.queued(ULIDS[0])
    drain = json.loads((core.queue / "drain.json").read_text(encoding="utf-8"))
    assert drain["reason"] == "native_tick"


def test_native_tick_from_inside_a_job_or_hub_drain_starts_nothing(core):
    core.hub()
    core.job(ULIDS[0], LIGHT)
    core.at(105.0)
    for flag in ("_busy", "_handling_hub"):
        core.run(f"LEC.runner.{flag} = true")
        core.reset_io()
        assert core.eval("LEC.runner.on_native_tick()") == "busy"
        assert core.io() == [] and core.host_calls() == 0
        core.run(f"LEC.runner.{flag} = false")
    assert core.queued(ULIDS[0])


def test_a_lost_post_event_cannot_wedge_native_ticks(core):
    core.hub()
    core.job(ULIDS[0], LIGHT)
    core.at(101.0)
    core.event(DATA_READY, "pre")   # its post never arrives
    core.at(120.0)
    assert core.eval("LEC.runner.on_native_tick()") == "settling"
    core.at(140.0)                   # > EVENT_OPEN_MAX_S after the pre
    assert core.eval("LEC.runner.on_native_tick()") == "ok"
    assert core.done(ULIDS[0])


def test_native_tick_during_simulation_is_paused_and_silent(core):
    core.hub()
    core.job(ULIDS[0], LIGHT)
    core.both(DAY_PASSED)
    core.reset_io()
    core.at(130.0)
    assert core.eval("LEC.runner.on_native_tick()") == "paused"
    assert core.io() == [] and core.host_calls() == 0


# ---------------------------------------------------------------------------
# Advertised contract
# ---------------------------------------------------------------------------


def test_session_advertises_the_2_6_42_contract(core):
    core.hub()
    session = json.loads((core.queue / "session.json").read_text(encoding="utf-8"))
    assert session["core_version"] == "2.6.42"
    caps = session["capabilities"]
    for key in ("hub_latch", "ui_event_jobs", "db_name_verify", "native_apc",
                "mirror_after_write"):
        assert caps.get(key) is True, key
    limits = session["limits"]
    assert limits["hub_drain_budget_ms"] >= 1000
    assert limits["ui_pump_every_ms"] >= 250
    assert limits["quiet_ms"] >= 500


def test_core_and_app_versions_agree():
    from companion import CORE_VERSION

    lua = (INGAME / "le_companion" / "version.lua").read_text(encoding="utf-8")
    assert f'version.VERSION = "{CORE_VERSION}"' in lua
