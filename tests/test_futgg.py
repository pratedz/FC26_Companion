"""Unit tests for FUT.GG row mapping and offline cache load (shipped modules)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.futgg_client import definition_to_le_row, _rarity_name  # noqa: E402
from src import card_catalog  # noqa: E402
from src import card_to_lua  # noqa: E402


class TestFutGGMapping(unittest.TestCase):
    def test_rarity_from_nested_dict(self) -> None:
        item = {
            "firstName": "Neymar",
            "lastName": "da Silva Santos Jr.",
            "nickname": "Neymar Jr",
            "overall": 97,
            "game": "26",
            "position": 18,
            "skillMoves": 5,
            "weakFoot": 5,
            "basePlayerEaId": 190871,
            "eaId": 50522519,
            "slug": "26-50522519",
            "rarity": {
                "name": "Festival of Football: Glory Hunters",
                "slug": "festival-of-football-glory-hunters",
                "isSpecial": True,
            },
            "attributeAcceleration": 96,
            "attributeSprintSpeed": 95,
            "attributeFinishing": 94,
        }
        self.assertEqual(_rarity_name(item), "Festival of Football: Glory Hunters")
        row = definition_to_le_row(item)
        self.assertEqual(row["name"], "Neymar Jr")
        self.assertEqual(row["overallrating"], 97)
        self.assertEqual(row["year"], "26")
        self.assertEqual(row["revision"], "Festival of Football: Glory Hunters")
        self.assertEqual(row["acceleration"], 96)
        self.assertEqual(row["sprintspeed"], 95)
        self.assertEqual(row["finishing"], 94)
        self.assertEqual(row["playerid"], 190871)

    def test_playstyle_id_masks(self) -> None:
        from src import futgg_client

        t1, t2 = futgg_client.futgg_playstyle_ids_to_masks([0, 5])
        self.assertEqual(t1 & 1, 1)
        self.assertEqual(t1 & (1 << 5), 1 << 5)

    def test_definition_maps_meta_and_playstyles(self) -> None:
        from src import futgg_client

        row = futgg_client.definition_to_le_row(
            {
                "firstName": "Enzo",
                "lastName": "Fernandez",
                "overall": 96,
                "skillMoves": 4,
                "weakFoot": 5,
                "height": 178,
                "weight": 76,
                "foot": 1,
                "bodytypeCode": 2,
                "playstyles": [0, 5],
                "playstylesPlus": [8],
                "club": {"name": "Chelsea", "eaId": 5},
                "nation": {"name": "Argentina", "eaId": 52},
                "league": {"name": "Premier League"},
                "rarity": {"name": "Summer Stars"},
                "basePlayerEaId": 247090,
                "eaId": 117687602,
                "game": "26",
                "attributeAcceleration": 94,
            }
        )
        self.assertEqual(row["club"], "Chelsea")
        self.assertEqual(row["nation"], "Argentina")
        self.assertEqual(row["height"], 178)
        self.assertEqual(row["preferredfoot"], 1)
        self.assertTrue(row["trait1"] & 1)
        self.assertTrue(row["icontrait1"] & (1 << 8))

    def test_full_card_apply_includes_body_and_ps(self) -> None:
        from src import card_to_lua

        card = {
            "name": "Enzo",
            "overallrating": 96,
            "acceleration": 94,
            "height": 178,
            "weight": 76,
            "skillmoves": 4,
            "weakfootabilitytypecode": 5,
            "preferredfoot": 1,
            "trait1": 33,
            "icontrait1": 256,
            "bodytypecode": 2,
        }
        lua = card_to_lua.generate_apply_card_lua(
            card,
            1,
            enabled_categories=["attributes", "skills", "playstyles", "body"],
        )
        self.assertIn("height", lua)
        self.assertIn("trait1", lua)
        self.assertIn("skillmoves", lua)

    def test_apply_lua_from_futgg_row(self) -> None:
        row = definition_to_le_row(
            {
                "nickname": "Neymar Jr",
                "overall": 97,
                "game": "26",
                "position": 27,
                "basePlayerEaId": 190871,
                "slug": "26-50522519",
                "rarity": {"name": "Festival of Football: Glory Hunters"},
                "attributeAcceleration": 96,
                "attributeFinishing": 94,
                "attributeDribbling": 98,
            }
        )
        lua = card_to_lua.generate_apply_card_lua(row, 190871)
        self.assertIn("target_playerid = 190871", lua)
        self.assertIn("overallrating", lua)
        self.assertIn("acceleration", lua)
        self.assertIn("finishing", lua)
        self.assertIn("SetRecordFieldValue", lua)

    def test_load_futgg_jsonl_offline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            futgg = root / "futgg" / "by_player"
            futgg.mkdir(parents=True)
            sample = {
                "name": "Neymar Jr",
                "overallrating": 97,
                "year": "26",
                "_year": "26",
                "revision": "Festival of Football: Glory Hunters",
                "origin": "Festival of Football: Glory Hunters",
                "playerid": 190871,
                "acceleration": 96,
                "finishing": 94,
                "slug": "26-50522519",
            }
            path = futgg / "Neymar.jsonl"
            path.write_text(json.dumps(sample) + "\n", encoding="utf-8")
            cards = card_catalog.load_futgg_cache(root)
            self.assertGreaterEqual(len(cards), 1)
            hits = card_catalog.search_cards(
                "Neymar", year="26", cards=cards, limit=5
            )
            self.assertTrue(hits)
            self.assertEqual(hits[0]["name"], "Neymar Jr")
            self.assertEqual(int(hits[0]["overallrating"]), 97)
            rev = str(hits[0].get("revision") or "")
            self.assertIn("Festival", rev)
            self.assertNotIn("{", rev)  # not a raw dict dump


if __name__ == "__main__":
    unittest.main()
