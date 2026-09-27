"""Futbin HTTP client, cache, and high-level import helpers."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Union
from urllib.parse import urlparse

from .futbin_models import (
    FUTBIN_HOST,
    FutbinBlockedError,
    FutbinError,
    FutbinParseError,
    FutbinPlayer,
    PathLike,
    _is_cloudflare_block,
    parse_futbin_url,
    player_image_url,
    year_to_cdn_folder,
)
from .futbin_parse import (
    import_html_file,
    import_json,
    parse_player_html,
    to_le_card_row,
)

DEFAULT_IMPERSONATE_ORDER: tuple[str, ...] = (
    "chrome146",
    "chrome145",
    "chrome142",
    "chrome136",
    "chrome131",
    "chrome124",
    "firefox147",
    "firefox144",
    "safari17_0",
    "chrome131_android",
)


class FutbinClient:
    """
    Best-effort Futbin fetcher.

    Parameters
    ----------
    cookies : dict or cookie header string
        Paste cf_clearance (+ optional __cf_bm, futbin session cookies) from
        a browser that already solved the challenge on futbin.com.
    cookie_file : path to Netscape cookies.txt or JSON {name: value}
    impersonate : curl_cffi browser profile
    timeout : request timeout seconds
    """

    def __init__(
        self,
        *,
        cookies: Optional[Union[Mapping[str, str], str]] = None,
        cookie_file: Optional[PathLike] = None,
        impersonate: str = "chrome146",
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
    ) -> None:
        self.timeout = timeout
        self.impersonate = impersonate
        self.user_agent = user_agent
        self._cookies: dict[str, str] = {}
        if cookies:
            self.set_cookies(cookies)
        if cookie_file:
            self.load_cookies_from_file(cookie_file)

    # -- cookie helpers ----------------------------------------------------

    def set_cookies(self, cookies: Union[Mapping[str, str], str]) -> None:
        if isinstance(cookies, str):
            # "a=b; c=d" header form
            for part in cookies.split(";"):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                k, v = part.split("=", 1)
                self._cookies[k.strip()] = v.strip()
        else:
            self._cookies.update({str(k): str(v) for k, v in cookies.items()})

    def load_cookies_from_file(self, path: PathLike) -> None:
        """
        Load cookies from:
          - JSON object {name: value} or [{name, value}, ...]
          - Netscape cookies.txt (tab-separated)
          - Raw Cookie header line in a .txt file
        """
        p = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return
        if p.suffix.lower() == ".json" or text[0] in "{[":
            data = json.loads(text)
            if isinstance(data, dict):
                # either {name: value} or {cookies: {...}}
                if "cookies" in data and isinstance(data["cookies"], dict):
                    self.set_cookies(data["cookies"])
                elif all(isinstance(v, (str, int, float)) for v in data.values()):
                    self.set_cookies({k: str(v) for k, v in data.items()})
                else:
                    # playwright storage state style
                    for c in data.get("cookies", []):
                        if c.get("name"):
                            self._cookies[str(c["name"])] = str(c.get("value", ""))
            elif isinstance(data, list):
                for c in data:
                    if isinstance(c, dict) and c.get("name"):
                        self._cookies[str(c["name"])] = str(c.get("value", ""))
            return
        # Netscape or header
        if "\t" in text and ("futbin" in text.lower() or text.startswith("#")):
            for line in text.splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    self._cookies[parts[5]] = parts[6]
            return
        self.set_cookies(text)

    def export_cookies_json(self, path: PathLike) -> None:
        Path(path).write_text(json.dumps(self._cookies, indent=2), encoding="utf-8")

    # -- low-level GET -----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{FUTBIN_HOST}/",
        }
        if self.user_agent:
            h["User-Agent"] = self.user_agent
        return h

    def get(
        self,
        url: str,
        *,
        impersonate: Optional[str] = None,
        try_fallbacks: bool = True,
    ) -> tuple[int, str, dict[str, str]]:
        """
        GET url. Returns (status, text, response_headers_subset).

        Tries curl_cffi first, then cloudscraper, then requests.
        """
        imp = impersonate or self.impersonate
        order = [imp]
        if try_fallbacks:
            order += [i for i in DEFAULT_IMPERSONATE_ORDER if i != imp]

        last_err: Optional[Exception] = None
        # curl_cffi
        try:
            from curl_cffi import requests as creq

            for profile in order:
                try:
                    r = creq.get(
                        url,
                        impersonate=profile,
                        timeout=self.timeout,
                        headers=self._headers(),
                        cookies=self._cookies or None,
                        allow_redirects=True,
                    )
                    text = r.text or ""
                    if r.status_code == 200 and not _is_cloudflare_block(r.status_code, text):
                        return r.status_code, text, dict(r.headers)
                    # cookies might still unlock — return even 403 for inspection
                    if self._cookies and r.status_code == 200:
                        return r.status_code, text, dict(r.headers)
                    last_status, last_text = r.status_code, text
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    continue
            else:
                # return last CF response if any
                if "last_status" in dir() or "last_status" in locals():
                    try:
                        return last_status, last_text, {}
                    except NameError:
                        pass
        except ImportError:
            pass

        # cloudscraper
        try:
            import cloudscraper

            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            if self._cookies:
                for k, v in self._cookies.items():
                    scraper.cookies.set(k, v, domain=".futbin.com")
            r = scraper.get(url, timeout=self.timeout, headers=self._headers())
            return r.status_code, r.text or "", dict(r.headers)
        except Exception as e:  # noqa: BLE001
            last_err = e

        # requests
        try:
            import requests

            r = requests.get(
                url,
                timeout=self.timeout,
                headers=self._headers(),
                cookies=self._cookies or None,
            )
            return r.status_code, r.text or "", dict(r.headers)
        except Exception as e:  # noqa: BLE001
            last_err = e

        raise FutbinError(f"All HTTP backends failed for {url}: {last_err}")

    # -- high-level fetch --------------------------------------------------

    def fetch_player_url(self, url: str) -> FutbinPlayer:
        """Fetch and parse a Futbin player URL (live)."""
        status, text, _hdrs = self.get(url)
        if _is_cloudflare_block(status, text) or (
            status != 200 and "info_content" not in text
        ):
            raise FutbinBlockedError(
                f"Futbin blocked or non-200 ({status}) for {url}. "
                "Paste browser cookies (cf_clearance) or import saved HTML/JSON."
            )
        player = parse_player_html(text, source_url=url, source="live")
        return player

    def fetch_player(
        self,
        *,
        year: int = 26,
        futbin_id: Optional[int] = None,
        slug: str = "player",
        url: Optional[str] = None,
        html_path: Optional[PathLike] = None,
        json_path: Optional[PathLike] = None,
        try_archive: bool = False,
    ) -> FutbinPlayer:
        """
        Resolve a player from the first successful source:

        1. json_path
        2. html_path
        3. live url / constructed year+futbin_id URL
        4. optional archive.org (best-effort)
        """
        if json_path:
            p = import_json(json_path)
            p.source = p.source or "json"
            return p
        if html_path:
            return import_html_file(html_path, source_url=url or "")

        target = url
        if not target:
            if futbin_id is None:
                raise FutbinError("Provide url, futbin_id, html_path, or json_path")
            target = f"{FUTBIN_HOST}/{int(year):02d}/player/{int(futbin_id)}/{slug or 'player'}"

        try:
            return self.fetch_player_url(target)
        except (FutbinBlockedError, FutbinError):
            if not try_archive:
                raise
            return self.fetch_from_archive(target)

    def fetch_from_archive(self, url: str, timestamp: str = "") -> FutbinPlayer:
        """
        Best-effort Wayback Machine fetch.
        timestamp empty → Wayback calendar redirect form /web/{ts}/{url} with latest.
        """
        if timestamp:
            snap = f"https://web.archive.org/web/{timestamp}/{url}"
        else:
            # id_ gets original without toolbar when available
            snap = f"https://web.archive.org/web/2id_/{url}"
            # fallback without id_
            snap = f"https://web.archive.org/web/{url}" if False else f"https://web.archive.org/web/2/{url}"

        status, text, _ = self.get(snap, try_fallbacks=False)
        if status != 200 or len(text) < 1000:
            # try generic
            snap2 = f"https://web.archive.org/web/2020/{url}"
            status, text, _ = self.get(snap2, try_fallbacks=False)
        if status != 200:
            raise FutbinError(f"Archive fetch failed ({status}) for {url}")
        return parse_player_html(text, source_url=url, source="archive")

    def fetch_player_prices(self, resource_id: int, year: Optional[int] = None) -> Any:
        """
        Historical price API. Usually CF-blocked without cookies.

        Classic: /playerPrices?player={resourceId}
                 /{year}/playerPrices?player={resourceId}
        """
        urls = [f"{FUTBIN_HOST}/playerPrices?player={int(resource_id)}"]
        if year:
            urls.insert(0, f"{FUTBIN_HOST}/{int(year):02d}/playerPrices?player={int(resource_id)}")
        last_err: Optional[Exception] = None
        for u in urls:
            try:
                status, text, _ = self.get(u)
                if _is_cloudflare_block(status, text):
                    last_err = FutbinBlockedError(f"CF block on {u}")
                    continue
                if status == 200:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"raw": text, "url": u, "status": status}
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise FutbinBlockedError(f"playerPrices failed: {last_err}")

    def image_url(self, playerid: int, year: int = 26) -> str:
        return player_image_url(playerid, year)

    def probe_live(self, year: int = 26) -> dict[str, Any]:
        """Quick connectivity probe for diagnostics."""
        url = f"{FUTBIN_HOST}/{int(year):02d}/players?page=1"
        t0 = time.time()
        try:
            status, text, _ = self.get(url)
            return {
                "url": url,
                "status": status,
                "blocked": _is_cloudflare_block(status, text),
                "len": len(text),
                "has_cookies": bool(self._cookies),
                "seconds": round(time.time() - t0, 2),
                "snippet": text[:160].replace("\n", " "),
            }
        except Exception as e:  # noqa: BLE001
            return {"url": url, "error": str(e), "seconds": round(time.time() - t0, 2)}


# ---------------------------------------------------------------------------
# Cookie paste workflow (documented + helper)
# ---------------------------------------------------------------------------

COOKIE_PASTE_INSTRUCTIONS = """
Browser cookie paste (when live CF blocks automation)
=====================================================

1. Open Chrome/Edge, visit https://www.futbin.com/ and solve any challenge.
2. Open DevTools → Application → Cookies → https://www.futbin.com
   (or DevTools → Network → any document request → Request Headers → cookie).
3. Copy at least:
     - cf_clearance
     - __cf_bm          (optional, short-lived)
     - any futbin_* / PHPSESSID / laravel_session cookies present
4. Save as JSON, e.g. cookies/futbin_cookies.json:
     {
       "cf_clearance": "....",
       "__cf_bm": "...."
     }
5. Use from Python:

     from futbin_client import FutbinClient
     c = FutbinClient(cookie_file=\"cookies/futbin_cookies.json\")
     # User-Agent SHOULD match the browser that solved CF when possible:
     # c = FutbinClient(cookie_file=..., user_agent=\"Mozilla/5.0 ... Chrome/146...\")
     p = c.fetch_player_url(\"https://www.futbin.com/26/player/ID/slug\")
     row = to_le_card_row(p)

Notes:
- cf_clearance is tied to IP + UA + TLS fingerprint. curl_cffi impersonate
  must be close to the browser that generated the cookie.
- Cookies expire; re-export when fetches return 403 again.
- Prefer offline HTML save if cookie reuse is unreliable on your network.
"""


def save_cookie_instructions(path: PathLike) -> None:
    Path(path).write_text(COOKIE_PASTE_INSTRUCTIONS.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Compatibility helpers used by main.py / gui.py
# ---------------------------------------------------------------------------

def _futbin_cache_dir() -> Path:
    try:
        from .paths import card_db_dir

        d = card_db_dir() / "futbin"
    except Exception:  # noqa: BLE001
        d = Path(__file__).resolve().parent.parent / "card_db" / "futbin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_player(player: FutbinPlayer, year: str = "26") -> Path:
    row = to_le_card_row(player)
    row["_source"] = "futbin"
    row["_year"] = str(year if year else (player.year or "26"))
    row["year"] = row["_year"]
    if player.name:
        row.setdefault("name", player.name)
    if player.rating is not None:
        row.setdefault("overallrating", player.rating)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(row.get("name") or "player"))[:60]
    path = _futbin_cache_dir() / f"{row['_year']}_{safe}_{row.get('overallrating', '')}.json"
    path.write_text(json.dumps(row, indent=2), encoding="utf-8")
    return path


def probe_futbin(year: str = "26") -> dict[str, Any]:
    """CLI/GUI helper: probe live Futbin reachability for a game year."""
    client = FutbinClient()
    try:
        y = int(str(year).strip())
    except ValueError:
        y = 26
    data = client.probe_live(y)
    return {"year": str(year), "results": data, "any_ok": bool(data.get("ok") or data.get("any_ok"))}


def import_from_html(
    html: str,
    *,
    year: str = "26",
    url: str = "",
    save: bool = True,
) -> dict[str, Any]:
    """Parse Futbin player HTML string → LE card row dict (optionally cache)."""
    player = parse_player_html(html, source_url=url)
    if player.year is None:
        try:
            player.year = int(str(year))
        except ValueError:
            player.year = 26
    row = to_le_card_row(player)
    row["_source"] = "futbin"
    row["_year"] = str(year)
    row["year"] = str(year)
    if save:
        _cache_player(player, year=str(year))
    return row


def import_from_json(
    obj: Union[Mapping[str, Any], list, str, Path],
    *,
    year: str = "26",
    save: bool = True,
) -> list[dict[str, Any]]:
    """Import one or more Futbin-like JSON cards into LE row dicts."""
    items: list[Any]
    if isinstance(obj, list):
        items = list(obj)
    else:
        items = [obj]
    out: list[dict[str, Any]] = []
    for item in items:
        player = import_json(item)
        if player.year is None:
            try:
                player.year = int(str(year))
            except ValueError:
                player.year = 26
        row = to_le_card_row(player)
        row["_source"] = "futbin"
        row["_year"] = str(year)
        row["year"] = str(year)
        if save:
            _cache_player(player, year=str(year))
        out.append(row)
    return out


def fetch_player_url(
    url: str,
    *,
    year: Optional[str] = None,
    save: bool = True,
    cookies: Optional[Union[Mapping[str, str], str]] = None,
    cookie_file: Optional[PathLike] = None,
) -> dict[str, Any]:
    """
    Live-fetch a Futbin player URL (or fail with guidance).
    Pass browser cookies when Cloudflare blocks anonymous access.
    """
    client = FutbinClient(cookies=cookies, cookie_file=cookie_file)
    try:
        player = client.fetch_player(url=url, try_archive=True)
    except FutbinBlockedError as e:
        raise RuntimeError(
            "Futbin live fetch failed (Cloudflare). "
            "Save the player page as HTML and use --import-futbin-html, "
            "or pass browser cookies (cf_clearance). "
            f"detail={e}"
        ) from e
    y = year
    if not y:
        y = str(player.year or parse_futbin_url(url).get("year") or "26")
    row = to_le_card_row(player)
    row["_source"] = "futbin"
    row["_year"] = str(y)
    row["year"] = str(y)
    if save:
        _cache_player(player, year=str(y))
    return row


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

