"""Focused safety contracts for the signing review boundary."""

from __future__ import annotations

import pytest

from companion.app.commands import team
from companion.domain.job import JobValidationError


def _preview(dummy_id: int, *, face: bool = True) -> team.TeamAddPreview:
    plan = team.TeamCardPlan(
        fields={"overallrating": 80}, names={"commonname": "One"},
        face={"headassetid": 1} if face else {},
    )
    entry = team.TeamAddEntry(
        card={"name": "One", "person_id": 1}, plan=plan, dummy_id=dummy_id,
        dummy_name="Free agent", dummy_overall=48,
    )
    return team.TeamAddPreview(entries=(entry,), available_slots=1)


def test_reviewed_add_refuses_changed_safe_target(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewed = _preview(10)
    monkeypatch.setattr(team, "active_team_add_jobs", lambda _svc: ())
    monkeypatch.setattr(team, "preview_cards_for_team", lambda *_args, **_kwargs: _preview(11))
    with pytest.raises(JobValidationError, match="targets changed"):
        team.add_reviewed_cards_to_team(object(), reviewed)


def test_reviewed_add_refuses_changed_face_result(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewed = _preview(10, face=True)
    monkeypatch.setattr(team, "active_team_add_jobs", lambda _svc: ())
    monkeypatch.setattr(team, "preview_cards_for_team", lambda *_args, **_kwargs: _preview(10, face=False))
    with pytest.raises(JobValidationError, match="targets changed"):
        team.add_reviewed_cards_to_team(object(), reviewed)
