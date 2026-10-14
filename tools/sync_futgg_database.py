#!/usr/bin/env python3
"""Sync, audit and install a complete FUT.GG card catalog (FC26 or FC27).

The FUT.GG API caps an unfiltered listing at 10,000 cards.  The client now
partitions by rating, validates every definition, and only replaces the cache
after a complete download.  This wrapper records the released squads as an
auditable coverage manifest and rebuilds both local indexes.

``--year`` selects the game (default 27).  Every managed year's FUT.GG slice
is replaced only from its own verified dump; all other rows are hashed before
and after an install and must stay byte-identical.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import card_index, futgg_client
from tools import build_universe as ub
from companion.domain.card_collections import campaign_label

#: Released squads audited against the dump, per game year.  FC26 coverage is
#: audited per collection by ``tools/verify_card_collections.py`` instead.
SQUAD_AUDITS: dict[int, dict[str, str]] = {
    27: {
        "Ones to Watch (Season 1)": "https://www.fut.gg/season-pass/",
        "TOTW 1": "https://www.fut.gg/squads/team-of-the-week/totw-1/",
        "TOTW 2": "https://www.fut.gg/squads/team-of-the-week/totw-2/",
        "Destined for Glory Team 1": "https://www.fut.gg/squads/destined-for-glory/destined-for-glory-1/",
        "TOTW 3": "https://www.fut.gg/squads/team-of-the-week/totw-3/",
        "Destined for Glory Team 2": "https://www.fut.gg/squads/destined-for-glory/destined-for-glory-2/",
        "TOTW 4": "https://www.fut.gg/squads/team-of-the-week/totw-4/",
        "Future Stars Team 1": "https://www.fut.gg/squads/future-stars/future-stars-1/",
    },
    26: {},
}
#: Years whose FUT.GG slice is owned by a verified full dump.
MANAGED_YEARS = (26, 27)
YEAR = 27
COLLECTIONS = SQUAD_AUDITS[YEAR]
DATA = ROOT / "card_db" / "futgg" / "futgg_27.jsonl"
MANIFEST = DATA.with_name("fc27_collections.json")


def select_year(year: int) -> None:
    """Point the module at one managed game year's dump and audit files."""
    global YEAR, COLLECTIONS, DATA, MANIFEST
    if int(year) not in MANAGED_YEARS:
        raise SystemExit(f"Unsupported FUT.GG year {year}; expected one of {MANAGED_YEARS}")
    YEAR = int(year)
    COLLECTIONS = SQUAD_AUDITS[YEAR]
    DATA = ROOT / "card_db" / "futgg" / f"futgg_{YEAR}.jsonl"
    MANIFEST = DATA.with_name(f"fc{YEAR}_collections.json")


def digest_rows(conn: sqlite3.Connection, query: str) -> str:
    digest = hashlib.sha256()
    for row in conn.execute(query):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _managed_clause(kind: str) -> str:
    years = ",".join(str(y) for y in MANAGED_YEARS)
    if kind == "universe":
        return f"NOT (source='futgg' AND year IN ({years}))"
    sources = ",".join(f"'futgg:futgg_{y}.jsonl'" for y in MANAGED_YEARS)
    return f"NOT (source IN ({sources}))"


def historical(conn: sqlite3.Connection, kind: str) -> dict[str, Any]:
    """Digest of every row no managed FUT.GG dump owns."""
    table = "observation" if kind == "universe" else "cards"
    clause = _managed_clause(kind)
    return {"count": conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {clause}").fetchone()[0],
            "sha256": digest_rows(conn, f"SELECT * FROM {table} WHERE {clause} ORDER BY " + ("obs_id" if kind == "universe" else "id"))}


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_revision: dict[str, int] = {}
    for row in rows:
        key = str(row.get("revision") or "")
        by_revision[key] = by_revision.get(key, 0) + 1
    return {"rows": len(rows), "unique_slugs": len({r.get("slug") for r in rows}),
            "unique_players": len({r.get("basePlayerEaId") or r.get("playerid") for r in rows}),
            "revisions": dict(sorted(by_revision.items(), key=lambda item: (-item[1], item[0])))}


def write_manifest(stats: dict[str, Any]) -> None:
    rows = _load_rows(DATA)
    by_slug = {row["slug"]: row for row in rows}
    collections = []
    for name, url in COLLECTIONS.items():
        req = urllib.request.Request("https://r.jina.ai/" + url, headers={"User-Agent": "FC26-Companion-data-audit"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
        slugs = sorted(set(re.findall(rf"https://www\.fut\.gg/players/[^/)]+/({YEAR}-\d+)/", body)))
        if not slugs or set(slugs) - set(by_slug):
            raise RuntimeError(f"Missing source collection cards: {name}: {set(slugs) - set(by_slug)}")
        item = {"name": name, "url": url, "source_cards": len(slugs), "slugs": slugs,
                "source_page_sha256": hashlib.sha256(body.encode()).hexdigest()}
        if "Ones to Watch" in name:
            item["season_pass_ones_to_watch"] = [s for s in slugs if by_slug[s]["revision"] == "Ones to Watch"]
            item["all_ones_to_watch"] = sorted(r["slug"] for r in rows if r["revision"] == "Ones to Watch")
        collections.append(item)
    manifest = {
        "game": f"FC {YEAR}", "source": "FUT.GG", "source_url": f"https://www.fut.gg/players/?game={YEAR}",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Every public player item, including regular/pristine holographics, returned by disjoint uncapped rating queries with show_holographics=true; user-created Evolution combinations are not a finite catalog.",
        "dataset_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "catalog": _coverage(rows),
        "sync": {k: stats[k] for k in ("year", "listed", "written", "total_reported", "partitions", "complete")},
        "collections": collections,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_data() -> list[dict[str, Any]]:
    label = f"FC{YEAR}"
    if not DATA.is_file():
        raise SystemExit(f"{label} cache is missing")
    rows = _load_rows(DATA)
    if not rows or len(rows) != len({r.get("slug") for r in rows}):
        raise SystemExit(f"{label} cache is empty or contains duplicate slugs")
    required = set(futgg_client.ATTR_MAP.values())
    for row in rows:
        if str(row.get("year")) != str(YEAR) or not row.get("slug") or not row.get("playerid"):
            raise SystemExit(f"{label} row has incomplete identity or year")
        if any(row.get(field) is None for field in required):
            raise SystemExit(f"{label} row {row.get('slug')} is missing detailed attributes")
        if row.get("_skillmoves_encoding") != "raw" or not 0 <= row["skillmoves"] <= 4:
            raise SystemExit(f"Wrong skill-move encoding for {row['slug']}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["dataset_sha256"] != hashlib.sha256(DATA.read_bytes()).hexdigest():
        raise SystemExit("Dataset digest differs from audit")
    sync = manifest["sync"]
    if not sync["complete"] or sync["listed"] != sync["written"] or sync["written"] != len(rows):
        raise SystemExit("Incomplete listing or definition counts")
    partitions = sorted(sync["partitions"], key=lambda p: p["overall_min"])
    next_min = 0
    for partition in partitions:
        if partition["overall_min"] != next_min or partition["total"] >= 10000:
            raise SystemExit("Gapped, overlapping or capped rating partition")
        actual = sum(partition["overall_min"] <= r["overallrating"] <= partition["overall_max"] for r in rows)
        if actual != partition["total"]:
            raise SystemExit("Rating partition count differs from listing")
        next_min = partition["overall_max"] + 1
    if next_min != 100 or sum(p["total"] for p in partitions) != len(rows):
        raise SystemExit("Rating partitions do not cover the full catalog")
    slugs = {r["slug"] for r in rows}
    if {c["name"] for c in manifest["collections"]} != set(COLLECTIONS):
        raise SystemExit("Named collection audit is missing")
    for collection in manifest["collections"]:
        if collection["source_cards"] != len(collection["slugs"]) or set(collection["slugs"]) - slugs:
            raise SystemExit(f"Missing cards from {collection['name']}")
    print("DATA VERIFIED", json.dumps(_coverage(rows), ensure_ascii=False))
    return rows


def verify_live() -> None:
    """Re-query FUT.GG now: every saved rating partition must still match the dump."""
    rows = _load_rows(DATA)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    partitions = sorted(manifest["sync"]["partitions"], key=lambda p: p["overall_min"])
    drift = []
    for partition in partitions:
        lo, hi = partition["overall_min"], partition["overall_max"]
        live = futgg_client.list_page(YEAR, page=1, count=1, overall_min=lo, overall_max=hi).get("total")
        cached = sum(lo <= r["overallrating"] <= hi for r in rows)
        if live != cached:
            drift.append(f"{lo}-{hi}: FUT.GG {live}, local {cached}")
    if drift:
        raise SystemExit(f"FC{YEAR} dump is behind FUT.GG: " + "; ".join(drift))
    print(f"LIVE COUNTS MATCH FC{YEAR}", len(rows), "cards")


def backup(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def update_universe(path: Path) -> None:
    # Reuse the existing source parser; merge just this year's observation slice.
    with closing(sqlite3.connect(":memory:")) as incoming, closing(sqlite3.connect(path)) as conn:
        incoming.executescript(ub.SCHEMA)
        builder = ub.Builder(incoming, ROOT / "card_db", ROOT, False, verbose=False)
        builder._load_futgg_file(DATA)
        builder.build_persons()
        old_ids = dict(conn.execute("SELECT variant_id,obs_id FROM observation WHERE year=? AND source='futgg'", (YEAR,)))
        next_id = conn.execute("SELECT COALESCE(MAX(obs_id),0)+1 FROM observation").fetchone()[0]
        conn.execute("DELETE FROM observation WHERE year=? AND source='futgg'", (YEAR,))
        columns = [r[1] for r in incoming.execute("PRAGMA table_info(person)")]
        conn.executemany(f"INSERT OR IGNORE INTO person VALUES ({','.join('?' for _ in columns)})", incoming.execute("SELECT * FROM person"))
        obs_columns = [r[1] for r in incoming.execute("PRAGMA table_info(observation)")]
        id_offset = obs_columns.index("variant_id")
        for row in incoming.execute("SELECT * FROM observation ORDER BY obs_id"):
            row = list(row)
            previous_id = old_ids.get(row[id_offset])
            row[0] = previous_id if previous_id is not None else next_id
            if previous_id is None:
                next_id += 1
            conn.execute(f"INSERT INTO observation VALUES ({','.join('?' for _ in obs_columns)})", row)
        conn.executemany("INSERT OR REPLACE INTO source_ledger VALUES (?,?,?,?,?,?,?,?,?,?)", builder.ledger)
        # Update aggregates while retaining all existing names and FC26 provenance.
        aggregates = conn.execute("SELECT person_id,MIN(year),MAX(year),MAX(overall),COUNT(*),SUM(DISTINCT 1 << (year-18)) FROM observation GROUP BY person_id").fetchall()
        conn.executemany("UPDATE person SET first_seen_year=?,last_seen_year=?,best_overall=?,obs_count=?,year_mask=? WHERE person_id=?", [(a,b,c,d,e,pid) for pid,a,b,c,d,e in aggregates])
        conn.execute("DELETE FROM person_fts")
        conn.execute("INSERT INTO person_fts(rowid,names) SELECT person_id,TRIM(COALESCE(display_name,'')||' '||COALESCE(name_norm,'')||' '||COALESCE(firstname,'')||' '||COALESCE(surname,'')||' '||COALESCE(commonname,'')) FROM person")
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", ub.summary_rows(conn) + [
            ("persons", str(conn.execute("SELECT COUNT(*) FROM person").fetchone()[0])),
            ("observations", str(conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0])),
            (f"fc{YEAR}_updated_utc", datetime.now(timezone.utc).isoformat()),
        ])
        conn.commit()


def update_catalog(path: Path, rows: list[dict[str, Any]]) -> None:
    source = f"futgg:futgg_{YEAR}.jsonl"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM cards WHERE year=? AND source=?", (str(YEAR), source))
        for item in rows:
            row = dict(item, source=source, source_kind="futgg")
            # The searchable label names the campaign; the payload stays FUT.GG's raw row.
            label = campaign_label(row)
            conn.execute("INSERT INTO cards(year,name,name_norm,ovr,revision,revision_norm,playerid,slug,source,payload) VALUES (?,?,?,?,?,?,?,?,?,?)", (
                str(YEAR), row["name"], card_index._norm(row["name"]), row["overallrating"],
                label, card_index._norm(label), str(row["playerid"]),
                row["slug"], row["source"], json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
        count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [("count", str(count)), (f"fc{YEAR}_updated_utc", datetime.now(timezone.utc).isoformat())])
        conn.commit()


def _verify_slice(conn: sqlite3.Connection, kind: str, year: int, rows: list[dict[str, Any]]) -> None:
    by_id = {str(r["eaId"]): r for r in rows}
    if kind == "universe":
        found = conn.execute("SELECT variant_id,person_id,overall,skillmoves,attrs FROM observation WHERE year=? AND source='futgg'", (year,)).fetchall()
        if len(found) != len(rows) or {r[0] for r in found} != set(by_id):
            raise RuntimeError(f"Universe does not contain the complete FC{year} item-ID set")
        for item_id, person_id, overall, skillmoves, attrs in found:
            row = by_id[item_id]
            if person_id != row["playerid"] or overall != row["overallrating"] or skillmoves != row["skillmoves"] + 1:
                raise RuntimeError(f"Universe identity/skills mismatch: {item_id}")
            parsed = json.loads(attrs)
            if parsed != {key: max(1, min(99, row[key])) for key in futgg_client.ATTR_MAP.values()}:
                raise RuntimeError(f"Universe attributes mismatch: {item_id}")
    else:
        found = conn.execute("SELECT slug,payload FROM cards WHERE year=? AND source=?", (str(year), f"futgg:futgg_{year}.jsonl")).fetchall()
        if len(found) != len(rows) or {r[0] for r in found} != {r["slug"] for r in rows}:
            raise RuntimeError(f"Catalog does not contain the complete FC{year} slug set")
        for slug, payload in found:
            row = json.loads(payload)
            if {k:v for k,v in row.items() if k not in ("source", "source_kind")} != by_id[slug.split('-')[1]]:
                raise RuntimeError(f"Catalog payload mismatch: {slug}")


def _managed_datasets() -> dict[int, list[dict[str, Any]]]:
    """Every managed year that has a dump, loaded and verified."""
    current = YEAR
    loaded: dict[int, list[dict[str, Any]]] = {}
    try:
        for year in MANAGED_YEARS:
            select_year(year)
            if DATA.is_file():
                loaded[year] = verify_data()
    finally:
        select_year(current)
    return loaded


def verify_databases(root: Path, datasets: dict[int, list[dict[str, Any]]], preservation: dict[str, Any]) -> None:
    for kind in ("universe", "catalog"):
        with closing(sqlite3.connect(root / "card_db" / f"{kind}.sqlite")) as conn:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"{kind} integrity check failed")
            if historical(conn, kind) != preservation[kind]:
                raise RuntimeError(f"Historical {kind} rows changed")
            for year, rows in datasets.items():
                _verify_slice(conn, kind, year, rows)


def _write_reports(target_root: Path, datasets: dict[int, list[dict[str, Any]]], preservation: dict[str, Any], backup_dir: Path) -> None:
    for year, rows in datasets.items():
        data = ROOT / "card_db" / "futgg" / f"futgg_{year}.jsonl"
        report = {"dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                  f"fc{year}_cards": len(rows), "preserved": preservation,
                  "managed_years": list(MANAGED_YEARS),
                  "backup_directory": str(backup_dir)}
        text = json.dumps(report, indent=2) + "\n"
        (target_root / "card_db" / f"fc{year}_install_report.json").write_text(text, encoding="utf-8")
        (ROOT / "card_db" / f"fc{year}_install_report.json").write_text(text, encoding="utf-8")


def install(target_root: Path) -> None:
    datasets = _managed_datasets()
    if YEAR not in datasets:
        raise SystemExit(f"FC{YEAR} cache is missing")
    rows = datasets[YEAR]
    target_db = target_root / "card_db"
    for kind in ("universe", "catalog"):
        if not (target_db / f"{kind}.sqlite").is_file():
            raise RuntimeError(f"Existing {kind} database required; rebuilding from only FC{YEAR} would lose history")
    backup_dir = target_root / "backups" / (f"card_db_before_fc{YEAR}_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup_dir.mkdir(parents=True)
    preservation = {}
    with tempfile.TemporaryDirectory(prefix=f"fc{YEAR}_catalog_") as temp:
        stage_root = Path(temp)
        stage_db = stage_root / "card_db"
        stage_db.mkdir()
        for kind in ("universe", "catalog"):
            name = f"{kind}.sqlite"
            backup(target_db / name, backup_dir / name)
            backup(backup_dir / name, stage_db / name)
            with closing(sqlite3.connect(backup_dir / name)) as conn:
                preservation[kind] = historical(conn, kind)
        # Install every managed dump so each year's slice matches its file.
        current = YEAR
        try:
            for year in datasets:
                select_year(year)
                update_universe(stage_db / "universe.sqlite")
                update_catalog(stage_db / "catalog.sqlite", datasets[year])
        finally:
            select_year(current)
        verify_databases(stage_root, datasets, preservation)
        try:
            for kind in ("universe", "catalog"):
                backup(stage_db / f"{kind}.sqlite", target_db / f"{kind}.sqlite")
            verify_databases(target_root, datasets, preservation)
        except Exception:
            for kind in ("universe", "catalog"):
                backup(backup_dir / f"{kind}.sqlite", target_db / f"{kind}.sqlite")
            raise
        if target_root != ROOT:
            for kind in ("universe", "catalog"):
                backup(stage_db / f"{kind}.sqlite", ROOT / "card_db" / f"{kind}.sqlite")
    target_futgg = target_db / "futgg"
    target_futgg.mkdir(exist_ok=True)
    for year in datasets:
        data = ROOT / "card_db" / "futgg" / f"futgg_{year}.jsonl"
        for source in (data, data.with_name(f"fc{year}_collections.json"), data.with_name(f"fc{year}_sync_stats.json")):
            if source.is_file() and source.parent.resolve() != target_futgg.resolve():
                shutil.copy2(source, target_futgg / source.name)
    _write_reports(target_root, datasets, preservation, backup_dir)
    print("INSTALLED", ", ".join(f"FC{y} {len(r)} cards" for y, r in sorted(datasets.items())),
          "; historical rows verified unchanged")


def verify_install(target_root: Path) -> None:
    datasets = _managed_datasets()
    if YEAR not in datasets:
        raise SystemExit(f"FC{YEAR} cache is missing")
    report = json.loads((target_root / "card_db" / f"fc{YEAR}_install_report.json").read_text(encoding="utf-8"))
    if report["dataset_sha256"] != hashlib.sha256(DATA.read_bytes()).hexdigest():
        raise RuntimeError("Installed source digest differs from committed dataset")
    verify_databases(target_root, datasets, report["preserved"])
    verify_databases(ROOT, datasets, report["preserved"])
    print("INSTALL VERIFIED", f"FC{YEAR}", report[f"fc{YEAR}_cards"], "cards in both catalogs;",
          "managed years", ",".join(str(y) for y in sorted(datasets)),
          "match their dumps; historical row hashes unchanged")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=27, choices=MANAGED_YEARS)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--audit", action="store_true", help="audit existing download using saved sync statistics")
    parser.add_argument("--install", metavar="PATH")
    parser.add_argument("--verify-data", action="store_true")
    parser.add_argument("--verify-live", action="store_true", help="compare the dump with FUT.GG's current listing totals")
    parser.add_argument("--verify-sync", action="store_true")
    parser.add_argument("--verify-install", metavar="PATH")
    args = parser.parse_args()
    select_year(args.year)
    if args.verify_sync:
        subprocess.run([sys.executable, "-m", "pytest", "tests/test_futgg_sync.py", "tests/test_futgg.py",
                        "tests/test_futgg_multiyear_load.py", "tests/v2/test_catalog_compare.py", "-q"],
                       cwd=ROOT, check=True)
        print("SYNC CHECKS VERIFIED")
    if args.download:
        stats = futgg_client.sync_year(YEAR, page_size=300, def_batch=50,
                                       progress=lambda msg: print(msg, flush=True))
        DATA.with_name(f"fc{YEAR}_sync_stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
        write_manifest(stats)
        print(json.dumps(stats, ensure_ascii=False))
    if args.verify_data:
        verify_data()
    if args.audit:
        write_manifest(json.loads(DATA.with_name(f"fc{YEAR}_sync_stats.json").read_text(encoding="utf-8")))
    if args.verify_live:
        verify_live()
    if args.install:
        install(Path(args.install).resolve())
    if args.verify_install:
        verify_install(Path(args.verify_install).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
