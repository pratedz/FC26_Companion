"""SQLite card catalog index for fast search without re-parsing CSVs every query.

Build once into card_db/catalog.sqlite (fingerprint of sources). Search uses
name/revision substring + year/ovr filters and returns full card payloads.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import paths

Progress = Optional[Callable[[float, str], None]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY,
  year TEXT NOT NULL,
  name TEXT,
  name_norm TEXT NOT NULL,
  ovr INTEGER,
  revision TEXT,
  revision_norm TEXT,
  playerid TEXT,
  slug TEXT,
  source TEXT,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name_norm);
CREATE INDEX IF NOT EXISTS idx_cards_year ON cards(year);
CREATE INDEX IF NOT EXISTS idx_cards_ovr ON cards(ovr);
CREATE INDEX IF NOT EXISTS idx_cards_year_name ON cards(year, name_norm);
"""


def index_path(card_db: Optional[Path] = None) -> Path:
    db = Path(card_db) if card_db else paths.card_db_dir()
    return db / "catalog.sqlite"


def _norm(s: str) -> str:
    from .card_catalog import _norm as catalog_norm

    return catalog_norm(s)


def _progress(progress: Progress, frac: float, msg: str) -> None:
    if progress:
        try:
            progress(frac, msg)
        except Exception:
            pass


def _source_files(card_db: Path, years: Optional[Sequence[str]]) -> List[Path]:
    """List source files that feed the index (stable order)."""
    from .card_catalog import (
        LOCAL_YEAR_ALIASES,
        _year_from_filename,
        _year_wanted,
        _years_filter_set,
    )

    wanted = _years_filter_set(years)
    files: List[Path] = []
    if card_db.is_dir():
        for csv_path in sorted(card_db.glob("*.csv")):
            if csv_path.name.startswith("_"):
                continue
            year = _year_from_filename(csv_path)
            if wanted is not None and not _year_wanted(year, wanted):
                continue
            files.append(csv_path)
        futbin = card_db / "futbin"
        if futbin.is_dir():
            files.extend(sorted(futbin.glob("*.json")))
        futgg = card_db / "futgg"
        if futgg.is_dir():
            for p in sorted(futgg.glob("*.jsonl")):
                if ".partial" in p.name:
                    continue
                yg = _year_from_filename(p)
                if wanted is not None and not _year_wanted(yg, wanted):
                    continue
                files.append(p)
            by_player = futgg / "by_player"
            if by_player.is_dir():
                files.extend(sorted(p for p in by_player.glob("*.jsonl") if ".partial" not in p.name))
    # local LE cards
    if wanted is None or bool(wanted & LOCAL_YEAR_ALIASES) or "local" in (wanted or set()):
        local = paths.cards_csv_path()
        if local.is_file():
            files.append(local)
    return files


def fingerprint(card_db: Optional[Path] = None, years: Optional[Sequence[str]] = None) -> str:
    db = Path(card_db) if card_db else paths.card_db_dir()
    parts: List[str] = []
    for p in _source_files(db, years):
        try:
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{p.name}:missing")
    ykey = ",".join(sorted(str(y) for y in (years or []))) or "ALL"
    return f"v2|{ykey}|{'|'.join(parts)}"


def _get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def index_ready(card_db: Optional[Path] = None, years: Optional[Sequence[str]] = None) -> bool:
    path = index_path(card_db)
    if not path.is_file():
        return False
    try:
        conn = _open(path)
        try:
            fp = fingerprint(card_db, years)
            return _get_meta(conn, "fingerprint") == fp and int(_get_meta(conn, "count") or 0) > 0
        finally:
            conn.close()
    except Exception:
        return False


def rebuild_index(
    card_db: Optional[Path] = None,
    *,
    years: Optional[Sequence[str]] = None,
    progress: Progress = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Build or refresh catalog.sqlite from card sources."""
    from .card_catalog import load_card_db, clear_catalog_cache

    db = Path(card_db) if card_db else paths.card_db_dir()
    path = index_path(db)
    fp = fingerprint(db, years)

    if not force and path.is_file():
        try:
            conn = _open(path)
            try:
                if _get_meta(conn, "fingerprint") == fp:
                    n = int(_get_meta(conn, "count") or 0)
                    if n > 0:
                        _progress(progress, 1.0, f"index ready ({n} cards)")
                        return {"ok": True, "path": str(path), "count": n, "cached": True}
            finally:
                conn.close()
        except Exception:
            pass

    t0 = time.perf_counter()
    _progress(progress, 0.02, "loading cards for index…")

    def prog(frac: float, msg: str) -> None:
        _progress(progress, 0.02 + 0.55 * max(0.0, min(1.0, frac)), msg)

    # Reuse catalog loader (populates memory once); we then persist and free.
    clear_catalog_cache()
    cards = load_card_db(
        db,
        include_local=True,
        include_futbin=True,
        years=years,
        progress=prog,
    )

    _progress(progress, 0.6, f"writing index ({len(cards)} cards)…")
    tmp = path.with_suffix(".sqlite.tmp")
    if tmp.is_file():
        try:
            tmp.unlink()
        except OSError:
            pass

    conn = _open(tmp)
    try:
        conn.execute("DELETE FROM cards")
        conn.execute("DELETE FROM meta")
        batch: List[tuple] = []
        n = len(cards)
        for i, card in enumerate(cards):
            name = str(card.get("name") or "")
            rev = str(card.get("revision") or card.get("origin") or "")
            ovr = card.get("overallrating")
            try:
                ovr_i = int(ovr) if ovr is not None and ovr != "" else None
            except (TypeError, ValueError):
                ovr_i = None
            batch.append(
                (
                    str(card.get("year") or ""),
                    name,
                    _norm(name),
                    ovr_i,
                    rev,
                    _norm(rev),
                    str(card.get("playerid") if card.get("playerid") is not None else ""),
                    str(card.get("slug") or ""),
                    str(card.get("source") or ""),
                    json.dumps(card, ensure_ascii=False, separators=(",", ":")),
                )
            )
            if len(batch) >= 2000:
                conn.executemany(
                    "INSERT INTO cards(year,name,name_norm,ovr,revision,revision_norm,playerid,slug,source,payload) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
                batch.clear()
                if i % 10000 == 0:
                    _progress(progress, 0.6 + 0.35 * (i / max(n, 1)), f"indexing {i}/{n}…")
        if batch:
            conn.executemany(
                "INSERT INTO cards(year,name,name_norm,ovr,revision,revision_norm,playerid,slug,source,payload) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
        _set_meta(conn, "fingerprint", fp)
        _set_meta(conn, "count", str(n))
        _set_meta(conn, "built_at", str(time.time()))
        conn.commit()
    finally:
        conn.close()

    # Atomic replace
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            bak = path.with_suffix(".sqlite.bak")
            try:
                if bak.is_file():
                    bak.unlink()
                path.rename(bak)
            except OSError:
                pass
    tmp.replace(path)

    # Drop memory cache of full lists; search will use SQLite
    clear_catalog_cache()
    elapsed = time.perf_counter() - t0
    _progress(progress, 1.0, f"index ready ({n} cards, {elapsed:.1f}s)")
    return {"ok": True, "path": str(path), "count": n, "cached": False, "seconds": elapsed}


def ensure_index(
    card_db: Optional[Path] = None,
    *,
    years: Optional[Sequence[str]] = None,
    progress: Progress = None,
) -> bool:
    try:
        info = rebuild_index(card_db, years=years, progress=progress, force=False)
        return bool(info.get("ok"))
    except Exception:
        return False


def _year_sql_clause(year: Optional[str], params: List[Any]) -> str:
    from .card_catalog import LOCAL_YEAR_ALIASES

    if year is None or str(year).strip() == "":
        return ""
    q = str(year).strip().lower()
    if q in LOCAL_YEAR_ALIASES:
        params.extend(list(LOCAL_YEAR_ALIASES) + ["local"])
        placeholders = ",".join("?" * (len(LOCAL_YEAR_ALIASES) + 1))
        return f" AND lower(year) IN ({placeholders})"
    if q in ("futbin", "fb"):
        # Only live Futbin cache rows (source = futbin:*.json), not CSV names with "futbin"
        params.append("futbin:%")
        return " AND (lower(year) IN ('futbin','fb') OR lower(source) LIKE ?)"
    if q.isdigit():
        qi = int(q)
        if qi >= 1000:
            short = str(qi % 100)
            params.extend([str(qi), short, short.zfill(2)])
            return " AND year IN (?,?,?)"
        long = str(2000 + qi)
        params.extend([q, long, q.zfill(2)])
        return " AND year IN (?,?,?)"
    params.append(q)
    return " AND lower(year)=?"


def _normalize_year_token(raw: str) -> str:
    """Map filename year tokens to short catalog years (2018→18, 26→26)."""
    y = str(raw or "").strip().lower()
    if y.isdigit() and len(y) == 4:
        short = int(y) % 100
        return f"{short:02d}" if short >= 10 else str(short)
    if y.isdigit() and len(y) == 1:
        return y
    if y.isdigit() and len(y) == 2:
        return y
    return y


def _default_years_scope(card_db: Path) -> List[str]:
    """Stable full scope for the on-disk index (never subset by query year).

    Always covers pre-downloaded career dumps 18–25 + local, plus any year
    that exists on disk (fc26_datahub.csv, futgg_26.jsonl, extra years, …).
    """
    from .card_catalog import _year_from_filename

    years: List[str] = ["18", "19", "20", "21", "22", "23", "24", "25", "local"]
    seen = set(years)

    def _add(token: str) -> None:
        t = _normalize_year_token(token)
        if t and t not in seen and t not in ("local",):
            # keep local only once via base list
            if t.isdigit() or t in ("futbin", "fb"):
                years.append(t)
                seen.add(t)

    if card_db.is_dir():
        for csv_path in card_db.glob("*.csv"):
            if csv_path.name.startswith("_"):
                continue
            _add(_year_from_filename(csv_path))
        futgg = card_db / "futgg"
        if futgg.is_dir():
            for p in futgg.glob("*.jsonl"):
                if ".partial" in p.name:
                    continue
                _add(_year_from_filename(p))
            for p in futgg.glob("futgg_*.jsonl"):
                if ".partial" in p.name:
                    continue
                stem = p.stem.replace("futgg_", "")
                if stem.isdigit():
                    _add(stem)
    return years


def search(
    query: str = "",
    *,
    year: Optional[str] = None,
    ovr: Optional[int] = None,
    ovr_min: Optional[int] = None,
    ovr_max: Optional[int] = None,
    limit: int = 50,
    card_db: Optional[Path] = None,
    progress: Progress = None,
) -> Optional[List[Dict[str, Any]]]:
    """Search index. Returns None if index missing/unusable (caller falls back).

    Never rebuilds the multi-year catalog on the search path — that freezes
    Cards UI for tens of seconds. Background ``ensure_index`` / CLI rebuilds.
    When a year is selected, SQL filters to that year only.
    """
    db = Path(card_db) if card_db else paths.card_db_dir()
    path = index_path(db)

    # Missing index → fall through so card_catalog can load only the selected year
    if not path.is_file():
        _progress(progress, 0.0, "no index · year-scoped load…")
        return None

    try:
        conn = _open(path)
    except Exception:
        return None

    try:
        # Empty / corrupt index file
        try:
            row0 = conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone()
        except sqlite3.Error:
            return None
        if row0 is None:
            return None

        qn = _norm(query)
        where: List[str] = []
        params: List[Any] = []
        if qn:
            where.append("(name_norm LIKE ? OR revision_norm LIKE ?)")
            like = f"%{qn}%"
            params.extend([like, like])
        ys = _year_sql_clause(year, params)
        if ys:
            where.append(ys.lstrip(" AND "))
        if ovr is not None:
            where.append("ovr=?")
            params.append(int(ovr))
        if ovr_min is not None:
            where.append("ovr>=?")
            params.append(int(ovr_min))
        if ovr_max is not None:
            where.append("ovr<=?")
            params.append(int(ovr_max))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            f"SELECT payload FROM cards{clause} "
            f"ORDER BY ovr DESC NULLS LAST, name_norm ASC LIMIT ?"
        )
        # SQLite older than 3.30 may not like NULLS LAST — fallback
        params.append(int(limit) * 3)  # over-fetch for dedupe
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            sql2 = (
                f"SELECT payload FROM cards{clause} "
                f"ORDER BY (ovr IS NULL), ovr DESC, name_norm ASC LIMIT ?"
            )
            rows = conn.execute(sql2, params).fetchall()

        results: List[Dict[str, Any]] = []
        seen: set = set()
        for (payload,) in rows:
            try:
                card = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(card, dict):
                continue
            key = (
                str(card.get("slug") or ""),
                str(card.get("name") or ""),
                str(card.get("overallrating") or ""),
                str(card.get("revision") or ""),
                str(card.get("year") or ""),
                str(card.get("playerid") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(card)
            if len(results) >= int(limit):
                break
        _progress(progress, 1.0, f"{len(results)} card(s)")
        return results
    finally:
        conn.close()


def invalidate() -> None:
    """Drop index file so next search rebuilds (e.g. after catalog sync)."""
    path = index_path()
    for p in (path, path.with_suffix(".sqlite-wal"), path.with_suffix(".sqlite-shm")):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
