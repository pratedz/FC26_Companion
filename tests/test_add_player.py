"""Tests for add-to-team: safe dummy pool + Lua builder."""

from __future__ import annotations

import unittest

from src import add_player


class DummyPoolTests(unittest.TestCase):
    def test_select_worst_rejects_catalog_superstars(self) -> None:
        """FUT specials (Messi etc.) must never become dummy overwrite targets."""
        pool = [
            {"playerid": 158023, "overallrating": 94, "name": "Messi"},
            {"playerid": 20801, "overallrating": 91, "name": "Ronaldo"},
            {"playerid": 11, "overallrating": 45, "teamid": 111592},  # free agent
            {"playerid": 13, "overallrating": 30, "teamid": 111592},
            {"playerid": 99, "overallrating": 20, "teamid": 5},  # protected + not free
        ]
        out = add_player.select_worst_dummies(
            pool, protected_ids={99}, limit=50
        )
        self.assertNotIn(158023, out)
        self.assertNotIn(20801, out)
        self.assertNotIn(99, out)
        self.assertEqual(out[0], 13)  # free, lowest ovr
        self.assertIn(11, out)

    def test_select_worst_never_exceeds_limit(self) -> None:
        pool = [
            {"playerid": i, "overallrating": i % 40, "teamid": 111592}
            for i in range(1, 200)
        ]
        out = add_player.select_worst_dummies(pool, protected_ids={1, 2}, limit=20)
        self.assertEqual(len(out), 20)
        self.assertNotIn(1, out)
        self.assertNotIn(2, out)

    def test_protected_squad_ids(self) -> None:
        squad = {
            "teamid": 243,
            "players": [
                {"playerid": 231747, "name": "Mbappe"},
                {"playerid": "bad"},
            ],
        }
        ids = add_player.protected_squad_ids(squad)
        self.assertEqual(ids, {231747})


class AddPlayerBuilderTests(unittest.TestCase):
    def test_generate_lua_auto_is_dummy_first_no_create(self) -> None:
        card = {
            "name": "Test Star",
            "overallrating": 88,
            "potential": 90,
            "acceleration": 90,
            "sprintspeed": 91,
            "height": 180,
            "weight": 75,
            "bodytypecode": 5,
            "headassetid": 20801,
            "preferredposition1": 25,
            "nationality": 18,
        }
        lua = add_player.generate_add_to_team_lua(
            card, teamid=243, mode="auto", dummy_candidate_ids=[11, 13, 20]
        )
        self.assertNotIn("pcall(CreatePlayer", lua)
        self.assertNotIn("function try_create", lua)
        self.assertIn("TransferPlayer", lua)
        self.assertIn("TARGET_TEAM = 243", lua)
        self.assertIn("SAFE v15", lua)
        self.assertIn("try_dummy_overwrite", lua)
        self.assertIn("FIELD_UPDATES", lua)
        self.assertIn("HINT_DUMMIES", lua)
        self.assertIn("11", lua)
        self.assertIn("job=add_to_team", lua)
        self.assertIn("editedplayernames", lua)
        self.assertIn("EditDBTableField", lua)
        self.assertIn("_add_team_crash.log", lua)
        self.assertIn("finish", lua)
        self.assertNotIn("GetPlayerIDSForTeam", lua)
        self.assertNotIn("ReloadPlayersManager", lua)

    def test_generate_lua_create_mode_has_createplayer(self) -> None:
        card = {"name": "Create Only", "overallrating": 80, "height": 180}
        lua = add_player.generate_add_to_team_lua(card, teamid=243, mode="create")
        self.assertIn("CreatePlayer", lua)
        self.assertIn("try_create", lua)
        self.assertIn("TransferPlayer", lua)

    def test_generate_lua_dummy_mode(self) -> None:
        card = {"name": "Dummy Path", "overallrating": 70}
        lua = add_player.generate_add_to_team_lua(
            card, teamid=10, mode="dummy", dummy_candidate_ids=[100, 200, 300]
        )
        self.assertIn('MODE = "dummy"', lua)
        self.assertNotIn("pcall(CreatePlayer", lua)
        self.assertNotIn("function try_create", lua)
        self.assertIn("100", lua)
        self.assertIn("200", lua)
        self.assertIn("TransferPlayer", lua)
        self.assertIn("try_dummy_overwrite", lua)

    def test_card_row_includes_visuals_strips_foreign_head(self) -> None:
        card = {
            "name": "Face Body",
            "overallrating": 80,
            "height": 185,
            "weight": 80,
            "bodytypecode": 6,
            "headassetid": 158023,  # Messi face — must NOT land on CreatePlayer
            "hairtypecode": 10,
        }
        row = add_player.card_to_players_row_data(card)
        self.assertEqual(row.get("height"), "185")
        self.assertEqual(row.get("weight"), "80")
        self.assertEqual(row.get("bodytypecode"), "6")
        self.assertNotIn("headassetid", row)
        self.assertIn("birthdate", row)
        self.assertTrue(int(row["birthdate"]) > 100000)

    def test_defaults_prevent_mannequin_stats(self) -> None:
        row = add_player.card_to_players_row_data({"name": "Bare", "overallrating": 75})
        self.assertGreaterEqual(int(row["height"]), 160)
        self.assertGreaterEqual(int(row["weight"]), 55)
        self.assertEqual(row["bodytypecode"], "5")
        self.assertIn("birthdate", row)
        self.assertEqual(row["skintonecode"], "3")
        # No invented England
        self.assertNotIn("nationality", row)

    def test_minimize_create_row_strips_nameids_and_bulk(self) -> None:
        fat = add_player.card_to_players_row_data(
            {
                "name": "Z",
                "overallrating": 90,
                "acceleration": 90,
                "aggression": 70,
                "curve": 80,
            }
        )
        fat["firstnameid"] = "0"
        fat["lastnameid"] = "0"
        slim = add_player.minimize_create_row(fat)
        self.assertNotIn("firstnameid", slim)
        self.assertNotIn("lastnameid", slim)
        self.assertIn("overallrating", slim)
        self.assertIn("height", slim)
        self.assertIn("acceleration", slim)
        # aggression/curve not in payload allowlist
        self.assertNotIn("aggression", slim)
        self.assertNotIn("curve", slim)

    def test_real_face_and_nation_gain_in_lua(self) -> None:
        """Nation + names + deferred face; create uses generic head."""
        card = {
            "name": "Zinedine Zidane",
            "firstName": "Zinedine",
            "lastName": "Zidane",
            "overallrating": 94,
            "basePlayerEaId": 1397,
            "nationality": 18,
            "height": 185,
        }
        lua = add_player.generate_add_to_team_lua(
            card,
            teamid=243,
            mode="auto",
            use_real_face=True,
            dummy_candidate_ids=[11, 13],
        )
        self.assertIn("DEFERRED_FACE_ID = 1397", lua)
        self.assertIn("NATION_ID = 18", lua)
        self.assertNotIn("pcall(CreatePlayer", lua)
        self.assertIn('FIRST = "Zinedine"', lua)
        self.assertIn('SUR = "Zidane"', lua)
        self.assertIn("commonname", lua)
        self.assertIn("try_dummy_overwrite", lua)
        # Name fix: detach free-agent dictionary nameids so UI shows card name
        self.assertIn("clear_name_dictionary_ids_on_players", lua)
        self.assertIn("firstnameid", lua)
        self.assertIn("usercaneditname", lua)
        self.assertIn("name_dict_clear", lua)

    def test_resolve_player_names_first_last_and_display_only(self) -> None:
        a = add_player.resolve_player_names(
            {
                "name": "David Beckham",
                "firstName": "David",
                "lastName": "Beckham",
                "nickname": "David Beckham",
            }
        )
        self.assertEqual(a[0], "David")
        self.assertEqual(a[1], "Beckham")
        self.assertTrue(a[2])  # jersey/common source non-empty
        b = add_player.resolve_player_names({"name": "Zinedine Zidane"})
        self.assertEqual(b[0], "Zinedine")
        self.assertEqual(b[1], "Zidane")
        self.assertTrue(b[2])

    def test_dummy_lua_name_strings_and_no_createplayer(self) -> None:
        card = {
            "name": "David Beckham",
            "firstName": "David",
            "lastName": "Beckham",
            "nickname": "David Beckham",
            "overallrating": 94,
            "basePlayerEaId": 250,
        }
        lua = add_player.generate_add_to_team_lua(
            card, teamid=2, mode="dummy", dummy_candidate_ids=[85308, 84610]
        )
        self.assertNotIn("pcall(CreatePlayer", lua)
        self.assertIn('FIRST = "David"', lua)
        self.assertIn('SUR = "Beckham"', lua)
        self.assertIn("editedplayernames", lua)
        self.assertIn("clear_name_dictionary_ids_on_players", lua)
        # players-row detach so GetPlayerName cannot keep FA label
        self.assertIn('firstnameid = "0"', lua)
        self.assertIn('usercaneditname = "1"', lua)
        self.assertIn("set_edited_name", lua)

    def test_real_face_and_nation_from_fut_fields(self) -> None:
        card = {
            "name": "Zinedine Zidane",
            "firstName": "Zinedine",
            "lastName": "Zidane",
            "overallrating": 94,
            "playerid": 1397,
            "basePlayerEaId": 1397,
            "nationality": 18,
            "height": 185,
            "weight": 77,
        }
        row = add_player.card_to_players_row_data(card, use_real_face=True)
        self.assertNotIn("headassetid", row)
        self.assertEqual(row.get("nationality"), "18")
        self.assertEqual(row.get("height"), "185")
        f, s, j = add_player.resolve_player_names(card)
        self.assertEqual(f, "Zinedine")
        self.assertEqual(s, "Zidane")
        lua = add_player.generate_add_to_team_lua(
            card, teamid=243, use_real_face=True, dummy_candidate_ids=[11]
        )
        self.assertIn("DEFERRED_FACE_ID = 1397", lua)
        self.assertIn("NATION_ID = 18", lua)
        self.assertNotIn("pcall(CreatePlayer", lua)

    def test_pele_base_maps_brazil_and_ascii_name(self) -> None:
        card = {
            "name": "Pelé",
            "firstName": "Edson",
            "lastName": "Arantes Nascimento",
            "overallrating": 96,
            "basePlayerEaId": 237067,
            "height": 173,
        }
        self.assertEqual(add_player.resolve_nationality_id(card), 54)
        lua = add_player.generate_add_to_team_lua(
            card, teamid=2, use_real_face=True, dummy_candidate_ids=[11]
        )
        self.assertIn("NATION_ID = 54", lua)
        self.assertIn("DEFERRED_FACE_ID = 237067", lua)
        self.assertIn('FIRST = "Edson"', lua)
        self.assertNotIn("PelAc", lua)
        self.assertIn("SAFE v15", lua)
        self.assertNotIn("pcall(CreatePlayer", lua)

    def test_describe_add_plan(self) -> None:
        card = {"name": "Plan Me", "overallrating": 75}
        pool = [
            {"playerid": i, "overallrating": 40 + i % 10, "teamid": 111592}
            for i in range(1, 60)
        ]
        plan = add_player.describe_add_plan(
            card, teamid=243, mode="auto", dummy_pool=pool, protected_ids={5}
        )
        self.assertEqual(plan["teamid"], 243)
        self.assertEqual(plan["path"], "dummy")
        self.assertGreaterEqual(plan["preferred_create_id"], add_player.GENERATED_ID_MIN)
        self.assertLessEqual(plan["dummy_count"], add_player.DUMMY_POOL_LIMIT)
        self.assertNotIn(5, plan["dummy_candidates"])
        self.assertTrue(plan.get("safe"))
        self.assertIn("no createplayer", plan.get("notes", "").lower())

    def test_allocate_generated_range(self) -> None:
        pid = add_player.allocate_generated_playerid(seed="x")
        self.assertGreaterEqual(pid, add_player.GENERATED_ID_MIN)
        self.assertLessEqual(pid, add_player.GENERATED_ID_MAX)
        self.assertEqual(
            add_player.allocate_generated_playerid(preferred=460123), 460123
        )

    def test_lua_rejects_not_in_career(self) -> None:
        lua = add_player.generate_add_to_team_lua(
            {"name": "X", "overallrating": 80}, teamid=1, mode="create"
        )
        self.assertIn("IsInCM", lua)
        self.assertIn("not_in_career", lua)


if __name__ == "__main__":
    unittest.main()
