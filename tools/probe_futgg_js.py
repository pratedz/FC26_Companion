from curl_cffi import requests
import re

r = requests.get(
    "https://assets.fut.gg/ts/assets/26.players-DKYiqrBu.js",
    impersonate="chrome131",
    timeout=30,
)
print("status", r.status_code, "len", len(r.text))
t = r.text
urls = set(re.findall(r"https?://[^\s\"'\\]{8,140}", t))
for u in sorted(urls):
    print("U", u)

for kw in ("graphql", "meili", "algolia", "elastic", "supabase", "firebase", "page=", "fetch("):
    print("has", kw, kw in t.lower())

i = 0
for m in re.finditer(r".{0,40}/api[^\"'\\s]{0,80}", t):
    print("API", m.group(0)[:140])
    i += 1
    if i > 30:
        break

i = 0
for m in re.finditer(r"[\"'](/[^\"']*player[^\"']*)[\"']", t, re.I):
    print("PATH", m.group(1)[:120])
    i += 1
    if i > 40:
        break

# also fetch main players page JS from best page assets
r2 = requests.get("https://www.fut.gg/players/best/", impersonate="chrome131", timeout=30)
scripts = re.findall(r"https://assets\.fut\.gg/ts/assets/[^\"']+\.js", r2.text)
print("page scripts", scripts[:20])
for s in scripts[:8]:
    if "player" in s.lower() or "index" in s.lower() or "entry" in s.lower():
        rr = requests.get(s, impersonate="chrome131", timeout=30)
        print("script", s, rr.status_code, len(rr.text))
        for m in re.finditer(r".{0,30}https?://[^\"'\\s]{10,100}", rr.text):
            u = m.group(0)
            if any(k in u.lower() for k in ("api", "player", "data", "cdn", "gg")):
                print("  ", u[:160])
