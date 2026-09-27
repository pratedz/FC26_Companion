"""Feature modules: favorites, history, presets, compare, undo, health."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import card_compare  # noqa: E402
from src import editor_presets  # noqa: E402
from src import favorites  # noqa: E402
from src import health_check  # noqa: E402
from src import job_history  # noqa: E402
from src import undo_apply  # noqa: E402


class FeatureModulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._p = mock.patch("src.paths.app_root", return_value=self.root)
        self._p.start()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_favorites_toggle(self) -> None:
        card = {"name": "Messi", "playerid": 158023, "year": "26", "overallrating": 93}
        self.assertTrue(favorites.toggle(card))
        self.assertTrue(favorites.is_favorite(card))
        self.assertFalse(favorites.toggle(card))
        self.assertFalse(favorites.is_favorite(card))

    def test_job_history(self) -> None:
        job_history.add(kind="apply", detail="test", outcome="applied", target_id=1)
        rows = job_history.load()
        self.assertEqual(len(rows), 1)
        self.assertIn("applied", job_history.format_line(rows[0]))

    def test_presets(self) -> None:
        c = editor_presets.apply_preset("max_99")
        self.assertEqual(c["overallrating"], 99)
        self.assertEqual(c["acceleration"], 99)

    def test_compare(self) -> None:
        before = {"overallrating": 80, "acceleration": 70}
        after = {"overallrating": 90, "acceleration": 99, "finishing": 88}
        lines = card_compare.format_compare_lines(before, after)
        self.assertIn("→", lines)

    def test_undo_snapshot(self) -> None:
        undo_apply.save_snapshot(
            target_id=10, fields=[("acceleration", 90), ("sprintspeed", 91)], label="t"
        )
        lua = undo_apply.generate_undo_lua()
        self.assertIn("target_playerid = 10", lua)
        self.assertIn("acceleration", lua)

    def test_health_report(self) -> None:
        (self.root / "card_db").mkdir()
        rep = health_check.format_report()
        self.assertIn("App:", rep)


if __name__ == "__main__":
    unittest.main()
