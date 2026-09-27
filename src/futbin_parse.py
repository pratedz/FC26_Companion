"""Futbin HTML/JSON parse and LE card row mapping."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from .futbin_models import (
    CDN_HOST,
    COMPOSITE_STATS,
    FUTBIN_HOST,
    FUTBIN_JSON_SCHEMA,
    FUTBIN_STAT_TO_LE,
    LE_CARD_HEADERS,
    FutbinBlockedError,
    FutbinParseError,
    FutbinPlayer,
    PathLike,
    _as_int,
    _is_cloudflare_block,
    _norm_key,
    map_stat_name,
    parse_futbin_url,
    player_image_url,
    position_to_code,
    year_to_cdn_folder,
)

def _stars_to_skillmoves(stars: Any) -> Any:
    """Star count (1-5) -> players.skillmoves raw value (0-4)."""
    try:
        from .player_schema import stars_to_skillmoves

        return stars_to_skillmoves(stars)
    except Exception:  # noqa: BLE001
        try:
            return max(0, min(4, int(stars) - 1))
        except (TypeError, ValueError):
            return stars


def parse_player_html(
    html: str,
    *,
    source_url: str = "",
    source: str = "html",
) -> FutbinPlayer:
    """
    Parse a saved Futbin player page (View Source / Save As / archive.org).

    Prefers embedded player_stats JSON (authoritative IG values), then
    info_content table, then tooltip/stat_val DOM, then card chrome.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise FutbinParseError("beautifulsoup4 is required to parse HTML") from e

    if not html or len(html) < 200:
        raise FutbinParseError("HTML too short / empty")
    if _is_cloudflare_block(403, html) and "info_content" not in html and "player_stats" not in html:
        raise FutbinBlockedError("HTML is a Cloudflare challenge page, not a player page")

    soup = BeautifulSoup(html, "lxml")
    player = FutbinPlayer(source_url=source_url, source=source)

    # URL meta
    if source_url:
        meta = parse_futbin_url(source_url)
        if meta.get("year"):
            player.year = meta["year"]
        if meta.get("futbin_id"):
            player.futbin_id = meta["futbin_id"]

    # Title / year fallback
    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if title else ""
    ym = re.search(r"(?:EA\s*FC|FIFA)\s*(\d{2})", title_text, re.I)
    if ym and player.year is None:
        player.year = int(ym.group(1))

    # Card chrome
    name_el = soup.select_one(".pcdisplay-name")
    if name_el:
        player.name = name_el.get_text(strip=True)
    pos_el = soup.select_one(".pcdisplay-pos")
    if pos_el:
        player.position = pos_el.get_text(strip=True).upper()
    rat_el = soup.select_one(".pcdisplay-rat")
    if rat_el:
        player.rating = _as_int(rat_el.get_text(strip=True))

    if not player.name:
        h1 = soup.find("h1")
        if h1:
            # "PELé  - ICON  EA FC 24 Prices and Rating"
            raw = h1.get_text(" ", strip=True)
            player.name = re.split(r"\s+-\s+", raw)[0].strip()

    # IDs from data attributes
    for el in soup.find_all(attrs={"data-player-resource": True}):
        player.playerid = _as_int(el.get("data-player-resource"))
        if player.playerid:
            break
    for el in soup.find_all(attrs={"data-player-id": True}):
        player.futbin_id = player.futbin_id or _as_int(el.get("data-player-id"))
        if player.futbin_id:
            break
    for el in soup.find_all(attrs={"data-base-id": True}):
        player.playerid = player.playerid or _as_int(el.get("data-base-id"))

    # info_content table
    info = soup.find("div", id="info_content") or soup.find(id="info_content")
    info_map: dict[str, str] = {}
    if info:
        for tr in info.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                key = th.get_text(" ", strip=True)
                val = td.get_text(" ", strip=True)
                info_map[key] = val
    player.raw_info = info_map

    def info_get(*keys: str) -> str:
        for k in keys:
            for ik, iv in info_map.items():
                if _norm_key(ik) == _norm_key(k):
                    return iv
        return ""

    if info_map:
        player.fullname = info_get("Name", "Fullname") or player.fullname
        if not player.name and player.fullname:
            player.name = player.fullname.split(" ")[-1]
        player.club = info_get("Club") or player.club
        player.nation = info_get("Nation", "Country") or player.nation
        player.league = info_get("League") or player.league
        player.skills = _as_int(info_get("Skills", "Skill Moves")) or player.skills
        player.weak_foot = _as_int(info_get("Weak Foot")) or player.weak_foot
        player.foot = info_get("Foot", "Preferred Foot") or player.foot
        player.revision = info_get("Revision", "Version") or player.revision
        player.origin = info_get("Origin") or player.origin
        player.att_wr = info_get("Att. WR", "Att.WR", "Attacking Work Rate") or player.att_wr
        player.def_wr = info_get("Def. WR", "Def.WR", "Defensive Work Rate") or player.def_wr
        player.playerid = player.playerid or _as_int(info_get("ID", "Resource ID", "Base ID"))
        player.club_id = _as_int(info_get("Club ID"))
        player.league_id = _as_int(info_get("League ID"))
        # Height: 173cm | 5'8"
        h = info_get("Height")
        hm = re.search(r"(\d+)\s*cm", h, re.I)
        if hm:
            player.height_cm = int(hm.group(1))
        player.weight_kg = _as_int(info_get("Weight"))

    # Embedded player_stats JSON (most reliable detailed attrs)
    stats_from_json = _extract_player_stats_json(html)
    if stats_from_json:
        _apply_stats_json(player, stats_from_json)

    # DOM tooltips as fill-in for any missing substats
    tooltips = soup.find_all("span", class_=re.compile(r"ig-stat-name-tooltip"))
    vals = soup.find_all("div", class_=re.compile(r"stat_val"))
    if tooltips and vals:
        for t_el, v_el in zip(tooltips, vals):
            label = t_el.get_text(strip=True)
            val = _as_int(v_el.get_text(strip=True))
            if val is None:
                continue
            le = map_stat_name(label)
            if le and le not in player.stats:
                player.stats[le] = val
            # composites
            nk = _norm_key(label)
            if nk in {"pace", "shooting", "passing", "defending", "physicality", "physical"}:
                key = "physicality" if nk == "physical" else nk
                player.composites.setdefault(key, val)
            elif nk == "dribbling" and "dribbling" not in player.composites:
                # ambiguous — only set composite if not already from JSON main
                pass

    # Rating fallback from title " - 95  - Rating"
    if player.rating is None and title_text:
        rm = re.search(r"\b(\d{2})\b\s*-\s*Rating", title_text, re.I)
        if rm:
            player.rating = int(rm.group(1))

    if not player.name and not player.stats:
        raise FutbinParseError("Could not extract player name or stats from HTML")

    return player


def _extract_player_stats_json(html: str) -> Optional[dict[str, Any]]:
    """Locate the [{pace:[...], shooting:[...], ...}] blob on classic pages."""
    # Common: hidden element or script near id="player_stats"
    start = html.find('[{"pace":')
    if start < 0:
        start = html.find("[{'pace':")
    if start < 0:
        # sometimes whitespace / different quote order
        m = re.search(r'\[\s*\{\s*"pace"\s*:\s*\[', html)
        if not m:
            return None
        start = m.start()

    depth = 0
    end = None
    for j, ch in enumerate(html[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        return None
    raw = html[start:end]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # minimal fixups
        try:
            data = json.loads(raw.replace("'", '"'))
        except json.JSONDecodeError:
            return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    if isinstance(data, dict):
        return data
    return None


# Futbin dual-labels main face stats with gk_stat_name for GK cards.
# Never map those labels through outfield names (e.g. "Positioning").
_GK_STAT_NAME_TO_LE: dict[str, str] = {
    "diving": "gkdiving",
    "handling": "gkhandling",
    "kicking": "gkkicking",
    "reflexes": "gkreflexes",
    "speed": "gkdiving",  # rare alternate label for DIV on some pages
    "positioning": "gkpositioning",
}


def _apply_stats_json(player: FutbinPlayer, obj: Mapping[str, Any]) -> None:
    """Flatten Futbin player_stats category object into stats/composites."""
    is_gk = (player.position or "").upper() == "GK"
    for _cat, items in obj.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or it.get("id") or "")
            val = _as_int(it.get("value"))
            if val is None:
                continue
            typ = str(it.get("type") or "")
            sid = str(it.get("id") or "")
            gk_name = it.get("gk_stat_name")

            if typ == "main":
                # composites (always useful for display)
                key = _norm_key(name)
                if key in {"physical", "heading"} and sid in {"heading", "physicality"}:
                    player.composites["physicality"] = val
                elif key == "dribbling" or sid in {"dribbling", "dribblingp"}:
                    player.composites["dribbling"] = val
                elif key in {"pace", "shooting", "passing", "defending", "physicality"}:
                    player.composites[key] = val
                # GK main face values only on GK cards (labels are dual-purpose on HTML)
                if is_gk and gk_name:
                    gle = _GK_STAT_NAME_TO_LE.get(_norm_key(str(gk_name)))
                    if gle:
                        player.stats[gle] = val
                continue

            # sub stats
            le = map_stat_name(sid) or map_stat_name(name)
            if le:
                player.stats[le] = val


# ---------------------------------------------------------------------------
# JSON dump importer
# ---------------------------------------------------------------------------

def import_json(data: Union[str, Mapping[str, Any], Path]) -> FutbinPlayer:
    """
    Import a player from JSON (file path, JSON string, or dict).

    Accepts FUTBIN_JSON_SCHEMA and common aliases (Rating, Weak Foot, etc.).
    """
    if isinstance(data, (str, Path)):
        p = Path(data)
        if p.is_file():
            obj = json.loads(p.read_text(encoding="utf-8"))
        else:
            obj = json.loads(str(data))
    else:
        obj = dict(data)

    if not isinstance(obj, dict):
        raise FutbinParseError("JSON root must be an object")

    # Nested under "player" / "data"
    for wrap in ("player", "data", "result"):
        if wrap in obj and isinstance(obj[wrap], dict):
            obj = {**obj, **obj[wrap]}

    player = FutbinPlayer(source="json")
    player.name = str(obj.get("name") or obj.get("Name") or "")
    player.fullname = str(obj.get("fullname") or obj.get("Fullname") or obj.get("Name") or "")
    player.rating = _as_int(obj.get("rating") or obj.get("Rating") or obj.get("overallrating"))
    player.position = str(obj.get("position") or obj.get("Position") or "").upper()
    pos_list = obj.get("positions") or obj.get("alt_positions") or []
    if isinstance(pos_list, str):
        pos_list = [p.strip() for p in pos_list.split(",") if p.strip()]
    player.positions = [str(p).upper() for p in pos_list]
    player.revision = str(obj.get("revision") or obj.get("Revision") or obj.get("version") or "")
    player.origin = str(obj.get("origin") or obj.get("Origin") or "")
    player.club = str(obj.get("club") or obj.get("Club") or "")
    player.nation = str(obj.get("nation") or obj.get("Nation") or obj.get("country") or "")
    player.league = str(obj.get("league") or obj.get("League") or "")
    player.skills = _as_int(obj.get("skills") or obj.get("Skills") or obj.get("skillmoves"))
    player.weak_foot = _as_int(
        obj.get("weak_foot") or obj.get("weakfoot") or obj.get("Weak Foot")
    )
    player.foot = str(obj.get("foot") or obj.get("Foot") or "")
    player.height_cm = _as_int(obj.get("height_cm") or obj.get("height"))
    player.weight_kg = _as_int(obj.get("weight_kg") or obj.get("weight"))
    player.att_wr = str(obj.get("att_wr") or obj.get("Att. WR") or "")
    player.def_wr = str(obj.get("def_wr") or obj.get("Def. WR") or "")
    player.year = _as_int(obj.get("year") or obj.get("game_year"))
    player.playerid = _as_int(
        obj.get("playerid") or obj.get("resource_id") or obj.get("ID") or obj.get("base_id")
    )
    player.futbin_id = _as_int(obj.get("futbin_id") or obj.get("card_id"))
    player.source_url = str(obj.get("source_url") or obj.get("url") or "")

    # stats dict
    stats_in = obj.get("stats") or obj.get("detailed_stats") or obj.get("attributes") or {}
    if isinstance(stats_in, dict):
        for k, v in stats_in.items():
            le = map_stat_name(str(k)) or (
                str(k).lower() if str(k).lower() in LE_CARD_HEADERS else None
            )
            iv = _as_int(v)
            if le and iv is not None:
                player.stats[le] = iv

    # flat keys at root (Acceleration, finishing, ...)
    for k, v in obj.items():
        if k in {
            "stats",
            "detailed_stats",
            "attributes",
            "composites",
            "positions",
            "player",
            "data",
        }:
            continue
        le = map_stat_name(str(k))
        iv = _as_int(v)
        if le and iv is not None and le not in player.stats:
            player.stats[le] = iv

    comps = obj.get("composites") or obj.get("face_stats") or {}
    if isinstance(comps, dict):
        for k, v in comps.items():
            iv = _as_int(v)
            if iv is not None:
                nk = _norm_key(str(k))
                if nk == "physical":
                    nk = "physicality"
                player.composites[nk] = iv

    # Full stats JSON blob style
    if "pace" in obj and isinstance(obj["pace"], list):
        _apply_stats_json(player, obj)

    if not player.name and not player.stats:
        raise FutbinParseError("JSON did not contain a recognizable player payload")
    return player


def import_html_file(path: PathLike, source_url: str = "") -> FutbinPlayer:
    p = Path(path)
    html = p.read_text(encoding="utf-8", errors="replace")
    # try to recover URL from HTML comment / canonical
    if not source_url:
        m = re.search(
            r'canonical["\']\s+href=["\']([^"\']+/player/[^"\']+)',
            html,
            re.I,
        )
        if m:
            source_url = m.group(1)
        else:
            m2 = re.search(r"https?://(?:www\.)?futbin\.com/\d{2}/player/\d+/[a-z0-9\-]+", html, re.I)
            if m2:
                source_url = m2.group(0)
    return parse_player_html(html, source_url=source_url, source="html")


# ---------------------------------------------------------------------------
# LE mapping
# ---------------------------------------------------------------------------

def to_le_card_row(
    player: FutbinPlayer,
    *,
    uid: int | str = "",
    defaults: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """
    Map FutbinPlayer → one row dict matching player_presets/cards.csv headers.

    Unmapped LE fields are left as defaults (0 / -1 / empty) so callers can
    merge with an existing base player row.
    """
    row: dict[str, Any] = {h: "" for h in LE_CARD_HEADERS}
    # sensible numeric defaults for unused slots
    for h in LE_CARD_HEADERS:
        if h.startswith("preferredposition") and h != "preferredposition1":
            row[h] = -1
        elif h in {
            "trait1",
            "trait2",
            "icontrait1",
            "icontrait2",
            "role1",
            "role2",
            "role3",
            "role4",
            "role5",
        }:
            row[h] = 0
        elif h.startswith("gk"):
            row[h] = 10

    if defaults:
        row.update(dict(defaults))

    row["uid"] = uid
    row["name"] = player.name or player.fullname
    row["revision"] = player.revision or "N/A"
    row["origin"] = player.origin or "N/A"
    row["playerid"] = player.playerid if player.playerid is not None else ""
    row["overallrating"] = player.rating if player.rating is not None else ""
    row["preferredposition1"] = position_to_code(player.position)
    extras = list(player.positions)
    for i, pos in enumerate(extras[:3], start=2):
        row[f"preferredposition{i}"] = position_to_code(pos)
    # Futbin reports skill moves as STARS (1-5); players.skillmoves is 0-indexed
    # (0 = 1 star), so stars must be converted or every player gains a star.
    row["skillmoves"] = (
        _stars_to_skillmoves(player.skills) if player.skills is not None else ""
    )
    # LE uses weakfootabilitytypecode; Futbin weak foot stars map 1:1
    row["weakfootabilitytypecode"] = (
        player.weak_foot if player.weak_foot is not None else ""
    )

    for le_col, val in player.stats.items():
        if le_col in row:
            row[le_col] = val

    # GK outfield defaults stay 10 unless stats overwrote
    return row


def to_le_base_player_patch(player: FutbinPlayer) -> dict[str, Any]:
    """
    Subset of base_players.csv fields that Futbin can reasonably supply.
    Caller merges into a full base row by playerid.
    """
    patch: dict[str, Any] = {}
    if player.playerid is not None:
        patch["playerid"] = player.playerid
    if player.rating is not None:
        patch["overallrating"] = player.rating
    if player.skills is not None:
        patch["skillmoves"] = _stars_to_skillmoves(player.skills)
    if player.weak_foot is not None:
        patch["weakfootabilitytypecode"] = player.weak_foot
    if player.height_cm is not None:
        patch["height"] = player.height_cm
    if player.weight_kg is not None:
        patch["weight"] = player.weight_kg
    if player.position:
        patch["preferredposition1"] = position_to_code(player.position)
    if player.foot:
        # FC: 1=Right, 2=Left (common LE convention)
        fl = player.foot.lower()
        if "left" in fl:
            patch["preferredfoot"] = 2
        elif "right" in fl:
            patch["preferredfoot"] = 1
    for le_col, val in player.stats.items():
        patch[le_col] = val
    # name parts
    if player.fullname:
        parts = player.fullname.split()
        if len(parts) >= 2:
            patch["firstname"] = parts[0]
            patch["surname"] = " ".join(parts[1:])
        else:
            patch["surname"] = player.fullname
    if player.name:
        patch["commonname"] = player.name
        patch["playerjerseyname"] = player.name
    return patch

