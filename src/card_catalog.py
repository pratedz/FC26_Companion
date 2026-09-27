"""Load multi-year card CSVs and search by name / year / overall."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from . import paths
from . import field_map
from .card_types import CardDict, CardRow

# Years that point at the local LE player_presets/cards.csv
# NOTE: "26" is a real FUT.GG year — do NOT alias it to local.
LOCAL_YEAR_ALIASES = frozenset({"local", "fc26-local", "current"})

# Attribute / meta columns we try to keep on unified cards
_UNIFIED_KEYS = (
    "year",
    "source",
    "name",
    "playerid",
    "overallrating",
    "potential",
    "skillmoves",
    "weakfootabilitytypecode",
    "preferredposition1",
    "preferredposition2",
    "preferredposition3",
    "preferredposition4",
    "revision",
    "origin",
    "uid",
    # Add-team / face / body (must not drop FUT.GG fields)
    "nationality",
    "nation",
    "nation_name",
    "basePlayerEaId",
    "base_id",
    "height",
    "weight",
    "bodytypecode",
    "hashighqualityhead",
    "isRealFace",
    "firstName",
    "lastName",
    "nickname",
    "slug",
    "eaId",
) + field_map.ATTR_FIELDS


def _norm(s: str) -> str:
    """Normalize for search; strip accents so Mbappe matches Mbappé."""
    import unicodedata

    t = re.sub(r"\s+", " ", (s or "").strip().lower())
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t


def _to_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    s = str(val).strip()
    # Pure int / playerid (full digits, not truncated)
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            return None
    # SoFIFA chemistry strings like "74+1" or "74-2"
    m = re.match(r"^(\d{1,3})([+-]\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    # Leading digits (e.g. "91cm")
    m = re.match(r"^(-?\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _year_from_filename(path: Path) -> str:
    """
    Infer year token from filename stem.
    Examples: fut18.csv -> 18, cards_2018.csv -> 2018, fifa17.csv -> 17
    """
    stem = path.stem.lower()
    # strip common prefixes
    for prefix in ("cards_", "card_", "fut_", "fut", "fifa_", "fifa", "fc_", "fc"):
        if stem.startswith(prefix) and len(stem) > len(prefix):
            rest = stem[len(prefix) :].lstrip("_-")
            if rest:
                stem = rest
                break
    m = re.search(r"(20\d{2}|\d{2})", stem)
    if m:
        return m.group(1)
    return stem


def unify_row(
    row: Dict[str, str],
    *,
    year: str,
    source: str,
) -> Dict[str, Any]:
    """
    Normalize a CSV row into a common card schema (LE field names + meta).
    """
    # Lowercase keys for alias lookup
    raw = {str(k).strip().lower(): (v if v is not None else "") for k, v in row.items()}
    mapped = field_map.map_card_keys(raw)

    # Prefer common name columns (SoFIFA / Futbin / FC25 "Player" / kafagy NAME)
    name = (
        mapped.get("name")
        or raw.get("name")
        or raw.get("player")
        or raw.get("short_name")
        or raw.get("long_name")
        or raw.get("playername")
        or raw.get("commonname")
        or raw.get("fullname")
        or raw.get("player name")
        or ""
    )
    if not name:
        first = raw.get("firstname") or raw.get("firstnameid") or ""
        last = raw.get("surname") or raw.get("lastname") or raw.get("lastnameid") or ""
        name = f"{first} {last}".strip()

    card: Dict[str, Any] = {
        "year": str(year),
        "source": source,
        "name": str(name).strip() if name else "",
    }

    # Overall / potential aliases used by FC25 and mixed dumps
    if "overallrating" not in mapped or mapped.get("overallrating") in (None, ""):
        for k in (
            "overall score",
            "overallscore",
            "best overall",
            "rating",
            "ovr",
        ):
            if raw.get(k) not in (None, ""):
                mapped["overallrating"] = raw[k]
                break
    if "potential" not in mapped or mapped.get("potential") in (None, ""):
        for k in ("potential score", "potentialscore", "pot"):
            if raw.get(k) not in (None, ""):
                mapped["potential"] = raw[k]
                break
    if "playerid" not in mapped or mapped.get("playerid") in (None, ""):
        for k in ("player id", "playerid", "sofifa_id", "id"):
            if raw.get(k) not in (None, ""):
                mapped["playerid"] = raw[k]
                break

    # Expand face-only PAC/SHO/… into sub-attrs before we drop raw face keys
    face_pairs = {
        "pace": ("acceleration", "sprintspeed"),
        "pac": ("acceleration", "sprintspeed"),
        "shooting": ("finishing", "shotpower", "longshots", "volleys", "penalties", "positioning"),
        "sho": ("finishing", "shotpower", "longshots", "volleys", "penalties", "positioning"),
        "passing": ("vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve"),
        "pas": ("vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve"),
        "dribbling": ("agility", "balance", "reactions", "ballcontrol", "dribbling", "composure"),
        "dri": ("agility", "balance", "reactions", "ballcontrol", "dribbling", "composure"),
        "defending": ("interceptions", "headingaccuracy", "defensiveawareness", "standingtackle", "slidingtackle"),
        "def": ("interceptions", "headingaccuracy", "defensiveawareness", "standingtackle", "slidingtackle"),
        "physical": ("jumping", "stamina", "strength", "aggression"),
        "physicality": ("jumping", "stamina", "strength", "aggression"),
        "phy": ("jumping", "stamina", "strength", "aggression"),
        "physic": ("jumping", "stamina", "strength", "aggression"),
    }
    for face_key, targets in face_pairs.items():
        if face_key not in raw or raw[face_key] in (None, ""):
            continue
        iv = _to_int(raw[face_key])
        if iv is None:
            continue
        iv = max(1, min(99, iv))
        for t in targets:
            if t not in mapped or mapped.get(t) in (None, ""):
                mapped[t] = iv

    for key in _UNIFIED_KEYS:
        if key in ("year", "source", "name"):
            continue
        if key in mapped and mapped[key] not in (None, ""):
            card[key] = mapped[key]
        elif key in raw and raw[key] not in (None, ""):
            # try alias again
            le = field_map.to_le_field(key)
            card[le or key] = raw[key]

    # Coerce numeric-ish fields
    for num_key in (
        "playerid",
        "overallrating",
        "potential",
        "skillmoves",
        "weakfootabilitytypecode",
        "preferredposition1",
        "preferredposition2",
        "preferredposition3",
        "preferredposition4",
        *field_map.ATTR_FIELDS,
    ):
        if num_key in card:
            iv = _to_int(card[num_key])
            if iv is not None:
                card[num_key] = iv

    # If potential missing, leave absent (card_to_lua can fall back)
    return card


def _read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        # Sniff delimiter lightly; default comma
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        return [dict(r) for r in reader]


def load_local_cards(
    csv_path: Optional[Union[str, Path]] = None,
    year_label: str = "local",
) -> List[Dict[str, Any]]:
    """Load LE player_presets/cards.csv as a year catalog."""
    p = Path(csv_path) if csv_path else paths.cards_csv_path()
    if not p.is_file():
        return []
    rows = _read_csv_dicts(p)
    return [
        unify_row(r, year=year_label, source=str(p))
        for r in rows
    ]


def load_futbin_cache(card_db: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load Futbin upgrade cards from card_db/futbin/*.json (live import cache)."""
    import json

    cards: List[Dict[str, Any]] = []
    db = Path(card_db) if card_db else paths.card_db_dir()
    futbin_dir = db / "futbin"
    if not futbin_dir.is_dir():
        return cards
    for path in sorted(futbin_dir.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            if not isinstance(item, dict):
                continue
            row = {str(k): "" if v is None else str(v) for k, v in item.items()}
            y = str(
                item.get("_year")
                or item.get("year")
                or item.get("Year")
                or _year_from_filename(path)
                or "futbin"
            )
            card = unify_row(row, year=y, source=f"futbin:{path.name}")
            card["source_kind"] = "futbin"
            cards.append(card)
    return cards


# Simple in-memory cache: key -> cards list
_YEAR_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _years_filter_set(years: Optional[Sequence[str]]) -> Optional[set]:
    if not years:
        return None
    out: set = set()
    for y in years:
        y = str(y).strip().lower()
        if not y:
            continue
        out.add(y)
        if y.isdigit() and len(y) == 2:
            out.add(str(2000 + int(y)))
        if y.isdigit() and len(y) == 4:
            out.add(str(int(y) % 100).zfill(2) if int(y) % 100 >= 10 else str(int(y) % 100))
            out.add(str(int(y) % 100))
    return out


def _year_wanted(y: str, wanted: Optional[set]) -> bool:
    if wanted is None:
        return True
    y = str(y).strip().lower()
    # Callers may pass ints (24) or strings ("24" / "2024") — normalize both sides.
    wanted_s = {str(x).strip().lower() for x in wanted}
    if y in wanted_s:
        return True
    if y.isdigit() and len(y) == 2 and str(2000 + int(y)) in wanted_s:
        return True
    if y.isdigit() and len(y) == 4 and str(int(y) % 100) in wanted_s:
        return True
    return False


def load_card_db(
    card_db: Optional[Union[str, Path]] = None,
    *,
    include_local: bool = True,
    include_futbin: bool = True,
    local_csv: Optional[Union[str, Path]] = None,
    years: Optional[Sequence[str]] = None,
    progress: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Load card_db CSVs + optional local LE cards + Futbin/FUT.GG caches.

    years: if set (e.g. ["18"]), only load files matching that year — skips
    incomplete FC26 bulk downloads when you search pre-downloaded 18–25.
    """
    wanted = _years_filter_set(years)
    cache_key = (
        f"y={','.join(sorted(wanted)) if wanted else 'ALL'}|"
        f"local={include_local}|fb={include_futbin}"
    )
    if cache_key in _YEAR_CACHE:
        if progress:
            progress(1.0, f"cache hit ({len(_YEAR_CACHE[cache_key])} cards)")
        return _YEAR_CACHE[cache_key]

    def prog(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    cards: List[Dict[str, Any]] = []
    db = Path(card_db) if card_db else paths.card_db_dir()
    csv_files: List[Path] = []
    if db.is_dir():
        for csv_path in sorted(db.glob("*.csv")):
            if csv_path.name.startswith("_"):
                continue
            year = _year_from_filename(csv_path)
            if wanted is not None and not _year_wanted(year, wanted):
                # also allow if any row year might match — filename is enough for our dumps
                continue
            csv_files.append(csv_path)

    n_files = max(len(csv_files), 1)
    for i, csv_path in enumerate(csv_files):
        year = _year_from_filename(csv_path)
        prog(0.05 + 0.55 * (i / n_files), f"loading {csv_path.name}…")
        try:
            rows = _read_csv_dicts(csv_path)
        except Exception:
            continue
        for r in rows:
            y = str(r.get("year") or r.get("Year") or year)
            if wanted is not None and not _year_wanted(y, wanted):
                continue
            cards.append(unify_row(r, year=y, source=str(csv_path)))

    if include_futbin:
        prog(0.65, "loading Futbin cache…")
        for c in load_futbin_cache(db):
            if wanted is None or _year_wanted(str(c.get("year", "")), wanted) or (
                "futbin" in wanted and c.get("source_kind") == "futbin"
            ):
                cards.append(c)
        prog(0.75, "loading FUT.GG cache…")
        for c in load_futgg_cache(db, years=wanted):
            cards.append(c)

    if include_local and (wanted is None or bool(wanted & LOCAL_YEAR_ALIASES) or "local" in (wanted or set())):
        prog(0.9, "loading LE local presets…")
        cards.extend(load_local_cards(local_csv, year_label="local"))

    prog(1.0, f"ready ({len(cards)} cards)")
    _YEAR_CACHE[cache_key] = cards
    return cards


def clear_catalog_cache(*, drop_index: bool = False) -> None:
    _YEAR_CACHE.clear()
    if drop_index:
        try:
            from . import card_index

            card_index.invalidate()
        except Exception:
            pass


def load_futgg_cache(
    card_db: Optional[Union[str, Path]] = None,
    years: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Load bulk FUT.GG dumps (card_db/futgg/*.jsonl and by_player/).

    Skips *.partial files (in-progress bulk download).
    years: optional set of year tokens to include.
    Streams line-by-line (no full-file read into one string).
    """
    cards: List[Dict[str, Any]] = []
    db = Path(card_db) if card_db else paths.card_db_dir()
    root = db / "futgg"
    if not root.is_dir():
        return cards
    files = [p for p in root.glob("*.jsonl") if ".partial" not in p.name]
    by_player = root / "by_player"
    if by_player.is_dir():
        files.extend(p for p in by_player.glob("*.jsonl") if ".partial" not in p.name)
    for path in files:
        # futgg_26.jsonl → only when year 26 wanted
        year_guess = _year_from_filename(path)
        if years is not None and path.parent.name != "by_player":
            if not _year_wanted(year_guess, years):
                continue
        try:
            from . import futgg_client as _futgg
        except Exception:
            _futgg = None  # type: ignore
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    y = str(
                        obj.get("_year") or obj.get("year") or obj.get("game") or year_guess or "futgg"
                    )
                    if years is not None and not _year_wanted(y, years):
                        continue
                    # Already-normalized jsonl: use as-is. Raw API defs: map once.
                    card: Dict[str, Any] = {}
                    if _futgg is not None and hasattr(_futgg, "is_le_normalized_row"):
                        try:
                            if _futgg.is_le_normalized_row(obj):
                                card = dict(obj)
                            elif hasattr(_futgg, "definition_to_le_row"):
                                card = dict(_futgg.definition_to_le_row(obj))
                        except Exception:
                            card = {}
                    elif _futgg is not None and hasattr(_futgg, "definition_to_le_row"):
                        try:
                            card = dict(_futgg.definition_to_le_row(obj))
                        except Exception:
                            card = {}
                    if not card.get("name"):
                        row = {
                            str(k): "" if v is None else str(v) for k, v in obj.items()
                        }
                        card = unify_row(row, year=y, source=f"futgg:{path.name}")
                    card["year"] = str(card.get("year") or y)
                    card["source"] = f"futgg:{path.name}"
                    card["source_kind"] = "futgg"
                    # Preserve base id + nation aliases for Add team face/nation
                    base = (
                        card.get("basePlayerEaId")
                        or obj.get("basePlayerEaId")
                        or card.get("playerid")
                    )
                    if base not in (None, ""):
                        try:
                            card["basePlayerEaId"] = int(base)
                            card["base_id"] = int(base)
                        except (TypeError, ValueError):
                            pass
                    if card.get("nationality") in (None, "") and obj.get("nationEaId") not in (
                        None,
                        "",
                    ):
                        card["nationality"] = obj.get("nationEaId")
                    if card.get("nationality") in (None, "") and obj.get("nationality") not in (
                        None,
                        "",
                    ):
                        card["nationality"] = obj.get("nationality")
                    cards.append(card)
        except OSError:
            continue
    return cards


def _year_matches(card_year: str, query_year: Optional[str]) -> bool:
    if query_year is None or str(query_year).strip() == "":
        return True
    q = str(query_year).strip().lower()
    cy = str(card_year).strip().lower()
    if q in LOCAL_YEAR_ALIASES:
        return cy in LOCAL_YEAR_ALIASES or cy == "local"
    if q in ("futbin", "fb"):
        return cy in ("futbin", "fb") or "futbin" in cy
    # normalize 2018 <-> 18
    if q.isdigit() and cy.isdigit():
        qi, ci = int(q), int(cy)
        if qi == ci:
            return True
        if qi >= 1000 and ci < 100:
            return (qi % 100) == ci
        if ci >= 1000 and qi < 100:
            return (ci % 100) == qi
        return False
    return q == cy


def search_cards(
    query: str = "",
    *,
    year: Optional[str] = None,
    ovr: Optional[int] = None,
    ovr_min: Optional[int] = None,
    ovr_max: Optional[int] = None,
    cards: Optional[Sequence[CardDict]] = None,
    limit: int = 50,
    card_db: Optional[Union[str, Path]] = None,
    include_local: bool = True,
    progress: Optional[Any] = None,
    use_index: bool = True,
) -> List[CardRow]:
    """
    Search loaded cards by substring name, optional year and overall filters.
    If cards is None, prefers SQLite index (card_db/catalog.sqlite), else loads
    from disk — scoped to `year` when set so pre-downloaded 18–25 searches do
    not scan an in-progress FC26 bulk dump.
    """
    # Fast path: SQLite index (no re-parse of multi-MB CSVs per search)
    if cards is None and use_index:
        try:
            from . import card_index

            hits = card_index.search(
                query,
                year=year,
                ovr=ovr,
                ovr_min=ovr_min,
                ovr_max=ovr_max,
                limit=limit,
                card_db=Path(card_db) if card_db else None,
                progress=progress,
            )
            if hits is not None:
                return hits
        except Exception:
            pass

    pool: Sequence[Dict[str, Any]]
    if cards is not None:
        pool = cards
    else:
        years = [year] if year and str(year).strip() else None
        # empty year = all pre-downloaded career dumps 18-25 + local, skip unfinished futgg_26 unless present complete
        if years is None:
            years = ["18", "19", "20", "21", "22", "23", "24", "25", "local"]
            # include 26 only if a non-partial futgg_26.jsonl exists
            fut26 = paths.card_db_dir() / "futgg" / "futgg_26.jsonl"
            if fut26.is_file() and fut26.stat().st_size > 1000:
                years.append("26")
            fut27 = paths.card_db_dir() / "futgg" / "futgg_27.jsonl"
            if fut27.is_file() and fut27.stat().st_size > 1000:
                years.append("27")
        pool = load_card_db(
            card_db,
            include_local=include_local,
            years=years,
            progress=progress,
        )

    q = _norm(query)
    yq = str(year).strip().lower() if year and str(year).strip() else ""
    results: List[Dict[str, Any]] = []
    for card in pool:
        if yq in ("futbin", "fb"):
            # Live Futbin cache only (not CSVs with "futbin" in the filename)
            src = str(card.get("source") or "")
            sk = str(card.get("source_kind") or "")
            if sk != "futbin" and not src.lower().startswith("futbin:"):
                continue
        elif not _year_matches(str(card.get("year", "")), year):
            continue
        name = _norm(str(card.get("name", "")))
        rev = _norm(str(card.get("revision") or card.get("origin") or ""))
        if q and q not in name and q not in rev:
            continue
        card_ovr = _to_int(card.get("overallrating"))
        if ovr is not None and card_ovr != int(ovr):
            continue
        if ovr_min is not None and (card_ovr is None or card_ovr < int(ovr_min)):
            continue
        if ovr_max is not None and (card_ovr is None or card_ovr > int(ovr_max)):
            continue
        results.append(card)

    # Prefer higher overall, then name (specials first when searching a star)
    def _sort_key(c: Dict[str, Any]) -> tuple:
        o = _to_int(c.get("overallrating")) or 0
        return (-o, _norm(str(c.get("name") or "")))

    results.sort(key=_sort_key)
    # Dedupe identical FUT cards (e.g. by_player + bulk year dump)
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for c in results:
        key = (
            str(c.get("slug") or ""),
            str(c.get("name") or ""),
            str(c.get("overallrating") or ""),
            str(c.get("revision") or ""),
            str(c.get("year") or ""),
            str(c.get("playerid") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    results = deduped
    if limit and len(results) > limit:
        results = results[:limit]
    return results


# LE preferredposition codes → short labels (for scanable list rows)
_POS_CODE_TO_ABBR: Dict[int, str] = {
    0: "GK",
    1: "SW",
    2: "RWB",
    3: "RB",
    4: "RCB",
    5: "CB",
    6: "LCB",
    7: "LB",
    8: "LWB",
    9: "RDM",
    10: "CDM",
    11: "LDM",
    12: "RM",
    13: "RCM",
    14: "CM",
    15: "LCM",
    16: "LM",
    17: "RAM",
    18: "CAM",
    19: "LAM",
    20: "RF",
    21: "CF",
    22: "LF",
    23: "RW",
    24: "RS",
    25: "ST",
    26: "LS",
    27: "LW",
}


def _pos_abbr(card: Dict[str, Any]) -> str:
    """Resolve preferredposition1 / position to a 2–3 letter label."""
    raw = card.get("preferredposition1")
    if raw in (None, ""):
        raw = card.get("position")
    if raw in (None, ""):
        return "—"
    if isinstance(raw, bool):
        return "—"
    if isinstance(raw, int) or (isinstance(raw, str) and str(raw).strip().lstrip("-").isdigit()):
        try:
            return _POS_CODE_TO_ABBR.get(int(raw), str(int(raw)))
        except (TypeError, ValueError):
            pass
    s = str(raw).upper().split(",")[0].split("/")[0].strip()
    if not s:
        return "—"
    return s[:3]


def format_card_line(card: CardDict, index: int = 0) -> str:
    """Fixed-column mono row for listboxes: ``91  ST  Neymar Jr        26  TOTY``.

    ``index`` kept for API compatibility (unused; columns replace [n] prefixes).
    """
    _ = index
    ovr = card.get("overallrating", "?")
    try:
        ovr_s = f"{int(ovr):>2}"
    except (TypeError, ValueError):
        ovr_s = f"{str(ovr if ovr not in (None, '') else '?'):>2}"[:2].rjust(2)

    pos = _pos_abbr(card)
    name = str(card.get("name") or "?")
    if len(name) > 18:
        name = name[:17] + "…"
    name_s = f"{name:<18}"

    year = card.get("year", "?")
    year_s = str(year if year not in (None, "") else "?")
    if year_s.startswith("20") and len(year_s) == 4 and year_s[2:].isdigit():
        year_s = year_s[2:]
    year_s = f"{year_s:>2}"[:4]

    rev = card.get("revision") or card.get("origin") or ""
    if isinstance(rev, dict):
        rev = rev.get("name") or rev.get("slug") or ""
    rev = str(rev).strip()
    if len(rev) > 16:
        rev = rev[:15] + "…"

    # Two spaces between fixed fields so Consolas columns align in Listbox
    return f"{ovr_s}  {pos:<3}  {name_s}  {year_s}  {rev}".rstrip()
