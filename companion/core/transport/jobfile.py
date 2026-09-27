"""Atomic job/result file I/O. The inside never sees a partial file.

Write: ``<name>.json.tmp`` in the same directory, then ``os.replace`` — atomic
on NTFS. Read: tolerate a file vanishing mid-read (the Lua side renames files
during a drain) by returning None rather than raising.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping

from ...domain.ids import is_ulid


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    """None on absent / vanished / partial / non-dict — never raises."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_worker_json(path: Path) -> dict[str, Any] | None:
    """Read strict JSON, with compatibility for LE's unescaped Windows paths.

    Some loaded workers echo snapshot paths with single backslashes. Repair
    only absolute drive paths in known path properties, then decode strictly.
    Never use this reader for jobs, grants, config or arbitrary malformed JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        pattern = r'("(?:path|snapshot_to)"\s*:\s*")([A-Za-z]:\\[^"\r\n]*)(")'
        def repair(match: re.Match[str]) -> str:
            value = re.sub(r'(?<!\\)\\(?!\\)', lambda _: '\\\\', match[2])
            return match[1] + value + match[3]
        fixed = re.sub(pattern, repair, text)
        if fixed == text:
            return None
        try:
            data = json.loads(fixed)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def job_filename(job_id: str) -> str:
    if not is_ulid(job_id):
        raise ValueError(f"job_id must be a ULID: {job_id!r}")
    return f"{job_id}.json"


def job_id_from_filename(name: str) -> str | None:
    """``<ULID>.json`` -> ULID; anything else -> None. SI-7's Python half."""
    if not name.endswith(".json"):
        return None
    stem = name[: -len(".json")]
    return stem if is_ulid(stem) else None


def list_job_ids(directory: Path) -> list[str]:
    """ULIDs of well-formed job files, sorted — creation order for free."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    out = [jid for n in names if (jid := job_id_from_filename(n))]
    out.sort()
    return out


def _acquire_index_lock(directory: Path, *, timeout_s: float = 2.0) -> Path | None:
    """Acquire a short-lived inter-process lock for an index rewrite.

    Queue files are written before this lock is taken.  Every writer then
    rebuilds the index while holding the lock, so a concurrent submit/cancel
    cannot publish an older directory snapshot after a newer one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".index.lock"
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30.0:
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.01)
            continue
        except OSError:
            return None
        try:
            os.write(fd, str(os.getpid()).encode("ascii", "replace"))
        finally:
            os.close(fd)
        return lock
    return None


def rewrite_job_index(directory: Path) -> None:
    """Write ``index.json`` so the Lua core can list jobs without dir enum.

    LE's Lua sandbox has no directory listing. The core reads
    ``queue/jobs/index.json`` (a flat JSON array of filenames). Python is the
    producer of jobs/, so every submit/cancel must refresh the sidecar.
    Neither ``index.json`` nor ``index.txt`` can be mistaken for a job: their
    stems are not 26-char ULIDs.
    """
    lock = _acquire_index_lock(Path(directory))
    if lock is None:
        return
    try:
        ids = list_job_ids(directory)
        names = [f"{jid}.json" for jid in ids]
        path = Path(directory) / "index.json"
        try:
            tmp = path.with_name(path.name + ".tmp")
            text = json.dumps(names, ensure_ascii=False, separators=(",", ":"))
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass
