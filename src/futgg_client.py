"""FUT.GG API client — bulk download of Ultimate Team cards (all specials).

Futbin itself is Cloudflare-blocked. FUT.GG exposes a public JSON API at
  https://www.fut.gg/api/fut/players/v2/...
which returns the full FUT card catalog (including promos, Icons, Heroes, etc.)
with detailed attributes — functionally the same job as \"pull every Futbin card\".

API note: each game year endpoint caps at ~10_000 cards (200 pages x 50).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import paths
from . import player_schema

try:
    from curl_cffi import requests as crequests
except ImportError:  # pragma: no cover
    crequests = None

import urllib.request

API_BASE = "https://www.fut.gg/api/fut"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.fut.gg/players/",
    "Origin": "https://www.fut.gg",
    "Content-Type": "application/json",
}

# FUT.GG attributeAcceleration → LE field
ATTR_MAP = {
    "attributeAcceleration": "acceleration",
    "attributeSprintSpeed": "sprintspeed",
    "attributeAgility": "agility",
    "attributeReactions": "reactions",
    "attributeBalance": "balance",
    "attributeFinishing": "finishing",
    "attributeShotPower": "shotpower",
    "attributeLongShots": "longshots",
    "attributeVolleys": "volleys",
    "attributePenalties": "penalties",
    "attributePositioning": "positioning",
    "attributeVision": "vision",
    "attributeCrossing": "crossing",
    "attributeFkAccuracy": "freekickaccuracy",
    "attributeShortPassing": "shortpassing",
    "attributeLongPassing": "longpassing",
    "attributeCurve": "curve",
    "attributeDribbling": "dribbling",
    "attributeBallControl": "ballcontrol",
    "attributeComposure": "composure",
    "attributeInterceptions": "interceptions",
    "attributeHeadingAccuracy": "headingaccuracy",
    "attributeDefensiveAwareness": "defensiveawareness",
    "attributeStandingTackle": "standingtackle",
    "attributeSlidingTackle": "slidingtackle",
    "attributeJumping": "jumping",
    "attributeStamina": "stamina",
    "attributeStrength": "strength",
    "attributeAggression": "aggression",
    "attributeGkDiving": "gkdiving",
    "attributeGkHandling": "gkhandling",
    "attributeGkKicking": "gkkicking",
    "attributeGkReflexes": "gkreflexes",
    "attributeGkPositioning": "gkpositioning",
}


class FutGGError(RuntimeError):
    pass


def _http_get_json(url: str, timeout: float = 40.0) -> Any:
    if crequests is not None:
        r = crequests.get(url, impersonate="chrome131", timeout=timeout, headers=DEFAULT_HEADERS)
        if r.status_code != 200:
            raise FutGGError(f"HTTP {r.status_code} for {url}: {(r.text or '')[:200]}")
        return r.json()
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_by_name(name: str) -> list[dict[str, Any]]:
    """Search FUT.GG for cards by player name (all specials for that name)."""
    from urllib.parse import quote

    url = f"{API_BASE}/players/v2/search/?name={quote(name)}"
    data = _http_get_json(url)
    return list(data.get("data") or [])


def list_page(year: str | int, page: int = 1, count: int = 50) -> dict[str, Any]:
    year = str(year)
    url = (
        f"{API_BASE}/players/v2/{year}/"
        f"?count={int(count)}&sorts=-overall_rating&page={int(page)}"
    )
    return _http_get_json(url)


def fetch_definitions(slugs: Iterable[str]) -> list[dict[str, Any]]:
    slugs = [s for s in slugs if s]
    if not slugs:
        return []
    joined = ",".join(slugs)
    url = f"{API_BASE}/players/v2/definition-data/?slugs={joined}"
    data = _http_get_json(url, timeout=60.0)
    return list(data.get("data") or [])


def all_versions(base_player_ea_id: int | str) -> list[dict[str, Any]]:
    url = f"{API_BASE}/players/v2/all-versions/{base_player_ea_id}/"
    data = _http_get_json(url)
    return list(data.get("data") or [])


def _rarity_name(item: dict[str, Any]) -> str:
    """Extract human rarity / promo name from nested or flat fields."""
    for key in ("rarityName", "rarity"):
        v = item.get(key)
        if isinstance(v, dict):
            name = v.get("name") or v.get("alternateName") or v.get("rarityGroupName")
            if name:
                return str(name)
            slug = v.get("slug")
            if slug:
                return str(slug).replace("-", " ").title()
        elif v:
            return str(v)
    if item.get("isHero"):
        return "Hero"
    rid = item.get("rarityEaId")
    return str(rid) if rid is not None else ""


def _nested_name(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("name") or obj.get("slug") or "")
    return str(obj or "")


def _foot_to_le(foot: Any) -> Optional[int]:
    """FUT.GG: 1 or 'Right' → LE preferredfoot 1; 2/'Left' → 2."""
    if foot is None or foot == "":
        return None
    if isinstance(foot, str):
        t = foot.strip().casefold()
        if t.startswith("r"):
            return 1
        if t.startswith("l"):
            return 2
        try:
            foot = int(foot)
        except ValueError:
            return None
    try:
        n = int(foot)
    except (TypeError, ValueError):
        return None
    if n in (1, 2):
        return n
    return None


def futgg_playstyle_ids_to_masks(ids: Any) -> tuple[int, int]:
    """Map FUT.GG playstyle index IDs → LE trait1/trait2 bitmasks.

    FUT.GG uses sequential enum indices (0=Finesse Shot, 1=Chip Shot, …)
    matching LE bit positions: bank1 bit = 1<<id for id 0..29;
    bank2 uses higher ids (30+ → bit 1<<(id-30)) as best-effort.
    """
    t1, t2 = 0, 0
    if not ids:
        return t1, t2
    for raw in ids:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid < 0:
            continue
        if pid < 30:
            t1 |= 1 << pid
        else:
            bit = pid - 30
            if 0 <= bit < 30:
                t2 |= 1 << bit
    return t1, t2


# AcceleRATE / accelerateType → LE runstylecode (common CM codes; best-effort)
_ACCEL_TO_RUNSTYLE = {
    "EXPLOSIVE": 2,
    "MOSTLY_EXPLOSIVE": 2,
    "CONTROLLED_EXPLOSIVE": 2,
    "CONTROLLED": 1,
    "LENGTHY": 3,
    "MOSTLY_LENGTHY": 3,
    "CONTROLLED_LENGTHY": 3,
}


def is_le_normalized_row(item: dict[str, Any]) -> bool:
    """True if dict is already a catalog LE row (not raw FUT.GG API definition).

    Raw definitions use attributeAcceleration / skillMoves; our jsonl uses acceleration / skillmoves.
    """
    if not isinstance(item, dict):
        return False
    if item.get("attributeAcceleration") is not None or item.get("skillMoves") is not None:
        return False
    if item.get("acceleration") is not None or item.get("skillmoves") is not None:
        return True
    # Thin meta-only row still LE-shaped if marked
    if item.get("_source") == "futgg" and (
        item.get("overallrating") is not None or item.get("basePlayerEaId") is not None
    ):
        return True
    return False


def definition_to_le_row(item: dict[str, Any]) -> dict[str, Any]:
    """Map a FUT.GG definition object to our catalog / LE-friendly row.

    Includes attributes, body, foot, SM/WF, playstyles/PlayStyle+, club/nation/league
    labels, rarity, accelerate type, shirt number, real face — enough for full card apply.

    Already-normalized jsonl rows are returned as a shallow copy (no double-map wipe).
    """
    if is_le_normalized_row(item):
        row = dict(item)
        row.setdefault("_source", "futgg")
        y = str(row.get("year") or row.get("_year") or row.get("game") or "")
        if y:
            row["year"] = y
            row["_year"] = y
        return row

    first = item.get("firstName") or ""
    last = item.get("lastName") or ""
    nick = item.get("nickname") or item.get("commonName") or ""
    name = nick or f"{first} {last}".strip() or str(item.get("searchableName") or "")
    rarity = _rarity_name(item)

    club = item.get("club") or item.get("uniqueClub") or {}
    nation = item.get("nation") or {}
    league = item.get("league") or {}

    ps_ids = item.get("playstyles") or item.get("playStyles") or []
    psp_ids = item.get("playstylesPlus") or item.get("playstyles_plus") or item.get("playStylesPlus") or []
    t1, t2 = futgg_playstyle_ids_to_masks(ps_ids)
    i1, i2 = futgg_playstyle_ids_to_masks(psp_ids)

    foot = _foot_to_le(item.get("foot"))
    if foot is None and item.get("preferredfoot") is not None:
        foot = _foot_to_le(item.get("preferredfoot"))
    accel = str(item.get("accelerateType") or item.get("acceleRATE") or "").upper().replace(" ", "_")
    runstyle = _ACCEL_TO_RUNSTYLE.get(accel)

    alt_pos = item.get("alternativePositionIds") or item.get("alternativePositions") or []
    if alt_pos and isinstance(alt_pos[0], dict):
        alt_pos = [p.get("id") or p.get("eaId") for p in alt_pos]

    overall = item.get("overall")
    if overall is None:
        overall = item.get("overallrating") or item.get("overallRating")

    row: dict[str, Any] = {
        "name": name,
        "firstName": first,
        "lastName": last,
        "nickname": nick,
        "overallrating": overall,
        "overall": overall,
        "potential": overall,
        "preferredposition1": item.get("position") if item.get("position") is not None else item.get("preferredposition1"),
        "position": item.get("position") if item.get("position") is not None else item.get("preferredposition1"),
        # FUT.GG reports skill moves as a STAR count (1-5) but players.skillmoves
        # is 0-indexed (0 = 1 star). Passing stars through made every imported
        # player one star too good. weakfootabilitytypecode really is 1-5, so it
        # passes through unchanged.
        "skillmoves": (
            player_schema.stars_to_skillmoves(item.get("skillMoves"))
            if item.get("skillMoves") is not None
            else item.get("skillmoves")
        ),
        "weakfootabilitytypecode": item.get("weakFoot") if item.get("weakFoot") is not None else item.get("weakfootabilitytypecode"),
        "preferredfoot": foot,
        "playerid": item.get("basePlayerEaId") or item.get("eaId") or item.get("playerid"),
        "eaId": item.get("eaId"),
        "item_id": item.get("eaId"),
        "basePlayerEaId": item.get("basePlayerEaId"),
        "slug": item.get("slug"),
        "revision": rarity,
        "origin": rarity,
        "rarity": rarity,
        "isHero": item.get("isHero"),
        "game": item.get("game"),
        "year": str(item.get("game") or item.get("year") or ""),
        "_year": str(item.get("game") or item.get("year") or ""),
        "_source": "futgg",
        "url": item.get("url") or "",
        "height": item.get("height"),
        "weight": item.get("weight"),
        "bodytypecode": item.get("bodytypeCode") if item.get("bodytypeCode") is not None else item.get("bodyTypeCode"),
        "hashighqualityhead": 1 if item.get("isRealFace") else (0 if item.get("isRealFace") is False else None),
        "isRealFace": item.get("isRealFace"),
        "accelerateType": accel,
        "acceleRATE": accel,
        "runstylecode": runstyle,
        "shirtnumber": item.get("shirtNumber") or item.get("jerseyNumber"),
        "club": _nested_name(club),
        "club_ea_id": item.get("clubEaId") or (club.get("eaId") if isinstance(club, dict) else None),
        "league": _nested_name(league),
        "league_ea_id": item.get("leagueEaId") or (league.get("eaId") if isinstance(league, dict) else None),
        "nation": _nested_name(nation),
        "nationality": item.get("nationEaId") or (nation.get("eaId") if isinstance(nation, dict) else None) or item.get("nationality"),
        "nation_name": _nested_name(nation) or item.get("nation_name") or item.get("nation"),
        "dateOfBirth": item.get("dateOfBirth") or item.get("birthDate") or item.get("dateOfBirth"),
        # FUT.GG encodes 1 = male; EA's players.gender is 0 = male / 1 = female.
        # Passing FUT.GG's value straight through flagged every imported player
        # female, loading a mismatched body/animation rig.
        "gender": 0 if item.get("gender") in (None, 1) else 1,
        # PlayStyles bitmasks for LE players table
        "trait1": t1,
        "trait2": t2,
        "icontrait1": i1,
        "icontrait2": i2,
        "playstyles": list(ps_ids) if isinstance(ps_ids, list) else [],
        "playstyles_plus": list(psp_ids) if isinstance(psp_ids, list) else [],
        "playstylesPlus": list(psp_ids) if isinstance(psp_ids, list) else [],
    }
    # alt positions 2..n
    if isinstance(alt_pos, list):
        for i, pid in enumerate(alt_pos[:6]):
            try:
                row[f"preferredposition{i+2}"] = int(pid)
            except (TypeError, ValueError):
                pass
    for src, le in ATTR_MAP.items():
        if item.get(src) is not None:
            row[le] = item[src]
    # Preserve LE attr keys if present on mixed inputs
    for le in ATTR_MAP.values():
        if row.get(le) is None and item.get(le) is not None:
            row[le] = item[le]
    return row


def futgg_cache_dir() -> Path:
    d = paths.card_db_dir() / "futgg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def year_jsonl_path(year: str | int) -> Path:
    return futgg_cache_dir() / f"futgg_{year}.jsonl"


def sync_year(
    year: str | int,
    *,
    page_size: int = 50,
    max_pages: Optional[int] = None,
    sleep_s: float = 0.15,
    progress: Optional[Callable[[str], None]] = None,
    enrich_definitions: bool = True,
    def_batch: int = 25,
) -> dict[str, Any]:
    """
    Download all FUT.GG cards for a game year into card_db/futgg/futgg_{year}.jsonl.

    Returns stats dict: {year, listed, written, path, total_reported}.
    """
    year = str(year)
    log = progress or (lambda m: None)
    out_path = year_jsonl_path(year)
    tmp_path = out_path.with_suffix(".jsonl.partial")

    page = 1
    listed: list[dict[str, Any]] = []
    total_reported = None

    while True:
        if max_pages is not None and page > max_pages:
            break
        log(f"[{year}] list page {page}…")
        try:
            payload = list_page(year, page=page, count=page_size)
        except FutGGError as e:
            if page == 1:
                raise
            log(f"[{year}] stop at page {page}: {e}")
            break
        batch = list(payload.get("data") or [])
        total_reported = payload.get("total")
        if not batch:
            break
        listed.extend(batch)
        nxt = payload.get("next")
        log(f"[{year}] page {page}: +{len(batch)} (listed={len(listed)} / total~{total_reported})")
        if not nxt:
            break
        page = int(nxt)
        time.sleep(sleep_s)

    # Deduplicate by slug
    by_slug: dict[str, dict[str, Any]] = {}
    for it in listed:
        slug = str(it.get("slug") or "")
        if slug:
            by_slug[slug] = it

    rows_out: list[dict[str, Any]] = []
    slugs = list(by_slug.keys())

    if enrich_definitions and slugs:
        for i in range(0, len(slugs), def_batch):
            chunk = slugs[i : i + def_batch]
            log(f"[{year}] definitions {i+1}-{i+len(chunk)} / {len(slugs)}")
            try:
                defs = fetch_definitions(chunk)
            except FutGGError as e:
                log(f"[{year}] def batch failed: {e} — using list stubs")
                defs = []
            if defs:
                for d in defs:
                    rows_out.append(definition_to_le_row(d))
            else:
                for s in chunk:
                    stub = by_slug[s]
                    rows_out.append(
                        definition_to_le_row(
                            {
                                **stub,
                                "overall": stub.get("overallRating") or stub.get("overall"),
                                "game": year,
                            }
                        )
                    )
            time.sleep(sleep_s)
    else:
        for s, stub in by_slug.items():
            rows_out.append(
                definition_to_le_row(
                    {
                        **stub,
                        "overall": stub.get("overallRating") or stub.get("overall"),
                        "game": year,
                    }
                )
            )

    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows_out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(out_path)

    try:
        from .card_catalog import clear_catalog_cache

        clear_catalog_cache()
    except Exception:
        pass

    stats = {
        "year": year,
        "listed": len(by_slug),
        "written": len(rows_out),
        "total_reported": total_reported,
        "path": str(out_path),
    }
    log(f"[{year}] done → {out_path} ({stats['written']} cards)")
    return stats


def sync_years(
    years: Iterable[str | int],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    return [sync_year(y, **kwargs) for y in years]


def sync_player_all_versions(
    name: str,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> list[dict[str, Any]]:
    """Fetch all FUT versions for a name (e.g. Neymar) and cache under futgg/by_player/."""
    log = progress or (lambda m: None)
    log(f"search {name!r}…")
    hits = search_by_name(name)
    if not hits:
        log("no hits")
        return []
    # collect unique base ids
    base_ids = []
    for h in hits:
        bid = h.get("basePlayerEaId")
        if bid and bid not in base_ids:
            base_ids.append(bid)
    rows: list[dict[str, Any]] = []
    for bid in base_ids:
        log(f"all-versions base={bid}")
        versions = all_versions(bid)
        slugs = [v.get("slug") for v in versions if v.get("slug")]
        # also include search hits slugs
        for h in hits:
            if h.get("basePlayerEaId") == bid and h.get("slug"):
                slugs.append(h["slug"])
        slugs = list(dict.fromkeys(slugs))
        for i in range(0, len(slugs), 20):
            defs = fetch_definitions(slugs[i : i + 20])
            rows.extend(definition_to_le_row(d) for d in defs)
            time.sleep(0.1)
    out_dir = futgg_cache_dir() / "by_player"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in name)[:40]
    out = out_dir / f"{safe}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        from .card_catalog import clear_catalog_cache

        clear_catalog_cache()
    except Exception:
        pass
    log(f"wrote {len(rows)} versions → {out}")
    return rows
