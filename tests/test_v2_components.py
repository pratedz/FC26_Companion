"""Tests for the v2 component set.

Only the display-free parts are exercised here: the one-primary-per-screen
budget, grid sorting/filtering, and the monogram fallback. Widget construction
needs a display and is covered by the smoke test instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.v2.ui.components import (  # noqa: E402
    Column,
    PrimaryBudget,
    filter_rows,
    initials,
    sort_rows,
)

ROWS = [
    {"name": "Lionel Messi", "ovr": 93, "pos": 23, "club": "Inter Miami"},
    {"name": "Kylian Mbappé", "ovr": 91, "pos": 25, "club": "Real Madrid"},
    {"name": "Erling Haaland", "ovr": 91, "pos": 25, "club": "Manchester City"},
    {"name": "Youth Player", "ovr": None, "pos": 0, "club": ""},
]


# ── the one-primary rule ─────────────────────────────────────────────


def test_primary_budget_allows_exactly_one() -> None:
    b = PrimaryBudget("Club")
    b.spend()
    with pytest.raises(ValueError, match="more than 1 primary"):
        b.spend()


def test_primary_budget_resets_between_screens() -> None:
    b = PrimaryBudget("Club")
    b.spend()
    b.reset()
    b.spend()  # must not raise


def test_primary_budget_error_names_the_screen() -> None:
    b = PrimaryBudget("Transfers")
    b.spend()
    with pytest.raises(ValueError, match="Transfers"):
        b.spend()


# ── grid sorting ─────────────────────────────────────────────────────


def test_numeric_column_sorts_high_to_low_by_default() -> None:
    col = Column("ovr", "OVR")
    out = sort_rows(ROWS, col)
    assert [r["ovr"] for r in out[:3]] == [93, 91, 91]


def test_missing_values_sort_last_not_first() -> None:
    """A blank OVR must not masquerade as the best player in the squad."""
    col = Column("ovr", "OVR")
    out = sort_rows(ROWS, col)
    assert out[-1]["ovr"] is None


def test_descending_reverses() -> None:
    col = Column("ovr", "OVR")
    asc = sort_rows(ROWS, col)
    desc = sort_rows(ROWS, col, descending=True)
    assert asc[0] != desc[0]


def test_text_column_sorts_case_insensitively() -> None:
    col = Column("name", "Name")
    out = [r["name"] for r in sort_rows(ROWS, col)]
    assert out[0] == "Erling Haaland"


def test_custom_sort_key_is_used() -> None:
    col = Column("name", "Name", sort_key=lambda r: len(str(r["name"])))
    out = [len(r["name"]) for r in sort_rows(ROWS, col)]
    assert out == sorted(out)
    assert out[-1] == len("Erling Haaland")


def test_render_overrides_display_without_affecting_sort() -> None:
    col = Column("ovr", "OVR", render=lambda r: f"[{r['ovr']}]")
    assert col.text(ROWS[0]) == "[93]"
    assert col.text(ROWS[3]) == "[None]"


def test_missing_value_renders_as_dash() -> None:
    col = Column("club", "Club")
    assert col.text(ROWS[3]) == "—"


# ── grid filtering ───────────────────────────────────────────────────


def test_filter_matches_any_named_key() -> None:
    out = filter_rows(ROWS, "madrid", ["name", "club"])
    assert len(out) == 1
    assert out[0]["name"] == "Kylian Mbappé"


def test_filter_is_case_insensitive() -> None:
    assert len(filter_rows(ROWS, "MESSI", ["name"])) == 1


def test_empty_filter_returns_everything() -> None:
    assert len(filter_rows(ROWS, "   ", ["name"])) == len(ROWS)


def test_filter_never_matches_none_values() -> None:
    assert filter_rows(ROWS, "none", ["ovr"]) == []


# ── monogram fallback ────────────────────────────────────────────────


def test_initials_from_full_name() -> None:
    assert initials("Lionel Messi") == "LM"


def test_initials_from_abbreviated_name() -> None:
    assert initials("L. Messi") == "LM"


def test_initials_from_single_name() -> None:
    assert initials("Ronaldinho") == "RO"


def test_initials_never_raises_on_empty() -> None:
    assert initials("") == "?"
    assert initials(None) == "?"  # type: ignore[arg-type]
