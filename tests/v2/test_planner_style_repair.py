"""Inherited incompatible styles must not poison every valid rebuild."""
import json

import pytest

from companion.domain.playstyles import (
    encode_names, no_ceiling_style_floor, repair_style_compatibility, validate_ai_styles,
)
from companion.domain.squad_plan import plan_from_rows, build_apply_jobs
from companion.domain.squad_session import (
    build_session, roster_from_members, parse_recipe, propose_payload, stage_per_player_plan,
)
from companion.integrations import grok


PROMPT = (
    "1. make all player 5 skill star move. 2.make all player have quick step+ playstyles+ "
    "3. arrange these player by jersey no as you recomended . "
    "4. improve thes player by adding playstyle and playstyle+ no limit ceiling"
)


def test_numbered_list_is_not_an_explicit_style_count():
    assert no_ceiling_style_floor(PROMPT, unlimited=True, position="ST", target_ovr=96) == (14, 10)
    for count in ("3 playstyles+", "playstyles+ count 3.", "playstyles+ = 3", "playstyles+ 3"):
        assert no_ceiling_style_floor("improve " + count, unlimited=True, position="ST", target_ovr=96) == (0, 0)


@pytest.mark.parametrize("unlimited", [False, True])
def test_inherited_conflicts_are_repaired_in_twenty_player_preview_and_jobs(monkeypatch, unlimited):
    rows = [{"playerid": pid, "name": f"Player {pid}", "position": "GK" if pid == 20 else "ST",
             "overallrating": 96, "potential": 96, "jerseynumber": pid, "teamid": 11,
             "skillmoves": 1, "trait1": 0, "trait2": 8192,
             "icontrait1": 0, "icontrait2": 0} for pid in range(1, 21)]
    rows[0].update(playerid=85308, name="Cristiano Ronaldo", icontrait2=16)
    rows[1].update(playerid=80131, name="Virgil van Dijk", position="CB", icontrait1=4096, icontrait2=32)
    rows[-1].update(icontrait1=1 << 21, trait1=1 << 21)
    prompt = PROMPT if unlimited else PROMPT.replace("no limit ceiling", "")
    plan = plan_from_rows(rows, source_save_uid="SAVE", source_teamid=11)
    session = build_session(roster_from_members(plan.members), parse_recipe(prompt))
    calls = []

    def request(messages, model):
        body = json.loads(messages[1]["content"])
        calls.append(body)
        items = [{"playerid": row["playerid"], "playstyles_plus":
                  ["GK Footwork"] if row["position"] == "GK" else ["First Touch"], "changes": []}
                 for row in body["players"]]
        return {"choices": [{"message": {"content": json.dumps({
            "selection_revision": body["selection_revision"], "players": items,
        })}}]}

    monkeypatch.setattr(grok, "_request", request)
    patches, reasons, hints = grok.propose_squad_players(
        players=propose_payload(plan, session), selection_revision=plan.selection_revision,
        request=prompt, recipe=session.recipe,
    )
    assert len(calls) == 4
    assert set(patches) == set(plan.playerids)
    staged = stage_per_player_plan(plan, session, patches, reasons=reasons, library_hints=hints)
    assert not staged.blocking_issues
    shirts = {member.playerid: staged.jersey_numbers.get(member.playerid, member.base["jerseynumber"])
              for member in staged.members}
    assert len(set(shirts.values())) == 20
    assert any("Cristiano Ronaldo: removed incompatible existing styles: GK Far Reach+" in s for s in staged.warnings)
    assert any("Virgil van Dijk: removed incompatible existing styles: GK Deflector+" in s for s in staged.warnings)
    for member in staged.members:
        patch = staged.patch_for(member.playerid)
        assert patch["skillmoves"] == 4
        validate_ai_styles(patch, require_plus=True, position=member.position)
        assert not {"overallrating", "potential", "acceleration", "finishing"}.intersection(patch)
        if member.position == "GK":
            assert patch["icontrait1"] == 0 and patch["trait1"] == 0
        else:
            assert patch["icontrait2"] == 0
            assert patch["icontrait1"] & encode_names(["Quick Step"], plus=True)["icontrait1"]
            if unlimited:
                assert patch["icontrait1"].bit_count() >= 10
                assert patch["trait2"] & 8192
    writes = [op.body["fields"] for job in build_apply_jobs(staged) for op in job.ops
              if op.body["table"] == "players"]
    assert len(writes) == 20
    assert all(set(fields) <= {"skillmoves", "trait1", "trait2", "icontrait1", "icontrait2"} for fields in writes)


def test_compatibility_repair_preserves_regular_career_bits_and_rejects_bad_storage():
    cleaned, removed = repair_style_compatibility({"trait2": 8192 | 16, "icontrait2": 8192 | 16}, "ST")
    assert cleaned == {"trait2": 8192, "icontrait2": 0}
    assert set(removed) == {"GK Far Reach", "GK Far Reach+"}
    with pytest.raises(ValueError):
        repair_style_compatibility({"trait2": 1 << 21}, "ST")


def test_missing_ai_proposal_stays_blocked_with_a_clear_repair_action(monkeypatch):
    plan = plan_from_rows([{"playerid": 85308, "name": "Cristiano Ronaldo", "position": "ST",
                           "overallrating": 96, "skillmoves": 1, "icontrait1": 0, "icontrait2": 16}])
    session = build_session(roster_from_members(plan.members), parse_recipe(PROMPT))
    staged = stage_per_player_plan(plan, session, {})
    assert staged.blocking_issues
    from companion.ui.surfaces import planner
    reasons = []
    monkeypatch.setattr(planner, "set_disabled", lambda button, reason: reasons.append(reason))
    planner._sync_session_apply({"apply_btn": object(), "plan": staged, "phase": "review"})
    assert reasons[-1] == "Repair the affected players to complete this plan."


@pytest.mark.parametrize("dirty,busy", [(False, False), (True, False), (False, True)])
def test_individual_repair_button_targets_selected_player_and_respects_edits(monkeypatch, dirty, busy):
    from unittest.mock import MagicMock
    import customtkinter as ctk
    from companion.ui.surfaces import planner_review as review
    plan = plan_from_rows([{"playerid": 85308, "name": "Cristiano Ronaldo", "position": "ST",
                           "overallrating": 96, "skillmoves": 1, "icontrait1": 0, "icontrait2": 16}])
    session = build_session(roster_from_members(plan.members), parse_recipe(PROMPT))
    staged = stage_per_player_plan(plan, session, {})
    widget = MagicMock()
    widget.winfo_children.return_value = []
    monkeypatch.setattr(ctk, "CTkFrame", lambda *a, **kw: widget)
    monkeypatch.setattr(review, "text_label", lambda *a, **kw: widget)
    monkeypatch.setattr(review, "muted_label", lambda *a, **kw: widget)
    buttons = {}
    def button(host, label, action, **kwargs):
        control = MagicMock()
        buttons[label] = (control, action)
        return control
    monkeypatch.setattr(review, "button", button)
    called = []
    scroll = MagicMock()
    scroll.inner = widget
    state = {"plan": staged, "original_plan": plan, "session": session, "phase": "review",
             "review_detail": scroll, "repair_player": called.append, "edits_dirty": dirty, "busy": busy}
    review.select_player(state, 85308)
    if dirty:
        assert "Repair this player" not in buttons
    else:
        control, action = buttons["Repair this player"]
        if busy:
            control.configure.assert_called_with(state="disabled")
        else:
            action()
            assert called == [85308]


def test_rebuild_retries_blocked_players_and_individual_action_retries_only_selected(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from companion.ui.surfaces import planner
    original = plan_from_rows([{"playerid": pid, "name": f"P{pid}", "position": "ST", "overallrating": 90}
                               for pid in range(1, 4)])
    incomplete = replace(original, blocking_issues=("P1: failed", "P3: failed"))
    invalidated = []
    monkeypatch.setattr(planner, "_snapshot_preview_edits", lambda state: ([], {}))
    monkeypatch.setattr(planner, "set_status", lambda *args: None)
    state = {"plan": incomplete, "original_plan": original, "prompt": "",
             "proposal_cache": SimpleNamespace(invalidate_players=invalidated.append)}
    planner._start_session_propose(None, state, None, None, None, None, None)
    assert invalidated == [{1, 3}]
    state["retry_playerids"] = {3}
    planner._start_session_propose(None, state, None, None, None, None, None)
    assert invalidated[-1] == {3} and "retry_playerids" not in state
