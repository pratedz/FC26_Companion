from pathlib import Path

import pytest


def runtime():
    lua = pytest.importorskip("lupa").LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        ops = {handlers={}}
        records = {{playerid=1, teamid=99, jerseynumber=9}, {playerid=1, teamid=11, jerseynumber=10},
                   {playerid=2, teamid=11, jerseynumber=7}}
        h = {}
        function h:records() local i=0; return function() i=i+1; return records[i] end end
        function h:get(field, rec) return rec[field] end
        db = {available=function() return true end, open=function() return h end}
        function db.scan_for(handle, field, value)
            for rec in handle:records() do if rec[field]==value then return rec end end
        end
        function db.resolve(_,field,value) return h, db.scan_for(h,field,value), "scan" end
        util = {now=function() return 1 end, write_atomic=function() return true end}
        version = {RESULT_SCHEMA=3}
        path_allowed = function() return true end
        GetUserTeamID = function() return 11 end
        package.preload.json = function() return {try_encode=function(payload) captured=payload; return "{}" end} end
        function fail_result(reason,detail) return {ok=false,reason=reason,detail=detail} end
        function ok_result(data,counts) return {ok=true,data=data,counts=counts} end
    ''')
    source = (Path(__file__).resolve().parents[2] / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    helpers = 'local function bound_team_ready' + source.split('local function bound_team_ready', 1)[1].split('-- diag.ping', 1)[0]
    lua.execute(helpers.replace('local function ', 'function '))
    lua.execute('ops.handlers["snapshot"]' + source.split('ops.handlers["snapshot"]', 1)[1].split('ops.handlers["growth_sync"]', 1)[0])
    lua.execute('function ops.capture_envelope_snapshot' + source.split('function ops.capture_envelope_snapshot', 1)[1].split('-- dispatch', 1)[0])
    return lua


def load_write_handler(lua):
    lua.execute('''
        util.is_empty=function(t) return next(t)==nil end
        util.count=function(t) local n=0; for _ in pairs(t) do n=n+1 end; return n end
        function db.set_many(handle,rec,fields,opts)
            for field,value in pairs(fields) do rec[field]=value end
            return util.count(fields), {}
        end
        mirror_growth=function() return 0,0,0 end
        growth_field_count=function() return 0 end
    ''')
    source = (Path(__file__).resolve().parents[2] / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    lua.execute('ops.handlers["set_fields"]' + source.split('ops.handlers["set_fields"]', 1)[1].split('ops.handlers["export_squad"]', 1)[0])


def test_player_write_revalidates_club_membership_in_game_immediately():
    lua = runtime()
    load_write_handler(lua)
    result = lua.execute('return ops.handlers.set_fields({table="players",teamid=22,key={value=1},fields={overallrating=90},growth_mirror="off"}, {}, {})')
    assert not result.ok and result.reason == "club_changed"
    assert lua.globals().records[1].overallrating is None
    valid = lua.execute('return ops.handlers.set_fields({table="players",teamid=11,key={value=1},fields={overallrating=90},growth_mirror="off"}, {}, {})')
    assert valid.ok and valid.counts.fields_written == 1


def test_reviewed_shirt_write_never_displaces_an_unreviewed_occupant():
    lua = runtime()
    load_write_handler(lua)
    result = lua.execute('return ops.handlers.set_fields({table="teamplayerlinks",teamid=11,key={value=1},fields={jerseynumber=7},displace=false}, {}, {})')
    assert not result.ok and result.reason == "shirt_taken"
    assert lua.globals().records[2].jerseynumber == 10 and lua.globals().records[3].jerseynumber == 7
    absent = lua.execute('return ops.handlers.set_fields({table="teamplayerlinks",teamid=22,key={value=1},fields={jerseynumber=5},displace=false}, {}, {})')
    assert not absent.ok and absent.reason == "club_changed"
    assert lua.globals().records[1].jerseynumber == 9


def test_readback_selects_correct_club_link_even_when_national_link_comes_first():
    lua = runtime()
    result = lua.execute('return ops.handlers.snapshot({table="teamplayerlinks",teamid=11,playerids={1},fields={"jerseynumber"}}, {}, {})')
    assert result.ok and result.data.rows[1].jerseynumber == 10 and result.data.rows[1].teamid == 11
    missing = lua.execute('return ops.handlers.snapshot({table="teamplayerlinks",teamid=22,playerids={1},fields={"jerseynumber"}}, {}, {})')
    assert not missing.ok and missing.reason == "club_changed"


def test_prewrite_snapshot_preserves_exact_club_key_and_original_shirt():
    lua = runtime()
    ok, _ = lua.execute('return ops.capture_envelope_snapshot({snapshot_to="test",ops={{id="shirt",op="set_fields",table="teamplayerlinks",teamid=11,key={field="playerid",value=1},fields={jerseynumber=7}}}}, {save_uid="A"})')
    assert ok
    row = lua.globals().captured.rows[1]
    assert row.teamid == 11 and row.fields.jerseynumber == 10
    ok, error = lua.execute('return ops.capture_envelope_snapshot({snapshot_to="test",ops={{op="set_fields",table="teamplayerlinks",key={field="playerid",value=1},fields={jerseynumber=7}}}}, {})')
    assert not ok and error.reason == "snapshot_team_required"
