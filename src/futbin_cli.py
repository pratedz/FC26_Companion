"""Optional CLI for futbin_client module."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .futbin_http import (
    FutbinClient,
    fetch_player_url,
    import_from_html,
    import_from_json,
    probe_futbin,
    save_cookie_instructions,
)
from .futbin_models import (
    COOKIE_PASTE_INSTRUCTIONS,
    FutbinBlockedError,
    FutbinPlayer,
)
from .futbin_parse import import_html_file, import_json, to_le_card_row

def _cli(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Futbin client for LE Profile Executor")
    parser.add_argument("--probe", action="store_true", help="Probe live Futbin access")
    parser.add_argument("--html", type=str, help="Parse saved player HTML file")
    parser.add_argument("--json", type=str, help="Import player JSON dump")
    parser.add_argument("--url", type=str, help="Live or archive player URL")
    parser.add_argument("--cookies", type=str, help="Cookie JSON / Netscape file")
    parser.add_argument("--archive", action="store_true", help="Try archive.org if live fails")
    parser.add_argument("--year", type=int, default=26)
    parser.add_argument("--image", type=int, help="Print CDN image URL for resource id")
    parser.add_argument("--le-row", action="store_true", help="Print LE cards.csv row JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    client = FutbinClient(cookie_file=args.cookies) if args.cookies else FutbinClient()

    if args.image is not None:
        print(client.image_url(args.image, args.year))
        return 0

    if args.probe:
        print(json.dumps(client.probe_live(args.year), indent=2))
        # CDN sanity
        img = client.image_url(158023, args.year)
        try:
            import requests

            r = requests.get(img, timeout=15)
            print(json.dumps({"cdn_image": img, "status": r.status_code, "bytes": len(r.content)}, indent=2))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"cdn_image": img, "error": str(e)}, indent=2))
        return 0

    player: Optional[FutbinPlayer] = None
    if args.json:
        player = import_json(args.json)
    elif args.html:
        player = import_html_file(args.html, source_url=args.url or "")
    elif args.url:
        try:
            player = client.fetch_player(url=args.url, try_archive=args.archive)
        except FutbinBlockedError as e:
            print(json.dumps({"error": str(e), "hint": "use --html/--json or --cookies"}, indent=2))
            return 2
    else:
        parser.print_help()
        print("\n" + COOKIE_PASTE_INSTRUCTIONS)
        return 1

    assert player is not None
    payload = player.to_dict()
    if args.le_row:
        print(json.dumps({"player": payload, "le_card_row": to_le_card_row(player)}, indent=2))
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
