"""Unit tests for card_to_lua and card_catalog (REAL modules)."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import card_catalog  # noqa: E402
from src import card_to_lua  # noqa: E402
from src import field_map  # noqa: E402


class TestCardToLua(unittest.TestCase):
    def setUp(self):
        self.card = {
            "year": "18",
            "name": "Lionel Messi",
            "playerid": 158023,
            "overallrating": 94,
            "potential": 94,
            "acceleration": 95,
            "sprintspeed": 90,
            "finishing": 95,
            "dribbling": 96,
            "skillmoves": 4,
            "weakfootabilitytypecode": 4,
            "preferredposition1": 23,
        }
        self.target = 20801  # different target to prove we use target_playerid

    def test_generate_contains_target_and_fields(self):
        lua = card_to_lua.generate_apply_card_lua(self.card, self.target)
        self.assertIn(str(self.target), lua)
        self.assertIn("target_playerid", lua)
        self.assertIn("SetRecordFieldValue", lua)
        self.assertIn("acceleration", lua)
        self.assertIn("finishing", lua)
        self.assertIn("overallrating", lua)
        self.assertIn("potential", lua)
        self.assertIn("skillmoves", lua)
        self.assertIn("weakfootabilitytypecode", lua)
        self.assertIn("modifier", lua)
        self.assertIn("preferredposition1", lua)
        self.assertIn("PlayerHasDevelopementPlan", lua)
        self.assertIn("PlayerSetValueInDevelopementPlan", lua)
        self.assertTrue(
            "imports/career_mode/helpers" in lua,
            "Lua must load career helpers",
        )
        # ReloadPlayersManager intentionally omitted — freezes many FC26 Career sessions
        self.assertNotIn("ReloadPlayersManager", lua)
        self.assertIn("tonumber", lua)
        # Cards without playstyle data must not force trait* = 0 (would wipe CM PS)
        self.assertNotIn('{"trait1", 0}', lua.replace(" ", ""))
        # values
        self.assertIn("95", lua)  # acceleration / finishing
        self.assertIn("94", lua)  # ovr

    def test_modifier_forced_zero(self):
        lua = card_to_lua.generate_apply_card_lua(self.card, self.target)
        self.assertIn('"modifier"', lua)
        compact = lua.replace(" ", "").replace("\n", "")
        self.assertIn('{"modifier",0}', compact)

    def test_field_map_aliases(self):
        self.assertEqual(field_map.to_le_field("Acceleration"), "acceleration")
        self.assertEqual(field_map.to_le_field("finishing"), "finishing")
        self.assertEqual(field_map.to_le_field("PAC"), "acceleration")
        self.assertEqual(field_map.to_le_field("weakfoot"), "weakfootabilitytypecode")
        self.assertEqual(field_map.to_le_field("OVR"), "overallrating")


class TestCardCatalog(unittest.TestCase):
    def test_unify_and_search_in_memory(self):
        rows = [
            {
                "name": "Lionel Messi",
                "playerid": "158023",
                "overallrating": "94",
                "acceleration": "95",
                "finishing": "95",
            }
        ]
        cards = [card_catalog.unify_row(r, year="18", source="test") for r in rows]
        hits = card_catalog.search_cards("messi", year="18", cards=cards)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["playerid"], 158023)
        self.assertEqual(hits[0]["overallrating"], 94)

    def test_year_normalization_2018_vs_18(self):
        cards = [
            card_catalog.unify_row(
                {"name": "Test Player", "overallrating": "80", "playerid": "1"},
                year="2018",
                source="t",
            )
        ]
        self.assertEqual(len(card_catalog.search_cards("test", year="18", cards=cards)), 1)
        self.assertEqual(len(card_catalog.search_cards("test", year="2018", cards=cards)), 1)

    def test_load_card_db_csv(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            csv_path = td_path / "fut18.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "name",
                        "playerid",
                        "overallrating",
                        "acceleration",
                        "finishing",
                    ],
                )
                w.writeheader()
                w.writerow(
                    {
                        "name": "Lionel Messi",
                        "playerid": "158023",
                        "overallrating": "94",
                        "acceleration": "95",
                        "finishing": "95",
                    }
                )
            cards = card_catalog.load_card_db(td_path, include_local=False)
            hits = card_catalog.search_cards("Messi", year="18", cards=cards)
            self.assertEqual(len(hits), 1)
            lua = card_to_lua.generate_apply_card_lua(hits[0], 158023)
            self.assertIn("158023", lua)
            self.assertIn("acceleration", lua)

    def test_local_cards_if_present(self):
        """If LE cards.csv exists, Messi should be findable under year=local."""
        from src import paths

        if not paths.cards_csv_path().is_file():
            self.skipTest("local cards.csv not present")
        hits = card_catalog.search_cards(
            "Messi", year="local", include_local=True, limit=5
        )
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(any("messi" in str(h.get("name", "")).lower() for h in hits))
        lua = card_to_lua.generate_apply_card_lua(hits[0], int(hits[0]["playerid"]))
        self.assertIn(str(hits[0]["playerid"]), lua)
        self.assertIn("SetRecordFieldValue", lua)


if __name__ == "__main__":
    unittest.main()
