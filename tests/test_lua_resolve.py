"""Unit tests for lua_resolve / snippets (REAL modules)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import lua_resolve  # noqa: E402
from src import snippets  # noqa: E402


class TestLuaResolve(unittest.TestCase):
    def test_full_fitness_snippet_contains_helper(self):
        lua = lua_resolve.resolve_profile_lua("full_fitness")
        self.assertIn("UserTeamSetPlayersFitness", lua)
        self.assertIn("require 'imports/career_mode/helpers'", lua)
        self.assertIn("95", lua)

    def test_full_sharpness_snippet_contains_helper(self):
        lua = lua_resolve.resolve_profile_lua("full_sharpness")
        self.assertIn("UserTeamSetPlayersSharpness", lua)
        self.assertIn("100", lua)

    def test_full_form_snippet(self):
        lua = lua_resolve.resolve_profile_lua("full_form")
        self.assertIn("UserTeamSetPlayersForm", lua)

    def test_full_morale_snippet(self):
        lua = lua_resolve.resolve_profile_lua("full_morale")
        self.assertIn("UserTeamSetPlayersMorale", lua)

    def test_full_form_morale_sharpness_snippet(self):
        lua = lua_resolve.resolve_profile_lua("full_form_morale_sharpness")
        self.assertIn("UserTeamSetPlayersFormSharpnessMorale", lua)

    def test_stock_script_fitness_event(self):
        """Stock auto fitness script content (event-driven)."""
        lua = lua_resolve.resolve_profile_lua("full_fitness_event")
        # Stock uses 100; either helper name or file content must be present
        self.assertIn("UserTeamSetPlayersFitness", lua)
        self.assertIn("AddEventHandler", lua)

    def test_stock_script_sharpness_event(self):
        lua = lua_resolve.resolve_profile_lua("full_sharpness_event")
        self.assertIn("UserTeamSetPlayersSharpness", lua)

    def test_dump_profile_alias(self):
        a = lua_resolve.dump_profile("full_fitness")
        b = snippets.get_snippet("full_fitness")
        self.assertEqual(a, b)

    def test_snippet_keys_complete(self):
        for key in (
            "full_fitness",
            "full_sharpness",
            "full_form",
            "full_morale",
            "full_form_morale_sharpness",
        ):
            self.assertIn(key, snippets.SNIPPETS)

    def test_export_user_squad_app_script(self):
        lua = lua_resolve.resolve_profile_lua("export_user_squad")
        self.assertIn("current_squad.json", lua)
        self.assertIn("GetUserSeniorTeamPlayerIDs", lua)
        self.assertIn("GetPlayerName", lua)


if __name__ == "__main__":
    unittest.main()
