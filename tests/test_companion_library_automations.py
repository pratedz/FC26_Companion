"""Small headless regressions for Library handoff and Automation safety copy."""

from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from companion.app.ui_actions import UiActions  # noqa: E402
from companion.ui.surfaces.automations import (  # noqa: E402
    _automation_scope,
    _requires_scope_confirmation,
)
from companion.ui.surfaces.library import _target_route_copy  # noqa: E402


def test_library_signing_handoff_copies_then_consumes_card() -> None:
    opened: list[str] = []
    ui = UiActions()
    ui.bind_navigation(opened.append)
    card = {"name": "Messi", "overallrating": 97}

    assert ui.open_signing(card)
    card["name"] = "Changed outside handoff"
    assert opened == ["add_player"]
    assert ui.take_signing_card() == {"name": "Messi", "overallrating": 97}
    assert ui.take_signing_card() is None


def test_automation_scopes_make_broad_and_non_mutating_actions_explicit() -> None:
    assert _automation_scope("ping") == "Diagnostic - no save changes"
    assert _automation_scope("export_squad") == "Data export - no save changes"
    assert _automation_scope("pot_99_team") == "Team-wide edit - current Career team"
    assert _requires_scope_confirmation("pot_99_team")
    assert not _requires_scope_confirmation("squad_boost")


def test_library_unselected_target_uses_one_explicit_club_route() -> None:
    assert _target_route_copy() == (
        "Lock a Club player to stage a card build, or Send to Sign with no lock."
    )
