"""Research GitHub for LE Profile Executor lookalikes."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

HEADERS = {
    "User-Agent": "LE-Research/1.0",
    "Accept": "application/vnd.github+json",
}


def gh_get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_repos(q: str, per_page: int = 10) -> list[dict]:
    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote(q)
        + f"&sort=stars&per_page={per_page}"
    )
    return gh_get(url).get("items", [])


def main() -> None:
    queries = [
        "Live Editor lua scripts FC OR FIFA in:name,description,readme",
        "careermode hub lua export",
        "icons injection Live Editor",
        "FIFA career companion tracker",
        "sofifa scraper",
        "futbin scrape player",
        "FC26 Manager Toolkit",
        "Aranaktu scripts",
    ]
    seen: set[str] = set()
    for q in queries:
        print("=" * 60)
        print("Q:", q)
        try:
            items = search_repos(q, 8)
        except Exception as e:
            print("ERR", e)
            continue
        for it in items:
            name = it["full_name"]
            if name in seen:
                continue
            seen.add(name)
            desc = (it.get("description") or "")[:120]
            print(f"  *{it['stargazers_count']:4d}  {name}")
            print(f"        {desc}")
            print(f"        {it.get('html_url')}")


if __name__ == "__main__":
    main()
