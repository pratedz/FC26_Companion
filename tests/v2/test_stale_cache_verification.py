"""Core 2.6.42: read-backs come from the database, not Live Editor's cache.

GetTeamIdFromPlayerId and GetPlayerName answer from LE's player cache, which
LE builds on first use and clears only on DAY_PASSED / POST_LOAD_PREPARE
(FCLiveEditor.DLL v26.3.6). Both "Release 29" jobs of 2026-10-17 ended
partial with ``unverified_side_effect`` because the cache read the same club
(1797, then 111592) before and after a release that worked.

The fake host freezes both functions at their first answer while the
database (contracts, teamplayerlinks, players, editedplayernames) changes.
Positive and negative controls: a working release / transfer / repair passes;
one the game refused still fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "ingame" / "le_companion"
TEAM = 11
FREE = 111592
OTHER = 77


def _runtime():
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


HOST = r"""
USER_TEAM = 11
CONTRACTS = {}          -- [pid] = teamid
CACHE_TEAM = {}         -- [pid] = what LE's cache says (frozen)
CACHE_NAME = {}         -- [pid] = what LE's cache says (frozen)
REFUSE = false          -- the game ignores the next transfer/release

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
    function t:SetRecordFieldValue(rec, field, value) self.rows[rec][field] = value end
    function t:add(row)
        self.rows[#self.rows + 1] = row
        self.written_records = #self.rows
        return #self.rows
    end
    return t
end
TABLES = {
    players = make_table("players", { "playerid", "overallrating", "potential",
        "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid", "usercaneditname" }),
    teamplayerlinks = make_table("teamplayerlinks", { "playerid", "teamid" }),
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

function link_of(pid)
    for _, row in ipairs(TABLES.teamplayerlinks.rows) do
        if row.playerid == pid then return row end
    end
end
function add_player(pid, teamid, cached_team)
    TABLES.players:add({ playerid = pid, overallrating = 90, potential = 41,
        firstnameid = 800, lastnameid = 801, commonnameid = 0,
        playerjerseynameid = 801, usercaneditname = 0 })
    TABLES.teamplayerlinks:add({ playerid = pid, teamid = teamid })
    if teamid == USER_TEAM then CONTRACTS[pid] = USER_TEAM end
    CACHE_TEAM[pid] = cached_team
    CACHE_NAME[pid] = "Dummy " .. pid
end

GetUserTeamID = function() return USER_TEAM end
GetTeamIdFromPlayerId = function(pid) return CACHE_TEAM[pid] or -1 end
GetPlayerName = function(pid) return CACHE_NAME[pid] or "" end
IsPlayerPresigned = function() return false end
IsPlayerLoanedOut = function() return false end
GetUserSeniorTeamPlayerIDs = function()
    local out = {}
    for pid, tid in pairs(CONTRACTS) do if tid == USER_TEAM then out[pid] = true end end
    return out
end
ReleasePlayerFromTeam = function(pid)
    if REFUSE then return end
    CONTRACTS[pid] = nil
    link_of(pid).teamid = 111592
end
TransferPlayer = function(pid, teamid)
    if REFUSE then return end
    CONTRACTS[pid] = teamid
    link_of(pid).teamid = teamid
end

function transfer_op(op)
    return require("ops").handlers["transfer"](op, {}, {})
end
"""


@pytest.fixture
def lua(tmp_path: Path):
    rt = _runtime()
    rt.globals().core_dir = _posix(CORE)
    rt.execute("package.path = core_dir .. '/?.lua;' .. package.path")
    rt.execute(HOST)
    rt.execute(f"for i = 1, 10 do add_player(5000 + i, {TEAM}, {TEAM}) end")
    return rt


def _call(lua, op: dict) -> dict:
    lua.globals()._op = lua.eval(f"require('json').decode([==[{json.dumps(op)}]==])")
    text = lua.eval("require('json').encode(transfer_op(_op))")
    return json.loads(text)


def _release(lua, pid: int) -> dict:
    return _call(lua, {"op": "transfer", "id": "r", "action": "release", "playerid": pid})


@pytest.mark.parametrize("cached", [FREE, 1797], ids=["cache_free_agent", "cache_old_club"])
def test_release_that_worked_is_verified_despite_a_stale_cache(lua, cached):
    # 2026-10-17: "release returned but team still read back as 111592 / 1797".
    lua.execute(f"add_player(700, {TEAM}, {cached})")
    res = _release(lua, 700)
    assert res["ok"] is True, res
    assert res["data"]["verified"] is True
    assert res["data"]["verify_source"] == "senior_squad"
    assert lua.eval("link_of(700).teamid") == FREE


@pytest.mark.parametrize("cached", [FREE, 1797, TEAM])
def test_release_the_game_refused_still_fails(lua, cached):
    lua.execute(f"add_player(701, {TEAM}, {cached}); REFUSE = true")
    res = _release(lua, 701)
    assert res["ok"] is False
    assert res["reason"] == "verify_mismatch"
    assert "still in squad" in res["detail"]


def test_transfer_that_worked_is_verified_from_the_roster_row(lua):
    lua.execute(f"add_player(702, {TEAM}, {TEAM})")  # cache keeps saying club 11
    res = _call(lua, {"op": "transfer", "id": "t", "action": "transfer",
                      "playerid": 702, "to_teamid": OTHER})
    assert res["ok"] is True, res
    assert res["data"]["verify_source"] == "teamplayerlinks"
    assert res["data"]["teamid"] == OTHER
    assert res["data"]["cached_teamid"] == TEAM


def test_transfer_the_game_refused_still_fails(lua):
    lua.execute(f"add_player(703, {TEAM}, {TEAM}); REFUSE = true")
    res = _call(lua, {"op": "transfer", "id": "t", "action": "transfer",
                      "playerid": 703, "to_teamid": OTHER})
    assert res["ok"] is False
    assert res["reason"] == "verify_mismatch"


# ---------------------------------------------------------------------------
# repair_partial_add: the stored identity, not the cached name, gates it.
# ---------------------------------------------------------------------------


def _repair(lua, op: dict, *, save: str = "SAVE") -> dict:
    g = lua.globals()
    g._op = lua.eval(f"require('json').decode([==[{json.dumps(op)}]==])")
    g._job = lua.eval("{ grants = { allow_add_to_team = true }, requires = { save_uid = 'SAVE' } }")
    g._ctx = lua.eval(f"{{ save_uid = '{save}' }}")
    return json.loads(lua.eval("require('json').encode(require('ops.repair_partial_add').run(_op, _job, _ctx))"))


def _repair_op(pid: int, **extra) -> dict:
    op = {"op": "repair_partial_add", "id": "repair", "v": 2, "playerid": pid, "teamid": TEAM,
          "expected": {"overallrating": 90, "potential": 41}, "potential": 90,
          "names": {"firstname": "Andres", "surname": "Iniesta", "commonname": "Andres Iniesta",
                    "playerjerseyname": "Iniesta"}}
    op.update(extra)
    return op


def test_repair_custom_name_passes_on_the_stored_identity_with_a_stale_cache(lua):
    lua.execute(f"add_player(710, {TEAM}, {FREE})")
    res = _repair(lua, _repair_op(710))
    assert res["ok"] is True, res
    assert res["data"]["name_verified_via"] == "database"
    assert res["data"]["le_cache_current"] is False
    assert res["data"]["le_cache_name"] == "Dummy 710"
    assert lua.eval("TABLES.players.rows[11].potential") == 90
    assert lua.eval("TABLES.players.rows[11].usercaneditname") == 1


def test_repair_native_identity_passes_on_the_stored_ids(lua):
    lua.execute(f"add_player(711, {TEAM}, {FREE})")
    op = _repair_op(711, native_name_ids={"firstnameid": 11, "lastnameid": 12,
                                          "commonnameid": 13, "playerjerseynameid": 12,
                                          "usercaneditname": 0})
    res = _repair(lua, op)
    assert res["ok"] is True, res
    assert lua.eval("TABLES.players.rows[11].commonnameid") == 13
    assert lua.eval("#TABLES.editedplayernames.rows") == 0


def test_repair_whose_name_does_not_store_fails_and_keeps_potential(lua):
    lua.execute(f"add_player(712, {TEAM}, {FREE})")
    # The players row refuses the dictionary detach.
    lua.execute(
        """
        local t = TABLES.players
        local real = t.SetRecordFieldValue
        t.SetRecordFieldValue = function(self, rec, field, value)
            if field == "usercaneditname" then return end
            return real(self, rec, field, value)
        end
        """
    )
    res = _repair(lua, _repair_op(712))
    assert res["ok"] is False
    assert lua.eval("TABLES.players.rows[11].potential") == 41


def test_repair_membership_is_read_from_the_database_first(lua):
    # LE's cache still names another club for a player the club's contracts list.
    lua.execute(f"add_player(713, {TEAM}, 1797)")
    res = _repair(lua, _repair_op(713))
    assert res["ok"] is True, res
    assert res["data"]["membership"] == "GetUserSeniorTeamPlayerIDs"
