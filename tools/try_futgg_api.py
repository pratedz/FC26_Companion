from curl_cffi import requests
import json

bases = [
    "https://www.fut.gg/api/",
    "https://api.fut.gg/",
    "https://www.fut.gg/",
    "https://r2.fut.gg/api/",
    "https://game-assets.fut.gg/api/",
]
paths = [
    "players/v2/search/?name=Neymar",
    "players/v2/search/?q=Neymar",
    "players/v2/search/?search=Neymar",
    "players/v2/def-search/?name=Neymar",
    "players/v2/26/?count=50&sorts=-overall_rating",
    "players/v2/26/?page=1",
    "players/v2/26/definitions/?count=50&sorts=-overall_rating",
    "players/v2/definition-data/?slugs=neymar-jr",
    "global-search/players/?q=Neymar&limit=20",
    "global-search/?q=Neymar",
]
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fut.gg/players/",
    "Origin": "https://www.fut.gg",
}

for b in bases:
    for p in paths:
        u = b + p
        try:
            r = requests.get(u, impersonate="chrome131", timeout=15, headers=headers)
            body = (r.text or "")[:150].replace("\n", " ")
            if r.status_code != 404 or "api" in b:
                print(r.status_code, u[:90], body[:100])
        except Exception as e:
            print("ERR", u[:70], e)
