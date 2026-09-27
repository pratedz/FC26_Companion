from curl_cffi import requests
import json

paths = [
    "https://www.fut.gg/players/v2/search/?name=Neymar",
    "https://www.fut.gg/players/v2/search/?q=Neymar",
    "https://www.fut.gg/players/v2/26/?count=30&sorts=-overall_rating",
    "https://www.fut.gg/players/v2/def-search/?name=Neymar",
    "https://www.fut.gg/global-search/players/?q=Neymar&limit=20",
    "https://www.fut.gg/players/v2/all-versions/190871/",
    "http://futgg-lb-341565785.eu-west-2.elb.amazonaws.com/players/v2/search/?name=Neymar",
    "https://futgg-lb-341565785.eu-west-2.elb.amazonaws.com/players/v2/search/?name=Neymar",
]

header_sets = [
    {
        "name": "json",
        "h": {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.fut.gg/players/",
            "Origin": "https://www.fut.gg",
        },
    },
    {
        "name": "html",
        "h": {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://www.fut.gg/players/",
        },
    },
    {
        "name": "rsc",
        "h": {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/x-component",
            "Referer": "https://www.fut.gg/players/?q=Neymar",
            "RSC": "1",
            "Next-Router-State-Tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22players%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D",
        },
    },
    {
        "name": "tsr",
        "h": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.fut.gg/players/",
            "Origin": "https://www.fut.gg",
            "x-requested-with": "XMLHttpRequest",
        },
    },
]

for url in paths:
    for hs in header_sets:
        try:
            r = requests.get(url, impersonate="chrome131", timeout=15, headers=hs["h"])
            body = (r.text or "")[:120].replace("\n", " ")
            print(f"{r.status_code} [{hs['name']:4}] {url[:70]} | {body[:90]}")
            if r.status_code == 200 and body.startswith(("{", "[")):
                print("  JSON OK!", body[:200])
        except Exception as e:
            print("ERR", hs["name"], url[:50], type(e).__name__, e)
