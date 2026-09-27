"""Shared control protocol (v2) for external program + inject-side.

Mirrors docs/PROTOCOL.md and native/le_companion_inject queue contract.
Does not touch FakeEAAC or inject into FC26.

Dry-run drain (Python process_queue_protocol / native ProcessQueue) requires
``_protocol_dry_run`` in the queue dir so production pending applies cannot be
falsely marked OK and moved to done/ without LE Lua.
"""

from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Side-file names (must match Lua bridge + native DLL)
PENDING_NAME = "_pending.txt"
RUN_NOW_NAME = "_run_now.lua"
WAKE_NAME = "_wake.txt"
ALIVE_NAME = "_bridge_alive.txt"
ARMED_NAME = "_bridge_armed.txt"
LAST_RESULT_NAME = "_last_result.txt"
JOB_STATUS_NAME = "_job_status.txt"
DONE_SUBDIR = "done"
# Required for native/Python dry protocol drain (not for in-LE Lua bridge)
DRY_RUN_MARKER = "_protocol_dry_run"

PROTOCOL_VERSION = 2


def _safe_stem(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", name.strip(), flags=re.UNICODE)
    return s.strip("_") or "job"


def safe_job_name(name: str) -> Optional[str]:
    """Single-component *.lua job name under the queue root, or None if unsafe.

    Rejects absolute paths, drive letters, ``..``, and any directory separators
    so wake/pending entries cannot traverse outside the queue directory.
    """
    raw = (name or "").strip()
    if not raw or raw.startswith("_"):
        return None
    if "\\" in raw or "/" in raw or ".." in raw:
        return None
    if re.match(r"^[A-Za-z]:", raw):
        return None
    if not raw.lower().endswith(".lua"):
        return None
    if Path(raw).name != raw:
        return None
    return raw


def confined_job_path(queue_dir: Path, name: str) -> Optional[Path]:
    """Resolve name under queue_dir only; None if outside or unsafe."""
    safe = safe_job_name(name)
    if not safe:
        return None
    q = Path(queue_dir).resolve()
    full = (q / safe).resolve()
    try:
        full.relative_to(q)
    except ValueError:
        return None
    return full


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_queue_layout(queue_dir: Path) -> Path:
    q = Path(queue_dir)
    q.mkdir(parents=True, exist_ok=True)
    (q / DONE_SUBDIR).mkdir(parents=True, exist_ok=True)
    pending = q / PENDING_NAME
    if not pending.is_file():
        pending.write_text("", encoding="utf-8")
    return q


def dry_run_allowed(queue_dir: Path) -> bool:
    return (Path(queue_dir) / DRY_RUN_MARKER).is_file()


def enable_dry_run(queue_dir: Path) -> Path:
    """Mark queue as protocol dry-run only (safe for self-check / unit tests)."""
    q = ensure_queue_layout(queue_dir)
    marker = q / DRY_RUN_MARKER
    marker.write_text("dry_run=1\n", encoding="utf-8", newline="\n")
    return marker


def make_isolated_selfcheck_queue() -> Path:
    """Temp queue dir with dry-run marker — never production paths.queue_dir()."""
    q = Path(tempfile.mkdtemp(prefix="le_companion_selfcheck_q_"))
    enable_dry_run(q)
    return q


def list_job_files(queue_dir: Path) -> List[str]:
    q = Path(queue_dir)
    if not q.is_dir():
        return []
    names = []
    for p in sorted(q.glob("*.lua")):
        if p.name.startswith("_"):
            continue
        names.append(p.name)
    return names


def rebuild_pending(queue_dir: Path) -> List[str]:
    """Rewrite _pending.txt from on-disk non-underscore *.lua jobs."""
    q = ensure_queue_layout(queue_dir)
    names = list_job_files(q)
    (q / PENDING_NAME).write_text(
        ("\n".join(names) + ("\n" if names else "")),
        encoding="utf-8",
        newline="\n",
    )
    return names


def read_pending(queue_dir: Path) -> List[str]:
    p = Path(queue_dir) / PENDING_NAME
    if not p.is_file():
        return []
    seen = set()
    out: List[str] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        safe = safe_job_name(line)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        out.append(safe)
    return out


def write_job(
    queue_dir: Path,
    lua_source: str,
    *,
    stem: str = "action",
    as_run_now: bool = True,
) -> Dict[str, Any]:
    """External program: enqueue a Lua job (protocol write path)."""
    q = ensure_queue_layout(queue_dir)
    text = lua_source.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    qname = f"{_safe_stem(stem)}_{_timestamp()}.lua"
    qfile = q / qname
    qfile.write_text(text, encoding="utf-8", newline="\n")
    if as_run_now:
        (q / RUN_NOW_NAME).write_text(text, encoding="utf-8", newline="\n")
    names = rebuild_pending(q)
    if qname not in names:
        with (q / PENDING_NAME).open("a", encoding="utf-8", newline="\n") as f:
            f.write(qname + "\n")
        names = rebuild_pending(q)
    (q / WAKE_NAME).write_text(f"wake {qname}\n", encoding="utf-8", newline="\n")
    return {
        "queue_file": str(qfile),
        "job_name": qname,
        "pending_names": names,
        "queue_dir": str(q.resolve()),
    }


def read_last_result(queue_dir: Path) -> str:
    p = Path(queue_dir) / LAST_RESULT_NAME
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def write_result(queue_dir: Path, text: str) -> None:
    q = ensure_queue_layout(queue_dir)
    (q / LAST_RESULT_NAME).write_text(
        str(text).rstrip() + "\n", encoding="utf-8", newline="\n"
    )


def heartbeat(queue_dir: Path, note: str = "") -> None:
    q = ensure_queue_layout(queue_dir)
    ts = int(time.time())
    msg = f"alive {ts} {note}\n".rstrip() + "\n"
    (q / ALIVE_NAME).write_text(msg, encoding="utf-8", newline="\n")
    (q / ARMED_NAME).write_text(f"armed {ts}\n", encoding="utf-8", newline="\n")


def bridge_alive(queue_dir: Path, max_age_sec: float = 90.0) -> bool:
    p = Path(queue_dir) / ALIVE_NAME
    if not p.is_file():
        return False
    try:
        age = time.time() - p.stat().st_mtime
        return 0.0 <= age <= max_age_sec
    except OSError:
        return False


def parse_result_line(text: str) -> Dict[str, Any]:
    """Parse inject-side _last_result.txt into structured fields."""
    t = (text or "").strip()
    out: Dict[str, Any] = {
        "raw": t,
        "ok": False,
        "fail": False,
        "idle": False,
        "processed": None,
        "ok_count": None,
        "last": None,
    }
    if not t:
        return out
    if t.startswith("OK"):
        out["ok"] = True
    if t.startswith("FAIL"):
        out["fail"] = True
    if "queue_empty" in t or "idle" in t.lower():
        out["idle"] = True
    m = re.search(r"processed=(\d+)", t)
    if m:
        out["processed"] = int(m.group(1))
    m = re.search(r"ok=(\d+)", t)
    if m:
        out["ok_count"] = int(m.group(1))
    m = re.search(r"last=(\S+)", t)
    if m:
        out["last"] = m.group(1)
    return out


@dataclass
class DrainStats:
    processed: int = 0
    ok: int = 0
    last_name: str = ""
    note: str = ""
    result_line: str = ""
    done_names: List[str] = field(default_factory=list)


def _finish_job(queue_dir: Path, full: Path, name: str) -> Path:
    done = Path(queue_dir) / DONE_SUBDIR
    done.mkdir(parents=True, exist_ok=True)
    safe = safe_job_name(name) or Path(name).name
    # Never write outside done/ — basename only
    safe = Path(safe).name
    if not safe or safe in (".", ".."):
        safe = f"job_{int(time.time())}.lua"
    dest = done / safe
    if dest.is_file():
        dest = done / f"{int(time.time())}_{safe}"
    # Only touch full if it resolves under the queue
    q = Path(queue_dir).resolve()
    try:
        full_r = full.resolve()
        full_r.relative_to(q)
    except (ValueError, OSError):
        return dest
    try:
        data = full_r.read_text(encoding="utf-8", errors="replace")
        dest.write_text(data, encoding="utf-8", newline="\n")
    except OSError:
        pass
    try:
        full_r.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if full_r.is_file():
            full_r.unlink()
    except OSError:
        pass
    return dest


def process_queue_protocol(
    queue_dir: Path,
    *,
    force: bool = True,
    execute_job=None,
    require_dry_run: bool = True,
) -> DrainStats:
    """Inject-side protocol consumer (Python reference implementation).

    execute_job: optional callable(body: str, name: str) -> bool
    Default: treat readable non-empty job as success (dry protocol; no LE APIs).

    require_dry_run: when True (default), refuse to move jobs unless
    ``_protocol_dry_run`` exists — protects production queue from false OK.
    """
    del force  # Python path always drains when called
    q = ensure_queue_layout(queue_dir)
    stats = DrainStats()

    if require_dry_run and not dry_run_allowed(q):
        stats.note = "dry_run_required"
        line = "FAIL dry_run_required"
        try:
            write_result(q, line)
            heartbeat(q, "dry_run_blocked")
        except OSError:
            pass
        stats.result_line = line
        return stats

    def _default_exec(body: str, name: str) -> bool:
        del name
        return body is not None and len(body) > 0

    exec_fn = execute_job or _default_exec

    try:
        heartbeat(q, "tick")
    except OSError:
        stats.note = "cannot_write_queue"
        write_result(q, f"FAIL cannot_write_queue path={q}")
        stats.result_line = read_last_result(q)
        return stats

    run_now_body: Optional[str] = None
    run_now_ran = False
    fb = q / RUN_NOW_NAME
    if fb.is_file():
        try:
            run_now_body = fb.read_text(encoding="utf-8", errors="replace")
        except OSError:
            run_now_body = ""
        ok = bool(exec_fn(run_now_body or "", RUN_NOW_NAME))
        _finish_job(q, fb, RUN_NOW_NAME)
        stats.processed += 1
        stats.last_name = RUN_NOW_NAME
        run_now_ran = True
        if ok:
            stats.ok += 1
        stats.done_names.append(RUN_NOW_NAME)

    pending = read_pending(q)
    wake_path = q / WAKE_NAME
    if wake_path.is_file():
        try:
            wake = wake_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            wake = ""
        for line in wake.splitlines():
            m = re.search(r"wake\s+(\S+)", line) or re.match(r"^\s*(\S+\.lua)\s*$", line)
            if not m:
                continue
            wname = safe_job_name(m.group(1))
            if not wname:
                continue
            if wname not in pending:
                pending.append(wname)

    stuck: List[str] = []
    for name in pending:
        full = confined_job_path(q, name)
        if full is None or not full.is_file():
            continue
        try:
            body = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            body = ""
        if run_now_ran and run_now_body is not None and body == run_now_body:
            _finish_job(q, full, name)
            stats.done_names.append(name)
            if full.is_file():
                stuck.append(name)
            continue
        ok = bool(exec_fn(body, name))
        _finish_job(q, full, name)
        stats.processed += 1
        stats.last_name = name
        if ok:
            stats.ok += 1
        stats.done_names.append(name)
        if full.is_file():
            stuck.append(name)

    if stuck:
        (q / PENDING_NAME).write_text(
            "\n".join(stuck) + "\n", encoding="utf-8", newline="\n"
        )
    else:
        (q / PENDING_NAME).write_text("", encoding="utf-8", newline="\n")
    try:
        wake_path.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if wake_path.is_file():
            wake_path.unlink()
    except OSError:
        pass

    heartbeat(q, f"n={stats.processed} ok={stats.ok}")
    if stats.processed > 0:
        if stats.ok < stats.processed:
            line = f"FAIL processed={stats.processed} ok={stats.ok} last={stats.last_name}"
        else:
            line = f"OK processed={stats.processed} ok={stats.ok} last={stats.last_name}"
    elif stuck:
        line = "FAIL stuck jobs still on disk"
    else:
        line = "OK idle queue_empty"
    write_result(q, line)
    (q / JOB_STATUS_NAME).write_text(
        f"{stats.last_name}\t{line}\n", encoding="utf-8", newline="\n"
    )
    stats.result_line = line
    stats.note = ""
    return stats


def observe_job_done(queue_dir: Path, job_name: str) -> bool:
    """True if job archived under done/ (protocol-visible completion)."""
    done = Path(queue_dir) / DONE_SUBDIR
    if not done.is_dir():
        return False
    if (done / job_name).is_file():
        return True
    # timestamp-prefixed archive
    for p in done.glob(f"*_{job_name}"):
        if p.is_file():
            return True
    return False


def protocol_self_check(queue_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Headless round-trip on an isolated dry-run queue (never production).

    If queue_dir is None, creates a temp dir with ``_protocol_dry_run``.
    If a path is passed, dry-run is enabled there; production queue_dir is refused.
    """
    from . import paths as _paths

    if queue_dir is None:
        q = make_isolated_selfcheck_queue()
        isolated = True
    else:
        q = Path(queue_dir)
        try:
            if q.resolve() == _paths.queue_dir().resolve():
                return {
                    "ok": False,
                    "error": "refused_production_queue",
                    "detail": "protocol_self_check must not use paths.queue_dir(); use isolated temp",
                    "protocol_version": PROTOCOL_VERSION,
                }
        except OSError:
            pass
        enable_dry_run(q)
        isolated = False

    q = ensure_queue_layout(q)
    body = (
        "-- LE_PROTOCOL_SELF_CHECK\n"
        f"-- issued_at={_timestamp()}\n"
        "return true\n"
    )
    issued = write_job(q, body, stem="protocol_self_check", as_run_now=True)
    stats = process_queue_protocol(q, force=True, require_dry_run=True)
    parsed = parse_result_line(stats.result_line)
    done_ok = observe_job_done(q, issued["job_name"]) or RUN_NOW_NAME in stats.done_names
    ok = bool(parsed.get("ok")) and int(parsed.get("processed") or 0) >= 1 and done_ok
    return {
        "ok": ok,
        "isolated": isolated,
        "queue_dir": str(q.resolve()),
        "issued": issued,
        "stats": {
            "processed": stats.processed,
            "ok": stats.ok,
            "last_name": stats.last_name,
            "result_line": stats.result_line,
            "done_names": list(stats.done_names),
        },
        "parsed": parsed,
        "job_done": done_ok,
        "alive": bridge_alive(q, max_age_sec=30.0),
        "protocol_version": PROTOCOL_VERSION,
        "dry_run": True,
    }
