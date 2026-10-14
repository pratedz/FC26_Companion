#!/usr/bin/env python3
"""Verify Sign's card collections against an installed Library.

Checks, for ``<install>/card_db/universe.sqlite``:

* every FC26/FC27 FUT.GG card label maps to a named collection;
* every requested FC26 collection has FC26 cards, except collections that the
  complete FC26 FUT.GG dump proves never ran in FC26;
* the Library's SQL collection filter returns exactly the cards the Python
  classifier puts in that collection.

Prints ``COLLECTIONS VERIFIED`` only when all of that holds.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion.domain import card_collections as cc  # noqa: E402
from companion.domain.catalog import LocalCatalog  # noqa: E402

#: The owner's FC26 list, in their words, mapped to Sign's collection names.
REQUESTED = {
    "Cornerstones": "Cornerstones",
    "Ratings Reload": "Ratings Reload",
    "Ultimate Scream": "Ultimate Scream",
    "UEFA Primetime": "UEFA Primetime",
    "FUT Captains": "FUT Captains",
    "FC Pro Live": "FC Pro Live",
    "Black Friday / Thunderstruck": "Black Friday / Thunderstruck",
    "Joga Bonito": "Joga Bonito",
    "Unbreakables": "Unbreakables",
    "Winter Wildcards": "Winter Wildcards",
    "Time Warp": "Time Warp",
    "Team of the Year (TOTY)": "Team of the Year",
    "Future Stars": "Future Stars",
    "Knockout Royalty": "Knockout Royalty",
    "Fantasy FC": "Fantasy FC",
    "FUT Birthday": "FUT Birthday",
    "Festival of Football: Answer the Call": "FOF: Answer the Call",
    "UEFA Road to the Final (RTTF)": "Road to the Final",
    "Trophy Titans": "Trophy Titans",
    "Team of the Season (TOTS)": "Team of the Season",
    "FOF: Path to Glory": "FOF: Path to Glory",
    "FOF: Greats of the Game": "FOF: Greats of the Game",
    "FOF: Glory Hunters": "FOF: Glory Hunters",
    "FOF: Phenoms": "FOF: Phenoms",
    "FOF: Summer Stars": "FOF: Summer Stars",
    "Shapeshifters": "Shapeshifters",
    "FUTTIES": "FUTTIES",
    "Ultimate Rewind": "Ultimate Rewind",
    "Pre-Season": "Pre-Season",
    "Team of the Week (TOTW)": "Team of the Week",
    "Base & Prime Heroes": cc.BASE_HEROES,
    "Base Icons": cc.BASE_ICONS,
    "Base Gold": cc.BASE_GOLD,
    "Base Silver": cc.BASE_SILVER,
    "Base Bronze": cc.BASE_BRONZE,
    "Sudamericana & Libertadores": "Libertadores & Sudamericana",
    "Flashback": "Flashback",
    "Moments": "Moments",
    "Showdown": "Showdown",
    "End of an Era": "End of an Era",
    "Player of the Month (POTM)": "Player of the Month",
    "Squad Foundations": "Squad Foundations",
}

#: Campaigns with no FC26 item on FUT.GG.  Checked below against the complete
#: dump, so a later FUT.GG addition fails this script instead of hiding.
NOT_IN_FC26 = {"Shapeshifters", "Libertadores & Sudamericana"}


def _fail(message: str) -> None:
    print("COLLECTION CHECK FAILED:", message)
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    install = Path(argv[1]) if len(argv) > 1 else ROOT
    universe = install / "card_db" / "universe.sqlite"
    dump = ROOT / "card_db" / "futgg" / "futgg_26.jsonl"
    if not universe.is_file() or not dump.is_file():
        _fail(f"missing {universe if not universe.is_file() else dump}")

    # 1. Every FUT.GG label of a managed year has a named collection.
    with closing(sqlite3.connect(f"file:{universe.as_posix()}?mode=ro", uri=True)) as con:
        rows = con.execute(
            "SELECT year, variant, club_name, overall FROM observation "
            "WHERE year IN (26, 27) AND source='futgg'"
        ).fetchall()
        fc26_counts: Counter[str] = Counter()
        for year, variant, club, overall in con.execute(
                "SELECT year, variant, club_name, overall FROM observation WHERE year=26"):
            fc26_counts[cc.classify({"variant": variant, "club": club, "overall": overall}).collection] += 1
    unmapped = sorted({f"FC{y} {v}" for y, v, _c, _o in rows if not cc.label_class(v).mapped})
    if not rows:
        _fail("no FC26/FC27 FUT.GG cards in the installed Library")
    if unmapped:
        _fail("labels without a collection: " + "; ".join(unmapped[:20]))
    print(f"{len(rows)} FC26/FC27 FUT.GG cards, every label in a named collection")

    # 2. Every requested collection exists in FC26, or provably never ran.
    dump_counts: Counter[str] = Counter()
    with dump.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                card = json.loads(line)
                dump_counts[cc.classify(dict(card, variant=cc.campaign_label(card))).collection] += 1
    missing = []
    for asked, name in REQUESTED.items():
        count = fc26_counts.get(name, 0)
        if name in NOT_IN_FC26:
            if dump_counts.get(name, 0):
                _fail(f"{asked} now has {dump_counts[name]} FC26 FUT.GG cards; remove it from NOT_IN_FC26")
            print(f"  {asked:40} not in FC26 (0 of {sum(dump_counts.values())} FUT.GG FC26 cards)")
            continue
        print(f"  {asked:40} {count:6d} FC26 cards")
        if count == 0:
            missing.append(asked)
    if missing:
        _fail("no FC26 cards for: " + ", ".join(missing))

    # 3. The Library's SQL filter and the classifier agree on real data.
    catalog = LocalCatalog(universe_path=universe)
    for name in cc.ordered_collections(fc26_counts):
        found = catalog.search("", year="26", collection=name, limit=500)
        wrong = [r for r in found if cc.classify(r).collection != name]
        if not found or wrong:
            _fail(f"Library filter for {name}: {len(found)} rows, {len(wrong)} outside the collection")
    for kind in cc.KINDS:
        found = catalog.search("", year="27", kind=kind, limit=500)
        if not found or any(cc.classify(r).kind != kind for r in found):
            _fail(f"Library type filter for {kind} is inconsistent")
    print("COLLECTIONS VERIFIED", len(REQUESTED) - len(NOT_IN_FC26), "requested FC26 collections present;",
          "not run in FC26:", ", ".join(sorted(NOT_IN_FC26)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
