"""Broader probe for bulk FUT player databases."""
from __future__ import annotations

import re
import json
from curl_cffi import requests

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/html,*/*"}


def try_get(url: str) -> None:
    try:
        r = requests.get(url, impersonate="chrome131", timeout=20, headers=UA)
        ct = r.headers.get("content-type", "")
        body = (r.text or "")[:180].replace("\n", " ")
        print(f"{r.status_code:3} {ct[:35]:35} {url[:85]}")
        print("   ", body)
    except Exception as e:
        print("ERR", url[:85], e)


def main() -> None:
    # FUT.GG main bundle
    r = requests.get(
        "https://assets.fut.gg/ts/assets/main-D0Fg3gMx.js",
        impersonate="chrome131",
        timeout=40,
        headers=UA,
    )
    print("main.js", r.status_code, len(r.text))
    t = r.text
    urls = sorted(set(re.findall(r"https?://[a-zA-Z0-9._\-/]{8,100}", t)))
    for u in urls:
        if any(k in u.lower() for k in ("api", "cdn", "asset", "data", "player", "gg")):
            print("MAINU", u)

    for m in re.finditer(r".{0,20}/api/[a-zA-Z0-9_\-./?=&]{3,80}", t):
        print("MAINAPI", m.group(0)[:140])

    # Known historical FUT web app endpoints (EA)
    candidates = [
        "https://www.easports.com/uk/fifa/ultimate-team/web-app/",
        "https://utas.mob.v5.prd.futc-ext.gcp.ea.com/ut/game/fc26/def?type=player",
        "https://www.futbin.com/25/players?page=1",
        "https://www.futbin.com/24/players?page=1",
        "https://www.futbin.com/23/players?page=1",
        "https://www.fut.gg/players/neymar-jr/",
        "https://www.fut.gg/players/?q=Neymar",
        "https://www.fut.gg/players/?search=Neymar",
        "https://www.fut.gg/players/new/",
        "https://cdn.jsdelivr.net/gh/",
        # FUTBIN mobile-ish
        "https://www.futbin.com/26/playerPrices?player=190871",
        "https://www.futbin.com/getPlayerCardJSON?player=190871&year=26",
        "https://www.futbin.com/26/exportPlayerPrices?player=190871",
        # archive.org snapshots of futbin dump?
        "https://web.archive.org/web/2024/https://www.futbin.com/25/players",
    ]
    for u in candidates:
        try_get(u)

    # FUT.GG search page HTML for Neymar link
    rr = requests.get(
        "https://www.fut.gg/players/?q=Neymar",
        impersonate="chrome131",
        timeout=30,
        headers=UA,
    )
    print("search page", rr.status_code, len(rr.text))
    links = re.findall(r'href="(/players/[^"]*neymar[^"]*)"', rr.text, re.I)
    print("neymar links", links[:20])
    links2 = re.findall(r'href="(/players/[^"]+)"', rr.text)
    print("any player links", links2[:30])
    # look for embedded player JSON
    if "Neymar" in rr.text:
        idx = rr.text.find("Neymar")
        print("context", rr.text[max(0, idx - 100) : idx + 200].replace("\n", " "))


if __name__ == "__main__":
    main()
