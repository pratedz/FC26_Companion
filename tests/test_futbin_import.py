"""Futbin offline import fixture → cache → searchable."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.futbin_client import import_from_json  # noqa: E402
from src import card_catalog  # noqa: E402
from src import paths  # noqa: E402


class TestFutbinImport(unittest.TestCase):
    def test_import_fixture_json_and_search(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "sample_futbin_card.json"
        self.assertTrue(fixture.is_file())
        obj = json.loads(fixture.read_text(encoding="utf-8"))
        cards = import_from_json(obj, year="26", save=True)
        self.assertGreaterEqual(len(cards), 1)
        # cache dir should exist
        cache = paths.card_db_dir() / "futbin"
        self.assertTrue(cache.is_dir())
        jsons = list(cache.glob("*.json"))
        self.assertTrue(jsons, "expected cached futbin json")
        # search should find by unique name
        hits = card_catalog.search_cards("Test Futbin Card", year="futbin", limit=10)
        if not hits:
            hits = card_catalog.search_cards("Test Futbin Card", year="26", limit=10)
        self.assertTrue(hits, "imported card should be searchable")
        self.assertTrue(str(hits[0].get("name") or ""))


if __name__ == "__main__":
    unittest.main()
