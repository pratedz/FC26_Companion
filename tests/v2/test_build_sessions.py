from __future__ import annotations

import json

import pytest

from companion.app import events as E
from companion.app.reducers import reduce
from companion.app.state import AppState
from companion.app.commands import builds as builds_cmd
from companion.domain.builds import (
    expand_overall_patch,
    is_ratings_only_patch,
    preset,
    proposal,
    revision,
)
from companion.integrations import grok


def test_draft_staging_is_atomic_and_tracks_provenance() -> None:
    state = reduce(
        AppState(),
        E.EditorLoaded(base={"overallrating": 80, "acceleration": 70}),
    )
    state = reduce(
        state,
        E.DraftStaged(
            fields={"overallrating": 90, "acceleration": 70},
            source="card",
            source_label="Messi · 26 · Special",
            warnings=("Review it.",),
        ),
    )
    assert state.editor.dirty == {"overallrating": 90}
    assert state.editor.source == "card"
    assert state.editor.source_label == "Messi · 26 · Special"
    assert state.editor.warnings == ("Review it.",)

    replaced = reduce(
        state,
        E.DraftStaged(
            fields={"acceleration": 99},
            source="preset",
            source_label="Pace monster",
        ),
    )
    assert replaced.editor.dirty == {"acceleration": 99}
    assert "overallrating" not in replaced.editor.dirty


def test_manual_edit_marks_a_sourced_draft_as_mixed() -> None:
    state = reduce(
        AppState(),
        E.DraftStaged(fields={"acceleration": 99}, source="grok", source_label="Grok"),
    )
    state = reduce(state, E.FieldEdited("finishing", 91))
    assert state.editor.source == "mixed"
    assert state.editor.source_label == "Mixed sources"


def test_presets_use_fc26_raw_skill_move_scale() -> None:
    assert preset("balanced_85").fields["skillmoves"] == 2
    assert preset("max_99").fields["skillmoves"] == 4
    assert preset("max_99").fields["weakfootabilitytypecode"] == 5


def test_grok_proposal_is_target_and_revision_bound(monkeypatch) -> None:
    current = {"overallrating": 82, "acceleration": 84}
    rev = revision(123, current)
    content = {
        "target_playerid": 123,
        "base_revision": rev,
        "summary": "Faster, capped build",
        "changes": [
            {"field": "acceleration", "value": 94, "reason": "faster"},
            {"field": "overallrating", "value": 88, "reason": "cap"},
        ],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{"message": {"content": json.dumps(content)}}]
        },
    )
    result = grok.propose_changes(
        playerid=123,
        player_name="Test",
        current=current,
        request="make him faster, max 88",
    )
    assert result.fields == {"acceleration": 94, "overallrating": 88}
    assert result.source == "grok"


def test_grok_rejects_unknown_fields_instead_of_silently_dropping(monkeypatch) -> None:
    current = {"overallrating": 82}
    content = {
        "target_playerid": 123,
        "base_revision": revision(123, current),
        "changes": [{"field": "teamid", "value": 241, "reason": "wrong scope"}],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{"message": {"content": json.dumps(content)}}]
        },
    )
    with pytest.raises(RuntimeError, match="unsupported fields"):
        grok.propose_changes(
            playerid=123,
            player_name="Test",
            current=current,
            request="move him",
        )


def test_ratings_only_patch_is_detected() -> None:
    assert is_ratings_only_patch({"overallrating": 92, "potential": 92})
    assert is_ratings_only_patch({"overallrating": 92})
    assert not is_ratings_only_patch(
        {
            "overallrating": 92,
            "acceleration": 90,
            "sprintspeed": 88,
            "finishing": 86,
            "positioning": 87,
            "shotpower": 80,
            "longshots": 84,
            "volleys": 82,
            "penalties": 81,
            "vision": 85,
        }
    )


def test_expand_overall_patch_fills_neymar_shaped_attributes() -> None:
    """OVR-only AI replies must not leave Career Mode with old in-stats."""
    current = {
        "overallrating": 89,
        "preferredposition1": 27,  # LW-ish; not GK
        "acceleration": 91,
        "sprintspeed": 90,
        "finishing": 83,
        "positioning": 86,
        "shotpower": 80,
        "longshots": 81,
        "volleys": 84,
        "penalties": 85,
        "vision": 86,
        "crossing": 84,
        "freekickaccuracy": 87,
        "shortpassing": 85,
        "longpassing": 78,
        "curve": 88,
        "agility": 93,
        "balance": 86,
        "reactions": 88,
        "ballcontrol": 92,
        "dribbling": 94,
        "composure": 86,
        "interceptions": 40,
        "headingaccuracy": 55,
        "defensiveawareness": 35,
        "standingtackle": 32,
        "slidingtackle": 30,
        "jumping": 62,
        "stamina": 80,
        "strength": 55,
        "aggression": 58,
        "potential": 89,
    }
    expanded = expand_overall_patch(current, {"overallrating": 92})
    assert expanded["overallrating"] == 92
    assert expanded["potential"] >= 92
    assert expanded["modifier"] == 0
    # Full outfield suite present.
    assert expanded["dribbling"] >= current["dribbling"]
    assert expanded["acceleration"] >= current["acceleration"]
    assert expanded["standingtackle"] >= current["standingtackle"]
    assert expanded["standingtackle"] < expanded["dribbling"]  # profile kept
    assert len(expanded) >= 25


def test_grok_overall_only_reply_is_expanded_before_staging(monkeypatch) -> None:
    current = {
        "overallrating": 89,
        "acceleration": 90,
        "sprintspeed": 89,
        "finishing": 82,
        "positioning": 85,
        "shotpower": 79,
        "longshots": 80,
        "volleys": 83,
        "penalties": 84,
        "vision": 85,
        "crossing": 83,
        "freekickaccuracy": 86,
        "shortpassing": 84,
        "longpassing": 77,
        "curve": 87,
        "agility": 92,
        "balance": 85,
        "reactions": 87,
        "ballcontrol": 91,
        "dribbling": 93,
        "composure": 85,
        "interceptions": 38,
        "headingaccuracy": 54,
        "defensiveawareness": 34,
        "standingtackle": 31,
        "slidingtackle": 29,
        "jumping": 61,
        "stamina": 79,
        "strength": 54,
        "aggression": 57,
        "potential": 89,
    }
    rev = revision(190871, current)
    content = {
        "target_playerid": 190871,
        "base_revision": rev,
        "summary": "Neymar overall 92",
        "changes": [
            {"field": "overallrating", "value": 92, "reason": "requested"},
            {"field": "potential", "value": 92, "reason": "match ovr"},
        ],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{"message": {"content": json.dumps(content)}}]
        },
    )
    result = grok.propose_changes(
        playerid=190871,
        player_name="Neymar",
        current=current,
        request="create neymar overall 92 stat",
    )
    assert result.fields["overallrating"] == 92
    assert result.fields["dribbling"] >= current["dribbling"]
    assert result.fields["acceleration"] >= current["acceleration"]
    assert len(result.fields) >= 25
    assert any("expanded" in w.lower() for w in result.warnings)


def test_stage_proposal_expands_thin_overall() -> None:
    from companion.app.state import TargetState

    base = {
        "overallrating": 88,
        "acceleration": 85,
        "sprintspeed": 84,
        "finishing": 80,
        "positioning": 81,
        "shotpower": 78,
        "longshots": 77,
        "volleys": 76,
        "penalties": 75,
        "vision": 82,
        "crossing": 80,
        "freekickaccuracy": 79,
        "shortpassing": 83,
        "longpassing": 74,
        "curve": 81,
        "agility": 86,
        "balance": 82,
        "reactions": 84,
        "ballcontrol": 87,
        "dribbling": 88,
        "composure": 83,
        "interceptions": 40,
        "headingaccuracy": 50,
        "defensiveawareness": 36,
        "standingtackle": 33,
        "slidingtackle": 31,
        "jumping": 60,
        "stamina": 78,
        "strength": 55,
        "aggression": 52,
    }
    state = reduce(AppState(), E.EditorLoaded(base=base))
    state = state.with_(
        target=TargetState(playerid=190871, name="Neymar", source="squad")
    )

    class _Store:
        def __init__(self) -> None:
            self.events: list = []
            self.state = state

        def snapshot(self):
            return self.state

        def dispatch(self, event):
            self.events.append(event)
            self.state = reduce(self.state, event)

    store = _Store()
    svc = type("Svc", (), {"store": store})()
    count = builds_cmd.stage_proposal(
        svc,
        proposal(
            {"overallrating": 92},
            source="grok",
            label="Grok",
            expand_thin_overall=False,  # force the stage-time safety net
        ),
    )
    assert count >= 25
    staged = store.events[-1]
    assert staged.fields["overallrating"] == 92
    assert "dribbling" in staged.fields
