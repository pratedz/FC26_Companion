"""Explicit squad instructions survive incomplete and inconsistent AI output."""

from dataclasses import replace

import pytest

from companion.domain.builds import outfield_attribute_names
from companion.domain.job import JobValidationError
from companion.domain.playstyles import encode_names, plus_names
from companion.domain.player import FIELD_SPECS
from companion.domain.squad_plan import MAX_OVERRIDE_FIELDS, build_apply_job, plan_from_rows
from companion.domain.squad_session import (
    build_session,
    parse_recipe,
    roster_from_rows,
    sanitize_patch,
    stage_per_player_plan,
)


def _row(pid: int, position: str = "CM") -> dict:
    values = {name: 75 for name in outfield_attribute_names()}
    values.update(playerid=pid, overallrating=80, potential=85, skillmoves=1,
                  trait1=0, trait2=0, icontrait1=0, icontrait2=0)
    return {"id": pid, "name": f"Player {pid}", "pos": position,
            "ovr": 80, "pot": 85, "_raw": values}


def _stage(rows: list[dict], prompt: str, patches: dict) -> object:
    recipe = parse_recipe(prompt)
    session = build_session(roster_from_rows(rows), recipe)
    return stage_per_player_plan(plan_from_rows(rows), session, patches)


@pytest.mark.parametrize("phrase", [
    "make all player 5 skill star move",
    "make all players 5 star skill moves",
    "make all players 5-star skill moves",
    "make all players 5* skill moves",
    "set skill moves to 5 stars",
    "give everyone five star skill moves",
])
def test_display_star_phrasings_are_explicit(phrase: str) -> None:
    recipe = parse_recipe(phrase)
    assert recipe.forced_skill_stars == 5
    staged = _stage([_row(1)], phrase, {1: {"skillmoves": 1}})
    assert staged.overrides[1]["skillmoves"] == 4


@pytest.mark.parametrize("phrase", [
    "make all player have quick step+ playstyles+",
    "give everyone Quick Step+",
    "give all players quickstep+",
    "give everyone Quick-Step as play style plus",
])
def test_named_plus_phrasings_are_explicit(phrase: str) -> None:
    recipe = parse_recipe(phrase)
    assert recipe.forced_playstyle_plus == ("Quick Step",)
    assert recipe.wants_playstyle_plus
    staged = _stage([_row(1)], phrase, {})
    assert "Quick Step" in plus_names(staged.overrides[1])
    assert not staged.blocking_issues


def test_twenty_selected_players_get_exact_requirements_when_ai_omits_most() -> None:
    rows = [_row(pid, "GK" if pid == 20 else "CM") for pid in range(1, 21)]
    staged = _stage(rows,
                    "1. make all player 5 skill star move. "
                    "2. make all player have quick step+ playstyles+",
                    {1: {"skillmoves": 2, **encode_names(["Anticipate"], plus=True)}})
    assert len(staged.overrides) == 20
    assert all(patch["skillmoves"] == 4 for patch in staged.overrides.values())
    assert all("Quick Step" in plus_names(staged.overrides[pid]) for pid in range(1, 20))
    assert "Anticipate" in plus_names(staged.overrides[1])
    assert "Quick Step" not in plus_names(staged.overrides[20])
    assert any("Player 20" in note and "Quick Step+ excluded for goalkeeper" in note
               for note in staged.warnings)
    assert not staged.blocking_issues


def test_required_plus_is_added_without_erasing_live_or_ai_plus() -> None:
    row = _row(1)
    row["_raw"].update(encode_names(["Rapid"], plus=True))
    staged = _stage([row], "give all players Quick Step+",
                    {1: encode_names(["Anticipate"], plus=True)})
    assert set(plus_names(staged.overrides[1])) == {"Rapid", "Anticipate", "Quick Step"}


def test_goalkeeper_cannot_receive_requested_outfield_plus_from_ai() -> None:
    staged = _stage([_row(1, "GK")], "give all players Quick Step+",
                    {1: encode_names(["Quick Step"], plus=True)})
    assert "Quick Step" not in plus_names(staged.overrides[1])
    assert any("excluded for goalkeeper" in note for note in staged.warnings)
    assert not staged.blocking_issues


def test_goalkeeper_named_plus_is_enforced_on_keepers_and_excluded_for_outfield() -> None:
    staged = _stage([_row(1, "GK"), _row(2)], "give all players GK Footwork+", {})
    assert "GK Footwork" in plus_names(staged.overrides[1])
    assert "GK Footwork" not in plus_names(staged.overrides.get(2, {}))
    assert any("excluded for outfield" in note for note in staged.warnings)
    assert not staged.blocking_issues


@pytest.mark.parametrize("patch", [{}, encode_names(["Quick Step"], plus=True)])
def test_general_goalkeeper_plus_request_blocks_missing_or_outfield_plus(patch: dict) -> None:
    staged = _stage([_row(1, "GK")], "give everyone playstyles+", {1: patch})
    assert staged.blocking_issues
    assert "Goalkeeper needs goalkeeper PlayStyle+" in staged.blocking_issues[0]


def test_required_star_field_survives_large_patch_truncation() -> None:
    row = _row(1)
    recipe = replace(parse_recipe("make all players 5 star skill moves"),
                     families=("attrs", "skills", "playstyles", "identity", "body"))
    target = build_session(roster_from_rows([row]), recipe).targets[0]
    patch = {key: spec.minimum if spec.minimum is not None else 0 for key, spec in FIELD_SPECS.items()}
    patch.update(overallrating=80, potential=85, skillmoves=0)
    cleaned = sanitize_patch(patch, recipe=recipe, target=target, current=row["_raw"])
    assert len(cleaned) <= MAX_OVERRIDE_FIELDS
    assert cleaned["skillmoves"] == 4


def test_rejected_player_cannot_leave_partial_exact_skill_plan_applyable() -> None:
    staged = _stage([_row(1), _row(2)], "make all players 5 star skill moves",
                    {1: {}, 2: {"not_a_player_field": 12}})
    assert 1 in staged.overrides
    assert 2 not in staged.overrides
    assert any("Player 2" in note for note in staged.blocking_issues)


def test_summary_chips_describe_mandatory_values() -> None:
    recipe = parse_recipe("make all player 5 skill star move and Quick Step+")
    session = build_session(roster_from_rows([_row(1)]), recipe)
    assert "All players: 5★ skill moves" in session.chips
    assert "Required where applicable: Quick Step+" in session.chips


def test_full_reported_twenty_player_request_builds_complete_apply_job() -> None:
    rows = [_row(pid, "GK" if pid == 20 else "CM") for pid in range(1, 21)]
    patches = {pid: {"skillmoves": 1, **encode_names(
        ["GK Footwork"] if pid == 20 else ["Anticipate"], plus=True,
    )} for pid in range(1, 21)}
    staged = _stage(rows,
                    "1. make all player 5 skill star move. "
                    "2. make all player have quick step+ playstyles+. "
                    "3. arrange these player by jersey no as you recomended. "
                    "4. improve thes player by adding playstyle and playstyle+ no limit ceiling",
                    patches)
    assert len(staged.overrides) == 20
    assert not staged.blocking_issues
    for pid in range(1, 20):
        patch = staged.overrides[pid]
        assert set(patch) <= {"skillmoves", "trait1", "trait2", "icontrait1", "icontrait2"}
        assert patch["skillmoves"] == 4
        assert "Quick Step" in plus_names(patch)
        assert len(plus_names(patch)) >= 6
    assert staged.overrides[20]["skillmoves"] == 4
    assert len(plus_names(staged.overrides[20])) >= 3
    assert "Quick Step" not in plus_names(staged.overrides[20])
    job = build_apply_job(staged)
    player_ops = [op for op in job.ops if op.body.get("table") == "players"]
    assert len(player_ops) == 20
    assert all(op.body["fields"]["skillmoves"] == 4 for op in player_ops)


def test_general_goalkeeper_plus_request_blocks_cpu_bits() -> None:
    patch = {"icontrait2": 1 | (1 << 12)}
    staged = _stage([_row(1, "GK")], "give everyone playstyles+", {1: patch})
    assert staged.blocking_issues
    assert "CPU/Career traits are not PlayStyle+" in staged.blocking_issues[0]


def test_missing_general_ai_build_stays_blocked_while_exact_fields_are_staged() -> None:
    staged = _stage([_row(1), _row(2, "GK")],
                    "make all player 5 skill star move and Quick Step+. "
                    "improve these players by adding playstyle+ no limit ceiling", {})
    assert all(patch["skillmoves"] == 4 for patch in staged.overrides.values())
    assert plus_names(staged.overrides[1]) == ("Quick Step",)
    assert not plus_names(staged.overrides[2])
    assert any("Player 1" in issue for issue in staged.blocking_issues)
    assert any("Player 2" in issue for issue in staged.blocking_issues)
    with pytest.raises(JobValidationError, match="Player 1"):
        build_apply_job(staged)
