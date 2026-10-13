from dataclasses import replace

import pytest

from companion.domain.squad_plan import plan_from_rows
from companion.domain.squad_session import build_session, parse_recipe, roster_from_rows
from companion.domain.team_plan import TeamContext, apply_team_context, context_from_rows, coverage, team_summary


def rows():
    return [{"playerid": i, "name": f"Player {i}", "position": pos,
             "overallrating": 75 + i, "potential": 90, "acceleration": 60, "sprintspeed": 70,
             "shortpassing": 70, "longpassing": 65, "vision": 60,
             "defensiveawareness": 50, "standingtackle": 55, "interceptions": 60}
            for i, pos in enumerate(("GK", "CB", "CM", "ST"), 1)]


def test_context_never_guesses_starters_from_highest_rating_or_order():
    data = rows()
    assert context_from_rows(data).starters == ()
    data[1]["is_starter"] = True
    data[2]["planning_role"] = "prospect"
    context = context_from_rows(data)
    assert context.starters == (2,) and context.roles == {3: "prospect"}
    assert replace(context, roles={2: "rotation"}).starters == ()


def test_role_and_band_policies_preserve_pinned_overall_and_no_downgrade():
    session = build_session(roster_from_rows(rows()), parse_recipe("improve team overall between 80 and 90"))
    context = TeamContext(roles={2: "starter", 3: "prospect"}, overall_min=84, overall_max=92)
    revised = apply_team_context(session, context)
    assert revised.target_for(2).rung == "star"
    assert revised.target_for(3).rung == "prospect"
    assert revised.target_for(3).target_ovr == 84
    pinned = replace(session.targets[1], pinned=True, target_ovr=96)
    revised = apply_team_context(replace(session, targets=(pinned,)), context)
    assert revised.targets[0].target_ovr == 96
    strong = replace(pinned, pinned=False, ovr=98)
    assert apply_team_context(replace(session, targets=(strong,)), context).targets[0].target_ovr == 98


def test_coverage_never_counts_one_flexible_player_twice():
    plan = plan_from_rows([{"playerid": 1, "position": "CB", "preferredposition2": 14}])
    filled, missing = coverage(plan.members, "4-3-3")
    assert filled == 1 and len(missing) == 10


def test_summary_reports_planned_delta_and_unknown_lineup_without_fabrication():
    plan = plan_from_rows(rows())
    staged = plan.with_overrides({2: {"acceleration": 90, "sprintspeed": 90, "overallrating": 92}})
    lines = team_summary(plan, staged, TeamContext())
    assert any("Starting XI unknown" in line for line in lines)
    assert any("Pace: 65.0 → 73.3" in line for line in lines)
    assert any("Lowest measured area: defence" in line for line in lines)
    sparse = plan_from_rows([{"playerid": 1, "position": "ST"}])
    assert "Pace: detailed values needed" in team_summary(sparse, sparse, TeamContext())


@pytest.mark.parametrize("kwargs", [{"starters": tuple(range(1, 13))}, {"formation": "1-9-1"},
                                    {"roles": {1: "best"}}, {"overall_min": 90},
                                    {"overall_min": 92, "overall_max": 80}])
def test_invalid_team_choices_fail_before_planning(kwargs):
    with pytest.raises(ValueError):
        TeamContext(**kwargs)
