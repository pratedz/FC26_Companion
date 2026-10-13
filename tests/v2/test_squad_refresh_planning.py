"""Detailed reads preserve identity; normal exports are sufficient for Build."""
from pathlib import Path

import pytest

from companion.app.commands.squad_session import rows_from_snapshot_result
from companion.domain.outcome import JobResult
from companion.domain.squad_plan import plan_from_rows
from companion.domain.squad_session import needs_live_base, parse_recipe


def test_database_snapshot_does_not_replace_exported_display_name():
    plan = plan_from_rows([dict(playerid=81834, name="Pelé", position="CAM",
                                overallrating=98, preferredposition1=18)])
    result = JobResult.from_wire(dict(job_id="read", state="done", ok=True, ops=[
        dict(id="squad.session.read", op="snapshot", ok=True,
             data=dict(rows=[dict(playerid=81834, overallrating=98, skillmoves=3)]))]))
    rows = rows_from_snapshot_result(result, [81834])
    assert "name" not in rows[81834]
    filled = plan.with_member_bases(rows)
    assert filled.members[0].base["name"] == "Pelé"
    assert filled.members[0].base["skillmoves"] == 3
    # Older readers normalized an absent name to an empty string.
    assert plan.with_member_bases({81834: {"name": "", "skillmoves": 3}}).members[0].base["name"] == "Pelé"


def test_normal_club_export_supplies_planning_values_in_one_scan(monkeypatch, tmp_path):
    lua = pytest.importorskip("lupa").LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        ops = { handlers = {} }
        scans, nonmember_reads = 0, 0
        function LEC_CAREER_SAVE_READY() return true end
        function GetUserSeniorTeamPlayerIDs()
            local ids = {}; for i=1,20 do ids[i]=i end; return ids
        end
        function GetPlayerName(pid) return "Player " .. pid end
        function GetPlayerPrimaryPositionName(pos) return pos == 0 and "GK" or "CM" end
        h = {}
        function h:records()
            scans = scans + 1; local index=0
            return function() index=index+1; if index<=21 then return index end end
        end
        function h:get(field, rec)
            if field == "playerid" then return rec end
            if rec == 21 then nonmember_reads=nonmember_reads+1 end
            if field == "preferredposition1" then return rec == 20 and 0 or 14 end
            if field == "overallrating" then return 80 end
            if field == "potential" then return 85 end
            if field == "skillmoves" then return 3 end
            if field:find("trait") then return 0 end
            return 75
        end
        db = { field=function() return true end,
               open=function(name) if name == "players" then return h end end }
        util = { now=function() return 123 end }
        function ok_result(data, counts) return { ok=true, data=data, counts=counts } end
        function fail_result(reason) return { ok=false, reason=reason } end
    ''')
    source = (Path(__file__).resolve().parents[2] / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    handler = 'ops.handlers["export_squad"]' + source.split('ops.handlers["export_squad"]', 1)[1].split("local function host_function", 1)[0]
    lua.execute(handler)
    result = lua.globals().ops.handlers.export_squad(lua.table_from(dict(teamid=45)), lua.table(), lua.table())
    assert result.ok and result.counts.found == 20
    rows = [dict(result.data.players[index]) for index in range(1, 21)]
    plan = plan_from_rows(rows)
    recipe = parse_recipe("make all players 5 star skill moves, Quick Step+ and improve PlayStyles+ no ceiling")
    assert all(not needs_live_base(member, recipe) for member in plan.members)
    assert lua.globals().scans == 1
    assert lua.globals().nonmember_reads == 0
    from companion.ui.surfaces import planner
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.core.clock import FakeClock
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport

    class Widget:
        def configure(self, **kwargs):
            pass

    svc = Services(paths=TempAppPaths(tmp_path), store=Store(), clock=FakeClock(),
                   executor=InlineExecutor(), transport=FakeTransport())
    submitted = []
    monkeypatch.setattr("companion.integrations.ai_provider.block_reason", lambda: "")
    monkeypatch.setattr("companion.app.commands.squad_session.snapshot_players",
                        lambda *a, **kw: pytest.fail("Build queued an unnecessary second read"))
    monkeypatch.setattr(planner, "_submit", lambda *a, **kw: submitted.append(a[1]))
    state = dict(plan=plan, prompt=recipe.prompt, include_vars={}, pin_vars={})
    planner._start_session_propose(svc, state, None, None, Widget(), Widget(), Widget())
    assert submitted == ["squad.grok.session"]
    assert state["phase"] == "proposing"
