"""Unlimited improvement must be substantial, additive and role appropriate."""
import json

import pytest

from companion.domain.playstyles import (
    GOALKEEPER, OUTFIELD, complete_additive_styles, encode_names,
    no_ceiling_style_floor, plus_names, validate_ai_styles,
)
from companion.domain.squad_session import parse_recipe
from companion.integrations import grok


PROMPT = "improve these players by adding playstyle and playstyle+ no limit ceiling"


@pytest.mark.parametrize("position,ovr,expected", [
    ("CB", 79, (8, 6)), ("CAM", 88, (12, 8)), ("ST", 95, (14, 10)),
    ("GK", 79, (4, 3)), (0, 88, (6, 5)), ("GK", 95, (6, 6)),
])
def test_improvement_floors_are_substantial_without_a_ceiling(position, ovr, expected):
    assert no_ceiling_style_floor(PROMPT, unlimited=True, position=position, target_ovr=ovr) == expected
    first = GOALKEEPER[0] if position in ("GK", 0) else "First Touch"
    patch = encode_names([first], plus=True)
    result, added = complete_additive_styles(
        patch, current={}, position=position, target_ovr=ovr, prompt=PROMPT, unlimited=True,
    )
    bank = 2 if position in ("GK", 0) else 1
    assert result[f"trait{bank}"].bit_count() >= expected[0]
    assert result[f"icontrait{bank}"].bit_count() >= expected[1]
    assert added
    validate_ai_styles(result, require_plus=True, position=position)
    assert result[f"icontrait{bank}"] & result[f"trait{bank}"] == result[f"icontrait{bank}"]


def test_preserves_existing_above_floor_ai_choices_and_regular_cpu_bits():
    existing = {**encode_names(list(OUTFIELD[:15])), **encode_names(list(OUTFIELD[:11]), plus=True)}
    existing["trait2"] = 2048 | 8192
    patch = {**encode_names(["Long Throw"]), **encode_names(["Quick Step"], plus=True)}
    result, _ = complete_additive_styles(
        patch, current=existing, position="CM", target_ovr=79, prompt=PROMPT, unlimited=True,
    )
    assert set(plus_names(result)) == set(OUTFIELD[:11]) | {"Quick Step"}
    assert result["trait1"] & patch["trait1"]
    assert result["trait1"] & existing["trait1"] == existing["trait1"]
    assert result["trait2"] == 2048 | 8192
    repeat, added = complete_additive_styles(
        result, current=existing, position="CM", target_ovr=79, prompt=PROMPT, unlimited=True,
    )
    assert repeat == result and not added


@pytest.mark.parametrize("prompt,unlimited", [
    ("playstyles+ no limit ceiling", True), ("improve playstyles+", False),
    ("add 2 playstyles+ no ceiling", True), ("improve playstyles+ count 3 no ceiling", True),
    ("add only Quick Step+ no ceiling", True),
    ("improve overall no ceiling", True),
])
def test_no_ceiling_alone_and_exact_counts_do_not_trigger_density(prompt, unlimited):
    assert no_ceiling_style_floor(prompt, unlimited=unlimited, position="CM", target_ovr=95) == (0, 0)


def test_ceiling_removal_alone_preserves_existing_without_automatic_density():
    current = {**encode_names(["Tiki Taka"]), **encode_names(["Tiki Taka"], plus=True), "trait2": 8192}
    proposed = {**encode_names(["First Touch"]), **encode_names(["First Touch"], plus=True)}
    result, added = complete_additive_styles(
        proposed, current=current, position="CM", target_ovr=95,
        prompt="playstyles+ no ceiling", unlimited=True,
    )
    assert set(plus_names(result)) == {"Tiki Taka", "First Touch"}
    assert result["trait2"] == 8192 and not added


def test_live_attributes_make_same_position_players_different():
    baseline = encode_names(["Relentless"], plus=True)
    passing = {"vision": 99, "shortpassing": 98, "longpassing": 97}
    carrying = {"dribbling": 99, "acceleration": 98, "ballcontrol": 97, "sprintspeed": 96}
    creator, _ = complete_additive_styles(
        baseline, current=passing, position="CM", target_ovr=80, prompt=PROMPT, unlimited=True,
    )
    carrier, _ = complete_additive_styles(
        baseline, current=carrying, position="CM", target_ovr=80, prompt=PROMPT, unlimited=True,
    )
    assert creator["icontrait1"] != carrier["icontrait1"]
    assert "Incisive Pass" in plus_names(creator)
    assert "Quick Step" in plus_names(carrier)


def test_twenty_sparse_proposals_completed_locally_with_no_extra_ai_calls(monkeypatch):
    recipe = parse_recipe(PROMPT)
    rows = [{"playerid": pid, "name": f"Player {pid}", "position": "GK" if pid == 20 else "CM",
             "target_ovr": 90, "playstyle_plus_max": None,
             "current_values": {"vision": 93, "shortpassing": 93, "trait2": 2048}}
            for pid in range(1, 21)]
    calls = []
    def request(messages, model):
        body = json.loads(messages[1]["content"])
        calls.append(body)
        assert "direction, not an order" not in messages[0]["content"]
        items = []
        for row in body["players"]:
            assert row["minimum_playstyles_plus"] == (5 if row["position"] == "GK" else 8)
            name = "GK Footwork" if row["position"] == "GK" else "Incisive Pass"
            items.append({"playerid": row["playerid"], "summary": "Live role",
                          "playstyles": [name], "playstyles_plus": [name], "changes": []})
        return {"choices": [{"message": {"content": json.dumps({
            "selection_revision": "rev", "players": items,
        })}}]}
    monkeypatch.setattr(grok, "_request", request)
    patches, reasons, _ = grok.propose_squad_players(
        players=rows, selection_revision="rev", request=PROMPT, recipe=recipe,
    )
    assert len(calls) == 4  # Existing five-player batches, no density repair calls.
    assert set(patches) == set(range(1, 21))
    for pid, patch in patches.items():
        assert len(plus_names(patch)) >= (5 if pid == 20 else 8)
        assert patch["trait2"] & 2048
        assert "Completed unlimited improvement locally" in reasons[pid]
        assert "playerid" not in patch and "headassetid" not in patch
        validate_ai_styles(patch, require_plus=True, position="GK" if pid == 20 else "CM")


def test_missing_or_invalid_players_are_still_repaired_instead_of_fabricated(monkeypatch):
    recipe = parse_recipe(PROMPT)
    rows = [{"playerid": 1, "position": "CM", "target_ovr": 80},
            {"playerid": 2, "position": 0, "target_ovr": 80}]
    calls = []
    def request(messages, model):
        body = json.loads(messages[1]["content"])
        calls.append(body)
        items = []
        for row in body["players"]:
            if row["playerid"] == 2 and len(calls) == 1:
                continue
            name = "GK Footwork" if row["playerid"] == 2 else "First Touch"
            items.append({"playerid": row["playerid"], "playstyles_plus": [name], "changes": []})
        return {"choices": [{"message": {"content": json.dumps({
            "selection_revision": "rev", "players": items,
        })}}]}
    monkeypatch.setattr(grok, "_request", request)
    patches, _, _ = grok._propose_squad_chunk(
        rows, selection_revision="rev", request=PROMPT, recipe=recipe, model="test",
    )
    assert set(patches) == {1, 2}
    assert [r["playerid"] for r in calls[1]["players"]] == [2]
    assert not patches[2]["icontrait1"] and patches[2]["icontrait2"].bit_count() == 3
