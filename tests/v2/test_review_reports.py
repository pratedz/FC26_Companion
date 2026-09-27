"""Structural proof that the mid-session UX review pack ships in-repo.

These are analysis deliverables under ``review/``, not runtime UI code, but the
goal requires durable, non-theatrical checks that the real artifacts exist and
are grounded (length + Career Mode framing + step/button critique language).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "review"

REQUIRED = (
    "club.md",
    "player.md",
    "add_player.md",
    "transfers.md",
    "automations.md",
    "library.md",
    "overall.md",
)


@pytest.mark.parametrize("name", REQUIRED)
def test_review_report_exists_and_is_substantive(name: str) -> None:
    path = REVIEW / name
    assert path.is_file(), f"missing review report: {path}"
    text = path.read_text(encoding="utf-8")
    assert len(text) > 3000, f"{name} looks like a stub ({len(text)} bytes)"
    lower = text.casefold()
    assert "career" in lower, f"{name} must frame Career Mode use"
    assert any(k in lower for k in ("button", "control", "cta")), (
        f"{name} must discuss controls/buttons"
    )
    assert any(k in lower for k in ("step", "click", "path")), (
        f"{name} must discuss multi-step flow / step reduction"
    )


def test_overall_synthesizes_cross_tab_priorities() -> None:
    text = (REVIEW / "overall.md").read_text(encoding="utf-8")
    lower = text.casefold()
    for needle in ("club", "player", "add player", "transfer", "automation", "library"):
        assert needle in lower, f"overall.md should reference {needle}"
    assert "review" in lower and "apply" in lower
    assert "p0" in lower or "priority" in lower


def test_reports_ground_named_actions_from_shipped_surfaces() -> None:
    """Spot-check: critiques mention controls that exist in surface source."""
    club_src = (ROOT / "companion/ui/surfaces/club.py").read_text(encoding="utf-8")
    club_rep = (REVIEW / "club.md").read_text(encoding="utf-8")
    assert "Refresh squad" in club_src
    assert "Refresh squad" in club_rep

    auto_src = (ROOT / "companion/ui/surfaces/automations.py").read_text(encoding="utf-8")
    auto_rep = (REVIEW / "automations.md").read_text(encoding="utf-8")
    assert "Activity" in auto_src
    assert "APPLY QUEUE" in auto_rep

    shell = (ROOT / "companion/ui/shell.py").read_text(encoding="utf-8")
    overall = (REVIEW / "overall.md").read_text(encoding="utf-8")
    assert "Review changes" in shell
    assert "Review" in overall
