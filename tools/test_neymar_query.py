"""Query Neymar (and special-looking) cards across years 18-26 + local LE presets."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import card_catalog  # noqa: E402


SPECIAL_KW = (
    "icon",
    "hero",
    "toty",
    "tots",
    "birthday",
    "flashback",
    "shapeshifter",
    "future",
    "winter",
    "summer",
    "sbc",
    "promo",
    "special",
    "moments",
    "showdown",
    "thunder",
    "fantasy",
    "rulebreakers",
    "inform",
    "if ",
    "rare gold",
    "gold rare",
)


def _ovr(c) -> int:
    try:
        return int(c.get("overallrating") or 0)
    except (TypeError, ValueError):
        return 0


def _blob(c) -> str:
    return f"{c.get('revision') or ''} {c.get('origin') or ''} {c.get('name') or ''}".lower()


def main() -> int:
    print("Loading catalog...")
    cards = card_catalog.load_card_db(include_local=True, include_futbin=True)
    print(f"Total cards loaded: {len(cards)}")
    yc = Counter(str(c.get("year")) for c in cards)
    print("Years:", dict(sorted(yc.items(), key=lambda x: str(x[0]))))

    print("\n=== NEYMAR hits by year ===")
    for y in ["18", "19", "20", "21", "22", "23", "24", "25", "26", "local", "futbin"]:
        hits = card_catalog.search_cards("Neymar", year=y, cards=cards, limit=25)
        print(f"-- year={y} count={len(hits)}")
        for i, c in enumerate(hits[:15]):
            src = Path(str(c.get("source") or "")).name
            print(
                f"  [{i}] name={c.get('name')!r} OVR={c.get('overallrating')} "
                f"rev={c.get('revision')!r} origin={c.get('origin')!r} src={src}"
            )

    print("\n=== NEYMAR special-looking (rev/origin keywords OR OVR>=90) ===")
    all_n = card_catalog.search_cards("Neymar", year=None, cards=cards, limit=300)
    specials = []
    for c in all_n:
        if _ovr(c) >= 90 or any(k in _blob(c) for k in SPECIAL_KW):
            specials.append(c)
    print(f"special-ish: {len(specials)} / {len(all_n)} Neymar-name hits")
    for c in specials[:50]:
        src = Path(str(c.get("source") or "")).name
        print(
            f"  y={c.get('year')} OVR={c.get('overallrating')} "
            f"rev={c.get('revision')!r} origin={c.get('origin')!r} "
            f"name={c.get('name')!r} src={src}"
        )

    # Local LE FUT presets (often have specials)
    print("\n=== LOCAL LE presets: Neymar ===")
    local = card_catalog.search_cards("Neymar", year="local", cards=cards, limit=30)
    for i, c in enumerate(local):
        src = Path(str(c.get("source") or "")).name
        print(
            f"  [{i}] OVR={c.get('overallrating')} rev={c.get('revision')!r} "
            f"origin={c.get('origin')!r} name={c.get('name')!r} pid={c.get('playerid')} src={src}"
        )

    # Sample other icons/heroes in local if any
    print("\n=== LOCAL sample Icons/Heroes (name or revision) ===")
    local_all = [c for c in cards if str(c.get("year")) in ("local", "fc26-local", "26")]
    ih = [
        c
        for c in local_all
        if any(k in _blob(c) for k in ("icon", "hero"))
    ][:20]
    print(f"local-ish icon/hero sample count shown: {len(ih)}")
    for c in ih:
        print(
            f"  y={c.get('year')} OVR={c.get('overallrating')} "
            f"rev={c.get('revision')!r} name={c.get('name')!r}"
        )

    # Honest summary
    print("\n=== SUMMARY ===")
    by_year = Counter(str(c.get("year")) for c in all_n)
    print("Neymar hits by year:", dict(sorted(by_year.items())))
    has_26 = any(str(c.get("year")) == "26" for c in all_n)
    has_special_rev = any(
        any(k in _blob(c) for k in ("icon", "hero", "toty", "tots", "birthday"))
        for c in all_n
    )
    print("Has year=26 Neymar:", has_26)
    print("Has explicit Icon/Hero/TOTY/TOTS/Birthday keyword on Neymar rows:", has_special_rev)
    print(
        "NOTE: SoFIFA-style year dumps are mostly career/base rosters; "
        "special FUT promos are mainly in LE player_presets/cards.csv (year=local) "
        "or Futbin import, not in plain fifaXX.csv files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
