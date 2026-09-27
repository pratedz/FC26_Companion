from curl_cffi import requests
import re

r = requests.get(
    "https://assets.fut.gg/ts/assets/main-D0Fg3gMx.js",
    impersonate="chrome131",
    timeout=40,
)
t = r.text

# Find Le({path: function definition and base URL usage
for label in ["function ae(", "ae=async", "async function ae", "baseSiteUrl", "baseApiUrl", "apiBase", "SERVER_URL", "VITE_", "fetch(e", "url:ya."]:
    i = t.find(label)
    print(label, "->", i)
    if i >= 0:
        print(t[max(0, i - 80) : i + 250].replace("\n", " ")[:350])
        print()

# ya object full definition
i = t.find("searchByName:Le({path:\"players/v2/search/\"})")
print("ya def area", i)
if i > 0:
    print(t[i - 400 : i + 600].replace("\n", " "))

# Le function
for m in re.finditer(r"function Le\(|Le=e=>|const Le=", t):
    print("Le@", m.start(), t[m.start() : m.start() + 200])
    break

# look for createServerFn or server base
for kw in ["createServerFn", "serverFn", "SERVER_API", "apiBaseUrl:Ct", "baseSiteUrl"]:
    print(kw, t.find(kw))
    if t.find(kw) >= 0:
        j = t.find(kw)
        print(t[j : j + 200].replace("\n", " "))
