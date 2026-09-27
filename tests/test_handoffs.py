"""Cross-tab handoff helpers — free-agent pool + card labels (no GUI)."""

from __future__ import annotations

from src.ui import handoffs


def test_free_agent_pool_count_empty() -> None:
    # Explicit empty pool on dict (do not treat as missing → disk fallback)
    assert handoffs.free_agent_pool_count({"free_agents": [], "dummy_pool": []}) == 0
    assert (
        handoffs.free_agent_pool_count(
            {"players": [], "free_agents": [], "dummy_pool": []}
        )
        == 0
    )


def test_free_agent_pool_count_mixed() -> None:
    sq = {
        "free_agents": [
            {"playerid": 100, "overallrating": 48},
            {"playerid": 101, "overallrating": 50},
            102,
            {"name": "no id"},
        ]
    }
    assert handoffs.free_agent_pool_count(sq) == 3


def test_free_agent_status_line() -> None:
    assert "0" in handoffs.free_agent_status_line({"free_agents": []})
    line = handoffs.free_agent_status_line(
        {"free_agents": [{"playerid": 1}, {"playerid": 2}]}
    )
    assert "2" in line


def test_card_short_label() -> None:
    assert handoffs.card_short_label(None) == "none"
    lab = handoffs.card_short_label(
        {"name": "Messi", "overallrating": 93, "preferredposition1": "RW"}
    )
    assert "Messi" in lab
    assert "93" in lab
