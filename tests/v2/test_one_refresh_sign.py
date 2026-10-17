"""Core 2.6.42: a signing finishes in ONE Team Management visit.

Reproduces the 2026-10-17 failure: an 11-player signing wrote every card,
transferred every player, then waited 8 Career events for GetPlayerName to
show the new names and ended ``name_not_visible``. GetPlayerName and
GetTeamIdFromPlayerId answer from Live Editor's own player cache, which LE
builds on first use and clears only on DAY_PASSED / POST_LOAD_PREPARE
(FCLiveEditor.DLL v26.3.6, MasterManager listeners at 0x180363d00). The fake
host below models exactly that: both functions read a snapshot taken at the
first call, while the database (players, teamplayerlinks, editedplayernames,
contracts) changes underneath.

The real runner, real add_to_team and real db module run under lupa.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parents[2]
INGAME = ROOT / "ingame"
TEAM = 11
FREE = 111592
TEAM_MANAGEMENT = 82
DATA_READY = 27
DAY_PASSED = 15
ULID_SIGN = "01J00000000000000000S1GN11"
ULID_SIGN2 = "01J00000000000000000S1GN22"
ULID_V3 = "01J00000000000000000S1GNV3"


def _runtime():
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


HOST = r"""
WRITES, TRANSFERS = {}, {}
CONTRACTS = {}          -- [pid] = teamid (career_playercontract)
USER_TEAM = 11
DICT = { [1] = "Lionel", [2] = "Messi", [3] = "Kylian", [4] = "Mbappe" }
LE_CACHE = nil          -- Live Editor's player cache: nil until first use
LE_CACHE_BUILDS = 0

local function make_table(name, fields)
    local t = { name = name, rows = {}, fields = fields, written_records = 0 }
    function t:GetFirstRecord() self.cursor = 1; return #self.rows >= 1 and 1 or 0 end
    function t:GetNextValidRecord()
        self.cursor = (self.cursor or 0) + 1
        return self.cursor <= #self.rows and self.cursor or 0
    end
    function t:GetRecordFieldValue(rec, field)
        local row = self.rows[rec]
        if row == nil or row[field] == nil then error("no field " .. tostring(field)) end
        return row[field]
    end
    function t:SetRecordFieldValue(rec, field, value)
        WRITES[#WRITES + 1] = { self.name, rec, field, value }
        self.rows[rec][field] = value
    end
    function t:add(row)
        self.rows[#self.rows + 1] = row
        self.written_records = #self.rows
        return #self.rows
    end
    return t
end

PLAYER_FIELDS = {
    "playerid", "overallrating", "potential", "isretiring", "acceleration",
    "headassetid", "firstnameid", "lastnameid", "commonnameid",
    "playerjerseynameid", "usercaneditname", "birthdate",
}
TABLES = {
    players = make_table("players", PLAYER_FIELDS),
    teamplayerlinks = make_table("teamplayerlinks", { "playerid", "teamid", "jerseynumber" }),
    editedplayernames = make_table("editedplayernames",
        { "playerid", "firstname", "surname", "playerjerseyname", "commonname" }),
}
GetDBTable = function(name) return TABLES[name] end
GetDBMeta = function()
    local shorts, descs = {}, {}
    for name, t in pairs(TABLES) do
        shorts[name] = name
        local list = {}
        for _, f in ipairs(t.fields) do
            local depth = (name == "editedplayernames" and f ~= "playerid") and 512 or 24
            list[#list + 1] = { name = f, depth = depth, min = 0 }
        end
        descs[name] = list
    end
    return { shortname_name_tables_map = shorts, field_desc_map = descs }
end
InsertDBTableRow = function(name, data)
    local row = {}
    for k, v in pairs(data) do row[k] = v end
    return { addr = TABLES[name]:add(row) }
end

function player_row(pid)
    for _, row in ipairs(TABLES.players.rows) do
        if row.playerid == pid then return row end
    end
end
function link_of(pid)
    for _, row in ipairs(TABLES.teamplayerlinks.rows) do
        if row.playerid == pid then return row end
    end
end
function add_player(pid, teamid, values)
    local row = {
        playerid = pid, overallrating = 55, potential = 60, isretiring = 0,
        acceleration = 41, headassetid = 7, firstnameid = 500 + pid % 100,
        lastnameid = 600 + pid % 100, commonnameid = 0, playerjerseynameid = 0,
        usercaneditname = 0, birthdate = 150000,
    }
    for k, v in pairs(values or {}) do row[k] = v end
    TABLES.players:add(row)
    TABLES.teamplayerlinks:add({ playerid = pid, teamid = teamid, jerseynumber = 0 })
    if teamid == USER_TEAM then CONTRACTS[pid] = USER_TEAM end
end

-- What Career renders, from the database (names_sync.lua header).
function fc_name(pid)
    local row = player_row(pid)
    if not row then return "" end
    if row.firstnameid == 0 and row.lastnameid == 0 and row.commonnameid == 0
        and row.playerjerseynameid == 0 and row.usercaneditname == 1 then
        for _, n in ipairs(TABLES.editedplayernames.rows) do
            if tonumber(n.playerid) == pid then return n.commonname end
        end
        return ""
    end
    local first, last = DICT[row.firstnameid], DICT[row.lastnameid]
    if first and last then return first .. " " .. last end
    return "Dummy " .. pid
end

-- Live Editor's cache: built on first use, stale afterwards.
local function le_cache()
    if LE_CACHE == nil then
        LE_CACHE, LE_CACHE_BUILDS = {}, LE_CACHE_BUILDS + 1
        for _, row in ipairs(TABLES.players.rows) do
            local link = link_of(row.playerid)
            LE_CACHE[row.playerid] = { name = fc_name(row.playerid),
                                       team = link and link.teamid or -1 }
        end
    end
    return LE_CACHE
end
function le_cache_clear() LE_CACHE = nil end   -- LE's DAY_PASSED / POST_LOAD_PREPARE
GetPlayerName = function(pid)
    local e = le_cache()[pid]
    return e and e.name or ("PLAYER_" .. pid)
end
GetTeamIdFromPlayerId = function(pid)
    local e = le_cache()[pid]
    return e and e.team or -1
end

GetUserTeamID = function() return USER_TEAM end
GetSaveUID = function() return "SAVE" end
IsInCM = function() return true end
PlayerExists = function(pid) return player_row(pid) ~= nil end
IsPlayerPresigned = function() return false end
IsPlayerLoanedOut = function() return false end
GetUserSeniorTeamPlayerIDs = function()
    local out = {}
    for pid, tid in pairs(CONTRACTS) do if tid == USER_TEAM then out[pid] = true end end
    return out
end
TransferPlayer = function(pid, teamid)
    TRANSFERS[#TRANSFERS + 1] = { pid, teamid }
    CONTRACTS[pid] = teamid
    link_of(pid).teamid = teamid
end
"""


class Game:
    def __init__(self, lua, queue: Path):
        self.lua = lua
        self.g = lua.globals()
        self.queue = queue
        self.hub_events = 0

    def run(self, code: str):
        return self.lua.execute(code)

    def eval(self, expr: str):
        return self.lua.eval(expr)

    def team_management(self):
        self.hub_events += 1
        self.eval(f"LEC.runner.on_career({TEAM_MANAGEMENT}, 'pre')")
        return self.eval(f"LEC.runner.on_career({TEAM_MANAGEMENT}, 'post')")

    def submit(self, job_id: str, op: dict) -> None:
        body = {"schema": 3, "job_id": job_id, "label": "Sign", "ops": [op],
                "budget_ms": 5000, "grants": {"allow_add_to_team": True},
                "requires": {"save_uid": "SAVE"}}
        (self.queue / "jobs" / f"{job_id}.json").write_text(json.dumps(body), encoding="utf-8")

    def result(self, job_id: str) -> dict | None:
        path = self.queue / "results" / f"{job_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def consumed(self) -> set[int]:
        path = self.queue / "state" / "consumed_free_agent_dummies_SAVE.json"
        if not path.exists():
            return set()
        return {int(k) for k in json.loads(path.read_text(encoding="utf-8"))["playerids"]}


@pytest.fixture
def game(tmp_path: Path) -> Game:
    lua = _runtime()
    queue = tmp_path / "queue"
    for sub in ("jobs", "claimed", "results", "state", "poison"):
        (queue / sub).mkdir(parents=True)
    g = lua.globals()
    g.ingame_root = _posix(INGAME)
    g.queue_dir = _posix(queue)
    g.py_dir_list = (lambda path: lua.table_from(sorted(os.listdir(str(path))))
                     if Path(str(path)).is_dir() else None)
    lua.execute(HOST)
    lua.execute(
        """
        CLOCK = 100.0
        _G.__mock_clock = function() return CLOCK end
        _G.__mock_time = function() return 1700000000 end
        LEC_DIR_LIST = function(dir) return py_dir_list(dir) end
        package.loadlib = function() return nil, "stubbed", "open" end
        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end
        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir, core_ops = { "add_to_team" }, mirror = false })
        assert(LEC.arm())
        """
    )
    return Game(lua, queue)


CARDS = [
    ("Gianluigi", "Donnarumma"), ("Achraf", "Hakimi"), ("Sergio", "Ramos"),
    ("Marquinhos", ""), ("Nuno", "Mendes"), ("Marco", "Verratti"), ("Vitinha", ""),
    ("Fabian", "Ruiz"), ("Lionel", "Messi"), ("Kylian", "Mbappe"), ("Neymar", "Jr"),
]


def _member(pid: int, first: str, sur: str, *, native: bool = False) -> dict:
    common = f"{first} {sur}".strip()
    player = {
        "fields": {"overallrating": 88, "potential": 88, "acceleration": 90, "isretiring": 0},
        "face": {"headassetid": 200000 + pid},
        "names": {"firstname": first, "surname": sur or first, "commonname": common,
                  "playerjerseyname": sur or first},
        "name_strategy": "custom",
        "detach_name_dictionary": True,
    }
    if native:
        # Messi / Mbappe have real FC dictionary ids (DICT above).
        ids = {"Messi": (1, 2), "Mbappe": (3, 4)}[sur]
        player["fields"].update({"firstnameid": ids[0], "lastnameid": ids[1],
                                 "commonnameid": 0, "playerjerseynameid": ids[1]})
        player["name_strategy"] = "native_ids"
        player.pop("detach_name_dictionary")
    return {
        "dummy_pool": [pid],
        "dummy_filter": {"max_overall": 75, "expected_overallrating": 55},
        "player": player,
        "contract": {"transfersum": 0, "wage": 5000, "months": 60},
    }


def _sign_op(members: list[dict]) -> dict:
    return {"op": "add_to_team", "id": "add", "v": 4, "teamid": TEAM,
            "strategy": "dummy_overwrite", "batch": members,
            "verify": {"name": True, "team": True, "fields": True}}


def _setup_club_and_dummies(game: Game, n: int = 11) -> list[int]:
    game.run(f"for i = 1, 18 do add_player(1000 + i, {TEAM}) end")
    dummies = [76000 + i for i in range(n)]
    for pid in dummies:
        game.run(f"add_player({pid}, {FREE})")
    # LE builds its cache when the career loads and Companion first asks.
    assert game.eval("GetPlayerName(76000)") == "Dummy 76000"
    return dummies


def test_eleven_player_signing_finishes_in_one_team_management_visit(game):
    dummies = _setup_club_and_dummies(game)
    members = []
    for pid, (first, sur) in zip(dummies, CARDS):
        members.append(_member(pid, first, sur, native=sur in ("Messi", "Mbappe")))
    game.submit(ULID_SIGN, _sign_op(members))

    # Screen events and the native tick never start a signing.
    game.eval(f"LEC.runner.on_career({DATA_READY}, 'post')")
    assert game.result(ULID_SIGN) is None

    game.team_management()
    result = game.result(ULID_SIGN)
    assert result is not None, "the signing must finish inside the one visit"
    assert game.hub_events == 1
    assert result["state"] == "done", result.get("error")
    data = result["ops"][0]["data"]
    assert data["playerids"] == dummies
    assert data["name_verified_via"] == "database"
    assert data["le_name_cache_stale"] is True
    assert not (game.queue / "claimed" / f"{ULID_SIGN}.json").exists()

    # Every player is on the club, with the name FC renders from the DB.
    for pid, (first, sur) in zip(dummies, CARDS):
        assert game.eval(f"link_of({pid}).teamid") == TEAM
        expected = "Lionel Messi" if sur == "Messi" else "Kylian Mbappe" if sur == "Mbappe" \
            else f"{first} {sur}".strip()
        assert game.eval(f"fc_name({pid})") == expected
    # ... while LE's cache still shows the dummies, exactly as on 2026-10-17.
    assert game.eval(f"GetPlayerName({dummies[0]})") == f"Dummy {dummies[0]}"
    assert game.eval("LE_CACHE_BUILDS") == 1
    # Every dummy is reserved for this save.
    assert game.consumed() == set(dummies)


def test_signing_also_needs_no_le_cache_refresh_on_the_v3_single_path(game):
    dummies = _setup_club_and_dummies(game, 1)
    member = _member(dummies[0], "Gianluigi", "Donnarumma")
    op = {"op": "add_to_team", "id": "add", "v": 3, "teamid": TEAM,
          "strategy": "dummy_overwrite", "dummy_pool": member["dummy_pool"],
          "dummy_filter": member["dummy_filter"], "player": member["player"],
          "contract": member["contract"], "verify": {"name": True}}
    game.submit(ULID_V3, op)
    game.team_management()
    result = game.result(ULID_V3)
    assert result is not None and result["state"] == "done", result
    data = result["ops"][0]["data"]
    assert data["name_verified_via"] == "database"
    assert data["host_player_name"] == "Gianluigi Donnarumma"
    assert data["le_name_cache_stale"] is True
    assert game.eval(f"fc_name({dummies[0]})") == "Gianluigi Donnarumma"


def test_a_dummy_signed_this_session_is_never_picked_again(game):
    dummies = _setup_club_and_dummies(game, 2)
    first = _member(dummies[0], "Sergio", "Ramos")
    game.submit(ULID_SIGN, _sign_op([first]))
    game.team_management()
    assert game.result(ULID_SIGN)["state"] == "done"
    # LE's cache still calls the signed dummy a free agent ...
    assert game.eval(f"GetTeamIdFromPlayerId({dummies[0]})") == FREE
    # ... and even with the consumed-slot record gone, the club's contracts
    # (a DB read) keep it from being overwritten by the next signing.
    (game.queue / "state" / "consumed_free_agent_dummies_SAVE.json").unlink()
    second = _member(dummies[0], "Marco", "Verratti")
    second["dummy_pool"] = [dummies[0], dummies[1]]
    game.submit(ULID_SIGN2, _sign_op([second]))
    game.g.CLOCK = 110.0
    game.team_management()
    result = game.result(ULID_SIGN2)
    assert result["state"] == "done", result
    assert result["ops"][0]["data"]["playerids"] == [dummies[1]]
    assert game.eval(f"fc_name({dummies[0]})") == "Sergio Ramos"


def test_le_cache_rebuild_after_a_day_advance_then_agrees(game):
    dummies = _setup_club_and_dummies(game, 1)
    game.submit(ULID_SIGN, _sign_op([_member(dummies[0], "Neymar", "Jr")]))
    game.team_management()
    assert game.result(ULID_SIGN)["state"] == "done"
    assert game.eval(f"GetPlayerName({dummies[0]})") == f"Dummy {dummies[0]}"
    game.run("le_cache_clear()")  # what LE does on DAY_PASSED
    assert game.eval(f"GetPlayerName({dummies[0]})") == "Neymar Jr"
    assert game.eval(f"GetTeamIdFromPlayerId({dummies[0]})") == TEAM
