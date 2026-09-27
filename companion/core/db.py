"""``state.sqlite`` -- the persistence layer that v1 never had.

v1 keeps user data in **seven loose JSON files** at the install root, each read
and written by its own module with its own ``except (OSError, JSONDecodeError):
pass`` and **no atomic write anywhere**::

    companion_config.json   job_history.json      snapshots/index.json + 31 files
    last_apply_snapshot.json  favorites.json      current_squad.json  .first_run_done

``src/job_history.py::add`` rewrites the whole 40-entry array on every boost with
a bare ``write_text``; a crash between truncate and flush leaves a truncated file
that the next ``load()`` swallows into ``[]`` -- silent, total history loss.
``snapshot_store`` has the same shape with the undo data in it. Two of the seven
also enforce arbitrary caps (``MAX_ENTRIES = 40``, ``MAX_SNAPSHOTS = 80``) purely
because rewriting a growing JSON array got slow.

One SQLite file fixes all of it at once: writes are transactional, readers never
observe a half-written state, and nothing needs a cap. See
``docs/V2_ARCHITECTURE.md §5.4`` for the file-to-table map and ``§7.2`` for the
``job_record`` columns.

**The migration runner is the point, not a detail.** ``card_index.fingerprint()``
is v1's only versioning and it detects *source* change, never *schema* change --
so a schema edit silently produced ``sqlite3.OperationalError: no such column``
at the next launch. Here: ``schema_version`` lives in ``meta``, migrations are an
ordered tuple, each runs inside one ``BEGIN IMMEDIATE`` with the version bump in
the *same* transaction, and a failure rolls the whole step back. Half-migrated is
not a reachable state.

**Two counters, not four.** v1 had ``PROTOCOL_VERSION = 2``, a sidecar
``{"version": 3}``, ``companion_config.json {"version": 1}`` and
``profiles.json {"version": 1}`` with no relationship between them. v2 has
``PROTOCOL_V`` (wire) and ``SCHEMA_VERSION`` (storage). This module owns the
second one and nothing else.

Threading: ``check_same_thread=False`` plus an ``RLock`` around every statement,
because ``core/executor.py::ThreadExecutor`` runs commands off the UI thread and
a per-thread connection pool would defeat WAL's single-writer discipline for no
gain at this scale (tens of writes per session, not thousands per second).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..domain.outcome import ApplyOutcome

__all__ = [
    "SCHEMA_VERSION",
    "SAVE_SNAPSHOT_TTL_S",
    "Migration",
    "MIGRATIONS",
    "Database",
    "JobRecord",
    "SnapshotRow",
    "FavoriteRow",
    "SaveSnapshotRow",
    "open_state_db",
    "run_migrations",
    "current_version",
    "utc_now",
]

#: Bumped by appending to :data:`MIGRATIONS`. Never edit a shipped migration.
SCHEMA_VERSION = 1

#: §5.3 -- live save state is a cache, never durable data.
SAVE_SNAPSHOT_TTL_S = 120.0


def utc_now() -> str:
    """The one storage timestamp format: ISO-8601, UTC, millisecond, ``Z``.

    Matches ``core/clock.py::Clock.stamp`` so a row written from a ``FakeClock``
    sorts identically to one written live. v1 stored bare ``time.time()`` floats
    in some files and formatted strings in others.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ts_to_utc(value: Any) -> str | None:
    """Coerce a v1 epoch float / ISO string into the storage format."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return (
                datetime.fromtimestamp(float(value), tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                + "Z"
            )
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _delta_ms(start: str | None, end: str | None) -> int | None:
    a, b = _parse_utc(start), _parse_utc(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Migration:
    """One ordered, atomic schema step.

    ``sql`` is executed as a script; ``fn`` (if given) runs after it inside the
    *same* transaction, for steps that need Python (backfills, data rewrites).
    """

    version: int
    name: str
    sql: str = ""
    fn: Callable[[sqlite3.Connection], None] | None = None


_M1 = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Audit trail. `schema_version` in meta says where we are; this says how we got
-- here, which is what you actually want when a user reports a broken upgrade.
CREATE TABLE IF NOT EXISTS schema_migration (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  applied_utc TEXT NOT NULL
);

-- companion_config.json. One row per setting, value as JSON so a bool stays a
-- bool: v1 round-tripped `auto_clear_stale` through str() in two places.
CREATE TABLE IF NOT EXISTS prefs (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL,
  updated_utc TEXT NOT NULL
);

-- job_history.json, uncapped. §7.2. latency_ms - exec_ms IS the event-starvation
-- cost; storing both is the only way to measure it rather than guess.
CREATE TABLE IF NOT EXISTS job_record (
  job_id        TEXT PRIMARY KEY,
  created_utc   TEXT NOT NULL,
  submitted_utc TEXT,
  claimed_utc   TEXT,
  finished_utc  TEXT,
  origin        TEXT NOT NULL DEFAULT '',
  label         TEXT NOT NULL DEFAULT '',
  save_uid      TEXT,
  session_id    TEXT,
  outcome       INTEGER NOT NULL,
  op_count      INTEGER NOT NULL DEFAULT 0,
  applied_count INTEGER NOT NULL DEFAULT 0,
  failed_count  INTEGER NOT NULL DEFAULT 0,
  latency_ms    INTEGER,
  exec_ms       INTEGER,
  trigger_event TEXT,
  job_json      TEXT NOT NULL DEFAULT '{}',
  result_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_job_outcome ON job_record(outcome, created_utc DESC);
CREATE INDEX IF NOT EXISTS ix_job_save    ON job_record(save_uid, created_utc DESC);
CREATE INDEX IF NOT EXISTS ix_job_created ON job_record(created_utc DESC);

-- snapshots/index.json + 31 sidecar files + last_apply_snapshot.json, which was
-- a *separate file duplicating a snapshot row*. Now one flagged row.
CREATE TABLE IF NOT EXISTS snapshot (
  id            TEXT PRIMARY KEY,
  ts            REAL NOT NULL,
  taken_utc     TEXT NOT NULL,
  save_uid      TEXT,
  target_id     INTEGER,
  label         TEXT NOT NULL DEFAULT '',
  kind          TEXT NOT NULL DEFAULT 'card',
  fields_json   TEXT NOT NULL,
  n_fields      INTEGER NOT NULL DEFAULT 0,
  extra_json    TEXT,
  is_last_apply INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_snapshot_ts     ON snapshot(ts DESC);
CREATE INDEX IF NOT EXISTS ix_snapshot_target ON snapshot(target_id, ts DESC);
-- At most one row can be the last apply. v1 could disagree with itself because
-- last_apply_snapshot.json and snapshots/ were written independently.
CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshot_last
  ON snapshot(is_last_apply) WHERE is_last_apply = 1;

-- favorites.json. v1 keyed on f"{year}|{pid}|{name}|{rev}", so renaming a card
-- in the catalog silently orphaned the favorite. Real composite PK, no name.
CREATE TABLE IF NOT EXISTS favorite (
  year       TEXT NOT NULL,
  playerid   TEXT NOT NULL,
  revision   TEXT NOT NULL DEFAULT '',
  name       TEXT NOT NULL DEFAULT '',
  ovr        INTEGER,
  added_utc  TEXT NOT NULL,
  card_json  TEXT NOT NULL,
  PRIMARY KEY (year, playerid, revision)
);
CREATE INDEX IF NOT EXISTS ix_favorite_added ON favorite(added_utc DESC);

-- current_squad.json, which had NO save key at all and would happily show save
-- A's squad while you edited save B. §5.3: keyed by (save_uid, kind), TTL'd.
-- save_uid '' means "unknown save" -- always stale, never served as fresh.
CREATE TABLE IF NOT EXISTS save_snapshot (
  save_uid  TEXT NOT NULL DEFAULT '',
  kind      TEXT NOT NULL,
  taken_utc TEXT NOT NULL,
  taken_ts  REAL NOT NULL,
  stale     INTEGER NOT NULL DEFAULT 0,
  payload   TEXT NOT NULL,
  PRIMARY KEY (save_uid, kind)
);
CREATE INDEX IF NOT EXISTS ix_save_snapshot_ts ON save_snapshot(taken_ts DESC);
"""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "base schema: prefs, job_record, snapshot, favorite, save_snapshot", _M1),
)


def _exec_script(conn: sqlite3.Connection, sql: str) -> None:
    """Run a multi-statement script **inside the caller's transaction**.

    ``Connection.executescript`` cannot be used here: it issues an implicit
    ``COMMIT`` before running, which would silently end the ``BEGIN IMMEDIATE``
    that :func:`run_migrations` opened and leave every migration non-atomic --
    exactly the failure mode this module exists to prevent. So statements are
    accumulated with ``sqlite3.complete_statement`` (the documented splitter,
    which understands string literals and trailing comments) and executed one by
    one on the open transaction.
    """
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                conn.execute(statement)
    # A final statement without its trailing semicolon never satisfies
    # ``complete_statement``. Run it rather than dropping it silently -- a
    # skipped CREATE TABLE would surface much later as "no such table". If it is
    # genuinely malformed, SQLite says so here, inside the transaction.
    leftover = "\n".join(
        ln for ln in buffer.splitlines() if ln.strip() and not ln.strip().startswith("--")
    ).strip()
    if leftover:
        conn.execute(leftover)


def current_version(conn: sqlite3.Connection) -> int:
    """Read ``meta.schema_version``; 0 on a fresh or pre-versioned file."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def run_migrations(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    target: int | None = None,
) -> list[int]:
    """Apply every pending migration in order. Returns the versions applied.

    Each step is one ``BEGIN IMMEDIATE`` covering the DDL, the optional Python
    body, the ``schema_migration`` audit row **and** the ``schema_version`` bump.
    If any of those raises, the whole step rolls back and the file stays exactly
    at the previous version -- so the next launch retries cleanly instead of
    hitting "no such column" forever.

    ``BEGIN IMMEDIATE`` (not deferred) takes the write lock up front: two
    processes launching at once -- the CLI and the GUI, which happens -- must not
    both decide they are the one to create the tables.
    """
    ordered = sorted(migrations, key=lambda m: m.version)
    versions = [m.version for m in ordered]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration versions: {versions}")

    applied: list[int] = []
    have = current_version(conn)
    for m in ordered:
        if m.version <= have:
            continue
        if target is not None and m.version > target:
            break
        conn.execute("BEGIN IMMEDIATE")
        try:
            if m.sql:
                _exec_script(conn, m.sql)
            if m.fn is not None:
                m.fn(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migration(version, name, applied_utc) "
                "VALUES (?,?,?)",
                (m.version, m.name, utc_now()),
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(m.version),),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(m.version)
        have = m.version
    return applied


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobRecord:
    """One row of ``job_record``. §7.2 verbatim, plus derived helpers."""

    job_id: str
    created_utc: str
    outcome: ApplyOutcome
    origin: str = ""
    label: str = ""
    submitted_utc: str | None = None
    claimed_utc: str | None = None
    finished_utc: str | None = None
    save_uid: str | None = None
    session_id: str | None = None
    op_count: int = 0
    applied_count: int = 0
    failed_count: int = 0
    latency_ms: int | None = None
    exec_ms: int | None = None
    trigger_event: str | None = None
    job_json: Mapping[str, Any] | None = None
    result_json: Mapping[str, Any] | None = None

    @property
    def wait_ms(self) -> int | None:
        """``latency_ms - exec_ms`` -- the measured event-starvation cost (§7.2).

        This single number decides whether §9's R3 mitigation is ever needed.
        """
        if self.latency_ms is None or self.exec_ms is None:
            return None
        return max(0, self.latency_ms - self.exec_ms)

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "JobRecord":
        return cls(
            job_id=r["job_id"],
            created_utc=r["created_utc"],
            outcome=ApplyOutcome(int(r["outcome"])),
            origin=r["origin"] or "",
            label=r["label"] or "",
            submitted_utc=r["submitted_utc"],
            claimed_utc=r["claimed_utc"],
            finished_utc=r["finished_utc"],
            save_uid=r["save_uid"],
            session_id=r["session_id"],
            op_count=int(r["op_count"] or 0),
            applied_count=int(r["applied_count"] or 0),
            failed_count=int(r["failed_count"] or 0),
            latency_ms=r["latency_ms"],
            exec_ms=r["exec_ms"],
            trigger_event=r["trigger_event"],
            job_json=_loads(r["job_json"]) or {},
            result_json=_loads(r["result_json"]),
        )


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    id: str
    ts: float
    taken_utc: str
    label: str = ""
    kind: str = "card"
    target_id: int | None = None
    save_uid: str | None = None
    fields: Sequence[Mapping[str, Any]] = ()
    extra: Mapping[str, Any] | None = None
    is_last_apply: bool = False

    @property
    def n_fields(self) -> int:
        return len(self.fields)

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "SnapshotRow":
        return cls(
            id=r["id"],
            ts=float(r["ts"]),
            taken_utc=r["taken_utc"],
            label=r["label"] or "",
            kind=r["kind"] or "card",
            target_id=r["target_id"],
            save_uid=r["save_uid"],
            fields=tuple(_loads(r["fields_json"]) or ()),
            extra=_loads(r["extra_json"]),
            is_last_apply=bool(r["is_last_apply"]),
        )


@dataclass(frozen=True, slots=True)
class FavoriteRow:
    year: str
    playerid: str
    revision: str = ""
    name: str = ""
    ovr: int | None = None
    added_utc: str = ""
    card: Mapping[str, Any] | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.year, self.playerid, self.revision)

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "FavoriteRow":
        return cls(
            year=r["year"],
            playerid=r["playerid"],
            revision=r["revision"] or "",
            name=r["name"] or "",
            ovr=r["ovr"],
            added_utc=r["added_utc"] or "",
            card=_loads(r["card_json"]) or {},
        )


@dataclass(frozen=True, slots=True)
class SaveSnapshotRow:
    save_uid: str
    kind: str
    taken_utc: str
    taken_ts: float
    payload: Any
    stale: bool = False

    def age_s(self, now_ts: float) -> float:
        return max(0.0, now_ts - self.taken_ts)

    def is_fresh(self, now_ts: float, ttl_s: float = SAVE_SNAPSHOT_TTL_S) -> bool:
        """Stale rows are never fresh, regardless of age.

        A migrated ``current_squad.json`` has no ``save_uid``, so it can only be
        shown with an explicit "imported, unverified" label -- never silently
        rendered as this save's squad.
        """
        return (not self.stale) and self.age_s(now_ts) < ttl_s

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "SaveSnapshotRow":
        return cls(
            save_uid=r["save_uid"] or "",
            kind=r["kind"],
            taken_utc=r["taken_utc"],
            taken_ts=float(r["taken_ts"]),
            payload=_loads(r["payload"]),
            stale=bool(r["stale"]),
        )


def _loads(text: Any) -> Any:
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class Database:
    """The one handle on ``state.sqlite``. Opened once, lives for the process.

    Every method is short and typed on purpose: nothing outside this module ever
    writes SQL against these tables, which is what makes migration 2 possible
    without a repo-wide grep.
    """

    def __init__(self, path: Path | str, *, migrate: bool = True) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,  # explicit BEGIN/COMMIT; no implicit-transaction surprises
            timeout=10.0,
        )
        self.conn.row_factory = sqlite3.Row
        self._pragmas()
        self.applied: list[int] = run_migrations(self.conn) if migrate else []

    def _pragmas(self) -> None:
        cur = self.conn
        # WAL: a reader (the diagnostics pane polling job history) never blocks
        # the writer (an apply finishing). Not available on network shares -- R5
        # already warns about those in doctor.py, and journal_mode silently stays
        # `delete` there rather than failing the launch.
        try:
            cur.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")

    # -- plumbing -------------------------------------------------------

    @property
    def version(self) -> int:
        with self._lock:
            return current_version(self.conn)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def tx(self) -> "_Tx":
        """``with db.tx(): ...`` -- one atomic unit, rolled back on any exception.

        Reentrant: a nested ``tx()`` joins the outer one rather than issuing a
        second ``BEGIN`` (SQLite has no nested transactions and would raise).
        """
        return _Tx(self)

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.execute("PRAGMA optimize")
            except sqlite3.DatabaseError:
                pass
            self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- prefs ----------------------------------------------------------

    def set_pref(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO prefs(key, value_json, updated_utc) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
            "updated_utc=excluded.updated_utc",
            (key, _dumps(value), utc_now()),
        )

    def set_prefs(self, values: Mapping[str, Any]) -> None:
        with self.tx():
            for key, value in values.items():
                self.set_pref(key, value)

    def get_pref(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value_json FROM prefs WHERE key=?", (key,))
        if row is None:
            return default
        value = _loads(row["value_json"])
        return default if value is None and row["value_json"] not in ("null",) else value

    def all_prefs(self) -> dict[str, Any]:
        return {r["key"]: _loads(r["value_json"]) for r in self.query("SELECT * FROM prefs")}

    def delete_pref(self, key: str) -> None:
        self.execute("DELETE FROM prefs WHERE key=?", (key,))

    # -- job records ----------------------------------------------------

    def record_submitted(
        self,
        *,
        job_id: str,
        job: Mapping[str, Any],
        origin: str = "",
        label: str = "",
        save_uid: str | None = None,
        session_id: str | None = None,
        created_utc: str | None = None,
        submitted_utc: str | None = None,
        op_count: int | None = None,
    ) -> None:
        """Insert (or refresh) the row for a job the moment it hits the queue.

        Written *before* the job file, so a job that exists on disk always has a
        row -- v1 appended to history only on completion, which is precisely why
        the jobs that crashed the game left no trace.
        """
        now = created_utc or utc_now()
        self.execute(
            """INSERT INTO job_record
               (job_id, created_utc, submitted_utc, origin, label, save_uid, session_id,
                outcome, op_count, applied_count, failed_count, job_json)
               VALUES (?,?,?,?,?,?,?,?,?,0,0,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 submitted_utc=excluded.submitted_utc,
                 origin=excluded.origin, label=excluded.label,
                 save_uid=COALESCE(excluded.save_uid, job_record.save_uid),
                 session_id=COALESCE(excluded.session_id, job_record.session_id),
                 op_count=excluded.op_count, job_json=excluded.job_json""",
            (
                job_id,
                now,
                submitted_utc or now,
                origin or str(job.get("origin", "")),
                label or str(job.get("label", "")),
                save_uid,
                session_id,
                int(ApplyOutcome.QUEUED),
                int(op_count if op_count is not None else len(job.get("ops") or ())),
                _dumps(job),
            ),
        )

    def record_claimed(
        self, job_id: str, *, claimed_utc: str | None = None, session_id: str | None = None
    ) -> None:
        self.execute(
            "UPDATE job_record SET claimed_utc=?, session_id=COALESCE(?, session_id) "
            "WHERE job_id=?",
            (claimed_utc or utc_now(), session_id, job_id),
        )

    def record_finished(
        self,
        job_id: str,
        *,
        outcome: ApplyOutcome,
        result: Mapping[str, Any] | None = None,
        finished_utc: str | None = None,
        claimed_utc: str | None = None,
        applied_count: int = 0,
        failed_count: int = 0,
        trigger_event: str | None = None,
        save_uid: str | None = None,
        exec_ms: int | None = None,
    ) -> JobRecord | None:
        """Close the row and compute both durations. §7.2.

        ``latency_ms`` = submitted -> finished (includes the event wait);
        ``exec_ms``    = claimed  -> finished (the actual work). The core may
        report ``exec_ms`` itself, in which case its number wins -- it measured
        inside the game process, we only see file mtimes.
        """
        with self.tx():
            row = self.one("SELECT * FROM job_record WHERE job_id=?", (job_id,))
            if row is None:
                # A result for a job we never saw submitted (imported history,
                # or a job submitted by another install). Keep it: an orphan row
                # is far more useful than a dropped result.
                self.execute(
                    "INSERT INTO job_record(job_id, created_utc, outcome, job_json) "
                    "VALUES (?,?,?,'{}')",
                    (job_id, finished_utc or utc_now(), int(outcome)),
                )
                row = self.one("SELECT * FROM job_record WHERE job_id=?", (job_id,))
            assert row is not None
            fin = finished_utc or utc_now()
            claim = claimed_utc or row["claimed_utc"]
            submitted = row["submitted_utc"] or row["created_utc"]
            self.execute(
                """UPDATE job_record SET
                     finished_utc=?, claimed_utc=?, outcome=?, result_json=?,
                     applied_count=?, failed_count=?, trigger_event=COALESCE(?, trigger_event),
                     save_uid=COALESCE(?, save_uid),
                     latency_ms=?, exec_ms=?
                   WHERE job_id=?""",
                (
                    fin,
                    claim,
                    int(outcome),
                    _dumps(result) if result is not None else None,
                    int(applied_count),
                    int(failed_count),
                    trigger_event,
                    save_uid,
                    _delta_ms(submitted, fin),
                    exec_ms if exec_ms is not None else _delta_ms(claim, fin),
                    job_id,
                ),
            )
            out = self.one("SELECT * FROM job_record WHERE job_id=?", (job_id,))
        return JobRecord.from_row(out) if out is not None else None

    def upsert_job_record(self, rec: JobRecord) -> None:
        """Write a fully-formed record. Used by ``migrate`` and by tests."""
        self.execute(
            """INSERT INTO job_record
               (job_id, created_utc, submitted_utc, claimed_utc, finished_utc, origin, label,
                save_uid, session_id, outcome, op_count, applied_count, failed_count,
                latency_ms, exec_ms, trigger_event, job_json, result_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 created_utc=excluded.created_utc, submitted_utc=excluded.submitted_utc,
                 claimed_utc=excluded.claimed_utc, finished_utc=excluded.finished_utc,
                 origin=excluded.origin, label=excluded.label, save_uid=excluded.save_uid,
                 session_id=excluded.session_id, outcome=excluded.outcome,
                 op_count=excluded.op_count, applied_count=excluded.applied_count,
                 failed_count=excluded.failed_count, latency_ms=excluded.latency_ms,
                 exec_ms=excluded.exec_ms, trigger_event=excluded.trigger_event,
                 job_json=excluded.job_json, result_json=excluded.result_json""",
            (
                rec.job_id,
                rec.created_utc,
                rec.submitted_utc,
                rec.claimed_utc,
                rec.finished_utc,
                rec.origin,
                rec.label,
                rec.save_uid,
                rec.session_id,
                int(rec.outcome),
                rec.op_count,
                rec.applied_count,
                rec.failed_count,
                rec.latency_ms,
                rec.exec_ms,
                rec.trigger_event,
                _dumps(rec.job_json or {}),
                _dumps(rec.result_json) if rec.result_json is not None else None,
            ),
        )

    def job_record(self, job_id: str) -> JobRecord | None:
        row = self.one("SELECT * FROM job_record WHERE job_id=?", (job_id,))
        return JobRecord.from_row(row) if row is not None else None

    def job_records(
        self,
        *,
        limit: int = 50,
        save_uid: str | None = None,
        outcome: ApplyOutcome | None = None,
        since_utc: str | None = None,
    ) -> list[JobRecord]:
        sql = "SELECT * FROM job_record WHERE 1=1"
        params: list[Any] = []
        if save_uid is not None:
            sql += " AND save_uid=?"
            params.append(save_uid)
        if outcome is not None:
            sql += " AND outcome=?"
            params.append(int(outcome))
        if since_utc:
            sql += " AND created_utc>=?"
            params.append(since_utc)
        sql += " ORDER BY created_utc DESC LIMIT ?"
        params.append(int(limit))
        return [JobRecord.from_row(r) for r in self.query(sql, params)]

    def job_stats(self, *, since_utc: str | None = None) -> dict[str, Any]:
        """Counts by outcome plus the two medians the doctor screen prints (§7.3)."""
        where, params = ("WHERE created_utc>=?", [since_utc]) if since_utc else ("", [])
        counts = {
            ApplyOutcome(int(r["outcome"])).name.lower(): int(r["n"])
            for r in self.query(
                f"SELECT outcome, COUNT(*) n FROM job_record {where} GROUP BY outcome", params
            )
        }
        lat = [
            int(r["latency_ms"])
            for r in self.query(
                f"SELECT latency_ms FROM job_record {where} "
                f"{'AND' if where else 'WHERE'} latency_ms IS NOT NULL",
                params,
            )
        ]
        ex = [
            int(r["exec_ms"])
            for r in self.query(
                f"SELECT exec_ms FROM job_record {where} "
                f"{'AND' if where else 'WHERE'} exec_ms IS NOT NULL",
                params,
            )
        ]
        median_lat, median_exec = _median(lat), _median(ex)
        return {
            "total": sum(counts.values()),
            "by_outcome": counts,
            "median_latency_ms": median_lat,
            "median_exec_ms": median_exec,
            # The gap is the event wait. This is the R3 decision number (§7.2).
            "median_wait_ms": (
                None if median_lat is None or median_exec is None
                else max(0, median_lat - median_exec)
            ),
        }

    # -- snapshots ------------------------------------------------------

    def put_snapshot(
        self,
        *,
        id: str,
        ts: float,
        fields: Sequence[Mapping[str, Any]],
        label: str = "",
        kind: str = "card",
        target_id: int | None = None,
        save_uid: str | None = None,
        extra: Mapping[str, Any] | None = None,
        is_last_apply: bool = False,
        taken_utc: str | None = None,
    ) -> None:
        with self.tx():
            if is_last_apply:
                self.execute("UPDATE snapshot SET is_last_apply=0 WHERE is_last_apply=1")
            self.execute(
                """INSERT INTO snapshot
                   (id, ts, taken_utc, save_uid, target_id, label, kind, fields_json,
                    n_fields, extra_json, is_last_apply)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     ts=excluded.ts, taken_utc=excluded.taken_utc, save_uid=excluded.save_uid,
                     target_id=excluded.target_id, label=excluded.label, kind=excluded.kind,
                     fields_json=excluded.fields_json, n_fields=excluded.n_fields,
                     extra_json=excluded.extra_json, is_last_apply=excluded.is_last_apply""",
                (
                    id,
                    float(ts),
                    taken_utc or _ts_to_utc(ts) or utc_now(),
                    save_uid,
                    target_id,
                    label,
                    kind,
                    _dumps(list(fields)),
                    len(fields),
                    _dumps(extra) if extra else None,
                    1 if is_last_apply else 0,
                ),
            )

    def snapshot(self, snapshot_id: str) -> SnapshotRow | None:
        row = self.one("SELECT * FROM snapshot WHERE id=?", (snapshot_id,))
        return SnapshotRow.from_row(row) if row is not None else None

    def snapshots(
        self, *, limit: int = 80, target_id: int | None = None
    ) -> list[SnapshotRow]:
        if target_id is None:
            rows = self.query("SELECT * FROM snapshot ORDER BY ts DESC LIMIT ?", (limit,))
        else:
            rows = self.query(
                "SELECT * FROM snapshot WHERE target_id=? ORDER BY ts DESC LIMIT ?",
                (target_id, limit),
            )
        return [SnapshotRow.from_row(r) for r in rows]

    def snapshot_by_time_target(
        self, ts: float, target_id: int | None
    ) -> SnapshotRow | None:
        """Find a snapshot twin without relying on an arbitrary history cap."""
        row = self.one(
            """SELECT * FROM snapshot
               WHERE target_id IS ? AND ABS(ts - ?) < 0.001
               ORDER BY ts DESC LIMIT 1""",
            (target_id, float(ts)),
        )
        return SnapshotRow.from_row(row) if row is not None else None

    def last_apply_snapshot(self) -> SnapshotRow | None:
        row = self.one("SELECT * FROM snapshot WHERE is_last_apply=1")
        return SnapshotRow.from_row(row) if row is not None else None

    def mark_last_apply(self, snapshot_id: str) -> None:
        with self.tx():
            self.execute("UPDATE snapshot SET is_last_apply=0 WHERE is_last_apply=1")
            self.execute("UPDATE snapshot SET is_last_apply=1 WHERE id=?", (snapshot_id,))

    def delete_snapshot(self, snapshot_id: str) -> None:
        self.execute("DELETE FROM snapshot WHERE id=?", (snapshot_id,))

    # -- favorites ------------------------------------------------------

    def add_favorite(
        self,
        *,
        year: str,
        playerid: str,
        revision: str = "",
        name: str = "",
        ovr: int | None = None,
        card: Mapping[str, Any] | None = None,
        added_utc: str | None = None,
    ) -> None:
        self.execute(
            """INSERT INTO favorite(year, playerid, revision, name, ovr, added_utc, card_json)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(year, playerid, revision) DO UPDATE SET
                 name=excluded.name, ovr=excluded.ovr, card_json=excluded.card_json""",
            (
                str(year),
                str(playerid),
                str(revision or ""),
                name,
                ovr,
                added_utc or utc_now(),
                _dumps(card or {}),
            ),
        )

    def remove_favorite(self, year: str, playerid: str, revision: str = "") -> None:
        self.execute(
            "DELETE FROM favorite WHERE year=? AND playerid=? AND revision=?",
            (str(year), str(playerid), str(revision or "")),
        )

    def is_favorite(self, year: str, playerid: str, revision: str = "") -> bool:
        return (
            self.one(
                "SELECT 1 FROM favorite WHERE year=? AND playerid=? AND revision=?",
                (str(year), str(playerid), str(revision or "")),
            )
            is not None
        )

    def favorites(self, *, limit: int = 500) -> list[FavoriteRow]:
        return [
            FavoriteRow.from_row(r)
            for r in self.query("SELECT * FROM favorite ORDER BY added_utc DESC LIMIT ?", (limit,))
        ]

    # -- live save state (§5.3) ----------------------------------------

    def put_save_snapshot(
        self,
        *,
        save_uid: str | None,
        kind: str,
        payload: Any,
        taken_ts: float | None = None,
        taken_utc: str | None = None,
        stale: bool = False,
    ) -> None:
        ts = float(taken_ts) if taken_ts is not None else datetime.now(timezone.utc).timestamp()
        self.execute(
            """INSERT INTO save_snapshot(save_uid, kind, taken_utc, taken_ts, stale, payload)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(save_uid, kind) DO UPDATE SET
                 taken_utc=excluded.taken_utc, taken_ts=excluded.taken_ts,
                 stale=excluded.stale, payload=excluded.payload""",
            (save_uid or "", kind, taken_utc or _ts_to_utc(ts) or utc_now(), ts,
             1 if stale else 0, _dumps(payload)),
        )

    def save_snapshot(
        self,
        save_uid: str | None,
        kind: str,
        *,
        now_ts: float | None = None,
        ttl_s: float | None = SAVE_SNAPSHOT_TTL_S,
    ) -> SaveSnapshotRow | None:
        """Fetch a cached live-state blob. ``None`` once it is past its TTL.

        Pass ``ttl_s=None`` to read the row regardless of age -- the UI does that
        to render "Squad, as of 4 min ago, refresh" rather than showing nothing.
        """
        row = self.one(
            "SELECT * FROM save_snapshot WHERE save_uid=? AND kind=?", (save_uid or "", kind)
        )
        if row is None:
            return None
        snap = SaveSnapshotRow.from_row(row)
        if ttl_s is None:
            return snap
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        return snap if snap.is_fresh(now, ttl_s) else None

    def latest_save_snapshot(self, kind: str) -> SaveSnapshotRow | None:
        """Return the newest cached value of ``kind``, including stale values.

        Startup does not yet know the live save UID, so hydration must not
        pretend a cache belongs to the current save.  The row is returned only
        to render the last-known value with its persisted stale flag; the first
        successful live sync replaces it with a save-scoped row.
        """
        row = self.one(
            "SELECT * FROM save_snapshot WHERE kind=? "
            "ORDER BY taken_ts DESC LIMIT 1",
            (kind,),
        )
        return SaveSnapshotRow.from_row(row) if row is not None else None

    def invalidate_save_snapshots(
        self, save_uid: str | None = None, kinds: Sequence[str] | None = None
    ) -> int:
        """Any successful write job invalidates the kinds it touched (§5.3).

        Marks stale rather than deleting: the UI can still show the last known
        squad with an explicit staleness badge, which beats an empty screen.
        """
        sql = "UPDATE save_snapshot SET stale=1 WHERE 1=1"
        params: list[Any] = []
        if save_uid is not None:
            sql += " AND save_uid=?"
            params.append(save_uid)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        return self.execute(sql, params).rowcount

    def purge_save_snapshots(self, *, older_than_ts: float) -> int:
        return self.execute(
            "DELETE FROM save_snapshot WHERE taken_ts < ?", (float(older_than_ts),)
        ).rowcount


class _Tx:
    """Reentrant transaction scope. See ``Database.tx``."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._owner = False

    def __enter__(self) -> "_Tx":
        self._db._lock.acquire()
        if not self._db.conn.in_transaction:
            self._db.conn.execute("BEGIN IMMEDIATE")
            self._owner = True
        return self

    def __exit__(self, exc_type: Any, *_rest: Any) -> None:
        try:
            if self._owner:
                self._db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._db._lock.release()


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def open_state_db(paths: Any, *, migrate: bool = True) -> Database:
    """Open (and migrate) ``paths.state_db``. The only constructor callers need."""
    return Database(getattr(paths, "state_db", Path("state.sqlite")), migrate=migrate)
