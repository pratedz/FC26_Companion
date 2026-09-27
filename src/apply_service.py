"""Single apply/export job orchestration for GUI and CLI.

GUI must not own queue write + wait + clear-stale logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from . import actions
from . import le_apply


OnTick = Optional[Callable[[float, str], None]]


# Outcomes an ApplyResult can carry. `timeout` means the worker never
# collected/finished the job inside the wait window — it is NOT a success.
OUTCOME_APPLIED = "applied"
OUTCOME_QUEUED_LIVE = "queued_live"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_BLOCKED = "blocked"
OUTCOME_ERROR = "error"
#: Outcomes that must never be reported to a client as ok/success.
FAILED_OUTCOMES = (OUTCOME_ERROR, OUTCOME_BLOCKED, OUTCOME_TIMEOUT)


@dataclass
class ApplyResult:
    applied: bool
    queued: bool
    live: bool
    reason: str
    queue_file: Optional[str] = None
    last_result: str = ""
    # applied | queued_live | timeout | blocked | error
    outcome: str = "blocked"
    detail: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


_STATUS_TOKEN_RE = re.compile(r"([A-Za-z_][\w]*)=([^\s]+)")
# msg= is the last key on the line and may carry spaces (Lua error text).
_STATUS_MSG_RE = re.compile(r"\bmsg=(.+)$", re.MULTILINE)
# Values that mean "nothing went wrong" for err= / reason=
_BENIGN_VALUES = ("", "ok", "none", "nil", "success", "-")


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _is_bad_value(v: Any) -> bool:
    """True when err=/reason= carries a real diagnosis rather than 'ok'."""
    if v is None:
        return False
    return str(v).strip().lower() not in _BENIGN_VALUES


def parse_job_status(text: str) -> Dict[str, Any]:
    """Parse LE Lua `_job_status.txt` side-file into structured fields.

    Supports edit jobs (found=true/false written=N failed=N reason=…) and
    add_to_team (ok=true/false path=create|dummy id=N team=T msg=… …).

    ``present`` is False when no status file was written at all. Absence is
    *not* success: the job may have died before it could report anything, so
    callers must gate on ``present`` before reading the other fields.
    ``failed`` stays a bool (job-level verdict); the numeric ``failed=N``
    field-write counter is exposed separately as ``failed_writes``.
    """
    raw = (text or "").strip()
    out: Dict[str, Any] = {
        "raw": raw,
        "present": bool(raw),
        "ok": None,
        "found": None,
        "path": None,
        "player_id": None,
        "team_id": None,
        "job": None,
        "reason": None,
        "msg": None,
        "scanned": None,
        "written": None,
        "failed_writes": None,
        "failed": False,
        "summary": "",
    }
    if not raw:
        return out
    # token key=value (space-separated)
    for m in _STATUS_TOKEN_RE.finditer(raw):
        k, v = m.group(1).lower(), m.group(2)
        if k == "ok":
            out["ok"] = v.lower() in ("true", "1", "yes")
        elif k == "found":
            out["found"] = v.lower() in ("true", "1", "yes")
        elif k == "path":
            out["path"] = v
        elif k in ("id", "playerid"):
            try:
                out["player_id"] = int(v)
            except ValueError:
                out["player_id"] = v
        elif k in ("team", "teamid"):
            try:
                out["team_id"] = int(v)
            except ValueError:
                out["team_id"] = v
        elif k == "job":
            out["job"] = v
        elif k == "reason":
            out["reason"] = v
        elif k == "msg":
            out["msg"] = v
        elif k == "scanned":
            out["scanned"] = _as_int(v)
        elif k == "written":
            out["written"] = _as_int(v)
        elif k == "failed":
            out["failed_writes"] = _as_int(v)
        elif k == "err" and v and v.lower() not in ("none", "nil", ""):
            out["err"] = v
    # msg= is written last and may carry spaces (Lua error text) — the token
    # regex above would truncate it at the first space.
    mm = _STATUS_MSG_RE.search(raw)
    if mm:
        msg = mm.group(1).strip()[:400]
        if msg:
            out["msg"] = msg
    # Failure conditions
    failed = False
    if out["ok"] is False:
        failed = True
    if out["found"] is False:
        failed = True
    if _is_bad_value(out.get("err")) and out["ok"] is not True:
        # transfer failure etc. while ok may be false already
        failed = True
    if _is_bad_value(out.get("reason")) and out["ok"] is not True:
        # reason=no_players_table / lua_error / not_found …
        failed = True
    # Wrote nothing while field writes were attempted and failed
    fw = out["failed_writes"]
    wr = out["written"]
    if fw is not None and fw > 0 and (wr is None or wr <= 0):
        failed = True
    # add_to_team success requires ok=true when job present
    job = str(out.get("job") or "")
    if job == "add_to_team" and out["ok"] is not True and "ok=" in raw.lower():
        failed = True
    out["failed"] = failed
    # Human summary for dock/CLI
    bits = []
    if out.get("job"):
        bits.append(str(out["job"]))
    if out.get("path"):
        bits.append(f"path={out['path']}")
    if out.get("player_id") is not None:
        bits.append(f"id={out['player_id']}")
    if out.get("team_id") is not None:
        bits.append(f"team={out['team_id']}")
    if out["ok"] is True:
        bits.append("ok")
    elif out["ok"] is False:
        bits.append("FAILED")
    if out["found"] is False:
        bits.append("player NOT FOUND")
    if wr is not None:
        bits.append(f"written={wr}")
    if fw:
        bits.append(f"failed={fw}")
    if _is_bad_value(out.get("reason")):
        bits.append(f"reason={out['reason']}")
    if out.get("err"):
        bits.append(f"err={out['err']}")
    if out.get("msg"):
        bits.append(f"msg={str(out['msg'])[:200]}")
    out["summary"] = " · ".join(bits) if bits else raw[:120]
    return out


def format_result_with_job_status(result: ApplyResult) -> str:
    """One-line extension for CLI / dock: path + playerid when present."""
    parsed = (result.meta or {}).get("job_status_parsed") or {}
    if not parsed and (result.meta or {}).get("job_status"):
        parsed = parse_job_status(str(result.meta.get("job_status") or ""))
    if not parsed or not parsed.get("present", True):
        return ""
    return str(parsed.get("summary") or "")


# le_apply.wait_until_applied only puts these keys on its timeout return.
_TIMEOUT_MARKER_KEYS = ("job_mtime0", "run_now_exists")
# Reasons le_apply uses when the worker actively reported/produced a failure.
_BRIDGE_FAILURE_MARKERS = (
    "bridge reported fail",
    "bridge ran jobs but all failed",
    "cleared/skipped before le ran",
)


def _is_timeout_result(result: Dict[str, Any]) -> bool:
    """True when the wait gave up rather than observing a verdict."""
    if result.get("applied"):
        return False
    if any(k in result for k in _TIMEOUT_MARKER_KEYS):
        return True
    return str(result.get("reason") or "").strip().lower().startswith("timed out")


def _is_bridge_failure(result: Dict[str, Any]) -> bool:
    reason = str(result.get("reason") or "").lower()
    return any(m in reason for m in _BRIDGE_FAILURE_MARKERS)


def _outcome_from_wait(result: Dict[str, Any], *, was_live: bool) -> str:
    """Map a wait_until_applied() dict onto an outcome.

    A hang and a never-collected job used to both come back as ``queued_live``,
    which reads as "fine, it is on its way" — they are now ``timeout``.
    """
    if result.get("applied"):
        return OUTCOME_APPLIED
    if _is_bridge_failure(result):
        return OUTCOME_ERROR
    if _is_timeout_result(result):
        return OUTCOME_TIMEOUT
    if was_live or result.get("bridge_alive"):
        return OUTCOME_QUEUED_LIVE
    return OUTCOME_BLOCKED


def apply_lua(
    lua: str,
    *,
    stem: str,
    detail: str = "",
    clear_stale: bool = True,
    wait: bool = True,
    timeout_live: float = 55.0,
    timeout_offline: float = 3.0,
    on_tick: OnTick = None,
    to_generated: bool = True,
    poll_sec: Optional[float] = None,
) -> ApplyResult:
    """Write job to queue and optionally wait for LE worker.

    - clear_stale: archive other jobs so only this apply waits (once, before write)
    - wait: poll until applied / timeout
    - short timeout when worker not LIVE
    - to_generated: also write a copy under generated/ (skip for turbo boosts)
    - poll_sec: override companion_config poll interval
    """
    live0 = le_apply.bridge_alive(90)
    try:
        # One clear before write. write_lua(pending_mode="set") then owns pending —
        # no second clear_stale + rebuild storm (was 4–6 disk scans per boost).
        if clear_stale:
            le_apply.clear_stale_jobs(rebuild=False)
        out = actions.write_lua(
            lua,
            stem=stem,
            clipboard=False,
            to_generated=to_generated,
            pending_mode="set" if clear_stale else "rebuild",
        )
        qf = out.get("queue_file")
        if not qf:
            return ApplyResult(
                applied=False,
                queued=False,
                live=live0,
                reason="Could not write queue file",
                outcome=OUTCOME_ERROR,
                detail=detail,
            )
        # Reset job status side-file so we don't read a previous apply
        try:
            js = le_apply.queue_dir() / "_job_status.txt"
            if js.is_file():
                js.unlink()
        except OSError:
            pass

        if not wait:
            return ApplyResult(
                applied=False,
                queued=True,
                live=live0,
                reason="Queued (no wait)",
                queue_file=qf,
                outcome=OUTCOME_QUEUED_LIVE if live0 else OUTCOME_BLOCKED,
                detail=detail,
                meta=out,
            )

        timeout = timeout_live if live0 else timeout_offline
        poll = 0.06 if poll_sec is None else float(poll_sec)
        if poll_sec is None:
            try:
                from . import companion_config as _cc

                poll = float(_cc.load_config().get("poll_sec") or 0.06)
            except Exception:
                pass
        result = le_apply.wait_until_applied(
            qf, timeout_sec=timeout, poll_sec=max(0.03, poll), on_tick=on_tick
        )
        outcome = _outcome_from_wait(result, was_live=live0)
        job_st = le_apply.job_status_text()
        parsed = parse_job_status(job_st)
        meta: Dict[str, Any] = {
            **result,
            "job_status": job_st,
            "job_status_parsed": parsed,
        }
        # Side-file is authoritative when present: ok=false / found=false.
        # This must not be gated on result["applied"] — a job that timed out
        # after writing `found=false` had its diagnosis parsed and thrown away.
        if parsed.get("failed"):
            reason = parsed.get("summary") or (
                "Worker ran the script but the job reported failure."
            )
            if parsed.get("found") is False and "NOT FOUND" not in reason:
                reason = (
                    "Worker ran the script but player id was NOT FOUND in the DB. "
                    "Check Target ID / export squad."
                )
            if not result.get("applied"):
                wait_reason = str(result.get("reason") or "").strip()
                if wait_reason and wait_reason not in reason:
                    reason = f"{reason} · {wait_reason}"
            return ApplyResult(
                applied=False,
                queued=True,
                live=bool(result.get("bridge_alive")),
                reason=str(reason),
                queue_file=qf,
                last_result=result.get("last_result") or "",
                outcome=OUTCOME_ERROR,
                detail=detail,
                meta=meta,
            )
        # Enrich success reason with path/id for add_to_team etc.
        reason = str(result.get("reason") or "")
        if result.get("applied") and parsed.get("summary") and not parsed.get("failed"):
            summary = str(parsed["summary"])
            if summary and summary not in reason:
                reason = f"{reason} · {summary}".strip(" ·") if reason else summary
        return ApplyResult(
            applied=bool(result.get("applied")),
            queued=True,
            live=bool(result.get("bridge_alive")),
            reason=reason,
            queue_file=qf,
            last_result=result.get("last_result") or "",
            outcome=outcome,
            detail=detail,
            meta=meta,
        )
    except Exception as e:  # noqa: BLE001
        return ApplyResult(
            applied=False,
            queued=False,
            live=le_apply.bridge_alive(90),
            reason=str(e),
            outcome=OUTCOME_ERROR,
            detail=detail,
        )


def install_worker() -> Dict[str, Any]:
    """Install queue worker only (no LE core patch)."""
    return le_apply.install_bridge()


def copy_bridge_to_clipboard() -> bool:
    """Copy bridge Lua to clipboard without scrubbing SAFE auto-arm."""
    from . import actions as _actions

    # Ensure worker file exists, but do not re-run install_bridge (that scrubs auto-arm).
    try:
        dest = le_apply.le_scripts_bridge_path()
        if not dest.is_file():
            install_worker()
        else:
            # Refresh queue-baked bridge text only
            text = le_apply._bridge_source_text_with_abs_queue()
            dest.write_text(text, encoding="utf-8", newline="\n")
    except Exception:
        try:
            install_worker()
        except Exception:
            pass
    return _actions.copy_to_clipboard(le_apply.bridge_script_text_for_clipboard())


def auto_clipboard_bridge_if_off(*, max_age_sec: float = 90.0) -> Dict[str, Any]:
    """If worker is not LIVE, put full bridge on clipboard (no user click).

    Safe to call from startup / status ticks. Returns {live, copied, reason}.
    """
    live = False
    try:
        live = bool(le_apply.bridge_alive(max_age_sec))
    except Exception:
        live = False
    if live:
        return {"live": True, "copied": False, "reason": "already_live"}
    ok = False
    try:
        ok = bool(copy_bridge_to_clipboard())
    except Exception as e:  # noqa: BLE001
        return {"live": False, "copied": False, "reason": str(e)}
    return {
        "live": False,
        "copied": ok,
        "reason": "bridge_copied" if ok else "clipboard_failed",
    }


def layout_warnings() -> list[str]:
    """Startup checks for missing data / wrong LE root."""
    from . import paths

    warns: list[str] = []
    ar = paths.app_root()
    if not (ar / "card_db").is_dir():
        warns.append(f"card_db/ missing next to app ({ar}) — card search will be empty")
    if not (ar / "profiles.json").is_file() and not (paths.resource_root() / "profiles.json").is_file():
        warns.append("profiles.json missing — Boost profiles unavailable")
    le = paths.le_root()
    if not (le / "lua" / "scripts").is_dir():
        warns.append(
            f"LE lua/scripts not found under {le} — Enable Sync cannot install worker"
        )
    if not (le / "FCLiveEditor.DLL").is_file() and not (le / "Launcher.exe").is_file():
        warns.append(
            f"Live Editor install not detected at {le} — place companion inside LE folder"
        )
    return warns
