"""Probe online FC card data sources."""
from __future__ import annotations

import json
import re
import sys

from curl_cffi import requests


def main() -> int:
    r = requests.get(
        "https://www.fut.gg/players/?q=Messi",
        impersonate="chrome131",
        timeout=30,
    )
    print("status", r.status_code, "len", len(r.text))
    urls = sorted(set(re.findall(r"https?://[^\s\"'<>]{8,160}", r.text)))
    for u in urls:
        low = u.lower()
        if any(k in low for k in ("api", "player", "graphql", "cdn", "json", "data")):
            print("URL", u)

    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S
    )
    print("NEXT_DATA", bool(m), len(m.group(1)) if m else 0)
    if m:
        data = json.loads(m.group(1))
        print("NEXT keys", list(data.keys())[:20])
        print(json.dumps(data, indent=0)[:1500])

    paths = sorted(
        set(
            re.findall(
                r"[\"'](/[^\"']*(?:api|player|search|fut)[^\"']*)[\"']",
                r.text,
                re.I,
            )
        )
    )
    print("paths", paths[:80])

    idx = r.text.lower().find("messi")
    print("messi idx", idx)
    if idx > 0:
        print(r.text[max(0, idx - 80) : idx + 200])

    # Try common FUT.GG API patterns
    candidates = [
        "https://www.fut.gg/api/fut/players/?search=Messi&game=26",
        "https://www.fut.gg/api/players?search=Messi",
        "https://www.fut.gg/api/v1/players?search=Messi",
        "https://www.fut.gg/api/fut/26/players/?search=Messi",
        "https://game-assets.fut.gg/cdn-cgi/image/",
        "https://www.fut.gg/players/messi/",
        "https://www.fut.gg/api/search?q=Messi",
    ]
    for url in candidates:
        try:
            rr = requests.get(
                url,
                impersonate="chrome131",
                timeout=20,
                headers={"Accept": "application/json, text/html, */*"},
            )
            print(
                "TRY",
                rr.status_code,
                rr.headers.get("content-type", "")[:40],
                url,
                (rr.text or "")[:120].replace("\n", " "),
            )
        except Exception as e:
            print("TRY ERR", url, e)

    # Player page from HTML link if present
    links = re.findall(r'href="(/players/[^"]+)"', r.text)
    print("player links", links[:20])
    if links:
        purl = "https://www.fut.gg" + links[0]
        pr = requests.get(purl, impersonate="chrome131", timeout=30)
        print("player page", purl, pr.status_code, len(pr.text))
        m2 = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pr.text, re.S
        )
        print("player NEXT", bool(m2))
        if m2:
            print(m2.group(1)[:2000])
        # look for stats numbers near labels
        for label in ("Pace", "Shooting", "Acceleration", "Sprint Speed", "overall"):
            i = pr.text.find(label)
            if i > 0:
                print(label, pr.text[i : i + 120].replace("\n", " "))

    return 0


if __name__ == "__main__":
    sys.exit(main())
