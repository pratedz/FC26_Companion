from curl_cffi import requests
import re

r = requests.get(
    "https://assets.fut.gg/ts/assets/main-D0Fg3gMx.js",
    impersonate="chrome131",
    timeout=40,
)
t = r.text
print("len", len(t))

# r2 / s3 / data dump hints
for pat in [
    r"r2\.fut\.gg[^\"'\\s]{0,80}",
    r"game-assets\.fut\.gg[^\"'\\s]{0,80}",
    r"s3\.eu-west-2[^\"'\\s]{0,100}",
    r"players[_\-]?db[^\"'\\s]{0,40}",
    r"carddefs?[^\"'\\s]{0,40}",
    r"\.json[^\"'\\s]{0,20}",
    r"apiBaseUrl[^,]{0,80}",
]:
    ms = re.findall(pat, t, re.I)
    uniq = sorted(set(ms))[:25]
    print(pat, "->", len(ms), uniq[:12])

# Find strings that look like data file paths
paths = re.findall(r"[\"'](/[a-zA-Z0-9_\-./]+\.(?:json|csv|bin|msgpack|gz))[\"']", t)
print("data files", sorted(set(paths))[:40])

# dehydrated query keys for players
for m in re.finditer(r".{0,30}players.{0,50}", t):
    s = m.group(0)
    if "query" in s.lower() or "fetch" in s.lower() or "api" in s.lower():
        print("CTX", s[:120])

# Try r2 root listings / known dump names
guesses = [
    "https://r2.fut.gg/",
    "https://r2.fut.gg/players.json",
    "https://r2.fut.gg/26/players.json",
    "https://r2.fut.gg/fc26/players.json",
    "https://r2.fut.gg/data/players.json",
    "https://game-assets.fut.gg/players.json",
    "https://game-assets.fut.gg/26/carddefs.json",
    "https://www.fut.gg/api/players/search/?q=Neymar",
    "https://www.fut.gg/api/supabase/players?select=*",
]
for u in guesses:
    try:
        rr = requests.get(u, impersonate="chrome131", timeout=15)
        print(rr.status_code, u, (rr.text or "")[:120].replace("\n", " "))
    except Exception as e:
        print("ERR", u, e)

# Parse search SSR page for embedded player objects
rr = requests.get(
    "https://www.fut.gg/players/?q=Neymar",
    impersonate="chrome131",
    timeout=30,
)
# look for overall ratings near Neymar
for m in re.finditer(r"Neymar.{0,200}", rr.text, re.I):
    print("N", m.group(0)[:200].replace("\n", " "))
    break

# script tags with JSON
for m in re.finditer(r"dehydratedData|queryStream|cardDefinition|overallRating|playerName", rr.text):
    print("key", m.group(0), "at", m.start())
