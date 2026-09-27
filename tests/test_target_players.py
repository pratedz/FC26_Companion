"""Tests for target player name → playerid resolution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import target_players


SAMPLE = {
    "mode": "career",
    "teamid": 1,
    "teamname": "Test FC",
    "count": 3,
    "players": [
        {
            "playerid": 158023,
            "name": "L. Messi",
            "position": "RW",
            "overallrating": 93,
            "potential": 93,
            "jerseynumber": 10,
        },
        {
            "playerid": 190871,
            "name": "Neymar Jr",
            "position": "LW",
            "overallrating": 89,
            "potential": 89,
            "jerseynumber": 10,
        },
        {
            "playerid": 20801,
            "name": "Cristiano Ronaldo",
            "position": "ST",
            "overallrating": 91,
            "potential": 91,
            "jerseynumber": 7,
        },
    ],
    "free_agents": [
        {
            "playerid": 85308,
            "overallrating": 56,
            "teamid": 111592,
            "free_agent": True,
            "source": "free_agent",
        },
        {"playerid": 84610, "overallrating": 58, "teamid": 111592},
    ],
}


class TargetPlayersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "current_squad.json"
        self.path.write_text(json.dumps(SAMPLE), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_squad(self) -> None:
        s = target_players.load_squad(self.path)
        self.assertTrue(s["loaded"])
        self.assertEqual(s["count"], 3)
        self.assertEqual(s["teamname"], "Test FC")
        # free_agents must survive load (SAFE add-to-team dummy pool)
        self.assertEqual(len(s.get("free_agents") or []), 2)
        self.assertEqual(s["free_agents"][0]["playerid"], 85308)
        self.assertTrue(s["free_agents"][0].get("free_agent"))
        self.assertEqual(len(s.get("dummy_pool") or []), 2)

    def test_search_exactish(self) -> None:
        hits = target_players.search_squad("neymar", path=self.path)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["playerid"], 190871)

    def test_search_partial(self) -> None:
        hits = target_players.search_squad("messi", path=self.path)
        self.assertEqual(hits[0]["playerid"], 158023)

    def test_search_ronaldo(self) -> None:
        hits = target_players.search_squad("ronaldo", path=self.path)
        self.assertEqual(hits[0]["playerid"], 20801)

    def test_search_by_id(self) -> None:
        hits = target_players.search_squad("190871", path=self.path)
        self.assertEqual(hits[0]["playerid"], 190871)

    def test_empty_query_lists_all(self) -> None:
        hits = target_players.search_squad("", path=self.path, limit=10)
        self.assertEqual(len(hits), 3)

    def test_format_line(self) -> None:
        line = target_players.format_target_line(SAMPLE["players"][0], 0)
        self.assertIn("Messi", line)
        self.assertIn("158023", line)

    def test_missing_file(self) -> None:
        s = target_players.load_squad(Path(self.tmp.name) / "nope.json")
        self.assertFalse(s["loaded"])
        self.assertEqual(s["players"], [])

    def test_free_agent_pool_survives_stripped_squad_dict(self) -> None:
        """Regression: FA · 0 bug — file has free_agents but caller dict dropped them."""
        stripped = {
            "mode": "career",
            "teamid": 1,
            "teamname": "Test FC",
            "count": 3,
            "players": SAMPLE["players"],
            "path": str(self.path),
            "loaded": True,
            # free_agents intentionally missing (old load_squad shape)
        }
        pool = target_players.free_agent_pool(stripped, path=self.path)
        self.assertGreaterEqual(len(pool), 2)
        self.assertEqual(pool[0]["playerid"], 85308)
        self.assertTrue(pool[0].get("free_agent"))
        self.assertEqual(target_players.free_agent_count(stripped, path=self.path), 2)

    def test_add_team_product_sees_free_agents(self) -> None:
        """product.add_card_to_user_team must not fail FA pool when export has dummies."""
        from src import add_player

        s = target_players.load_squad(self.path)
        pool = target_players.free_agent_pool(s, path=self.path)
        self.assertGreaterEqual(len(pool), 2)
        prot = add_player.protected_squad_ids(s)
        dummies = add_player.select_worst_dummies(pool, protected_ids=prot, limit=20)
        self.assertGreaterEqual(len(dummies), 1)


if __name__ == "__main__":
    unittest.main()
