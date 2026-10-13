"""A Club roster is not a detailed snapshot; read all selected players once."""
from pathlib import Path

import pytest

from companion.domain.builds import attribute_names
from companion.domain.squad_plan import plan_from_rows
from companion.domain.squad_session import (
    build_session, needs_live_base, parse_recipe, roster_from_members,
)


def roster_row(pid=1):
    return dict(playerid=pid, name=f"Player {pid}", position="CM", teamid=10,
                teamname="Club", jerseynumber=pid, overallrating=80, potential=84,
                preferredposition1=14, nationality=1, height=180, weight=75,
                birthdate=145000)


def detailed_row(pid=1):
    return {**roster_row(pid), **{name: 75 for name in attribute_names()},
            "trait1": 0, "trait2": 0, "icontrait1": 0, "icontrait2": 0,
            "skillmoves": 3}


def test_metadata_cannot_satisfy_attribute_and_playstyle_read():
    recipe = parse_recipe("improve playstyle+ no limit ceiling and 5 star skill moves")
    member = plan_from_rows([roster_row()]).members[0]
    assert len(member.base) > 10
    assert needs_live_base(member, recipe)
    detailed = plan_from_rows([detailed_row()]).members[0]
    assert not needs_live_base(detailed, recipe)
    missing_styles = detailed_row()
    missing_styles.pop("icontrait2")
    assert needs_live_base(plan_from_rows([missing_styles]).members[0], recipe)


def test_relative_edit_reads_requested_attribute_and_shirt_only_skips_stat_read():
    member = plan_from_rows([roster_row()]).members[0]
    assert needs_live_base(member, parse_recipe("+5 stamina under 80"))
    with_stamina = plan_from_rows([{**roster_row(), "stamina": 75}]).members[0]
    assert not needs_live_base(with_stamina, parse_recipe("+5 stamina under 80"))
    assert not needs_live_base(member, parse_recipe("Messi jersey 10"))


class Widget:
    def configure(self, **kwargs):
        self.values = kwargs


def test_incomplete_live_read_blocks_whole_selection_instead_of_silently_shrinking(monkeypatch, tmp_path):
    from companion.ui.surfaces import planner
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.core.clock import FakeClock
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport

    plan = plan_from_rows([roster_row(1), roster_row(2)])
    recipe = parse_recipe("improve playstyle+ no limit ceiling")
    state = dict(plan=plan, session=build_session(roster_from_members(plan.members), recipe),
                 phase="preview", prompt=recipe.prompt, include_vars={}, pin_vars={})
    svc = Services(paths=TempAppPaths(tmp_path), store=Store(), clock=FakeClock(),
                   executor=InlineExecutor(), transport=FakeTransport())
    requested = []

    def snapshot(_svc, ids, *, on_rows, **kwargs):
        requested.extend(ids)
        on_rows({1: detailed_row(1)})

    monkeypatch.setattr("companion.integrations.ai_provider.block_reason", lambda: "")
    monkeypatch.setattr("companion.app.commands.squad_session.snapshot_players", snapshot)
    monkeypatch.setattr(planner, "_submit", lambda *a: pytest.fail("incomplete read started AI proposal"))
    planner._start_session_propose(svc, state, None, None, Widget(), Widget(), Widget())
    assert requested == [1, 2]
    assert state["phase"] == "preview" and not state["busy"]
    assert state["plan"].playerids == state["session"].playerids == (1, 2)


@pytest.mark.parametrize("ids, expected", [([900, 20, 2], [900, 20, 2]),
                                           ([20, 2000, 2], [20, 2]),
                                           ([20], [20])])
def test_lua_batch_snapshot_scans_once_and_preserves_requested_order(ids, expected):
    lua = pytest.importorskip("lupa").LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        ops = { handlers = {} }
        scans, visits = 0, 0
        h = {}
        function h:records()
            scans = scans + 1
            local index = 0
            return function()
                index = index + 1
                if index > 1000 then return nil end
                visits = visits + 1
                return index
            end
        end
        function h:get(field, rec)
            if field == "playerid" then return rec end
            if field == "stamina" then return 75 end
        end
        db = { available = function() return true end, open = function() return h end }
        function db.scan_for(handle, field, value)
            for rec in handle:records() do
                if handle:get(field, rec) == value then return rec end
            end
        end
        util = { now = function() return 123 end }
        function ok_result(data, counts) return { ok = true, data = data, counts = counts } end
        function fail_result(code, detail) return { ok = false, code = code } end
    ''')
    source = (Path(__file__).resolve().parents[2] / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    handler = 'ops.handlers["snapshot"]' + source.split('ops.handlers["snapshot"]', 1)[1].split('ops.handlers["growth_sync"]', 1)[0]
    lua.execute(handler)
    result = lua.globals().ops.handlers.snapshot(
        lua.table_from(dict(playerids=lua.table_from(ids), fields=lua.table_from(["stamina"]))),
        lua.table(), lua.table_from(dict(save_uid="SAVE")),
    )
    assert result.ok
    assert [result.data.rows[i].playerid for i in range(1, len(result.data.rows) + 1)] == expected
    assert all(result.data.rows[i].stamina == 75 for i in range(1, len(result.data.rows) + 1))
    assert result.counts.found == len(expected)
    assert lua.globals().scans == 1
    assert lua.globals().visits <= 1000
