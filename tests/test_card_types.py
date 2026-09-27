"""CardRow typing helpers stay pure dicts at runtime."""

from __future__ import annotations

from src.card_types import CardDict, CardRow, as_card_row


def test_as_card_row_roundtrip() -> None:
    raw: CardDict = {"name": "Messi", "overallrating": 93, "playerid": 158023}
    row: CardRow = as_card_row(raw)
    assert row["name"] == "Messi"
    assert row["overallrating"] == 93
    assert as_card_row(None) == {}
