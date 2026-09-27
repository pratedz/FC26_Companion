"""Multi-year FUT.GG dumps are first-class library sources."""

from __future__ import annotations

import json
from pathlib import Path

from src.card_catalog import load_futgg_cache
from src.futgg_client import year_jsonl_path


ROOT = Path(__file__).resolve().parents[1]


def test_year_jsonl_paths_and_existing_26_dump():
    p26 = year_jsonl_path(26)
    assert p26.name == "futgg_26.jsonl"
    assert year_jsonl_path(25).name == "futgg_25.jsonl"
    # FC26 bulk dump ships with the repo workspace after prior sync.
    if p26.is_file():
        # First line should be a LE-normalized object with year/playerid.
        line = p26.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        obj = json.loads(line)
        assert isinstance(obj, dict)
        assert obj.get("playerid") or obj.get("basePlayerEaId")


def test_load_futgg_cache_accepts_any_year_glob(tmp_path: Path):
    root = tmp_path / "futgg"
    root.mkdir()
    for year, n in ((25, 2), (24, 1)):
        path = root / f"futgg_{year}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(
                    json.dumps(
                        {
                            "playerid": 1000 + year + i,
                            "name": f"Player{year}_{i}",
                            "year": year,
                            "overallrating": 90 - i,
                            "revision": "Team of the Season",
                        }
                    )
                    + "\n"
                )
    # Point card_db root at tmp_path parent layout: load_futgg_cache expects card_db/futgg
    card_db = tmp_path
    cards = load_futgg_cache(card_db, years={24, 25})
    assert len(cards) == 3
    years = {str(c.get("year") or c.get("_year") or "") for c in cards}
    assert "24" in years or 24 in {c.get("year") for c in cards} or any(
        "24" in str(c.get("year")) for c in cards
    )


def test_build_universe_load_futgg_is_multiyear():
    src = (ROOT / "tools" / "build_universe.py").read_text(encoding="utf-8")
    assert "futgg_*.jsonl" in src
    assert "def _load_futgg_file" in src
    assert 'glob("futgg_*.jsonl")' in src


def test_cli_registers_sync_futgg_and_rebuild():
    from companion.cli import build_parser

    p = build_parser()
    assert p.parse_args(["sync-futgg", "--years", "25"]).years == "25"
    assert p.parse_args(["rebuild-catalog"]).cmd == "rebuild-catalog"
