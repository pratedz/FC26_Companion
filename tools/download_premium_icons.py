"""Download premium line icons (Icons8 fluency-systems-regular, cyan) into assets/icons.

Run: python tools/download_premium_icons.py
Does not invent artwork — pulls public CDN PNGs.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icons"
COLOR = "22d3ee"  # ACCENT cyan
SIZE = 64
BASE = f"https://img.icons8.com/fluency-systems-regular/{SIZE}/{COLOR}"

# Local stem -> Icons8 icon id
MAP = {
    "search": "search",
    "apply": "checkmark",
    "run": "play",
    "target": "center-direction",
    "download": "download",
    "bridge": "cable",
    "copy": "copy",
    "help": "help",
    "queue": "list",
    "refresh": "refresh",
    "back": "back",
    "forward": "forward",
    "favorite": "star",
    "star": "star",
    "export": "export",
    "import": "import",
    "shield_check": "checked-shield",
    "clipboard": "clipboard",
    "fitness": "dumbbell",
    "card": "bank-card-back-side",
    "edit": "edit",
    "edit_box": "edit-property",
    "ai": "artificial-intelligence",
    "connect": "connected",
    "home": "home",
    "medal": "medal2",
    "contract": "document",
    "unlock": "unlock",
    "lock": "lock",
    "energy": "flash-on",
    "status_online": "online",
    "status_offline": "offline",
    "trash": "trash",
    "filter": "filter",
    "user": "user",
    "settings": "settings",
    "info": "info",
    "warning": "error",
    "success": "ok",
    "close": "close",
    "add": "plus-math",
    "minus": "minus-math",
    "save": "save",
    "folder": "folder-invoices",
    "globe": "globe",
    "link": "link",
    "message": "speech-bubble",
    "loading": "spinner-frame-5",
    "progress": "progress-indicator",
    "trophy": "trophy",
    "team": "conference-call",
    "player": "person-male",
    "squad": "people",
    "terminal": "console",
    "code": "source-code",
    "key": "key",
    "pin": "map-pin",
    "location": "marker",
    "calendar": "calendar",
    "chart": "combo-chart",
    "sort": "alphabetical-sorting",
    "eye": "visible",
    "eye_off": "invisible",
    "logout": "exit",
    "ban": "cancel",
    "paste": "paste",
    "tip": "idea",
    "whistle": "whistle",
    "flag": "flag",
    "transfer": "data-transfer",
    "browser": "internet",
    "boot": "soccer-ball",
    "jersey": "t-shirt",
    "kit": "clothes",
    "playstyle": "lightning-bolt",
    "potential": "increase",
    "ovr": "circled",
    "pace": "running",
    "shoot": "goal",
    "pass": "pass",
    "dribble": "dribbling",
    "defend": "security-checked",
    "physical": "muscle",
    "gk": "goalkeeper",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LECompanion/1.11",
            "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for stem, ico in MAP.items():
        url = f"{BASE}/{ico}.png"
        dest = OUT / f"{stem}.png"
        try:
            data = fetch(url)
            if len(data) < 100 or data[:4] != b"\x89PNG":
                # try fallback without color path style
                url2 = f"https://img.icons8.com/ios-glyphs/{SIZE}/{COLOR}/{ico}.png"
                data = fetch(url2)
            if data[:4] != b"\x89PNG":
                raise ValueError(f"not png ({len(data)} bytes)")
            dest.write_bytes(data)
            ok += 1
            print(f"OK  {stem:16} <- {ico}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"FAIL {stem:16} {e}")
    # Remove heavy photo-style app mark so UI never loads it
    for junk in ("app.png", "app_alt.png"):
        p = OUT / junk
        if p.is_file():
            # Keep backup once
            bak = OUT / f"{junk}.bak_photo"
            if not bak.is_file():
                p.replace(bak)
            elif p.is_file():
                p.unlink(missing_ok=True)
            print(f"REMOVED heavy logo {junk}")
    print(f"\nDone: {ok} ok, {fail} fail → {OUT}")
    return 0 if fail < len(MAP) // 2 else 1


if __name__ == "__main__":
    sys.exit(main())
