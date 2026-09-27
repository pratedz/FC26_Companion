"""Download FIFA/FC 18-25 card datasets into card_db/ (best-effort multi-source)."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD_DB = ROOT / "card_db"
CARD_DB.mkdir(parents=True, exist_ok=True)

# (dest_name, url)
SOURCES: list[tuple[str, str]] = [
    # kafagy face-stat history (includes 18-20)
    ("fifa18_futhead.csv", "https://raw.githubusercontent.com/kafagy/fifa-FUT-Data/master/FIFA18.csv"),
    ("fifa19_futhead.csv", "https://raw.githubusercontent.com/kafagy/fifa-FUT-Data/master/FIFA19.csv"),
    ("fifa20_futhead.csv", "https://raw.githubusercontent.com/kafagy/fifa-FUT-Data/master/FIFA20.csv"),
    ("fifa19_futbin_detailed.csv", "https://raw.githubusercontent.com/kafagy/fifa-FUT-Data/master/FutBinDetailed19.csv"),
    ("fifa19_futbin_cards.csv", "https://raw.githubusercontent.com/kafagy/fifa-FUT-Data/master/FutBinCards19.csv"),
    # SoFIFA-style full attribute sets
    ("fifa22_sofifa.csv", "https://raw.githubusercontent.com/abineshta/FIFA-22-complete-player-dataset-EDA/main/players_22.csv"),
    ("fifa23_official.csv", "https://raw.githubusercontent.com/rahulkumargit1/FIFA-Player-Rating-Prediction-with-Linear-Regression/master/FIFA23_official_data.csv"),
    # FC26 hub (bonus; useful for current-ish attrs)
    ("fc26_datahub.csv", "https://raw.githubusercontent.com/ismailoksuz/EAFC26-DataHub/main/data/players.csv"),
]

UA = "LE-Profile-Executor/1.0 (dataset download; +https://github.com/)"


def download(url: str, dest: Path) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 100:
            return False, f"too small ({len(data)} bytes)"
        dest.write_bytes(data)
        return True, f"{len(data)} bytes"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def main() -> int:
    print(f"Downloading into {CARD_DB}")
    ok_n = 0
    for name, url in SOURCES:
        dest = CARD_DB / name
        print(f"-> {name} ...", flush=True)
        ok, msg = download(url, dest)
        print(f"   {'OK' if ok else 'FAIL'}: {msg}")
        if ok:
            ok_n += 1
    # Write manifest of missing years for user clarity
    manifest = CARD_DB / "MANIFEST.txt"
    lines = [
        "Card DB download manifest",
        f"Succeeded: {ok_n}/{len(SOURCES)}",
        "",
        "Year coverage strategy:",
        "  18-20: kafagy FutHead dumps (face stats) + FIFA19 Futbin detailed",
        "  22: SoFIFA complete players_22",
        "  23: FIFA23_official_data",
        "  26: EAFC26 DataHub (bonus)",
        "  21/24/25: use Futbin import (HTML/JSON) or add CSVs named *21* *24* *25*",
        "  local LE: player_presets/cards.csv always loaded by catalog",
        "",
        "Drop any extra CSV into this folder; filenames containing 18-26 are auto-detected.",
        "Futbin upgrades: put JSON under card_db/futbin/ or use --import-futbin-html",
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(manifest.read_text(encoding="utf-8"))
    return 0 if ok_n else 1


if __name__ == "__main__":
    sys.exit(main())
