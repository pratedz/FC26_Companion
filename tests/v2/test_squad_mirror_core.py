"""Live squad mirror, in-game side: ``mirror.lua`` driven through the real core.

The fake Live Editor below has real valid-record cursors (one per table, as
LE has) and advances the mock budget clock on every field read, so chunking
is measured in the same units the core budgets in.

Pinned here:

* ``export_squad`` output is byte-identical to the pre-refactor export (golden);
* nothing changed -> no rebuild and no file writes, however many ticks;
* a rebuild is spread over ticks within ``mirror_tick_ms`` and resumes;
* readiness dropping, a job running, or a foreign cursor move abandons it;
* squad.json is written atomically and only when its content hash changes;
* the hub event drives the mirror without the native plugin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parents[2]
INGAME = ROOT / "ingame"

HUB = 26          # ENTERED_HUB_FIRST_TIME
DAY_PASSED = 15   # simulation event
TEAM = 45
SQUAD = (1005, 1017, 1042, 1099, 1250, 1333)
ULID_A = "01J0000000000000000000000A"


def _runtime():
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


# A Live Editor stand-in: tables with one shared valid-record cursor each,
# schema metadata, and the host helpers the export uses.
FAKE_LE = r"""
CLOCK = 100.0
FIELD_COST = 0.0           -- seconds of budget clock per field read
_G.__mock_clock = function() return CLOCK end
_G.__mock_time = function() return 1700000000 end

host_calls = 0
in_cm = true
IsInCM = function() host_calls = host_calls + 1; return in_cm end

DB = {}
local function make_table(name, rows)
    local t = { name = name, rows = rows, cursor = 0, written_records = #rows,
                walks = 0, reads = 0 }
    function t:GetFirstRecord()
        self.walks = self.walks + 1
        self.cursor = 1
        return self.rows[1] and 1 or 0
    end
    function t:GetNextValidRecord()
        self.cursor = self.cursor + 1
        return self.rows[self.cursor] and self.cursor or 0
    end
    function t:GetRecordFieldValue(rec, field)
        self.reads = self.reads + 1
        CLOCK = CLOCK + FIELD_COST
        local row = self.rows[rec]
        if row then return row[field] end
        return nil
    end
    function t:SetRecordFieldValue(rec, field, value)
        local row = self.rows[rec]
        if row then row[field] = value end
    end
    DB[name] = t
    return t
end
GetDBTable = function(name) return DB[name] end

local function descs(names)
    local out = {}
    for i, n in ipairs(names) do out[i] = { name = n, depth = 20, min = 0 } end
    return out
end
PLAYER_FIELDS = { "playerid", "overallrating", "potential", "preferredposition1",
                  "birthdate", "acceleration", "skillmoves", "firstnameid", "isretiring" }
GetDBMeta = function()
    return {
        shortname_name_tables_map = { pl = "players", tpl = "teamplayerlinks",
                                      epn = "editedplayernames" },
        field_desc_map = {
            pl = descs(PLAYER_FIELDS),
            tpl = descs({ "teamid", "playerid", "jerseynumber" }),
            epn = descs({ "playerid", "firstname", "surname", "commonname" }),
        },
    }
end

SQUAD = { 1005, 1017, 1042, 1099, 1250, 1333 }
local in_squad = {}
for _, pid in ipairs(SQUAD) do in_squad[pid] = true end

players, links = {}, {}
for i = 1, 400 do
    local pid = 1000 + i
    players[#players + 1] = {
        playerid = pid, overallrating = 50 + (i % 40), potential = 60 + (i % 30),
        preferredposition1 = i % 28, birthdate = 150000 + i, acceleration = 40 + (i % 50),
        skillmoves = i % 5, firstnameid = 7000 + i, isretiring = 0,
    }
    links[#links + 1] = { teamid = in_squad[pid] and 45 or (900 + i % 5),
                          playerid = pid, jerseynumber = (i % 99) + 1 }
end
-- A national-team link for a squad player, before nothing in particular.
links[#links + 1] = { teamid = 1337, playerid = 1042, jerseynumber = 77 }
names = {
    { playerid = 1017, firstname = "", surname = "", commonname = "Seventeen" },
    { playerid = 1042, firstname = "Ada", surname = "Lovelace", commonname = "" },
    { playerid = 1200, firstname = "Not", surname = "Squad", commonname = "" },
}
make_table("players", players)
make_table("teamplayerlinks", links)
make_table("editedplayernames", names)

user_team = 45
GetUserTeamID = function() host_calls = host_calls + 1; return user_team end
senior_calls = 0
GetUserSeniorTeamPlayerIDs = function()
    senior_calls = senior_calls + 1
    local out = {}
    for i, pid in ipairs(SQUAD) do out[i] = pid end
    return out
end
GetPlayerIDsForTeam = function(tid)
    if tid == 111592 then return { 1300, 1301, 1302, 1005 } end
    return {}
end
GetPlayerName = function(pid) return "Player " .. pid end
GetPlayerPrimaryPositionName = function(pos) return "POS" .. tostring(pos) end
GetTeamName = function(tid) return tid == 45 and "Fake FC" or "Other" end
PlayerExists = function(pid) return 1 end
GetTeamIdFromPlayerId = function(pid) if pid == 1302 then return 900 end return 111592 end
IsPlayerPresigned = function(pid) return 0 end
IsPlayerLoanedOut = function(pid) return pid == 1301 end
save_uid = "SAVE-A"
GetSaveUID = function() return save_uid end
"""


def make_core(tmp_path: Path, ingame: Path = INGAME, *, mirror_dir: bool = True):
    lua = _runtime()
    queue = tmp_path / "queue"
    subs = ["jobs", "claimed", "results", "state", "poison"]
    if mirror_dir:
        subs.append("mirror")
    for sub in subs:
        (queue / sub).mkdir(parents=True, exist_ok=True)

    def dir_list(path):
        p = Path(str(path))
        if not p.is_dir():
            return None
        return lua.table_from(sorted(os.listdir(p)))

    g = lua.globals()
    g.ingame_root = _posix(ingame)
    g.queue_dir = _posix(queue)
    g.py_dir_list = dir_list
    lua.execute(FAKE_LE)
    lua.execute(
        """
        io_log = {}
        local real_open, real_rename, real_remove = io.open, os.rename, os.remove
        io.open = function(p, m)
            io_log[#io_log + 1] = "open:" .. tostring(m or "r") .. ":" .. tostring(p)
            return real_open(p, m)
        end
        os.rename = function(a, b)
            io_log[#io_log + 1] = "rename:" .. tostring(a) .. "->" .. tostring(b)
            return real_rename(a, b)
        end
        os.remove = function(p)
            io_log[#io_log + 1] = "remove:" .. tostring(p)
            return real_remove(p)
        end
        LEC_DIR_LIST = function(dir) return py_dir_list(dir) end
        package.loadlib = function() return nil, "stubbed in tests", "open" end
        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end
        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir })
        assert(LEC.arm())
        """
    )
    return Core(lua, queue)


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

    def advance(self, dt: float) -> None:
        self.g.CLOCK = self.g.CLOCK + dt

    def hub(self) -> str:
        return self.eval(f"LEC.runner.on_career({HUB}, 'post')")

    def tick(self):
        return self.eval("LEC.runner.tick_mirror('native')")

    def native_tick(self):
        return self.eval("LEC.runner.on_native_tick()")

    def reset_io(self) -> None:
        self.run("io_log = {}")

    def io_log(self) -> list[str]:
        return [self.g.io_log[i] for i in range(1, self.eval("#io_log") + 1)]

    def writes(self) -> list[str]:
        return [e for e in self.io_log() if e.startswith(("open:w", "rename:", "remove:"))]

    def mirror_writes(self) -> list[str]:
        return [e for e in self.writes() if "/mirror/" in e]

    @property
    def mirror_file(self) -> Path:
        return self.queue / "mirror" / "squad.json"

    def mirror(self) -> dict:
        return json.loads(self.mirror_file.read_text(encoding="utf-8"))

    def walks(self, table: str) -> int:
        return int(self.eval(f"DB['{table}'].walks"))

    def until_idle(self, limit: int = 400, step: float = 0.2) -> list[str]:
        """Tick (advancing time between ticks) until the mirror stops building."""
        seen = []
        for _ in range(limit):
            self.advance(step)
            out = self.tick()
            seen.append(out)
            if out not in ("building", "restarted", "stale", "incomplete"):
                if not self.eval("LEC.mirror._build ~= nil"):
                    return seen
        raise AssertionError(f"mirror never settled: {seen[-10:]}")

    def settle_hub(self) -> None:
        """Hub event with the hub path's mirror slice disabled."""
        self.run("LEC.config.mirror = false")
        self.hub()
        self.run("LEC.config.mirror = true")


@pytest.fixture
def core(tmp_path: Path) -> Core:
    return make_core(tmp_path)


def _export(core: Core, op: str = "{ op = 'export_squad', free_agent_candidates = { 1300, 1305, 1010 } }"):
    core.run("LEC.runner._hub_ready = true; LEC.runner._simulating = false")
    return core.eval(
        f"LEC.json.encode(LEC.ops.handlers.export_squad({op}, {{}}, {{ cfg = LEC.config }}))"
    )


# Captured from export_squad before the resumable refactor (core 2.6.38,
# commit f2e5c64) on FAKE_LE. Any change here changes what the app ingests.
GOLDEN_EXPORT = json.loads(r"""
{
 "counts": {
  "found": 6,
  "targets": 6
 },
 "data": {
  "count": 6,
  "free_agent_diagnostics": {
   "accepted": 3,
   "host_checks_unavailable": 0,
   "rejected_not_free": 1,
   "rejected_presigned_or_loaned": 1,
   "rejected_rating_or_retirement": 0,
   "template_candidates": 5
  },
  "free_agents": [
   {
    "name": "Player 1010",
    "overallrating": 60,
    "playerid": 1010,
    "source": "free_agent_template",
    "teamid": 111592
   },
   {
    "name": "Player 1300",
    "overallrating": 70,
    "playerid": 1300,
    "source": "free_agent_template",
    "teamid": 111592
   },
   {
    "name": "Player 1305",
    "overallrating": 75,
    "playerid": 1305,
    "source": "free_agent_template",
    "teamid": 111592
   }
  ],
  "players": [
   {
    "acceleration": 45,
    "birthdate": 150005,
    "firstnameid": 7005,
    "jerseynumber": 6,
    "name": "Player 1005",
    "overallrating": 55,
    "playerid": 1005,
    "position": "POS5",
    "potential": 65,
    "preferredposition1": 5,
    "skillmoves": 0,
    "teamid": 45,
    "teamname": "Fake FC"
   },
   {
    "acceleration": 57,
    "birthdate": 150017,
    "firstnameid": 7017,
    "jerseynumber": 18,
    "name": "Seventeen",
    "overallrating": 67,
    "playerid": 1017,
    "position": "POS17",
    "potential": 77,
    "preferredposition1": 17,
    "skillmoves": 2,
    "teamid": 45,
    "teamname": "Fake FC"
   },
   {
    "acceleration": 82,
    "birthdate": 150042,
    "firstnameid": 7042,
    "jerseynumber": 43,
    "name": "Ada Lovelace",
    "overallrating": 52,
    "playerid": 1042,
    "position": "POS14",
    "potential": 72,
    "preferredposition1": 14,
    "skillmoves": 2,
    "teamid": 45,
    "teamname": "Fake FC"
   },
   {
    "acceleration": 89,
    "birthdate": 150099,
    "firstnameid": 7099,
    "jerseynumber": 1,
    "name": "Player 1099",
    "overallrating": 69,
    "playerid": 1099,
    "position": "POS15",
    "potential": 69,
    "preferredposition1": 15,
    "skillmoves": 4,
    "teamid": 45,
    "teamname": "Fake FC"
   },
   {
    "acceleration": 40,
    "birthdate": 150250,
    "firstnameid": 7250,
    "jerseynumber": 53,
    "name": "Player 1250",
    "overallrating": 60,
    "playerid": 1250,
    "position": "POS26",
    "potential": 70,
    "preferredposition1": 26,
    "skillmoves": 0,
    "teamid": 45,
    "teamname": "Fake FC"
   },
   {
    "acceleration": 73,
    "birthdate": 150333,
    "firstnameid": 7333,
    "jerseynumber": 37,
    "name": "Player 1333",
    "overallrating": 63,
    "playerid": 1333,
    "position": "POS25",
    "potential": 63,
    "preferredposition1": 25,
    "skillmoves": 3,
    "teamid": 45,
    "teamname": "Fake FC"
   }
  ],
  "teamid": 45,
  "teamname": "Fake FC"
 },
 "failures": {},
 "ok": true
}
""")


def test_export_squad_output_is_unchanged_by_the_refactor(core):
    assert json.loads(_export(core)) == GOLDEN_EXPORT


def test_export_squad_one_shot_walks_each_table_once(core):
    _export(core)
    # Helper ids present: no membership walk; one jersey walk, one names
    # walk, one players walk. Exactly the pre-refactor pass count.
    assert core.walks("players") == 1
    assert core.walks("teamplayerlinks") == 1
    assert core.walks("editedplayernames") == 1


# --------------------------------------------------------------------------
# The mirror
# --------------------------------------------------------------------------


def _write_job(core: Core, job_id: str, op: dict) -> Path:
    body = {"schema": 3, "job_id": job_id, "label": "test", "ops": [op], "budget_ms": 200}
    path = core.queue / "jobs" / f"{job_id}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _row(mirror: dict, pid: int) -> dict:
    return next(p for p in mirror["players"] if p["playerid"] == pid)


def test_first_settled_tick_writes_the_mirror_in_export_row_shape(core):
    core.settle_hub()
    outs = core.until_idle()
    assert outs[-1] == "written"
    m = core.mirror()
    assert m["schema"] == 1 and m["kind"] == "squad_mirror"
    assert m["save_uid"] == "SAVE-A"
    assert m["session_id"] == core.eval("LEC.session_id()")
    assert m["core_version"] == core.eval("LEC.VERSION")
    assert m["teamid"] == TEAM and m["teamname"] == "Fake FC"
    assert m["generated_at"] == 1700000000 and m["started_at"] == 1700000000
    assert m["seq"] >= 1700000000 * 1000
    assert len(m["hash"]) == 16 and int(m["hash"], 16) >= 0
    assert m["writes_seen"] == [] and m["source"] == "native"
    assert m["count"] == len(SQUAD)
    assert [p["playerid"] for p in m["players"]] == sorted(SQUAD)
    # Exactly the rows export_squad returns (Python reuses its ingest).
    exported = sorted(GOLDEN_EXPORT["data"]["players"], key=lambda p: p["playerid"])
    assert m["players"] == exported
    assert "free_agents" not in m  # the mirror skips the free-agent probe


def test_nothing_changed_means_no_rebuild_and_no_file_writes(core):
    core.settle_hub()
    assert core.until_idle()[-1] == "written"
    core.native_tick()  # first live native tick advertises the timer once
    first = core.mirror_file.read_bytes()
    walks = {t: core.walks(t) for t in ("players", "teamplayerlinks", "editedplayernames")}
    checks = core.eval("LEC.mirror.stats.checks")
    core.reset_io()
    for _ in range(300):  # one minute of native ticks
        core.advance(0.2)
        assert core.native_tick() == "ok"
    assert {t: core.walks(t) for t in walks} == walks
    assert core.writes() == []
    assert core.mirror_file.read_bytes() == first
    # The fingerprint probe is throttled to LIMITS.mirror_check_ms (1 s).
    # (0.2 s steps in float drift, so some probes land on the 6th tick.)
    assert 45 <= core.eval("LEC.mirror.stats.checks") - checks <= 61


def test_rebuild_is_chunked_within_the_tick_budget_and_resumes(core):
    core.settle_hub()
    core.run("FIELD_COST = 0.0005")  # 0.5 ms per field read: 25 ms = 50 reads
    outs, spent = [], []
    for _ in range(500):
        core.advance(0.2)
        before = core.g.CLOCK
        out = core.tick()
        spent.append(core.g.CLOCK - before)
        outs.append(out)
        if out != "building":
            break
    assert outs[-1] == "written"
    assert outs.count("building") >= 10
    # Budget plus at most one clock stride / one built row of overshoot.
    assert max(spent) <= 0.025 + 0.007
    # One walk per table, paused and resumed across ticks, never restarted.
    assert core.walks("players") == 1
    assert core.walks("editedplayernames") == 1
    expected = sorted(GOLDEN_EXPORT["data"]["players"], key=lambda p: p["playerid"])
    assert core.mirror()["players"] == expected
    assert core.mirror()["ticks"] == len(outs)


def test_tick_budget_follows_limits_override(core):
    core.settle_hub()
    core.run("FIELD_COST = 0.0005; require('version').LIMITS.mirror_tick_ms = 5")
    spent = []
    out = None
    for _ in range(2000):
        core.advance(0.2)
        before = core.g.CLOCK
        out = core.tick()
        spent.append(core.g.CLOCK - before)
        if out != "building":
            break
    assert out == "written"
    assert max(spent) <= 0.005 + 0.007


def _build_until_phase(core: Core, phase: str) -> None:
    core.settle_hub()
    core.run("FIELD_COST = 0.0005")
    probe = (
        f"LEC.mirror._build ~= nil and LEC.mirror._build.st.phase == '{phase}' "
        f"and LEC.mirror._build.st.iters.{phase} ~= nil"
    )
    for _ in range(500):
        core.advance(0.2)
        assert core.tick() == "building"
        if core.eval(probe):
            return
    raise AssertionError(f"never reached phase {phase}")


def test_readiness_drop_mid_build_abandons_without_io_and_restarts(core):
    _build_until_phase(core, "players")
    core.eval(f"LEC.runner.on_career({DAY_PASSED}, 'pre')")  # calendar simulate starts
    assert core.eval("LEC.mirror._build == nil")
    assert core.eval("LEC.mirror._dirty") is True
    core.reset_io()
    calls = core.eval("host_calls")
    for _ in range(5):
        core.advance(0.2)
        assert core.tick() == "sim_paused"
        assert core.native_tick() == "paused"
    assert core.io_log() == []
    assert core.eval("host_calls") == calls
    assert not core.mirror_file.exists()

    core.settle_hub()
    assert core.until_idle(limit=600)[-1] == "written"
    assert core.walks("players") == 2  # the abandoned walk plus a fresh one
    assert [p["playerid"] for p in core.mirror()["players"]] == sorted(SQUAD)


def test_hub_pre_phase_is_a_screen_change_and_keeps_progress(core):
    _build_until_phase(core, "players")
    core.eval(f"LEC.runner.on_career({HUB}, 'pre')")
    assert core.tick() == "sim_paused"  # the pre phase pauses like any event
    assert core.eval("LEC.mirror._build ~= nil")
    core.hub()  # settled again; the hub slice continues the same walk
    assert core.until_idle(limit=600)[-1] == "written"
    assert core.walks("players") == 1


def test_a_job_between_ticks_restarts_the_build(core):
    _build_until_phase(core, "players")
    _write_job(core, ULID_A, {"op": "diag.ping"})
    assert core.eval("LEC.runner.pump('test')") == "ok"
    assert (core.queue / "results" / f"{ULID_A}.json").exists()
    core.advance(0.2)
    assert core.tick() == "restarted"
    assert core.until_idle(limit=600)[-1] == "written"
    assert len(core.mirror()["players"]) == len(SQUAD)


def test_another_core_reader_on_the_table_makes_the_walk_stale(core):
    _build_until_phase(core, "players")
    core.run("local it = LEC.db.open('players'):records(); it()")  # a second walk
    core.advance(0.2)
    assert core.tick() == "stale"
    assert core.eval("LEC.mirror._build == nil")
    assert core.until_idle(limit=600)[-1] == "written"
    assert len(core.mirror()["players"]) == len(SQUAD)


def test_foreign_cursor_jump_is_caught_by_the_completeness_check(core):
    _build_until_phase(core, "players")
    core.run("DB['players'].cursor = 395")  # LE's own UI walked the table
    outs = core.until_idle(limit=800)
    assert "incomplete" in outs
    assert outs[-1] == "written"
    assert [p["playerid"] for p in core.mirror()["players"]] == sorted(SQUAD)


def test_squad_json_is_atomic_and_written_only_when_content_changes(core):
    core.settle_hub()
    assert core.until_idle()[-1] == "written"
    m1 = core.mirror()

    core.run("LEC.mirror.mark_dirty('test')")
    core.reset_io()
    assert core.until_idle()[-1] == "unchanged"
    assert core.walks("players") == 2, "dirty forces a rebuild"
    assert core.mirror_writes() == []
    assert core.mirror() == m1

    core.run("players[17].overallrating = 99; LEC.mirror.mark_dirty('test')")  # playerid 1017
    core.reset_io()
    assert core.until_idle()[-1] == "written"
    final = _posix(core.mirror_file)
    tmp = final + ".tmp"
    writes = core.mirror_writes()
    assert writes[0].startswith("open:w") and writes[0].endswith(tmp)
    assert f"rename:{tmp}->{final}" in writes
    assert not Path(tmp).exists()
    m2 = core.mirror()
    assert m2["seq"] > m1["seq"] and m2["hash"] != m1["hash"]
    assert _row(m2, 1017)["overallrating"] == 99


def test_membership_change_is_seen_by_the_fingerprint(core):
    core.settle_hub()
    assert core.until_idle()[-1] == "written"
    core.run("SQUAD[#SQUAD + 1] = 1100")
    core.advance(0.2)
    assert core.tick() == "idle", "probe waits for mirror_check_ms"
    core.advance(1.0)
    assert core.until_idle()[-1] == "written"
    assert len(core.mirror()["players"]) == len(SQUAD) + 1


def test_periodic_verify_rebuilds_without_a_signal_and_stays_silent(core):
    core.settle_hub()
    assert core.until_idle()[-1] == "written"
    walks = core.walks("players")
    core.advance(100.0)
    assert core.tick() == "unchanged"
    assert core.walks("players") == walks
    core.advance(25.0)
    core.reset_io()
    assert core.until_idle()[-1] == "unchanged"
    assert core.walks("players") == walks + 1
    assert core.mirror_writes() == []


def test_write_job_marks_dirty_and_the_next_file_names_it(core):
    core.settle_hub()
    assert core.until_idle()[-1] == "written"
    _write_job(core, ULID_A, {
        "op": "set_fields", "table": "players",
        "key": {"field": "playerid", "value": 1042},
        "fields": {"overallrating": 88}, "growth_mirror": "off",
    })
    core.advance(2.0)
    core.hub()  # pumps the write, then the mirror slice in the same budget
    result = json.loads((core.queue / "results" / f"{ULID_A}.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    core.until_idle()
    m = core.mirror()
    assert m["writes_seen"] == [ULID_A]
    assert _row(m, 1042)["overallrating"] == 88


def test_read_only_jobs_do_not_dirty_the_mirror(core):
    core.settle_hub()
    assert core.until_idle()[-1] == "written"
    _write_job(core, ULID_A, {"op": "diag.ping"})
    core.run("LEC.runner.pump('test')")
    assert core.eval("LEC.mirror._dirty") is False
    assert core.eval("#LEC.mirror._writes") == 0


def test_hub_events_alone_keep_the_mirror_current(core):
    assert core.eval("LEC.native.loaded") is False
    core.run("FIELD_COST = 0.002")  # a 120 ms drain budget holds ~60 reads
    events = 0
    for events in range(1, 300):
        core.advance(1.0)
        core.hub()
        if core.mirror_file.exists():
            break
    assert core.mirror_file.exists()
    assert events > 3, "the rebuild was spread over several hub events"
    m1 = core.mirror()
    assert m1["source"] == "hub"

    # A day passes, then the hub settles again: the mirror rebuilds.
    core.run("players[5].potential = 95")  # playerid 1005
    core.eval(f"LEC.runner.on_career({DAY_PASSED}, 'pre')")
    core.eval(f"LEC.runner.on_career({DAY_PASSED}, 'post')")
    for _ in range(300):
        core.advance(1.0)
        core.hub()
        if core.mirror()["seq"] != m1["seq"]:
            break
    m2 = core.mirror()
    assert _row(m2, 1005)["potential"] == 95

    # More hub events with nothing changed: no walk, no mirror write.
    walks = core.walks("players")
    core.reset_io()
    for _ in range(5):
        core.advance(0.3)
        core.hub()
    assert core.walks("players") == walks
    assert core.mirror_writes() == []


def test_mirror_gates(core):
    assert core.tick() == "hub_required"
    core.settle_hub()
    calls = core.eval("host_calls")
    core.reset_io()
    core.run("LEC.runner._awaiting_event = true")
    assert core.tick() == "awaiting_event"
    core.run("LEC.runner._awaiting_event = false; LEC.runner._busy = true")
    assert core.tick() == "busy"
    core.run("LEC.runner._busy = false; LEC.runner._handling_hub = true")
    assert core.tick() == "busy"
    core.run("LEC.runner._handling_hub = false; LEC.config.mirror = false")
    assert core.tick() == "off"
    core.run("LEC.config.mirror = true; LEC.runner._simulating = true")
    assert core.tick() == "sim_paused"
    assert core.eval("host_calls") == calls
    assert core.io_log() == []
    core.run("LEC.runner._simulating = false")
    assert core.until_idle()[-1] == "written"


def test_missing_mirror_dir_backs_off_and_retries_only_the_write(tmp_path):
    core = make_core(tmp_path, mirror_dir=False)
    core.settle_hub()
    assert core.until_idle()[-1] == "write_failed"
    walks = core.walks("players")
    core.reset_io()
    for _ in range(10):
        core.advance(1.0)
        assert core.tick() == "backoff"
    assert core.writes() == []
    (core.queue / "mirror").mkdir()
    core.advance(30.0)
    assert core.tick() == "written"
    assert core.walks("players") == walks
    assert len(core.mirror()["players"]) == len(SQUAD)


def test_mirror_kill_switch_from_config(core):
    core.run("LEC.configure({ queue_dir = queue_dir, mirror = false })")
    assert core.eval("LEC.config.mirror") is False
    core.run("LEC.runner._hub_ready = true; LEC.runner._simulating = false")
    for _ in range(5):
        core.advance(1.0)
        assert core.tick() == "off"
        core.native_tick()
    assert not core.mirror_file.exists()


def test_hash_is_fnv1a_64(core):
    # Published FNV-1a 64 reference values.
    assert core.eval("LEC.mirror.hash('')") == "cbf29ce484222325"
    assert core.eval("LEC.mirror.hash('a')") == "af63dc4c8601ec8c"


def test_a_throwing_tick_backs_off_instead_of_retrying_every_tick(core):
    core.settle_hub()
    core.run(
        """
        throws = 0
        real_step = LEC.ops.squad_export_step
        LEC.ops.squad_export_step = function() throws = throws + 1; error("boom") end
        """
    )
    core.advance(0.2)
    assert core.tick() == "failed"
    assert core.eval("LEC.runner._busy") is False
    for _ in range(20):
        core.advance(1.0)
        assert core.tick() == "backoff"
    assert core.eval("throws") == 1
    core.run("LEC.ops.squad_export_step = real_step")
    core.advance(15.0)
    assert core.until_idle()[-1] == "written"


def test_no_club_yet_probes_once_per_check_interval(core):
    core.run("user_team = 0")
    core.settle_hub()
    calls = core.eval("host_calls")
    outs = []
    for _ in range(10):  # 2 s of ticks
        core.advance(0.2)
        outs.append(core.tick())
    assert outs.count("no_team") == 2
    assert not core.mirror_file.exists()
    assert core.eval("host_calls") - calls <= 6
