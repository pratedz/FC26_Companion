"""``companion migrate`` -- import v1's seven loose JSON files into ``state.sqlite``.

Three properties, and they are the whole design (``docs/V2_ARCHITECTURE.md §8.1``):

1. **Idempotent.** Every write is an upsert on a stable key, and every key is
   derived from the v1 content, not from the clock. Running it five times leaves
   the same rows. Prefs are *first-import-wins*: once a key exists in
   ``state.sqlite`` a re-run reports it as ``kept`` and does not clobber a value
   the user has since changed in v2.
2. **Never deletes a v1 file.** Not even after a verified import. v1 must keep
   working the day after v2 is installed -- that is the prime directive of §8 --
   and a migration that eats its source cannot be re-run when it turns out to
   have been wrong. The one file §8.1 *does* mark for deletion,
   ``xai_credentials.json``, is deliberately not deleted here either: shredding
   plaintext secrets belongs to ``platform/secrets.py``, which owns the DPAPI
   round-trip that must succeed first.
3. **Writes a report.** ``logs/migrate-report.json`` plus the returned
   :class:`Report`. Per-source counts, every dropped key with the reason it was
   dropped, and every error -- because a migration that half-worked and said
   nothing is how v1 lost history in the first place.

The hard part is job ids. v1's ``job_history.json`` rows have **no identifier**
at all (``ts``/``kind``/``detail``/``outcome``/``reason``/``target_id``/
``queue_file``/``last_result``, verified against the real 40-row file on this
machine). ``job_record.job_id`` is the primary key, so an import needs one that
is stable across runs. :func:`_stable_ulid` builds a *genuine* ULID -- 48-bit
timestamp from the row's own ``ts``, 80 bits of "randomness" from a BLAKE2b
digest of the row's canonical JSON -- so the id is deterministic, still sorts by
time like every other job id, and still passes ``domain.ids.is_ulid``. Imported
rows are history only; they are never replayable (``job_json`` is ``{}``,
``op_count`` is 0), which the report states plainly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ...core.db import Database, JobRecord, open_state_db, utc_now
from ...domain.ids import _CROCKFORD, is_ulid  # noqa: PLC2701 -- same package, one encoder
from ...domain.outcome import ApplyOutcome

__all__ = ["Report", "SourceResult", "run", "OUTCOME_MAP", "DROPPED_PREFS"]

_MISSING = object()

#: v1's outcome vocabulary (``src/apply_service.py`` lines 21-25, plus
#: ``boost_queue`` job statuses) mapped onto the closed enum.
#:
#: ``error`` -> ``FAILED`` even for the "player NOT FOUND" rows that v2 would
#: today classify ``NO_OP``: the import preserves v1's own verdict rather than
#: re-litigating a job whose result payload no longer exists.
OUTCOME_MAP: Mapping[str, ApplyOutcome] = {
    "applied": ApplyOutcome.APPLIED,
    "queued_live": ApplyOutcome.QUEUED,
    "queued": ApplyOutcome.QUEUED,
    "timeout": ApplyOutcome.EXPIRED,
    "expired": ApplyOutcome.EXPIRED,
    "blocked": ApplyOutcome.REJECTED,
    "cancelled": ApplyOutcome.REJECTED,
    "rejected": ApplyOutcome.REJECTED,
    "error": ApplyOutcome.FAILED,
    "failed": ApplyOutcome.FAILED,
    "partial": ApplyOutcome.PARTIAL,
    "no_op": ApplyOutcome.NO_OP,
    "no_change": ApplyOutcome.NO_OP,
    "crashed": ApplyOutcome.CRASHED,
}

#: Config keys that do **not** become prefs, each with the reason. The v1 file
#: keeps them; v2 simply has no meaning for them.
DROPPED_PREFS: Mapping[str, str] = {
    "timeout_live": "protocol v3 drains on a CareerModeEvent; there is no poll timeout to tune",
    "timeout_offline": "same -- offline submission is fire-and-forget under v3",
    "poll_sec": "same -- the app awaits a result file, it does not poll on an interval",
    "version": "§8.1: v2 has exactly two counters, PROTOCOL_V and SCHEMA_VERSION",
    "queue_rel": "queue layout is owned by core/paths.py; a stored relative path is an SI-7 hazard",
    "inject_dll_name": "SI-1: injection belongs to the LE Launcher; v2 has no injection path",
    "inject_autoarm": "SI-3: v2 arms only via lua/autorun, never by an auto-inject flag",
}

#: v1's snapshot sidecars live here, relative to the app root.
_SNAPSHOT_DIR = "snapshots"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class SourceResult:
    """What happened to one v1 file."""

    source: str
    present: bool = False
    imported: int = 0
    skipped: int = 0          # already present in state.sqlite (idempotent re-run)
    dropped: int = 0          # deliberately not carried over
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "present": self.present,
            "imported": self.imported,
            "skipped": self.skipped,
            "dropped": self.dropped,
            "errors": self.errors,
            "notes": self.notes,
        }


@dataclass
class Report:
    root: str = ""
    state_db: str = ""
    schema_version: int = 0
    started_utc: str = ""
    finished_utc: str = ""
    sources: list[SourceResult] = field(default_factory=list)
    report_path: str = ""

    @property
    def ok(self) -> bool:
        return not any(s.errors for s in self.sources)

    @property
    def total_imported(self) -> int:
        return sum(s.imported for s in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "root": self.root,
            "state_db": self.state_db,
            "schema_version": self.schema_version,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "total_imported": self.total_imported,
            "sources": [s.to_dict() for s in self.sources],
            "report_path": self.report_path,
            "note": "No v1 file is modified or deleted by migrate.",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path, result: SourceResult) -> Any:
    """Read a v1 JSON file, recording rather than swallowing every failure.

    This is the inverse of v1, where all seven readers ended in
    ``except (OSError, JSONDecodeError): pass`` and a corrupt file was
    indistinguishable from an empty one.
    """
    if not path.is_file():
        return None
    result.present = True
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        result.errors.append(f"{path.name}: unreadable ({type(e).__name__}: {e})")
        return None


def _encode_c32(value: int, length: int) -> str:
    out = ["0"] * length
    for i in range(length - 1, -1, -1):
        out[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(out)


def _stable_ulid(ts: float, payload: Mapping[str, Any] | str) -> str:
    """A real ULID that is a pure function of the row -- so re-running is a no-op.

    48-bit ms timestamp from the row's own ``ts`` (keeping the time-sortable
    property every other job id has), 80 bits from BLAKE2b over the row's
    canonical JSON in place of ``os.urandom``. Collisions would need two
    byte-identical history rows, which are the same row.
    """
    blob = payload if isinstance(payload, str) else json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    digest = hashlib.blake2b(blob.encode("utf-8"), digest_size=10).digest()
    try:
        ms = int(float(ts) * 1000)
    except (TypeError, ValueError):
        ms = 0
    ms = max(0, ms) & ((1 << 48) - 1)
    return _encode_c32(ms, 10) + _encode_c32(int.from_bytes(digest, "big"), 16)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-source importers
# ---------------------------------------------------------------------------


def _migrate_config(db: Database, root: Path) -> SourceResult:
    """``companion_config.json`` -> ``prefs``. First import wins."""
    res = SourceResult("companion_config.json")
    data = _read_json(root / "companion_config.json", res)
    if not isinstance(data, dict):
        if res.present and not res.errors:
            res.errors.append("companion_config.json is not a JSON object")
        return res
    for key, value in data.items():
        if key in DROPPED_PREFS:
            res.dropped += 1
            res.notes.append(f"dropped {key}: {DROPPED_PREFS[key]}")
            continue
        if db.get_pref(key, _MISSING) is not _MISSING:
            res.skipped += 1
            continue
        db.set_pref(key, value)
        res.imported += 1
    return res


def _migrate_first_run(db: Database, root: Path) -> SourceResult:
    """``.first_run_done`` (a marker file holding ``1``) -> a boolean pref."""
    res = SourceResult(".first_run_done")
    marker = root / ".first_run_done"
    if not marker.is_file():
        return res
    res.present = True
    if db.get_pref("first_run_done", _MISSING) is not _MISSING:
        res.skipped += 1
        return res
    db.set_pref("first_run_done", True)
    res.imported += 1
    return res


def _migrate_job_history(db: Database, root: Path) -> SourceResult:
    """``job_history.json`` (array, capped at 40) -> ``job_record``.

    v1 stored a machine-absolute ``queue_file`` path in every row. That path is
    meaningless on any other machine and is exactly the kind of thing that makes
    a bug report unusable, so it is preserved inside ``result_json`` for
    forensics but never becomes a column.
    """
    res = SourceResult("job_history.json")
    data = _read_json(root / "job_history.json", res)
    if data is None:
        return res
    if not isinstance(data, list):
        res.errors.append("job_history.json is not a JSON array")
        return res

    for i, row in enumerate(data):
        if not isinstance(row, dict):
            res.errors.append(f"row {i} is not an object")
            continue
        ts = row.get("ts") or 0
        job_id = _stable_ulid(ts, row)
        if not is_ulid(job_id):  # pragma: no cover -- encoder guarantees this
            res.errors.append(f"row {i}: could not derive a job id")
            continue
        if db.job_record(job_id) is not None:
            res.skipped += 1
            continue
        outcome = OUTCOME_MAP.get(
            str(row.get("outcome") or "").strip().lower(), ApplyOutcome.FAILED
        )
        created = _iso(ts)
        db.upsert_job_record(
            _make_record(
                job_id=job_id,
                created=created,
                outcome=outcome,
                row=row,
            )
        )
        res.imported += 1

    if res.imported:
        res.notes.append(
            "imported rows are history only: job_json is empty, so they are not replayable"
        )
        res.notes.append("latency_ms/exec_ms are NULL -- v1 recorded neither")
    return res


def _make_record(
    *, job_id: str, created: str, outcome: ApplyOutcome, row: Mapping[str, Any]
) -> JobRecord:
    detail = str(row.get("detail") or "")
    kind = str(row.get("kind") or "job")
    return JobRecord(
        job_id=job_id,
        created_utc=created,
        outcome=outcome,
        origin=f"v1.{kind}",
        label=detail[:200],
        submitted_utc=created,
        finished_utc=created,
        op_count=0,
        applied_count=1 if outcome.is_success else 0,
        failed_count=1 if outcome is ApplyOutcome.FAILED else 0,
        job_json={},
        result_json={
            "imported_from": "v1 job_history.json",
            "kind": kind,
            "detail": detail,
            "outcome_v1": row.get("outcome"),
            "reason": row.get("reason"),
            "target_id": row.get("target_id"),
            "queue_file": row.get("queue_file"),
            "last_result": row.get("last_result"),
        },
    )


def _iso(ts: Any) -> str:
    """A v1 epoch float -> the storage timestamp format. Falls back to now."""
    try:
        return (
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return utc_now()


def _migrate_snapshots(db: Database, root: Path) -> SourceResult:
    """``snapshots/index.json`` + one sidecar per entry -> the ``snapshot`` table.

    The index is the authority on *which* snapshots exist; the sidecar holds the
    ``fields`` list that makes undo possible. An index entry whose sidecar is
    missing or corrupt is recorded as an error and skipped -- importing it with
    an empty field list would produce an undo button that silently does nothing.
    """
    res = SourceResult("snapshots/")
    snap_dir = root / _SNAPSHOT_DIR
    index = _read_json(snap_dir / "index.json", res)
    if index is None:
        return res
    if not isinstance(index, list):
        res.errors.append("snapshots/index.json is not a JSON array")
        return res

    for entry in index:
        if not isinstance(entry, dict):
            res.errors.append("index entry is not an object")
            continue
        sid = str(entry.get("id") or "").strip()
        if not sid:
            res.errors.append("index entry has no id")
            continue
        if db.snapshot(sid) is not None:
            res.skipped += 1
            continue
        # SI-7: the stored path is untrusted input. Only its basename, only
        # under snapshots/ -- v1's own loader had the same guard for this reason.
        name = Path(str(entry.get("path") or f"{sid}.json")).name
        sidecar = snap_dir / name
        if not sidecar.is_file():
            sidecar = snap_dir / f"{sid}.json"
        probe = SourceResult("sidecar")
        body = _read_json(sidecar, probe)
        if not isinstance(body, dict):
            # Carry the specific reason (truncated JSON vs absent file) forward.
            res.errors.append(
                f"{sid}: " + (probe.errors[0] if probe.errors else f"sidecar missing ({sidecar.name})")
            )
            continue
        fields = body.get("fields")
        if not isinstance(fields, list):
            res.errors.append(f"{sid}: sidecar has no fields list")
            continue
        db.put_snapshot(
            id=sid,
            ts=float(entry.get("ts") or body.get("ts") or 0.0),
            fields=fields,
            label=str(entry.get("label") or body.get("label") or ""),
            kind=str(entry.get("kind") or body.get("kind") or "card"),
            target_id=_as_int(entry.get("target_id") or body.get("target_id")),
            save_uid=None,  # v1 snapshots have no save key -- §5.3's core defect
            extra=body.get("extra") if isinstance(body.get("extra"), dict) else None,
        )
        res.imported += 1

    if res.imported:
        res.notes.append("save_uid is NULL: v1 never recorded which save a snapshot came from")
    return res


def _migrate_last_apply(db: Database, root: Path) -> SourceResult:
    """``last_apply_snapshot.json`` -> the ``is_last_apply`` flag on one row.

    v1 kept this as a *separate file duplicating a snapshot*, so the two stores
    could and did disagree. If a snapshot with the same ``ts`` and target was
    already imported from the index, flag that row instead of inserting a twin;
    otherwise insert this one. The partial unique index in ``core/db.py``
    guarantees at most one row ends up flagged either way.
    """
    res = SourceResult("last_apply_snapshot.json")
    data = _read_json(root / "last_apply_snapshot.json", res)
    if not isinstance(data, dict):
        if res.present and not res.errors:
            res.errors.append("last_apply_snapshot.json is not a JSON object")
        return res
    fields = data.get("fields")
    if not isinstance(fields, list):
        res.errors.append("last_apply_snapshot.json has no fields list")
        return res

    ts = float(data.get("ts") or 0.0)
    target = _as_int(data.get("target_id"))
    twin = db.snapshot_by_time_target(ts, target)
    if twin is not None:
        if twin.is_last_apply:
            res.skipped += 1
        else:
            db.mark_last_apply(twin.id)
            res.imported += 1
            res.notes.append(f"flagged existing snapshot {twin.id} as the last apply")
        return res

    sid = "la" + _stable_ulid(ts, data)[-10:].lower()
    existing = db.snapshot(sid)
    if existing is not None and existing.is_last_apply:
        res.skipped += 1
        return res
    db.put_snapshot(
        id=sid,
        ts=ts,
        fields=fields,
        label=str(data.get("label") or ""),
        kind=str(data.get("kind") or "card"),
        target_id=target,
        save_uid=None,
        is_last_apply=True,
    )
    res.imported += 1
    return res


def _migrate_favorites(db: Database, root: Path) -> SourceResult:
    """``favorites.json`` -> ``favorite`` with a real composite PK.

    v1 keyed on ``f"{year}|{pid}|{name}|{rev}"``, so a card whose name changed in
    the catalog silently became a second, orphaned favorite. The PK here is
    ``(year, playerid, revision)`` and the name is carried as data.
    """
    res = SourceResult("favorites.json")
    data = _read_json(root / "favorites.json", res)
    if data is None:
        if not res.present:
            res.notes.append("not present on this install -- nothing to import")
        return res
    if not isinstance(data, list):
        res.errors.append("favorites.json is not a JSON array")
        return res

    for card in data:
        if not isinstance(card, dict):
            res.errors.append("favorite entry is not an object")
            continue
        # v1's _key() fell back to `uid` when `playerid` was absent; match it.
        pid = card.get("playerid") or card.get("uid")
        year = card.get("year")
        if pid in (None, "") or year in (None, ""):
            res.errors.append(f"favorite {card.get('name')!r} has no year/playerid -- skipped")
            continue
        revision = str(card.get("revision") or card.get("origin") or "")
        if db.is_favorite(str(year), str(pid), revision):
            res.skipped += 1
            continue
        db.add_favorite(
            year=str(year),
            playerid=str(pid),
            revision=revision,
            name=str(card.get("name") or ""),
            ovr=_as_int(card.get("overallrating")),
            card=card,
        )
        res.imported += 1
    return res


def _migrate_current_squad(db: Database, root: Path) -> SourceResult:
    """``current_squad.json`` -> one ``save_snapshot`` row, marked stale.

    Marked stale on purpose and permanently: v1's file carried **no save key**,
    so it would happily show save A's squad while the user edited save B (§5.3).
    The data is worth keeping for the "last known squad" view; presenting it as
    current would be the same lie with a database behind it.
    """
    res = SourceResult("current_squad.json")
    path = root / "current_squad.json"
    data = _read_json(path, res)
    if data is None:
        return res
    if not isinstance(data, dict):
        res.errors.append("current_squad.json is not a JSON object")
        return res

    existing = db.save_snapshot("", "squad", ttl_s=None)
    if existing is not None and existing.stale:
        res.skipped += 1
        return res
    try:
        taken = path.stat().st_mtime
    except OSError:
        taken = 0.0
    db.put_save_snapshot(save_uid="", kind="squad", payload=data, taken_ts=taken, stale=True)
    res.imported += 1
    res.notes.append(
        f"save_uid='' and stale=1: v1 had no save key, "
        f"so {data.get('teamname') or 'this squad'} can never be served as fresh"
    )
    return res


def _migrate_credentials(root: Path) -> SourceResult:
    """``xai_credentials.json`` -> DPAPI, never SQLite.

    The file holds a plaintext ``access_token`` and ``refresh_token`` at the
    install root. Putting them in a world-readable ``state.sqlite`` next to the
    exe would be the same bug with extra steps (§5.4), so this hands off to
    ``platform/secrets.py`` if that module exists yet and otherwise reports the
    file as still-plaintext and moves on. **The file is never deleted here** --
    only a verified DPAPI round-trip may justify that, and that decision belongs
    to the module that performs it.
    """
    res = SourceResult("xai_credentials.json")
    path = root / "xai_credentials.json"
    if not path.is_file():
        return res
    res.present = True
    try:
        from ...platform import secrets as secrets_mod  # type: ignore[attr-defined]
    except ImportError:
        res.skipped += 1
        res.notes.append(
            "platform/secrets.py not available -- credentials left as plaintext at the "
            "install root; re-run migrate once the secrets module ships"
        )
        return res

    importer = getattr(secrets_mod, "import_v1_credentials", None) or getattr(
        secrets_mod, "migrate_from_file", None
    )
    if importer is None:
        res.skipped += 1
        res.notes.append(
            "platform.secrets exposes no import_v1_credentials()/migrate_from_file() -- skipped"
        )
        return res
    # Prime directive (§8 / DECISIONS): migrate never deletes a v1 file.
    # secrets.import_v1_credentials defaults to shred=True for standalone use;
    # at this boundary we seal only and leave the plaintext in place.
    try:
        importer(path, shred=False)
    except TypeError:
        # A path-only legacy importer may default to deleting its source.  Do
        # not retry it without shred=False: migration must never erase v1 data.
        res.skipped += 1
        res.notes.append(
            "credential importer does not support shred=False; plaintext left untouched"
        )
        return res
    except Exception as e:  # noqa: BLE001 -- the report is the only place this can surface
        res.errors.append(f"DPAPI import failed ({type(e).__name__}: {e}); file left untouched")
        return res
    res.imported += 1
    res.notes.append(
        "re-encrypted to DPAPI via platform.secrets; plaintext left in place "
        "(migrate never shreds v1 files)"
    )
    return res


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    paths: Any,
    *,
    db: Database | None = None,
    write_report: bool = True,
    log: Any = None,
) -> Report:
    """Import every v1 store. Safe to run repeatedly; never raises on bad data.

    ``paths`` is an ``AppPaths``. ``db`` may be injected by a test or by the app
    when it already holds the handle; otherwise one is opened (and migrated) on
    ``paths.state_db`` and closed again before returning.
    """
    root = Path(getattr(paths, "root", "."))
    owned = db is None
    database = db if db is not None else open_state_db(paths)
    report = Report(
        root=str(root),
        state_db=str(getattr(paths, "state_db", "")),
        schema_version=database.version,
        started_utc=utc_now(),
    )
    try:
        report.sources = [
            _migrate_config(database, root),
            _migrate_first_run(database, root),
            _migrate_job_history(database, root),
            _migrate_snapshots(database, root),
            _migrate_last_apply(database, root),
            _migrate_favorites(database, root),
            _migrate_current_squad(database, root),
            _migrate_credentials(root),
        ]
        report.finished_utc = utc_now()
        database.set_pref("migrate.last_run_utc", report.finished_utc)
        database.set_pref("migrate.schema_version", database.version)
        if write_report:
            report.report_path = _write_report(paths, report, log=log)
    finally:
        if owned:
            database.close()

    if log is not None:
        try:
            log.info(
                "migrate: %d rows imported from %d sources (%s)",
                report.total_imported,
                len(report.sources),
                "ok" if report.ok else "with errors",
                extra={"command": "migrate"},
            )
        except Exception:  # noqa: BLE001 -- logging must never break the migration
            pass
    return report


def _write_report(paths: Any, report: Report, *, log: Any = None) -> str:
    """Write ``logs/migrate-report.json`` atomically (rename over a temp file).

    Atomic because this is the file a user will be asked to paste into a bug
    report, and v1's habit of half-written JSON is the whole reason for this
    module's existence.
    """
    logs = Path(getattr(paths, "logs", Path("logs")))
    try:
        logs.mkdir(parents=True, exist_ok=True)
        target = logs / "migrate-report.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(target)
        return str(target)
    except OSError as e:
        if log is not None:
            log.warning("migrate: could not write report: %s", e)
        report.sources.append(
            SourceResult("migrate-report.json", errors=[f"could not write report: {e}"])
        )
        return ""
