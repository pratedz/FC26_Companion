"""Offline protocol-v3 drain mock — exercises claim → result without FC 26 / Lua.

Mirrors the in-game core's wire behaviour for the ops that must work offline
(``diag.ping`` always; ``set_fields`` / ``export_squad`` as honest dry-path
results when no host DB is present). Used by tests and by developers who want
to prove the Python↔queue contract without launching Live Editor.

This is intentionally a *protocol twin*, not a reimplementation of every Lua
line: it claims by rename, writes ``schema:3`` results, and updates
``session.json`` / ``drain.json`` the same way ``ingame/le_companion`` does.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from ..domain.ids import is_ulid
from .transport.jobfile import job_filename, list_job_ids, read_json, rewrite_job_index

CORE_VERSION = "2.6.11"
SCHEMA = 3


def arm_session(queue_dir: Path, *, session_id: str = "offline01", le_pid: int | None = None) -> dict[str, Any]:
    """Write session.json as the Lua core does at arm (handlers-only, no drain)."""
    q = Path(queue_dir)
    q.mkdir(parents=True, exist_ok=True)
    for sub in ("jobs", "claimed", "results", "state", "poison"):
        (q / sub).mkdir(parents=True, exist_ok=True)
    if le_pid is None:
        le_pid = os.getpid()
    body = {
        "schema": SCHEMA,
        "session_id": session_id,
        "core_version": CORE_VERSION,
        "contract": SCHEMA,
        "armed_at": int(time.time()),
        "le_pid": int(le_pid),
        "queue_dir": str(q.resolve()).replace("\\", "/"),
        "capabilities": {
            "claim_by_rename": True,
            "enumerate": "index",
            "legacy_lua_drain": True,
        },
        "phase": "offline_arm",
    }
    _atomic_write(q / "session.json", body)
    return body


def drain_once(
    queue_dir: Path,
    *,
    session_id: str = "offline01",
    max_jobs: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    """Claim and execute pending jobs under *queue_dir*. Returns a drain summary."""
    q = Path(queue_dir)
    jobs_dir = q / "jobs"
    claimed_dir = q / "claimed"
    results_dir = q / "results"
    for d in (jobs_dir, claimed_dir, results_dir, q / "state", q / "poison"):
        d.mkdir(parents=True, exist_ok=True)

    rewrite_job_index(jobs_dir)
    ids = list_job_ids(jobs_dir)[:max_jobs]
    done = failed = deferred = 0
    outcomes: list[dict[str, Any]] = []

    for jid in ids:
        try:
            result = _run_one(q, jid, session_id=session_id, force=force)
            outcomes.append(result)
            if result.get("state") == "deferred":
                deferred += 1
            elif result.get("ok"):
                done += 1
            else:
                failed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            outcomes.append({"job_id": jid, "ok": False, "error": str(exc)})

    summary = {
        "schema": SCHEMA,
        "at": int(time.time()),
        "session_id": session_id,
        "reason": "offline_drain",
        "jobs_seen": len(ids),
        "jobs_done": done,
        "jobs_failed": failed,
        "jobs_deferred": deferred,
        "duration_ms": 0,
        "outcomes": outcomes,
    }
    _atomic_write(q / "drain.json", {
        k: v for k, v in summary.items() if k != "outcomes"
    })
    rewrite_job_index(jobs_dir)
    return summary


def _run_one(
    queue: Path, job_id: str, *, session_id: str, force: bool = False
) -> dict[str, Any]:
    src = queue / "jobs" / job_filename(job_id)
    dst = queue / "claimed" / job_filename(job_id)
    if not src.is_file():
        raise FileNotFoundError(src)
    pending = read_json(src)
    if pending is not None and pending.get("deadline_kind") == "manual" and not force:
        return {
            "schema": SCHEMA,
            "job_id": job_id,
            "state": "deferred",
            "ok": False,
            "outcome": "deferred",
        }
    if dst.exists():
        raise RuntimeError("already_claimed")
    os.replace(src, dst)
    job = read_json(dst)
    if job is None:
        result = _reject(job_id, session_id, "bad_json", "claimed file not a job object")
        _finish(queue, job_id, dst, result)
        return result

    if int(job.get("schema") or 0) != SCHEMA:
        result = _reject(job_id, session_id, "bad_schema", f"schema {job.get('schema')}")
        _finish(queue, job_id, dst, result)
        return result
    if not is_ulid(str(job.get("job_id") or "")):
        result = _reject(job_id, session_id, "bad_job_id", str(job.get("job_id")))
        _finish(queue, job_id, dst, result)
        return result
    if job.get("atomic") and not job.get("dry_run"):
        result = _reject(
            job_id, session_id, "atomic_unavailable",
            "offline core cannot guarantee rollback; no writes were attempted",
        )
        _finish(queue, job_id, dst, result)
        return result
    expires_at = int(job.get("expires_at") or 0)
    if expires_at and int(time.time()) >= expires_at:
        result = _reject(job_id, session_id, "expired", "job expires_at deadline has passed")
        result["outcome"] = "expired"
        _finish(queue, job_id, dst, result)
        return result
    requires = job.get("requires") if isinstance(job.get("requires"), Mapping) else {}
    if job.get("require_cm", True) or requires.get("career_mode") is True:
        result = _reject(job_id, session_id, "career_required", "offline core has no loaded save")
        _finish(queue, job_id, dst, result)
        return result
    if requires.get("save_uid") is not None:
        result = _reject(
            job_id, session_id, "save_uid_unavailable",
            "offline core cannot verify requires.save_uid",
        )
        _finish(queue, job_id, dst, result)
        return result
    if job.get("snapshot_to") and not job.get("dry_run"):
        result = _reject(
            job_id, session_id, "snapshot_no_db",
            "offline core cannot capture a pre-write save snapshot",
        )
        _finish(queue, job_id, dst, result)
        return result

    op_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    counts: dict[str, int] = {
        "ops_total": len(job.get("ops") or []),
        "ops_ok": 0,
        "ops_failed": 0,
        "fields_written": 0,
        "fields_failed": 0,
        "fields_requested": 0,
        "found": 0,
        "missing": 0,
        "targets": 0,
        "side_effects": 0,
    }
    all_ok = True
    for op in job.get("ops") or []:
        if not isinstance(op, Mapping):
            all_ok = False
            counts["ops_failed"] += 1
            failures.append({"reason": "bad_op", "detail": "op not an object"})
            continue
        res = _dispatch_op(dict(op), dict(job), session_id=session_id, queue=queue)
        op_ok = bool(res.get("ok"))
        if op_ok:
            counts["ops_ok"] += 1
        else:
            all_ok = False
            counts["ops_failed"] += 1
        # Merge field/target counts only — never re-add ops_ok/ops_failed/ops_total.
        for k, v in (res.get("counts") or {}).items():
            if k in ("ops_ok", "ops_failed", "ops_total"):
                continue
            if isinstance(v, int) and k in counts:
                counts[k] += v
        op_results.append({
            "id": op.get("id", ""),
            "op": op.get("op", ""),
            "ok": op_ok,
            "counts": res.get("counts") or {},
            "data": res.get("data") or {},
        })
        if not op_ok:
            failures.append({
                "op": op.get("op"),
                "reason": res.get("reason") or "failed",
                "detail": res.get("detail") or "",
                "phase": "execute",
            })
            if (op.get("on_error") or "abort") == "abort":
                break

    state = "done" if all_ok else ("partial" if counts["fields_written"] else "failed")
    if all_ok and counts["fields_requested"] and not counts["found"] and not counts["fields_written"]:
        state = "no_op"
    for f in failures:
        if f.get("reason") in ("grant_denied", "bad_schema", "unknown_op", "unsupported_op"):
            # unsupported_op from offline set_fields is a failed execution, not reject
            if f.get("reason") in ("grant_denied", "bad_schema", "unknown_op"):
                state = "rejected"
                break

    now = int(time.time())
    result = {
        "schema": SCHEMA,
        "job_id": job_id,
        "label": job.get("label") or "",
        "session_id": session_id,
        "core_version": CORE_VERSION,
        "state": state,
        "ok": all_ok,
        "outcome": (
            "applied" if state == "done"
            else "no_op" if state == "no_op"
            else "partial" if state == "partial"
            else "rejected" if state == "rejected"
            else "failed"
        ),
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "steps": {"total": 1, "completed": 1, "resumes": 0, "attempts": 1},
        "counts": counts,
        "ops": op_results,
        "failures": failures,
        "error": None if all_ok else {
            "phase": "execute",
            "message": failures[0]["reason"] if failures else "failed",
        },
        "env": {"queue_dir": str(queue.resolve()).replace("\\", "/"), "offline": True},
    }
    _finish(queue, job_id, dst, result)
    return result


def _dispatch_op(
    op: dict[str, Any],
    job: dict[str, Any],
    *,
    session_id: str,
    queue: Path,
) -> dict[str, Any]:
    name = str(op.get("op") or "")
    if name == "diag.ping":
        # No ops_ok in counts — runner owns that tally (parity with ops.lua).
        return {
            "ok": True,
            "counts": {},
            "data": {
                "note": op.get("note") or "",
                "core_version": CORE_VERSION,
                "session_id": session_id,
                "queue_dir": str(queue.resolve()).replace("\\", "/"),
                "at": int(time.time()),
                "contract": SCHEMA,
                "offline": True,
            },
        }
    if name == "set_fields":
        fields = op.get("fields") or {}
        n = len(fields) if isinstance(fields, Mapping) else 0
        if job.get("dry_run") or op.get("dry_run"):
            return {
                "ok": True,
                "counts": {
                    "fields_requested": n,
                    "fields_written": 0,
                    "fields_failed": 0,
                    "found": 0,
                },
                "data": {"dry_run": True, "table": op.get("table") or "players"},
            }
        return {
            "ok": False,
            "reason": "no_db",
            "detail": "database not available (Career Mode save not loaded?)",
            "counts": {
                "fields_requested": n,
                "fields_written": 0,
                "fields_failed": n,
                "found": 0,
            },
            "data": {},
        }
    if name == "export_squad":
        if job.get("dry_run"):
            return {
                "ok": True,
                "counts": {"targets": 0},
                "data": {"dry_run": True, "players": [], "teamid": None},
            }
        return {
            "ok": False,
            "reason": "no_career",
            "detail": "export_squad needs Career Mode (no team id / DB)",
            "counts": {},
            "data": {},
        }
    if name == "bulk_edit":
        table = str(op.get("table") or "players")
        where = op.get("where") or {}
        scope = op.get("scope")
        teamid = op.get("teamid")
        empty_where = not isinstance(where, Mapping) or len(where) == 0
        if table == "players" and empty_where and not scope and teamid is None:
            return {
                "ok": False,
                "reason": "need_where",
                "detail": (
                    "bulk_edit on players with empty where is refused — pass where={...}, "
                    "teamid=N, or scope=user_team (never the whole players table)"
                ),
                "counts": {},
                "data": {},
            }
        if scope in ("user_team", "user_senior_team") or teamid is not None:
            return {
                "ok": False,
                "reason": "no_db",
                "detail": "bulk_edit team scope needs a loaded Career Mode save",
                "counts": {},
                "data": {},
            }
        return {
            "ok": False,
            "reason": "no_db",
            "detail": "bulk_edit needs a loaded Career Mode save",
            "counts": {},
            "data": {},
        }
    if name == "career.set":
        scope = op.get("scope") or "player"
        pid = op.get("playerid")
        if pid is None and scope in ("user_senior_team", "user_team"):
            return {
                "ok": False,
                "reason": "no_team",
                "detail": (
                    f"career.set scope={scope} needs GetUserTeamID "
                    "(Career Mode save loaded)"
                ),
                "counts": {},
                "data": {},
            }
        if pid is None:
            return {
                "ok": False,
                "reason": "no_playerid",
                "detail": (
                    "career.set requires playerid, or scope=user_senior_team "
                    "with a resolvable user team"
                ),
                "counts": {},
                "data": {},
            }
        return {
            "ok": False,
            "reason": "unsupported_op",
            "detail": "career.set: host career APIs not available offline",
            "counts": {},
            "data": {},
        }
    if name in ("injury.scan", "injury.cure"):
        return {
            "ok": False,
            "reason": "no_career",
            "detail": f"{name}: host injury APIs not available offline",
            "counts": {},
            "data": {},
        }
    known = {
        "diag.ping", "db.dump", "set_fields", "create_player", "add_to_team", "repair_partial_add",
        "transfer", "budget", "bulk_edit", "export_squad", "snapshot",
        "growth_sync", "career.set", "injury.scan", "injury.cure",
    }
    if name not in known:
        return {
            "ok": False,
            "reason": "unknown_op",
            "detail": f"op {name} is not in this core's registry",
            "counts": {},
            "data": {},
        }
    return {
        "ok": False,
        "reason": "unsupported_op",
        "detail": f"op {name}: offline core cannot execute host-dependent op",
        "counts": {},
        "data": {},
    }


def _reject(job_id: str, session_id: str, reason: str, detail: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "schema": SCHEMA,
        "job_id": job_id,
        "session_id": session_id,
        "core_version": CORE_VERSION,
        "state": "rejected",
        "ok": False,
        "outcome": "rejected",
        "finished_at": now,
        "error": {"phase": "validate", "message": reason, "detail": detail},
        "counts": {},
        "ops": [],
        "failures": [{"reason": reason, "detail": detail, "phase": "validate"}],
    }


def _finish(queue: Path, job_id: str, claimed: Path, result: Mapping[str, Any]) -> None:
    _atomic_write(queue / "results" / job_filename(job_id), dict(result))
    try:
        claimed.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if claimed.is_file():
            claimed.unlink()
    except OSError:
        pass


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
