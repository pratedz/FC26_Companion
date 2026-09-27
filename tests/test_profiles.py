"""Unit tests for profiles loading/validation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Import REAL modules from the app package
APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import profiles  # noqa: E402
from src import snippets  # noqa: E402
from src.profiles import ProfileError  # noqa: E402


class TestProfiles(unittest.TestCase):
    def test_load_real_profiles_json(self):
        loaded = profiles.load_profiles()
        self.assertGreaterEqual(len(loaded), 5)
        ids = {p.id for p in loaded}
        self.assertIn("full_fitness", ids)
        self.assertIn("full_sharpness", ids)
        self.assertIn("export_user_squad", ids)
        exp = profiles.get_profile("export_user_squad")
        self.assertEqual(exp.kind, "app_script")

    def test_get_profile_full_fitness(self):
        p = profiles.get_profile("full_fitness")
        self.assertEqual(p.kind, "snippet")
        self.assertEqual(p.snippet_key, "full_fitness")
        self.assertIn(p.snippet_key, snippets.SNIPPETS)

    def test_list_summaries(self):
        rows = profiles.list_profile_summaries()
        self.assertTrue(any(r["id"] == "full_fitness" for r in rows))

    def test_unknown_profile(self):
        with self.assertRaises(KeyError):
            profiles.get_profile("does_not_exist_xyz")

    def test_invalid_snippet_key_rejected(self):
        bad = {
            "version": 1,
            "profiles": [
                {
                    "id": "broken",
                    "kind": "snippet",
                    "snippet_key": "not_a_real_snippet",
                    "label": "Broken",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profiles.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ProfileError):
                profiles.load_profiles(path)

    def test_duplicate_id_rejected(self):
        bad = {
            "version": 1,
            "profiles": [
                {
                    "id": "full_fitness",
                    "kind": "snippet",
                    "snippet_key": "full_fitness",
                },
                {
                    "id": "full_fitness",
                    "kind": "snippet",
                    "snippet_key": "full_sharpness",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profiles.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ProfileError):
                profiles.load_profiles(path)


if __name__ == "__main__":
    unittest.main()
