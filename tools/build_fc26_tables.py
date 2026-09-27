"""Generate ``src/generated/fc26_tables.py`` from the files that ship with FC 26.

Run from the companion root::

    python tools/build_fc26_tables.py            # regenerate
    python tools/build_fc26_tables.py --check    # fail if the file is stale

Sources (all read-only, none of them are written back):

* ``<le_root>/lua/scripts/fix_players_headmodels.lua`` — Live Editor's own list of
  playerids that ship with a head model, plus the player name in each comment.
* ``<le_root>/player_presets/base_players.csv`` — every base player row; supplies
  the second half of the real-head set and the empirical generic-head map.
* ``<app_root>/card_db/fc26_datahub.csv`` — carries id *and* name for nation,
  league and club, which is what makes the reverse lookups authoritative.

The output is plain Python literals (pure ASCII, deterministic order) so it can be
diffed, and so PyInstaller picks it up as ordinary package data. See
``src/fc26_data.py`` for why each table exists.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.fc26_data import normalize_name  # noqa: E402  (needs sys.path above)

DEFAULT_OUT = APP_ROOT / "src" / "generated" / "fc26_tables.py"

# Live Editor writes 149-column rows; the default 128 KiB field cap is plenty,
# but a stray quote in a name field can produce one huge logical field.
csv.field_size_limit(10 * 1024 * 1024)

# ``    [158023] = true, -- Lionel Messi``
_LUA_ENTRY = re.compile(
    r"^\s*\[(?P<id>\d+)\]\s*=\s*true\s*,?\s*(?:--\s*(?P<name>.*?))?\s*$"
)
_LUA_LOOKS_LIKE_ENTRY = re.compile(r"^\s*\[\s*\d+\s*\]")

# Club-name noise that users drop when typing ("FC Bayern München" -> "bayern
# munchen"). Only used to build a secondary index, and only where the reduced
# form stays unique.
_CLUB_NOISE = frozenset(
    """ac afc bk bsc ca cd cf club de fc fk fsv gnk hnk if ik nk rc rcd sc sd sk
    ss ssc sv tsv ud us vfb vfl""".split()
)


class BuildError(RuntimeError):
    """A source file is missing or unparseable."""


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_lua_heads(path: Path) -> Tuple[Dict[int, str], List[str]]:
    """playerid -> name from the ``valid_headmodels`` table. Also returns warnings."""
    if not path.is_file():
        raise BuildError("missing head-model script: " + str(path))
    heads: Dict[int, str] = {}
    warnings: List[str] = []
    duplicates = 0
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        if not _LUA_LOOKS_LIKE_ENTRY.match(line):
            continue
        match = _LUA_ENTRY.match(line)
        if match is None:
            warnings.append("lua line %d not understood: %s" % (lineno, line.strip()))
            continue
        pid = int(match.group("id"))
        name = (match.group("name") or "").strip()
        if pid in heads:
            duplicates += 1
        heads[pid] = name or heads.get(pid, "")
    if duplicates:
        warnings.append("lua table has %d duplicate playerids" % duplicates)
    if not heads:
        raise BuildError("no head-model entries parsed from " + str(path))
    return heads, warnings


def _int_field(row: Mapping[str, str], column: str) -> Optional[int]:
    raw = (row.get(column) or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _base_display_name(row: Mapping[str, str]) -> str:
    common = (row.get("commonname") or "").strip()
    if common:
        return common
    parts = [
        (row.get("firstname") or "").strip(),
        (row.get("surname") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def read_base_players(
    path: Path,
) -> Tuple[Dict[int, str], Dict[int, Counter], int, List[str]]:
    """Real-head ids (with names), nationality -> headtypecode counts, row count."""
    if not path.is_file():
        raise BuildError("missing base players csv: " + str(path))
    real: Dict[int, str] = {}
    generic: Dict[int, Counter] = {}
    warnings: List[str] = []
    rows = 0
    missing_nation = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for column in (
            "playerid",
            "hashighqualityhead",
            "headclasscode",
            "headtypecode",
            "nationality",
        ):
            if column not in (reader.fieldnames or ()):
                raise BuildError(
                    "base_players.csv has no %r column (got %d columns)"
                    % (column, len(reader.fieldnames or ()))
                )
        for row in reader:
            rows += 1
            pid = _int_field(row, "playerid")
            if pid is None or pid <= 0:
                continue
            head_class = _int_field(row, "headclasscode")
            high_quality = _int_field(row, "hashighqualityhead")
            if high_quality == 1 or head_class == 0:
                real[pid] = _base_display_name(row)
                continue
            if head_class == 1:
                nation = _int_field(row, "nationality")
                head_type = _int_field(row, "headtypecode")
                if nation is None:
                    missing_nation += 1
                    continue
                if head_type is None:
                    continue
                generic.setdefault(nation, Counter())[head_type] += 1
    if missing_nation:
        warnings.append(
            "%d generic-head rows have no nationality and were skipped" % missing_nation
        )
    if not real:
        raise BuildError("no real-head rows found in " + str(path))
    return real, generic, rows, warnings


def read_datahub(
    path: Path,
) -> Tuple[Dict[str, Dict[int, str]], Dict[str, Counter], int, List[str]]:
    """id -> name for nation/league/club, plus how many players back each id."""
    if not path.is_file():
        raise BuildError("missing datahub csv: " + str(path))
    columns = {
        "nation": ("nationality_id", "nationality_name"),
        "league": ("league_id", "league_name"),
        "team": ("club_team_id", "club_name"),
    }
    names: Dict[str, Dict[int, Counter]] = {k: {} for k in columns}
    weights: Dict[str, Counter] = {k: Counter() for k in columns}
    warnings: List[str] = []
    rows = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        header = set(reader.fieldnames or ())
        for kind, (id_col, name_col) in columns.items():
            missing = [c for c in (id_col, name_col) if c not in header]
            if missing:
                raise BuildError(
                    "fc26_datahub.csv is missing %s columns %s"
                    % (kind, ", ".join(missing))
                )
        for row in reader:
            rows += 1
            for kind, (id_col, name_col) in columns.items():
                key = _int_field(row, id_col)
                value = (row.get(name_col) or "").strip()
                if key is None or not value:
                    continue
                names[kind].setdefault(key, Counter())[value] += 1
                weights[kind][key] += 1
    resolved: Dict[str, Dict[int, str]] = {}
    for kind, by_id in names.items():
        if not by_id:
            raise BuildError("no %s rows parsed from %s" % (kind, path))
        out: Dict[int, str] = {}
        for key, counter in by_id.items():
            if len(counter) > 1:
                variants = ", ".join(sorted(counter))
                warnings.append(
                    "%s id %d has several names (%s); kept the most common"
                    % (kind, key, variants)
                )
            # most rows wins, ties broken alphabetically so output is stable
            out[key] = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        resolved[kind] = out
    return resolved, weights, rows, warnings


# --------------------------------------------------------------------------
# derived indexes
# --------------------------------------------------------------------------
def build_reverse_index(
    id_to_name: Mapping[int, str], weights: Mapping[int, int]
) -> Tuple[Dict[str, int], Dict[str, Tuple[int, ...]]]:
    """normalized name -> id, plus the ambiguous names and every id they match.

    A name can legitimately belong to several ids ("Premier League" is England's
    13 and Russia's 332). The winner is the id with the most players, then the
    lowest id, so the choice never depends on dict ordering.
    """
    candidates: Dict[str, List[int]] = {}
    for key, name in id_to_name.items():
        norm = normalize_name(name)
        if not norm:
            continue
        candidates.setdefault(norm, []).append(key)
    index: Dict[str, int] = {}
    collisions: Dict[str, Tuple[int, ...]] = {}
    for norm, ids in candidates.items():
        ordered = sorted(ids, key=lambda i: (-weights.get(i, 0), i))
        index[norm] = ordered[0]
        if len(ids) > 1:
            collisions[norm] = tuple(sorted(ids))
    return index, collisions


def build_loose_index(
    id_to_name: Mapping[int, str], strict: Mapping[str, int]
) -> Dict[str, int]:
    """Secondary club index with "FC"/"CF"/... dropped, unique reductions only."""
    reduced: Dict[str, List[int]] = {}
    for key, name in id_to_name.items():
        norm = normalize_name(name)
        tokens = [t for t in norm.split() if t not in _CLUB_NOISE]
        short = " ".join(tokens)
        if not short or short == norm or short in strict:
            continue
        reduced.setdefault(short, []).append(key)
    return {k: v[0] for k, v in sorted(reduced.items()) if len(v) == 1}


def flatten_generic(
    generic: Mapping[int, Counter]
) -> Tuple[Dict[int, Tuple[Tuple[int, int], ...]], Tuple[Tuple[int, int], ...]]:
    """nation -> ((headtypecode, uses), ...) sorted by uses desc, plus the global mix."""
    out: Dict[int, Tuple[Tuple[int, int], ...]] = {}
    overall: Counter = Counter()
    for nation, counter in generic.items():
        out[nation] = tuple(
            sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        overall.update(counter)
    return out, tuple(sorted(overall.items(), key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------
def _ints(values: Iterable[int], per_line: int = 12, indent: str = "    ") -> str:
    items = list(values)
    lines: List[str] = []
    for start in range(0, len(items), per_line):
        chunk = items[start : start + per_line]
        lines.append(indent + " ".join("%d," % v for v in chunk))
    return "\n".join(lines)


def _int_str_dict(mapping: Mapping[int, str], indent: str = "    ") -> str:
    return "\n".join(
        "%s%d: %s," % (indent, key, ascii(mapping[key])) for key in sorted(mapping)
    )


def _str_int_dict(mapping: Mapping[str, int], indent: str = "    ") -> str:
    return "\n".join(
        "%s%s: %d," % (indent, ascii(key), mapping[key]) for key in sorted(mapping)
    )


def _str_ints_dict(
    mapping: Mapping[str, Sequence[int]], indent: str = "    "
) -> str:
    return "\n".join(
        "%s%s: (%s),"
        % (indent, ascii(key), " ".join("%d," % v for v in mapping[key]))
        for key in sorted(mapping)
    )


def _pairs(pairs: Sequence[Tuple[int, int]]) -> str:
    return " ".join("(%d, %d)," % (a, b) for a, b in pairs)


def _weighted_dict(
    mapping: Mapping[int, Sequence[Tuple[int, int]]], indent: str = "    "
) -> str:
    return "\n".join(
        "%s%d: (%s)," % (indent, key, _pairs(mapping[key])) for key in sorted(mapping)
    )


def _sources_literal(sources: Mapping[str, Mapping[str, object]]) -> str:
    lines = []
    for key in sorted(sources):
        info = sources[key]
        body = ", ".join(
            "%s: %s" % (ascii(field), ascii(info[field])) for field in sorted(info)
        )
        lines.append("    %s: {%s}," % (ascii(key), body))
    return "\n".join(lines)


_HEADER = '''"""Generated FC 26 lookup tables - do not edit by hand.

Rebuild with ``python tools/build_fc26_tables.py``. Every value here comes from a
file that ships with the game or Live Editor; see ``src/fc26_data.py`` for the
public API and for why each table exists. Pure ASCII and sorted, so a rebuild
only diffs where the game data actually changed.
"""
'''


def _block(declaration: str, body: str, close: str, comment: str = "") -> str:
    """One ``NAME: type = {...}`` literal, skipping the body when it is empty."""
    lines: List[str] = []
    if comment:
        lines.append(comment)
    lines.append(declaration)
    if body:
        lines.append(body)
    lines.append(close)
    return "\n".join(lines)


def render_module(
    real_heads: Sequence[int],
    head_names: Mapping[int, str],
    nations: Mapping[int, str],
    nation_ids: Mapping[str, int],
    nation_collisions: Mapping[str, Sequence[int]],
    leagues: Mapping[int, str],
    league_ids: Mapping[str, int],
    league_collisions: Mapping[str, Sequence[int]],
    teams: Mapping[int, str],
    team_ids: Mapping[str, int],
    team_ids_loose: Mapping[str, int],
    team_collisions: Mapping[str, Sequence[int]],
    generic: Mapping[int, Sequence[Tuple[int, int]]],
    generic_any: Sequence[Tuple[int, int]],
    sources: Mapping[str, Mapping[str, object]],
) -> str:
    blocks: List[str] = [
        _HEADER,
        _block(
            "SOURCES: dict[str, dict[str, object]] = {",
            _sources_literal(sources),
            "}",
            "#: Provenance of every table below: source path, size and sha256.",
        ),
        _block(
            "REAL_HEADS: frozenset[int] = frozenset((",
            _ints(real_heads),
            "))",
            "#: Playerids that ship with a real (scanned) head model.",
        ),
        _block(
            "HEAD_NAMES: dict[int, str] = {",
            _int_str_dict(head_names),
            "}",
            "#: Playerid -> display name. A few REAL_HEADS ids are nameless"
            " placeholder rows and are absent here.",
        ),
        _block("NATION_NAMES: dict[int, str] = {", _int_str_dict(nations), "}"),
        _block("NATION_IDS: dict[str, int] = {", _str_int_dict(nation_ids), "}"),
        _block(
            "NATION_ID_COLLISIONS: dict[str, tuple[int, ...]] = {",
            _str_ints_dict(nation_collisions),
            "}",
        ),
        _block("LEAGUE_NAMES: dict[int, str] = {", _int_str_dict(leagues), "}"),
        _block("LEAGUE_IDS: dict[str, int] = {", _str_int_dict(league_ids), "}"),
        _block(
            "LEAGUE_ID_COLLISIONS: dict[str, tuple[int, ...]] = {",
            _str_ints_dict(league_collisions),
            "}",
            "#: Names shared by several leagues; LEAGUE_IDS keeps the biggest one.",
        ),
        _block("TEAM_NAMES: dict[int, str] = {", _int_str_dict(teams), "}"),
        _block("TEAM_IDS: dict[str, int] = {", _str_int_dict(team_ids), "}"),
        _block(
            "TEAM_IDS_LOOSE: dict[str, int] = {",
            _str_int_dict(team_ids_loose),
            "}",
            '#: Club names with "FC"/"CF"/... dropped, where that stays unambiguous.',
        ),
        _block(
            "TEAM_ID_COLLISIONS: dict[str, tuple[int, ...]] = {",
            _str_ints_dict(team_collisions),
            "}",
        ),
        _block(
            "GENERIC_HEADTYPES: dict[int, tuple[tuple[int, int], ...]] = {",
            _weighted_dict(generic),
            "}",
            "#: Nationality -> ((headtypecode, base players using it), ...),"
            " most used first.",
        ),
        _block(
            "GENERIC_HEADTYPES_ANY: tuple[tuple[int, int], ...] = (",
            "    " + _pairs(generic_any),
            ")",
            "#: The same distribution over every nationality (fallback pool).",
        ),
    ]
    return "\n\n".join(blocks) + "\n"


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def build(le_root: Path, app_root: Path) -> Tuple[str, Dict[str, object], List[str]]:
    """Render the module text; also returns row counts and non-fatal warnings."""
    lua_path = le_root / "lua" / "scripts" / "fix_players_headmodels.lua"
    base_path = le_root / "player_presets" / "base_players.csv"
    hub_path = app_root / "card_db" / "fc26_datahub.csv"

    lua_heads, warnings = parse_lua_heads(lua_path)
    base_real, generic_counts, base_rows, base_warnings = read_base_players(base_path)
    hub, hub_weights, hub_rows, hub_warnings = read_datahub(hub_path)
    warnings = warnings + base_warnings + hub_warnings

    # Union of both authorities; the Lua comment wins as the display name because
    # it is the name EA shows, while base_players splits first/last.
    real_heads: Dict[int, str] = {}
    for pid, name in base_real.items():
        real_heads[pid] = name
    for pid, name in lua_heads.items():
        if name or pid not in real_heads:
            real_heads[pid] = name
    head_names = {pid: name for pid, name in real_heads.items() if name}
    unnamed = sorted(pid for pid, name in real_heads.items() if not name)
    if unnamed:
        warnings.append(
            "%d real-head ids carry no name in either source and are omitted from "
            "HEAD_NAMES (blank base_players rows, e.g. %s)"
            % (len(unnamed), ", ".join(str(p) for p in unnamed[:3]))
        )

    nation_ids, nation_collisions = build_reverse_index(
        hub["nation"], hub_weights["nation"]
    )
    league_ids, league_collisions = build_reverse_index(
        hub["league"], hub_weights["league"]
    )
    team_ids, team_collisions = build_reverse_index(hub["team"], hub_weights["team"])
    team_ids_loose = build_loose_index(hub["team"], team_ids)
    generic, generic_any = flatten_generic(generic_counts)

    sources = {
        "headmodels_lua": {
            "path": _relative(lua_path, le_root),
            "bytes": lua_path.stat().st_size,
            "sha256": _sha256(lua_path),
            "entries": len(lua_heads),
        },
        "base_players_csv": {
            "path": _relative(base_path, le_root),
            "bytes": base_path.stat().st_size,
            "sha256": _sha256(base_path),
            "rows": base_rows,
        },
        "fc26_datahub_csv": {
            "path": _relative(hub_path, le_root),
            "bytes": hub_path.stat().st_size,
            "sha256": _sha256(hub_path),
            "rows": hub_rows,
        },
    }

    text = render_module(
        sorted(real_heads),
        head_names,
        hub["nation"],
        nation_ids,
        nation_collisions,
        hub["league"],
        league_ids,
        league_collisions,
        hub["team"],
        team_ids,
        team_ids_loose,
        team_collisions,
        generic,
        generic_any,
        sources,
    )
    stats: Dict[str, object] = {
        "lua_head_entries": len(lua_heads),
        "base_rows": base_rows,
        "base_real_heads": len(base_real),
        "real_heads": len(real_heads),
        "real_heads_named": len(head_names),
        "real_heads_lua_only": len(set(lua_heads) - set(base_real)),
        "real_heads_base_only": len(set(base_real) - set(lua_heads)),
        "datahub_rows": hub_rows,
        "nations": len(hub["nation"]),
        "leagues": len(hub["league"]),
        "teams": len(hub["team"]),
        "league_name_collisions": len(league_collisions),
        "team_name_collisions": len(team_collisions),
        "generic_nations": len(generic),
        "generic_pairs": sum(len(v) for v in generic.values()),
        "generic_headtypes": len(generic_any),
    }
    warnings.extend(_sanity_warnings(nation_ids, hub, real_heads))
    return text, stats, warnings


def _sanity_warnings(
    nation_ids: Mapping[str, int],
    hub: Mapping[str, Mapping[int, str]],
    real_heads: Mapping[int, str],
) -> List[str]:
    """Loud, cheap checks against values that are known-good in FC 26."""
    problems: List[str] = []
    for name, expected in (("spain", 45), ("italy", 27), ("nigeria", 133)):
        actual = nation_ids.get(name)
        if actual != expected:
            problems.append(
                "nation %r resolved to %r, expected %d" % (name, actual, expected)
            )
    if hub["team"].get(243) != "Real Madrid":
        problems.append("team 243 is %r, expected Real Madrid" % hub["team"].get(243))
    for pid, who in ((1397, "Zidane"), (158023, "Messi")):
        if pid not in real_heads:
            problems.append("%s (%d) is missing from the real-head set" % (who, pid))
    return problems


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="ascii", newline="\n")
    os.replace(tmp, path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--le-root",
        type=Path,
        default=APP_ROOT.parent,
        help="FC 26 Live Editor install root (default: the companion's parent)",
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=APP_ROOT,
        help="companion root that holds card_db/ (default: this checkout)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the generated file is missing or out of date",
    )
    args = parser.parse_args(argv)

    try:
        text, stats, warnings = build(args.le_root, args.app_root)
    except BuildError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.check:
        current = args.out.read_text(encoding="ascii") if args.out.is_file() else ""
        if current != text:
            print("stale: %s needs regenerating" % args.out, file=sys.stderr)
            return 1
        print("up to date: %s" % args.out)
        return 0

    _write(args.out, text)
    init = args.out.parent / "__init__.py"
    if not init.is_file():
        init.write_text(
            '"""Generated data tables. Do not edit; see tools/build_fc26_tables.py."""\n',
            encoding="ascii",
            newline="\n",
        )

    size = args.out.stat().st_size
    print("wrote %s (%.1f KiB)" % (args.out, size / 1024.0))
    for key in sorted(stats):
        print("  %-24s %s" % (key, stats[key]))
    if size > 2 * 1024 * 1024:
        warnings.append("generated file is %.1f MiB, over the 2 MiB budget" % (size / 1048576.0))
    for warning in warnings:
        print("  warning: %s" % warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
