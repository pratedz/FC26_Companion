"""Probe FUT.GG for bulk player JSON endpoints."""
from __future__ import annotations

import json
import re
import sys

from curl_cffi import requests

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def main() -> int:
    r = requests.get("https://www.fut.gg/players/", impersonate="chrome131", timeout=30, headers=UA)
    print("status", r.status_code, "len", len(r.text))
    text = r.text

    # collect interesting URLs
    urls = sorted(set(re.findall(r"https?://[^\"'\\s>]{10,160}", text)))
    for u in urls:
        low = u.lower()
        if any(k in low for k in ("api", "player", "json", "cdn", "graphql", "data")):
            print("URL", u)

    paths = sorted(set(re.findall(r"[\"'](/[^\"']*(?:api|player|search|fut)[^\"']*)[\"']", text, re.I)))
    print("paths", paths[:80])

    # next data
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    print("NEXT_DATA", bool(m), len(m.group(1)) if m else 0)
    if m:
        print(m.group(1)[:2000])

    # try common patterns from HTML
    candidates = re.findall(r"[\"']([^\"']*players[^\"']*)[\"']", text)[:40]
    print("player strings sample", candidates[:20])

    # RSC / tanstack / other
    for pat in [
        r"window\.__[A-Z_]+__\s*=\s*(\{.*?\});",
        r'"totalPages"\s*:\s*(\d+)',
        r'"total_count"\s*:\s*(\d+)',
        r'"count"\s*:\s*(\d+)',
    ]:
        ms = re.findall(pat, text[:500000])
        print(pat, "->", ms[:10])

    # Try API guesses based on modern apps
    guesses = [
        "https://www.fut.gg/api/fut/26/players/?page=1",
        "https://www.fut.gg/api/26/players?page=1",
        "https://www.fut.gg/api/players/26?page=1",
        "https://www.fut.gg/players/data.json",
        "https://www.fut.gg/api/search?q=Neymar",
        "https://www.fut.gg/api/fut/players/search/?name=Neymar",
        "https://game-assets.fut.gg/",
        "https://www.fut.gg/api/meta/",
    ]
    # extract from script src
    scripts = re.findall(r'src="([^"]+\.js)"', text)
    print("scripts", scripts[:15])

    for u in guesses:
        try:
            rr = requests.get(u, impersonate="chrome131", timeout=15, headers=UA)
            print("TRY", rr.status_code, u, (rr.text or "")[:100].replace("\n", " "))
        except Exception as e:
            print("TRY ERR", u, e)

    # fetch a player page if link present
    links = re.findall(r'href="(/players/[^"]+)"', text)
    print("player links", links[:15])
    if links:
        purl = "https://www.fut.gg" + links[0]
        pr = requests.get(purl, impersonate="chrome131", timeout=30, headers=UA)
        print("player page", purl, pr.status_code, len(pr.text))
        m2 = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pr.text, re.S)
        print("player NEXT", bool(m2))
        if m2:
            print(m2.group(1)[:2500])
        # look for json in script type application
        for jm in re.finditer(r"<script[^>]*type=\"application/(?:ld\+json|json)\"[^>]*>(.*?)</script>", pr.text, re.S | re.I):
            print("JSON script", jm.group(1)[:400])

    return 0


if __name__ == "__main__":
    sys.exit(main())
