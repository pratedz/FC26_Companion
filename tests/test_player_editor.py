"""Tests for full LE player editor schema + category filtering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import player_apply  # noqa: E402
from src import player_schema  # noqa: E402


class PlayerEditorTests(unittest.TestCase):
    def test_categories_cover_le_fields(self):
        ids = player_schema.category_ids()
        for need in (
            "attributes",
            "ratings",
            "skills",
            "positions",
            "playstyles",
            "body",
            "movement",
            "face",
            "kit",
            "tattoos",
            "career",
        ):
            self.assertIn(need, ids)

        fields = set(player_schema.ALL_EDIT_FIELDS)
        for f in (
            "acceleration",
            "runstylecode",
            "height",
            "weight",
            "bodytypecode",
            "trait1",
            "icontrait1",
            "hairtypecode",
            "shoetypecode",
            "tattoohead",
            "isretiring",
            "preferredposition1",
            "skillmoves",
        ):
            self.assertIn(f, fields)

    def test_category_filter_attributes_only(self):
        card = player_schema.normalize_player_card(
            {
                "name": "Test",
                "overallrating": 90,
                "acceleration": 88,
                "sprintspeed": 90,
                "height": 187,
                "runstylecode": 3,
                "skillmoves": 5,
                "playstyles_plus": ["Finesse Shot", "Quick Step"],
            }
        )
        updates = player_schema.card_to_field_updates(
            card, enabled_categories=["attributes"]
        )
        fields = {f for f, _ in updates}
        self.assertIn("acceleration", fields)
        self.assertIn("sprintspeed", fields)
        self.assertNotIn("overallrating", fields)
        self.assertNotIn("height", fields)
        self.assertNotIn("runstylecode", fields)
        self.assertNotIn("skillmoves", fields)
        self.assertNotIn("icontrait1", fields)

    def test_category_filter_body_and_movement(self):
        card = player_schema.normalize_player_card(
            {
                "height": 187,
                "weight": 83,
                "bodytypecode": 8,
                "runstylecode": 2,
                "runningcode1": 1,
                "acceleration": 80,
            }
        )
        updates = player_schema.card_to_field_updates(
            card, enabled_categories=["body", "movement"]
        )
        fields = {f for f, _ in updates}
        self.assertEqual(
            fields,
            {"height", "weight", "bodytypecode", "runstylecode", "runningcode1"},
        )

    def test_playstyle_bits(self):
        t1, t2 = player_schema.playstyle_names_to_masks(
            ["Finesse Shot", "Technical", "Quick Step"]
        )
        self.assertTrue(t1 & 1)  # finesse
        self.assertTrue(t1 & 1048576)  # technical
        self.assertTrue(t1 & 33554432)  # quick step
        names = player_schema.masks_to_playstyle_names(t1, t2)
        self.assertIn("Finesse Shot", names)

    def test_apply_lua_respects_categories(self):
        card = {
            "name": "CR7 2008",
            "overallrating": 92,
            "acceleration": 91,
            "height": 187,
            "runstylecode": 5,
            "skillmoves": 5,
            "playstyles_plus": ["Power Shot", "Quick Step"],
        }
        lua = player_apply.generate_apply_player_lua(
            card,
            20801,
            enabled_categories=["attributes", "playstyles", "body"],
        )
        self.assertIn("acceleration", lua)
        self.assertIn("height", lua)
        self.assertIn("icontrait1", lua)
        self.assertIn("attributes", lua)  # topics in header
        self.assertIn("body", lua)
        self.assertIn("playstyles", lua)
        self.assertNotIn('{"overallrating"', lua.replace(" ", ""))
        self.assertNotIn('{"runstylecode"', lua.replace(" ", ""))
        self.assertNotIn("MessageBox(", lua)

    def test_empty_categories_raises(self):
        with self.assertRaises(ValueError):
            player_apply.generate_apply_player_lua(
                {"acceleration": 80}, 1, enabled_categories=[]
            )

    def test_missing_playstyles_do_not_write_zero_masks(self):
        """Catalog cards without PS data must not emit trait*=0 (wipes CM PS)."""
        card = player_schema.normalize_player_card(
            {
                "name": "No PS Card",
                "overallrating": 85,
                "acceleration": 80,
                "skillmoves": 4,
            }
        )
        updates = player_schema.card_to_field_updates(
            card, enabled_categories=player_schema.default_enabled_categories()
        )
        fields = {f for f, _ in updates}
        self.assertNotIn("trait1", fields)
        self.assertNotIn("trait2", fields)
        self.assertNotIn("icontrait1", fields)
        self.assertNotIn("icontrait2", fields)

    def test_explicit_zero_masks_still_written(self):
        card = player_schema.normalize_player_card(
            {
                "name": "Clear PS",
                "overallrating": 85,
                "trait1": 0,
                "trait2": 0,
                "icontrait1": 0,
                "icontrait2": 0,
            }
        )
        updates = player_schema.card_to_field_updates(
            card, enabled_categories=["playstyles"]
        )
        by_f = dict(updates)
        self.assertEqual(by_f.get("trait1"), 0)
        self.assertEqual(by_f.get("icontrait1"), 0)


if __name__ == "__main__":
    unittest.main()
