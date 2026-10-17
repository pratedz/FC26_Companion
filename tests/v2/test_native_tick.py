"""Core 2.6.38: the lec_native.dll tick, driven through the real Lua core.

The DLL itself is never loaded here. ``package.loadlib`` is always stubbed, so
these tests behave the same whether or not
``ingame/le_companion/native/lec_native.dll`` has been built yet.

What is pinned:

* ``native.load`` binds only when every step succeeds and never raises;
* the Career event handlers mark the native thread on *every* event, before
  any early return, including simulation events and events without an id;
* a native tick during simulation or outside the hub touches neither the host
  nor the disk;
* native writes only run on a settled, quiet hub with no job awaiting a real
  Career event, and stay disk-silent when the queue is empty;
* the first live tick advertises ``fast_read_timer`` exactly once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parents[2]
INGAME = ROOT / "ingame"
CORE = INGAME / "le_companion"

HUB = 26          # ENTERED_HUB_FIRST_TIME (events.FALLBACK_IDS)
DAY_PASSED = 15   # simulation event
ULID_A = "01J0000000000000000000000A"
ULID_B = "01J0000000000000000000000B"


def _runtime():
    # The core targets Live Editor's Lua 5.4; lupa's default may be newer.
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover - older lupa builds
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


# --------------------------------------------------------------------------
# native.lua in isolation
# --------------------------------------------------------------------------


@pytest.fixture
def native_rt(tmp_path: Path):
    lua = _runtime()
    g = lua.globals()
    g.native_path = _posix(CORE / "native.lua")
    lua.execute(
        """
        loadlib_calls = {}
        dll = { opens = 0, marks = 0, hotkeys = 0 }
        fake_exports = {
            lec_native_open = function() dll.opens = dll.opens + 1 end,
            lec_native_mark_career = function() dll.marks = dll.marks + 1 end,
            lec_native_mark_hotkey = function() dll.hotkeys = dll.hotkeys + 1 end,
        }
        package.loadlib = function(path, sym)
            loadlib_calls[#loadlib_calls + 1] = { path = path, sym = sym }
            local f = fake_exports[sym]
            if f then return f end
            return nil, "no symbol " .. tostring(sym), "init"
        end
        fake_runner = { ticks = 0, fast = 0 }
        function fake_runner.on_native_tick()
            fake_runner.ticks = fake_runner.ticks + 1
            return "ok"
        end
        function fake_runner.tick_fast_read()
            fake_runner.fast = fake_runner.fast + 1
            return "idle"
        end
        native = dofile(native_path)
        """
    )
    return lua


def _core_dir_with_dll(tmp_path: Path) -> str:
    (tmp_path / "native").mkdir(exist_ok=True)
    (tmp_path / "native" / "lec_native.dll").write_bytes(b"MZ not a real dll")
    return _posix(tmp_path)


def _load(lua, core_dir, cfg="{}"):
    lua.globals().core_dir = core_dir
    return lua.eval(
        f"(function() local ok, r = pcall(native.load, core_dir, {cfg}, fake_runner); "
        "return ok, r end)()"
    )


def test_native_load_success_binds_entry_points_and_global_tick(native_rt, tmp_path):
    lua = native_rt
    ok, loaded = _load(lua, _core_dir_with_dll(tmp_path))
    assert ok is True and loaded is True
    native = lua.globals().native
    assert native.loaded is True
    assert native.reason == "loaded"
    assert native.path.endswith("/native/lec_native.dll")
    assert lua.globals().dll.opens == 1
    assert lua.eval("_G.LECompanion_NativeTick == native.tick") is True

    # The DLL's tick entry point reaches the runner.
    assert lua.eval("LECompanion_NativeTick()") == "ok"
    assert lua.globals().fake_runner.ticks == 1
    assert native.ticks == 1

    lua.execute("native.mark_career(); native.mark_career()")
    assert lua.globals().dll.marks == 2

    assert lua.eval("native.hotkey()") == "idle"
    assert lua.globals().dll.hotkeys == 1
    assert lua.globals().fake_runner.fast == 1


def test_native_load_missing_dll_file(native_rt, tmp_path):
    lua = native_rt
    ok, loaded = _load(lua, _posix(tmp_path))
    assert ok is True and loaded is False
    assert lua.globals().native.reason == "dll_missing"
    assert lua.globals().native.loaded is False
    assert lua.eval("#loadlib_calls") == 0
    assert lua.eval("_G.LECompanion_NativeTick") is None
    # Unbound mark_career is a harmless no-op.
    lua.execute("native.mark_career()")


def test_native_load_disabled_by_config_touches_nothing(native_rt, tmp_path):
    lua = native_rt
    ok, loaded = _load(lua, _core_dir_with_dll(tmp_path), cfg="{ native = false }")
    assert ok is True and loaded is False
    assert lua.globals().native.reason == "disabled_by_config"
    assert lua.eval("#loadlib_calls") == 0
    assert lua.eval("_G.LECompanion_NativeTick") is None


def test_native_load_loadlib_failure_reports_error(native_rt, tmp_path):
    lua = native_rt
    lua.execute(
        'package.loadlib = function() return nil, "The specified module could not be found.", "open" end'
    )
    ok, loaded = _load(lua, _core_dir_with_dll(tmp_path))
    assert ok is True and loaded is False
    reason = lua.globals().native.reason
    assert reason.startswith("loadlib_failed:")
    assert "could not be found" in reason
    assert lua.eval("_G.LECompanion_NativeTick") is None


def test_native_load_missing_exports(native_rt, tmp_path):
    lua = native_rt
    lua.execute("fake_exports.lec_native_mark_hotkey = nil")
    ok, loaded = _load(lua, _core_dir_with_dll(tmp_path))
    assert ok is True and loaded is False
    assert lua.globals().native.reason == "exports_missing"
    assert lua.globals().dll.opens == 0, "open() must not run without every export"
    assert lua.eval("_G.LECompanion_NativeTick") is None


def test_native_load_open_throwing_stays_unloaded(native_rt, tmp_path):
    lua = native_rt
    lua.execute('fake_exports.lec_native_open = function() error("signature mismatch") end')
    ok, loaded = _load(lua, _core_dir_with_dll(tmp_path))
    assert ok is True and loaded is False
    native = lua.globals().native
    assert native.reason.startswith("open_failed:")
    assert "signature mismatch" in native.reason
    assert native.loaded is False
    assert lua.eval("_G.LECompanion_NativeTick") is None
    # mark_career must not reach the half-bound DLL either.
    lua.execute("native.mark_career()")
    assert lua.globals().dll.marks == 0


@pytest.mark.parametrize(
    "setup, core_dir, reason",
    [
        ("native.own_dir = nil", None, "no_core_dir"),
        ("native.own_dir = nil", "", "no_core_dir"),
        ("package.loadlib = nil", "DIR", "no_loadlib"),
    ],
)
def test_native_load_never_raises_on_bad_environment(native_rt, tmp_path, setup, core_dir, reason):
    lua = native_rt
    if setup:
        lua.execute(setup)
    target = _core_dir_with_dll(tmp_path) if core_dir == "DIR" else core_dir
    ok, loaded = _load(lua, target)
    assert ok is True and loaded is False
    assert lua.globals().native.reason == reason


def test_native_own_dir_is_the_module_directory(native_rt):
    # init.lua's DIR is nil under stock Lua 5.4 (pcall(debug.getinfo, 1)
    # inspects pcall itself), so native.lua resolves its own directory.
    assert native_rt.globals().native.own_dir == _posix(CORE)


def test_native_load_without_core_dir_falls_back_to_own_dir(native_rt, tmp_path):
    lua = native_rt
    lua.globals().fallback = _core_dir_with_dll(tmp_path)
    lua.execute("native.own_dir = fallback")
    ok, loaded = _load(lua, None)
    assert ok is True and loaded is True
    assert lua.globals().native.path == _posix(tmp_path) + "/native/lec_native.dll"


@pytest.mark.parametrize(
    "config, reason, writes",
    [
        ('{"queue_dir": "x", "native": false}', "disabled_by_config", True),
        ('{"native_tick_writes": false}', "loaded", False),
        ('{"native": true, "native_tick_writes": true}', "loaded", True),
    ],
)
def test_native_load_fallback_honours_install_config_kill_switches(
    native_rt, tmp_path, config, reason, writes
):
    lua = native_rt
    lua.globals().fallback = _core_dir_with_dll(tmp_path)
    (tmp_path / "config.json").write_text(config, encoding="utf-8")
    lua.execute("native.own_dir = fallback; test_cfg = { native = true, native_tick_writes = true }")
    assert lua.eval("native.load(nil, test_cfg, fake_runner)") is (reason == "loaded")
    assert lua.globals().native.reason == reason
    assert lua.eval("test_cfg.native_tick_writes") is writes


def test_explicit_core_dir_does_not_reread_install_config(native_rt, tmp_path):
    lua = native_rt
    core_dir = _core_dir_with_dll(tmp_path)
    (tmp_path / "config.json").write_text('{"native": false}', encoding="utf-8")
    # LEC.configure already merged config.json when it knew the directory.
    ok, loaded = _load(lua, core_dir, cfg="{ native = true }")
    assert ok is True and loaded is True


# --------------------------------------------------------------------------
# The whole core under a fake Live Editor host
# --------------------------------------------------------------------------


class Core:
    def __init__(self, lua, queue: Path):
        self.lua = lua
        self.g = lua.globals()
        self.queue = queue

    def run(self, code: str):
        return self.lua.execute(code)

    def eval(self, expr: str):
        return self.lua.eval(expr)

    def set_clock(self, t: float) -> None:
        self.g.CLOCK = t

    def post(self, event_id=HUB):
        return self.g.handlers["post__CareerModeEvent"](None, event_id)

    def pre(self, event_id=DAY_PASSED):
        return self.g.handlers["pre__CareerModeEvent"](None, event_id)

    def hub(self, at: float):
        self.set_clock(at)
        return self.eval(f"LEC.runner.on_career({HUB}, 'post')")

    def reset_io(self) -> None:
        self.run("io_log = {}; host_calls = 0")

    def io_log(self) -> list[str]:
        return [self.g.io_log[i] for i in range(1, self.eval("#io_log") + 1)]

    def write_job(self, job_id: str, op: dict, **extra) -> Path:
        body = {
            "schema": 3,
            "job_id": job_id,
            "label": "test",
            "ops": [op],
            "budget_ms": 200,
            **extra,
        }
        path = self.queue / "jobs" / f"{job_id}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path


@pytest.fixture
def core(tmp_path: Path):
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

        -- Host probes, counted.
        host_calls = 0
        in_cm = true
        IsInCM = function() host_calls = host_calls + 1; return in_cm end

        -- Disk activity, recorded (reads included: a paused tick must not
        -- even probe the queue).
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

        -- Never load a real DLL, whether or not one has been built.
        loadlib_calls = 0
        package.loadlib = function(path, sym)
            loadlib_calls = loadlib_calls + 1
            return nil, "stubbed in tests", "open"
        end

        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end

        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir })
        assert(LEC.arm())
        """
    )
    c = Core(lua, queue)
    assert c.eval("type(handlers['pre__CareerModeEvent'])") == "function"
    assert c.eval("type(handlers['post__CareerModeEvent'])") == "function"
    return c


def test_arm_reports_native_status_without_loading_a_real_dll(core):
    info = core.eval("LEC.core_info()")
    assert info.native.loaded is False
    # Either the DLL is not built yet, or our stubbed loadlib refused it.
    assert info.native.reason in ("dll_missing", "loadlib_failed:stubbed in tests")
    # Resolved next to the core even though LEC.dir is nil on stock Lua 5.4.
    assert core.eval("LEC.native.path") == _posix(CORE) + "/native/lec_native.dll"
    assert core.eval("_G.LECompanion_NativeTick") is None
    # The runner is wired for native.tick even when loading failed.
    assert core.eval("LEC.native.runner == LEC.runner") is True


def test_arm_survives_native_load_throwing(tmp_path):
    lua = _runtime()
    g = lua.globals()
    g.ingame_root = _posix(INGAME)
    g.queue_dir = _posix(tmp_path)
    lua.execute(
        """
        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end
        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir })
        LEC.native.load = function() error("kaboom") end
        """
    )
    assert lua.eval("LEC.arm()") is True
    assert lua.eval("LEC.native.reason").startswith("load_threw:")
    assert "kaboom" in lua.eval("LEC.native.reason")
    assert lua.eval("type(handlers['post__CareerModeEvent'])") == "function"


def test_configure_reads_native_switches(core):
    assert core.eval("LEC.config.native") is True
    assert core.eval("LEC.config.native_tick_writes") is True
    core.run("LEC.configure({ queue_dir = queue_dir, native = false, native_tick_writes = false })")
    assert core.eval("LEC.config.native") is False
    assert core.eval("LEC.config.native_tick_writes") is False
    # Non-booleans are ignored, the previous value stays.
    core.run("LEC.configure({ queue_dir = queue_dir, native_tick_writes = 'yes' })")
    assert core.eval("LEC.config.native_tick_writes") is False


def test_career_handler_marks_native_on_every_event_before_runner(core):
    core.run(
        """
        seq = {}
        LEC.native._mark_career = function() seq[#seq + 1] = "mark" end
        local real = LEC.runner.on_career
        LEC.runner.on_career = function(id, phase)
            seq[#seq + 1] = "career:" .. phase .. ":" .. tostring(id)
            return real(id, phase)
        end
        """
    )
    pre = core.g.handlers["pre__CareerModeEvent"]
    post = core.g.handlers["post__CareerModeEvent"]
    pre(None, DAY_PASSED)       # simulation pre
    post(None, DAY_PASSED)      # simulation post (non-hub)
    pre(None, HUB)
    post(None, HUB)             # settled hub
    pre(None, None)             # no id at all
    post(None, None)
    post(DAY_PASSED, None)      # id delivered in the first argument
    post(None, "not-a-number")  # junk id

    seq = [core.g.seq[i] for i in range(1, core.eval("#seq") + 1)]
    assert seq == [
        "mark", f"career:pre:{DAY_PASSED}",
        "mark", f"career:post:{DAY_PASSED}",
        "mark", f"career:pre:{HUB}",
        "mark", f"career:post:{HUB}",
        "mark",
        "mark",
        "mark", f"career:post:{DAY_PASSED}",
        "mark",
    ]


def test_native_tick_paused_before_hub_does_no_host_calls_or_io(core):
    core.reset_io()
    assert core.eval("LEC.runner.on_native_tick()") == "paused"
    assert core.eval("LEC.native.tick()") == "paused"
    assert core.eval("host_calls") == 0
    assert core.io_log() == []
    assert core.eval("LEC.runner._native_live") is False


def test_native_tick_paused_during_simulation_does_no_host_calls_or_io(core):
    core.write_job(ULID_A, {"op": "diag.ping"})
    core.hub(100.0)
    assert core.eval("LEC.runner._hub_ready") is True
    core.pre(DAY_PASSED)  # calendar simulate starts
    assert core.eval("LEC.runner._simulating") is True
    core.reset_io()
    core.set_clock(500.0)  # well past every settle / throttle window
    for _ in range(5):
        assert core.eval("LEC.native.tick()") == "paused"
    assert core.eval("host_calls") == 0
    assert core.io_log() == []

    # A simulation POST event keeps it paused too.
    core.post(DAY_PASSED)
    core.reset_io()
    assert core.eval("LEC.runner.on_native_tick()") == "paused"
    assert core.eval("host_calls") == 0
    assert core.io_log() == []


def _drain(core) -> Path:
    return core.queue / "drain.json"


def test_tick_native_writes_gates_in_order(core):
    tnw = "LEC.runner.tick_native_writes()"

    # Before any hub event: no hub.
    assert core.eval(tnw) == "hub_required"

    core.hub(100.0)
    assert core.eval("LEC.runner._last_hub_clock") == 100.0

    core.run("LEC.config.native_tick_writes = false")
    assert core.eval(tnw) == "off"
    core.run("LEC.config.native_tick_writes = true")

    core.run("LEC.runner._simulating = true")
    assert core.eval(tnw) == "sim_paused"
    core.run("LEC.runner._simulating = false; LEC.runner._hub_ready = false")
    assert core.eval(tnw) == "hub_required"
    core.run("LEC.runner._hub_ready = true")

    core.run("LEC.runner._busy = true")
    assert core.eval(tnw) == "busy"
    core.run("LEC.runner._busy = false; LEC.runner._handling_hub = true")
    assert core.eval(tnw) == "busy"
    core.run("LEC.runner._handling_hub = false")

    core.run("LEC.runner._awaiting_event = true")
    core.set_clock(200.0)
    assert core.eval(tnw) == "awaiting_event"
    core.run("LEC.runner._awaiting_event = false")

    # Settling: within 1.5 s of the last hub event.
    core.set_clock(101.0)
    assert core.eval(tnw) == "settling"
    core.set_clock(101.49)
    assert core.eval(tnw) == "settling"
    assert core.eval("LEC.runner._native_write_at") is None

    # Quiet hub, empty queue: idle, and disk-silent (no drain.json stamp).
    _drain(core).unlink(missing_ok=True)
    core.reset_io()
    core.set_clock(101.6)
    assert core.eval(tnw) == "idle"
    assert not _drain(core).exists()
    writes = [e for e in core.io_log() if e.startswith(("open:w", "rename:", "remove:"))]
    assert writes == []

    # Throttled: within 1 s of the previous attempt.
    core.set_clock(102.5)
    assert core.eval(tnw) == "throttled"

    # Work present after the quiet period: pump runs as a native tick.
    core.write_job(ULID_A, {"op": "diag.ping"})
    core.run(
        """
        pumped = {}
        real_pump = LEC.runner.pump
        LEC.runner.pump = function(reason, deadline, ro)
            pumped[#pumped + 1] = { reason = reason, ro = ro == true }
            return "pumped"
        end
        """
    )
    core.set_clock(102.7)
    assert core.eval(tnw) == "pumped"
    assert core.eval("#pumped") == 1
    assert core.eval("pumped[1].reason") == "native_tick"
    assert core.eval("pumped[1].ro") is False, "native writes use the write pump"

    # Settle window resets on every hub event.
    core.run("LEC.runner.pump = real_pump")
    core.hub(110.0)
    core.set_clock(111.0)
    assert core.eval(tnw) == "settling"


def test_tick_native_writes_idle_when_host_left_career(core):
    core.hub(100.0)
    core.write_job(ULID_A, {"op": "diag.ping"})
    core.run("in_cm = false")
    core.set_clock(105.0)
    _drain(core).unlink(missing_ok=True)
    assert core.eval("LEC.runner.tick_native_writes()") == "idle"
    assert not _drain(core).exists()
    assert (core.queue / "jobs" / f"{ULID_A}.json").exists()


def test_native_tick_runs_a_queued_job_end_to_end(core):
    core.hub(100.0)
    job = core.write_job(ULID_A, {"op": "diag.ping", "note": "native"})
    core.set_clock(102.0)
    # Call the write path directly: on_native_tick would let the read tick
    # take diag.ping first (it is an allow-listed bootstrap read).
    assert core.eval("LEC.runner.tick_native_writes()") == "ok"
    result = core.queue / "results" / f"{ULID_A}.json"
    assert result.exists(), "native tick should have drained the job"
    body = json.loads(result.read_text(encoding="utf-8"))
    assert body["ok"] is True
    assert not job.exists()
    drain = json.loads(_drain(core).read_text(encoding="utf-8"))
    assert drain["reason"] == "native_tick"


def test_native_tick_writes_disabled_still_does_reads_only(core):
    core.run("LEC.configure({ queue_dir = queue_dir, native_tick_writes = false })")
    core.hub(100.0)
    job = core.write_job(ULID_A, {"op": "diag.ping"})
    core.set_clock(105.0)
    assert core.eval("LEC.native.tick()") == "ok"
    # diag.ping is an allow-listed bootstrap read, so the read tick takes it;
    # the write path itself reported "off".
    assert core.eval("LEC.runner.tick_native_writes()") == "off"
    assert not job.exists()


_ADD_OP = {"op": "add_to_team", "v": 4, "playerid": 1, "teamid": 2}


def _install_add_stub(core):
    # Step 1: transfer issued, wait for a real Career event. Step 2: verified.
    core.run(
        """
        dispatches = 0
        local ops = package.loaded["ops"]
        ops.dispatch = function(op, job, ctx)
            dispatches = dispatches + 1
            if dispatches == 1 then
                return { ok = false, deferred = true, awaiting_event = true,
                         progress = { step = 6, total_steps = 10, counts = {},
                                      started_at = 1700000000, resumes = 0 } }
            end
            return { ok = true, counts = { side_effects = 1 }, data = { verified = true } }
        end
        """
    )


def test_awaiting_event_set_by_deferred_add_and_cleared_by_settled_hub(core):
    _install_add_stub(core)
    core.write_job(ULID_A, dict(_ADD_OP), grants={"allow_add_to_team": True})

    assert core.eval("LEC.runner._awaiting_event") is False
    core.hub(100.0)  # claims and runs step 1 -> deferred, awaiting a real event
    assert core.eval("dispatches") == 1
    assert core.eval("LEC.runner._awaiting_event") is True
    assert (core.queue / "claimed" / f"{ULID_A}.json").exists()
    state = json.loads((core.queue / "state" / f"{ULID_A}.state.json").read_text(encoding="utf-8"))
    assert state["state"] == "awaiting_event"

    # Native ticks never resume it, however long the hub stays quiet.
    for t in (102.0, 104.0, 106.0, 108.0):
        core.set_clock(t)
        assert core.eval("LEC.runner.tick_native_writes()") == "awaiting_event"
        assert core.eval("LEC.native.tick()") == "ok"
    assert core.eval("dispatches") == 1

    # A non-hub event (e.g. TRANSFER_MOVE_COMPLETE) does not clear it.
    core.post(86)
    assert core.eval("LEC.runner._awaiting_event") is True

    # A settled hub event clears it and resumes the job itself.
    core.hub(120.0)
    assert core.eval("LEC.runner._last_hub_clock") == 120.0
    assert core.eval("dispatches") == 2
    assert core.eval("LEC.runner._awaiting_event") is False
    result = json.loads((core.queue / "results" / f"{ULID_A}.json").read_text(encoding="utf-8"))
    assert result["state"] == "done"
    assert not (core.queue / "claimed" / f"{ULID_A}.json").exists()


def test_deferred_without_awaiting_does_not_block_native_writes(core):
    core.run(
        """
        dispatches = 0
        package.loaded["ops"].dispatch = function()
            dispatches = dispatches + 1
            return { ok = false, deferred = true, awaiting_event = false,
                     progress = { step = 2, counts = {} } }
        end
        """
    )
    # Not add_to_team, so the in-job chunk loop does not spin on it.
    core.write_job(ULID_B, {"op": "diag.ping"})
    core.hub(100.0)
    assert core.eval("dispatches") == 1
    assert core.eval("LEC.runner._awaiting_event") is False
    core.set_clock(102.0)
    assert core.eval("LEC.runner.tick_native_writes()") == "ok"
    assert core.eval("dispatches") == 2


def _session(core) -> dict:
    return json.loads((core.queue / "session.json").read_text(encoding="utf-8"))


def test_first_live_native_tick_advertises_timer_once_and_only_on_hub(core):
    core.run(
        """
        session_phases = {}
        local status = package.loaded["status"]
        local real = status.write_session
        status.write_session = function(extra)
            session_phases[#session_phases + 1] = type(extra) == "table" and extra.phase or "?"
            return real(extra)
        end
        """
    )

    def timer_phases():
        n = core.eval("#session_phases")
        return [core.g.session_phases[i] for i in range(1, n + 1) if core.g.session_phases[i] == "fast_read_timer"]

    # Not on the hub: no advertisement.
    assert core.eval("LEC.native.tick()") == "paused"
    assert timer_phases() == []
    assert core.eval("LEC.runner._native_live") is False

    core.hub(100.0)
    # This LE build has no createTimer: the hub path gives up on the CE timer.
    assert core.eval("LEC.runner._timer_gave_up") is True
    assert _session(core)["capabilities"]["fast_read_timer"] is False

    # 2.6.42: the first live tick advertises at once; work waits for a quiet hub.
    outcomes = []
    for t in (100.2, 100.4, 100.6, 103.0):
        core.set_clock(t)
        outcomes.append(core.eval("LEC.native.tick()"))
    assert outcomes == ["settling", "settling", "settling", "ok"]
    assert timer_phases() == ["fast_read_timer"]
    assert core.eval("LEC.runner._native_live") is True
    assert core.eval("LEC.runner._fast_read_timer.native") is True
    assert core.eval("LEC.version and require('version').CAPABILITIES.fast_read_timer") is True
    assert _session(core)["capabilities"]["fast_read_timer"] is True

    # Later hub events keep it advertised and do not re-advertise.
    core.hub(110.0)
    core.set_clock(112.0)
    core.eval("LEC.native.tick()")
    assert timer_phases() == ["fast_read_timer"]
    assert _session(core)["capabilities"]["fast_read_timer"] is True


@pytest.mark.parametrize(
    "cfg, calls, apc",
    [("{}", 1, False), ("{ native_apc = false }", 1, False), ("{ native_apc = true }", 0, True)],
)
def test_apc_carrier_is_off_unless_config_turns_it_on(native_rt, tmp_path, cfg, calls, apc):
    # Core 2.6.42 review: the APC carrier stays off by default. native.load
    # switches it off right after open, before any Career event.
    lua = native_rt
    lua.execute(
        """
        dll.no_apc = 0
        fake_exports.lec_native_no_apc = function() dll.no_apc = dll.no_apc + 1 end
        """
    )
    ok, loaded = _load(lua, _core_dir_with_dll(tmp_path), cfg=cfg)
    assert ok is True and loaded is True
    assert lua.globals().dll.no_apc == calls
    assert lua.globals().native.apc is apc


def test_old_dll_without_the_apc_export_still_loads(native_rt, tmp_path):
    ok, loaded = _load(native_rt, _core_dir_with_dll(tmp_path), cfg="{ native_apc = true }")
    assert ok is True and loaded is True
    assert native_rt.globals().native.apc is False
