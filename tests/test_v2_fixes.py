"""Regression tests for the crash / name / face / arming fixes.

Every test here corresponds to a defect that was reproduced from evidence in
queue/_add_team_crash.log, Logs/live_editor_*.log, or the FCLiveEditor.DLL
symbol table. Each one should fail loudly if the fix is ever reverted.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

APP_ROOT = str(Path(__file__).resolve().parents[1])
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from src import add_player, add_team_lua, base_players, le_apply, paths  # noqa: E402


class TestCreateIdCeiling(unittest.TestCase):
    """CreatePlayer with playerid >= 500000 terminates FC26.exe.

    Evidence: ids 463139/467843/470126/476918/494215 all completed;
    506153/510761/514083/516987/527153/537425/546619 all killed the process.
    """

    HARD_CEILING = 500000

    def test_generated_max_is_below_ceiling(self) -> None:
        self.assertLess(add_player.GENERATED_ID_MAX, self.HARD_CEILING)

    def test_allocator_never_returns_lethal_id(self) -> None:
        for seed in ["", "Zidane", "Pele", "Marco van Basten", "x" * 64, "12345"]:
            pid = add_player.allocate_generated_playerid(seed=seed)
            self.assertGreaterEqual(pid, add_player.GENERATED_ID_MIN)
            self.assertLess(
                pid, self.HARD_CEILING, f"allocator returned lethal id {pid} for seed {seed!r}"
            )

    def test_preferred_id_above_ceiling_is_rejected(self) -> None:
        pid = add_player.allocate_generated_playerid(seed="x", preferred=546619)
        self.assertLess(pid, self.HARD_CEILING)

    def test_lua_carries_a_hard_ceiling_guard(self) -> None:
        lua = add_team_lua.render_add_to_team_lua(
            name="Test Player",
            first="Test",
            sur="Player",
            jersey="Player",
            teamid=5,
            mode="create",
            preferred_create_id=470000,
            player_row={"overallrating": "80"},
            field_updates=[("overallrating", 80)],
            dummy_ids=[],
            transfersum=0,
            wage=1000,
            contract_months=36,
            age=25,
            fallback_birthdate="150000",
            use_real_face=False,
            base_face_id=0,
            nation_id=None,
            gen_min=add_player.GENERATED_ID_MIN,
            gen_max=add_player.GENERATED_ID_MAX,
            crash_log_path="C:/tmp/crash.log",
            status_path="C:/tmp/status.txt",
        )
        self.assertIn("HARD_MAX", lua)
        self.assertIn("499999", lua)


class TestBasePlayersLookup(unittest.TestCase):
    """base_players.csv is the ground truth for identity and appearance."""

    @classmethod
    def setUpClass(cls) -> None:
        if not paths.base_players_csv_path().is_file():
            raise unittest.SkipTest("base_players.csv not present")

    def test_index_loads(self) -> None:
        self.assertGreater(base_players.count(), 20000)

    def test_hashighqualityhead_is_not_always_one(self) -> None:
        # The old code hardcoded 1. These three legends are all 0, which is
        # exactly why importing them produced a blank face.
        for pid in (1397, 237067, 192181):  # Zidane, Pele, van Basten
            profile = base_players.face_profile(pid)
            self.assertTrue(profile["real"], f"{pid} should have a real head")
            self.assertEqual(
                profile["hashighqualityhead"], 0, f"{pid} must not be forced to HQ"
            )

    def test_headtypecode_is_carried(self) -> None:
        self.assertEqual(base_players.face_profile(1397)["headtypecode"], 2502)
        self.assertEqual(base_players.face_profile(192181)["headtypecode"], 19)

    def test_hq_players_are_detected(self) -> None:
        for pid in (158023, 20801):  # Messi, R. Ronaldo
            self.assertEqual(base_players.face_profile(pid)["hashighqualityhead"], 1)

    def test_unknown_player_falls_back_to_safe_generic(self) -> None:
        profile = base_players.face_profile(999999)
        self.assertFalse(profile["real"])
        self.assertEqual(profile["hashighqualityhead"], 0)
        self.assertEqual(profile["headclasscode"], 1)
        self.assertEqual(profile["headassetid"], 0)

    def test_gender_uses_ea_encoding(self) -> None:
        # EA: 0 = male. FUT.GG uses 1 = male, which is the bug source.
        self.assertEqual(base_players.gender_of(158023), 0)

    def test_full_payload_is_substantial(self) -> None:
        self.assertGreater(len(base_players.full_payload(1397)), 100)


class TestFaceWiring(unittest.TestCase):
    """Generated Lua must carry the real face quartet, not guesses."""

    @classmethod
    def setUpClass(cls) -> None:
        if not paths.base_players_csv_path().is_file():
            raise unittest.SkipTest("base_players.csv not present")

    def _render(self, face_id: int) -> str:
        return add_team_lua.render_add_to_team_lua(
            name="Test",
            first="Test",
            sur="Player",
            jersey="Player",
            teamid=5,
            mode="auto",
            preferred_create_id=470000,
            player_row={"overallrating": "88"},
            field_updates=[("overallrating", 88)],
            dummy_ids=[100, 101],
            transfersum=0,
            wage=1000,
            contract_months=36,
            age=30,
            fallback_birthdate="150000",
            use_real_face=True,
            base_face_id=face_id,
            nation_id=None,
            gen_min=add_player.GENERATED_ID_MIN,
            gen_max=add_player.GENERATED_ID_MAX,
            crash_log_path="C:/tmp/crash.log",
            status_path="C:/tmp/status.txt",
        )

    def test_non_hq_player_does_not_get_forced_hq(self) -> None:
        lua = self._render(1397)  # Zidane, hq=0
        self.assertIn("local FACE_HQ = 0", lua)
        self.assertIn("local FACE_HEADTYPE = 2502", lua)

    def test_hq_player_keeps_hq(self) -> None:
        lua = self._render(158023)  # Messi, hq=1
        self.assertIn("local FACE_HQ = 1", lua)

    def test_hashighqualityhead_is_never_hardcoded(self) -> None:
        lua = self._render(1397)
        self.assertNotIn('hashighqualityhead = "1"', lua)


class TestNameInsert(unittest.TestCase):
    """editedplayernames must be upserted once and validated."""

    def _lua(self) -> str:
        return add_team_lua.render_add_to_team_lua(
            name="Zinedine Zidane",
            first="Zinedine",
            sur="Zidane",
            jersey="Zidane",
            teamid=5,
            mode="auto",
            preferred_create_id=470000,
            player_row={"overallrating": "91"},
            field_updates=[("overallrating", 91)],
            dummy_ids=[100],
            transfersum=0,
            wage=1000,
            contract_months=36,
            age=30,
            fallback_birthdate="150000",
            use_real_face=False,
            base_face_id=0,
            nation_id=None,
            gen_min=add_player.GENERATED_ID_MIN,
            gen_max=add_player.GENERATED_ID_MAX,
            crash_log_path="C:/tmp/crash.log",
            status_path="C:/tmp/status.txt",
        )

    def test_existing_rows_are_updated_before_insert(self) -> None:
        lua = self._lua()
        self.assertIn("name_existing", lua)
        self.assertIn("if existing == 0 then", lua)

    def test_insert_result_is_validated_not_just_pcall_status(self) -> None:
        # LE returns a row whose addr is "0" on failure; pcall still succeeds.
        lua = self._lua()
        self.assertIn("name_insert_fail", lua)
        self.assertIn('addr ~= "0"', lua)


class TestAutorunArming(unittest.TestCase):
    """LE auto-executes lua/autorun at startup: no manual paste required."""

    def test_data_dir_resolves(self) -> None:
        candidates = paths.le_autorun_dirs()
        self.assertTrue(candidates, "no autorun candidate directories resolved")
        self.assertTrue(any("autorun" in str(p).lower() for p in candidates))

    def test_stub_arms_safely_without_draining(self) -> None:
        stub = le_apply._AUTORUN_STUB
        self.assertIn("__LE_COMPANION_SAFE_ARM", stub)
        self.assertIn("dofile", stub)
        # Guard against re-entry if LE scans more than one root.
        self.assertIn("__LE_COMPANION_AUTORUN_DONE", stub)

    def test_status_reports_candidates(self) -> None:
        st = le_apply.autorun_status()
        self.assertIn("candidates", st)
        self.assertIn("bridge", st)


class TestBridgeEventNames(unittest.TestCase):
    """FCLiveEditor.DLL fires exactly four event names; the rest never dispatch."""

    REAL_EVENTS = {
        "pre__CareerModeEvent",
        "post__CareerModeEvent",
        "pre__LEInitDoneEvent",
        "post__LEInitDoneEvent",
    }
    INVENTED = (
        "postCareerModeLoadedFromSave",
        "eventCareerModeHubEntered",
        "eventPostMatch",
    )

    def _bridge_text(self) -> str:
        src = paths.bridge_source_path()
        if not src.is_file():
            self.skipTest("bridge source not found")
        return src.read_text(encoding="utf-8", errors="replace")

    def test_no_invented_events_are_registered(self) -> None:
        text = self._bridge_text()
        for name in self.INVENTED:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("--"):
                    continue  # explanatory comment is fine
                self.assertNotIn(
                    f'"{name}"', stripped, f"{name} does not exist in FCLiveEditor.DLL"
                )

    def test_real_career_event_is_registered(self) -> None:
        text = self._bridge_text()
        self.assertIn('"post__CareerModeEvent"', text)

    def test_init_done_event_is_registered(self) -> None:
        text = self._bridge_text()
        self.assertIn("post__LEInitDoneEvent", text)

    def test_handler_receives_event_id(self) -> None:
        """A no-arg handler drains on all 276 message types indiscriminately."""
        text = self._bridge_text()
        self.assertIn("local function on_cm(_mgr, event_id)", text)

    def test_save_events_are_blacked_out(self) -> None:
        """Draining while the game serialises a save can land a partial write in it."""
        text = self._bridge_text()
        for name in (
            "ENUM_CM_EVENT_MSG_PREPARE_FOR_SAVE",
            "ENUM_CM_EVENT_MSG_PREPARE_FOR_FIRST_SAVE",
            "ENUM_CM_EVENT_MSG_POST_LOAD_PREPARE",
        ):
            self.assertIn(name, text, f"{name} must be in the drain blackout")
        self.assertIn("BLACKOUT", text)

    def test_blackout_ids_match_le_enums(self) -> None:
        """The numeric fallback must agree with the shipped enum table."""
        enums = (
            paths.le_root() / "lua" / "libs" / "v2" / "imports" / "career_mode" / "enums.lua"
        )
        if not enums.is_file():
            self.skipTest("LE enums.lua not present")
        text = enums.read_text(encoding="utf-8", errors="replace")
        expected = {
            "ABOUT_TO_INIT_MODE": 5,
            "SEASON_RESET": 23,
            "DATA_READY": 27,
            "PREPARE_FOR_SAVE": 28,
            "POST_LOAD_PREPARE": 29,
            "PREPARE_FOR_FIRST_SAVE": 30,
        }
        for name, want in expected.items():
            needle = f"ENUM_CM_EVENT_MSG_{name} = {want}"
            self.assertIn(needle, text, f"LE enum drift: expected {needle}")


class TestGenderMapping(unittest.TestCase):
    """FUT.GG uses 1 = male; EA uses 0 = male. Never pass it through."""

    def test_futgg_male_maps_to_ea_male(self) -> None:
        from src import futgg_client

        src_file = Path(futgg_client.__file__).read_text(encoding="utf-8", errors="replace")
        self.assertNotIn('"gender": item.get("gender"),', src_file)
        self.assertIn('"gender": 0 if item.get("gender") in (None, 1) else 1', src_file)


if __name__ == "__main__":
    unittest.main()
