"""Extract FUT.GG player API path constants from main JS bundle."""
from curl_cffi import requests
import re

r = requests.get(
    "https://assets.fut.gg/ts/assets/main-D0Fg3gMx.js",
    impersonate="chrome131",
    timeout=40,
)
t = r.text

# Look for search URL definitions near "players","search"
for label in (
    "search",
    "search-by-name",
    "getPlayer",
    "getAll",
    "definition-data",
    "globalSearchPlayer",
    "filtered-items",
):
    idx = 0
    found = 0
    while found < 5:
        i = t.find(label, idx)
        if i < 0:
            break
        print(f"\n--- {label} @ {i} ---")
        print(t[max(0, i - 120) : i + 180].replace("\n", " "))
        idx = i + len(label)
        found += 1

# ya= or similar object with search:
for m in re.finditer(r"search:\s*[\"']([^\"']+)[\"']", t):
    print("search:", m.group(1))

for m in re.finditer(r"[\"'](/api/[^\"']+)[\"']", t):
    print("APISTR", m.group(1)[:120])

# path helpers
for m in re.finditer(r"path:\s*[\"']([^\"']*player[^\"']*)[\"']", t, re.I):
    print("PATH", m.group(1)[:120])

# Le object urls
for m in re.finditer(r"urls:\s*\{[^}]{0,500}\}", t):
    s = m.group(0)
    if "player" in s.lower() or "search" in s.lower():
        print("URLS", s[:400])
