"""Verify search+apply for years 18-25."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import card_catalog, card_to_lua  # noqa: E402


def main() -> int:
    ok = True
    for y in ["18", "19", "20", "21", "22", "23", "24", "25"]:
        hits = card_catalog.search_cards("Messi", year=y, limit=5)
        # FC25 dump may not include Messi depending on scrape date
        if not hits and y == "25":
            hits = card_catalog.search_cards("Mbappe", year=y, limit=3)
            if not hits:
                hits = card_catalog.search_cards("Mbappé", year=y, limit=3)
        print(f"YEAR {y}: {len(hits)} hits")
        if not hits:
            print("  WARNING: no hits")
            if y != "25":
                ok = False
            continue
        c = hits[0]
        lua = card_to_lua.generate_apply_card_lua(c, 158023)
        keys = ["acceleration", "sprintspeed", "finishing", "overallrating", "dribbling"]
        present = {k: (k in lua) for k in keys}
        name = str(c.get("name") or "")[:48]
        print(f"  name={name!r} ovr={c.get('overallrating')} {present} len={len(lua)}")
        if not present["overallrating"] or not present["acceleration"]:
            ok = False
            print("  FAIL missing core attrs")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
