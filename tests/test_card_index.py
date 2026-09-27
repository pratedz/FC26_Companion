"""SQLite card index build + search."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import card_catalog  # noqa: E402
from src import card_index  # noqa: E402


class CardIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "card_db"
        self.db.mkdir()
        # minimal CSV
        csv_path = self.db / "fifa25.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["name", "overallrating", "playerid", "acceleration", "sprintspeed"],
            )
            w.writeheader()
            w.writerow(
                {
                    "name": "Test Neymar",
                    "overallrating": "91",
                    "playerid": "190871",
                    "acceleration": "95",
                    "sprintspeed": "94",
                }
            )
            w.writerow(
                {
                    "name": "Other Player",
                    "overallrating": "80",
                    "playerid": "1",
                    "acceleration": "70",
                    "sprintspeed": "70",
                }
            )
        self._p_root = mock.patch("src.paths.app_root", return_value=self.root)
        self._p_db = mock.patch("src.paths.card_db_dir", return_value=self.db)
        self._p_cards = mock.patch(
            "src.paths.cards_csv_path", return_value=self.root / "missing_cards.csv"
        )
        self._p_root.start()
        self._p_db.start()
        self._p_cards.start()
        card_catalog.clear_catalog_cache()
        card_index.invalidate()

    def tearDown(self) -> None:
        self._p_root.stop()
        self._p_db.stop()
        self._p_cards.stop()
        self._tmp.cleanup()

    def test_rebuild_and_search(self) -> None:
        # Index always uses full default scope (search never rebuilds a year subset)
        scope = card_index._default_years_scope(self.db)
        info = card_index.rebuild_index(self.db, years=scope, force=True)
        self.assertTrue(info["ok"])
        self.assertGreaterEqual(info["count"], 2)
        hits = card_index.search("neymar", year="25", limit=10, card_db=self.db)
        self.assertIsNotNone(hits)
        assert hits is not None
        self.assertTrue(any("Neymar" in str(h.get("name")) for h in hits))
        # second rebuild is cache hit (same fingerprint)
        info2 = card_index.rebuild_index(self.db, years=scope, force=False)
        self.assertTrue(info2.get("cached"))

    def test_search_cards_uses_index(self) -> None:
        scope = card_index._default_years_scope(self.db)
        card_index.rebuild_index(self.db, years=scope, force=True)
        hits = card_catalog.search_cards("neymar", year="25", limit=10, card_db=self.db)
        self.assertTrue(hits)
        self.assertIn("Neymar", hits[0].get("name", ""))

    def test_search_without_index_returns_none_fast(self) -> None:
        """Search must not rebuild multi-year index on the hot path."""
        # ensure no sqlite
        card_index.invalidate()
        t0 = __import__("time").perf_counter()
        hits = card_index.search("neymar", year="25", limit=10, card_db=self.db)
        elapsed = __import__("time").perf_counter() - t0
        self.assertIsNone(hits)
        self.assertLess(elapsed, 1.0)

    def test_search_year_filter_sql(self) -> None:
        # Add a year-26 row via second csv
        csv26 = self.db / "fc26_datahub.csv"
        with csv26.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["name", "overallrating", "playerid", "acceleration", "sprintspeed"],
            )
            w.writeheader()
            w.writerow(
                {
                    "name": "Year26 Only",
                    "overallrating": "99",
                    "playerid": "999",
                    "acceleration": "99",
                    "sprintspeed": "99",
                }
            )
        scope = card_index._default_years_scope(self.db)
        card_index.rebuild_index(self.db, years=scope, force=True)
        hits26 = card_index.search("Year26", year="26", limit=10, card_db=self.db)
        hits25 = card_index.search("Year26", year="25", limit=10, card_db=self.db)
        self.assertIsNotNone(hits26)
        assert hits26 is not None
        self.assertTrue(any("Year26" in str(h.get("name")) for h in hits26))
        self.assertIsNotNone(hits25)
        assert hits25 is not None
        self.assertFalse(any("Year26" in str(h.get("name")) for h in hits25))


if __name__ == "__main__":
    unittest.main()
