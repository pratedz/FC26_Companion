"""Use installed LE Lua classes with simulated native memory, never the game."""
from pathlib import Path

import pytest

from companion.platform import registry


@pytest.fixture
def lua():
    lupa = pytest.importorskip("lupa")
    install = registry.le_install_root()
    if install is None or not (install / "lua/libs/v2/imports/t3db/table.lua").is_file():
        pytest.skip("No installed Live Editor Lua API to exercise")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    companion = Path(__file__).resolve().parents[2] / "ingame/le_companion"
    runtime.globals().api_root = str(install / "lua/libs/v2").replace("\\", "/")
    runtime.globals().companion_root = str(companion).replace("\\", "/")
    runtime.execute('''
        package.path = companion_root .. "/?.lua;" .. api_root .. "/?.lua;" .. package.path
        package.preload["imports/core/logger"] = function()
            return {LogInfo=function() end, LogDebug=function() end, LogError=function() end}
        end
        words = {[4096]=701, [4104]=90, [4112]=702, [4120]=50, [4128]=703, [4136]=88}
        ReadQword = function(address) return words[address] or 0 end
        WriteQword = function(address, value) words[address]=value; return true end
        ReadBytes = function(address, count) return {address == 4127 and 128 or 0} end
        local TABLE = require("imports/t3db/table")
        local FIELD = require("imports/t3db/field")
        native_table = TABLE:new()
        native_table.name = "players"
        native_table.first_record = 4096
        native_table.record_size = 16
        native_table.written_records = 3
        native_table.last_record_idx = 2
        local function field(offset, depth)
            local value = FIELD:new()
            value.type=3; value.offset=offset; value.startbit=0
            value.fld_desc={depth=depth, min=0}
            return value
        end
        native_table.fields={playerid=field(0, 32), overallrating=field(8, 7)}
        LE={db={GetTable=function(_, name) if name=="players" then return native_table end end}}
        GetDBMeta=function() return {
            shortname_name_tables_map={PLAY="players"},
            field_desc_map={PLAY={{name="playerid", depth=32, min=0},
                                 {name="overallrating", depth=7, min=0}}}
        } end
        adapter = require("db")
    ''')
    return runtime


def test_companion_reads_valid_records_using_installed_le_table_api(lua):
    lua.execute('''
        local h=assert(adapter.open("players"))
        local ids={}
        for rec in h:records() do ids[#ids+1]=h:get("playerid", rec) end
        assert(#ids==2 and ids[1]==701 and ids[2]==703)
        local _, rec=adapter.resolve("players", "playerid", 703)
        assert(rec==4128)
    ''')


def test_companion_verifies_field_writes_using_installed_le_field_api(lua):
    lua.execute('''
        local h=assert(adapter.open("players"))
        local ok, back=h:set("overallrating", 4096, 95)
        assert(ok and back==95 and words[4104]==95)
        local rejected, _, reason=h:set("overallrating", 4096, 128)
        assert(not rejected and reason=="out_of_range" and words[4104]==95)
    ''')


def test_companion_resolves_career_events_from_installed_le_enums(lua):
    lua.execute('''
        local events=require("events")
        assert(events.resolved_from=="enums")
        assert(events.is_hub(ENUM_CM_EVENT_MSG_ENTERED_HUB_FIRST_TIME))
        assert(events.is_hub(ENUM_CM_EVENT_MSG_ENTERING_TEAM_MANAGEMENT))
        assert(events.is_blackout(ENUM_CM_EVENT_MSG_PREPARE_FOR_SAVE))
        assert(events.cache_action(ENUM_CM_EVENT_MSG_PLAYER_ADDED_TO_TEAM)=="invalidate_squads")
    ''')
