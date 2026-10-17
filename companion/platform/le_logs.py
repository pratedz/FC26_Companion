r"""Live Editor's own log files: find them and tail them. Read-only.

LE writes one log per day into ``<LE install dir>\Logs``:

    live_editor_<dd-mm-yyyy>.log           <- FC26.exe + LE DLL + every Lua print
    live_editor_launcher_<dd-mm-yyyy>.log  <- Launcher.exe

Each line is ``HH:MM:SS.ffffff<TAB>LEVEL<TAB>message`` with CRLF endings and
UTF-8 text. Worker errors such as LE refusing a ``TransferPlayer`` because the
squad is full appear **only** here, which is why the Companion tails it.

Facts measured on this machine that shaped the tailer:

* The file is named by the date the LE session *started* and the session keeps
  writing to it past midnight (``live_editor_08-10-2026.log`` runs 17:38 →
  13:40 the next day). A later session on a new day creates a new file. So the
  "current" file is simply the newest-dated one, not necessarily today's name.
* Several sessions on one day append to the same file.
* LE keeps the file open while FC 26 runs. It is opened here with
  ``FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`` so the reader can
  never block LE from writing, rotating or deleting it.

Nothing in this module writes to the logs directory.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import BinaryIO

from . import registry

ENV_LOGS_DIR = "FC26_LE_LOGS_DIR"
#: Pref key (``prefs`` table) that overrides every other source.
PREF_LOGS_DIR = "le_logs_dir"

KIND_EDITOR = "editor"
KIND_LAUNCHER = "launcher"

#: Upper bound on bytes read from disk by one :meth:`LogTail.poll`.
MAX_READ_BYTES = 256 * 1024
#: How far back from the end a freshly attached "end" tail starts.
BACKLOG_BYTES = 256 * 1024
#: Bytes of the file head remembered to notice a replaced file of equal size.
_HEAD_BYTES = 64

_NAME_RE = re.compile(r"^live_editor_(launcher_)?(\d{2})-(\d{2})-(\d{4})\.log$", re.IGNORECASE)


# --- locating the directory ------------------------------------------------


@dataclass(frozen=True, slots=True)
class LogsDir:
    """The resolved ``Logs`` directory plus where the answer came from."""

    path: Path | None
    source: str = "unset"
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.path is not None

    def to_dict(self) -> dict[str, object]:
        return {"path": str(self.path or ""), "source": self.source, "detail": self.detail}


def _as_dir(raw: object) -> Path | None:
    text = str(raw or "").strip().strip('"')
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        return path if path.is_dir() else None
    except OSError:
        return None


def _has_logs(path: Path) -> bool:
    try:
        return any(_NAME_RE.match(p.name) for p in path.iterdir())
    except OSError:
        return False


def _logs_of(root: Path | None) -> Path | None:
    """``root`` itself when it already is a Logs dir, else ``root/Logs``."""
    if root is None:
        return None
    if root.name.lower() == "logs" and root.is_dir():
        return root
    candidate = root / "Logs"
    return candidate if candidate.is_dir() else None


def resolve_logs_dir(*, override: object = None, hint: Path | None = None) -> LogsDir:
    r"""Find LE's ``Logs`` directory.

    Order: the ``le_logs_dir`` pref (``override``), ``FC26_LE_LOGS_DIR``, the
    install root from the registry (``Install Dir`` or the older ``Dir``) or a
    walk up from ``hint`` (the Companion normally lives inside the LE install),
    then ``<LE data dir>\Logs``. An explicit override that does not exist is
    reported as such rather than silently replaced by a guess.
    """
    if str(override or "").strip():
        path = _as_dir(override)
        if path is None:
            return LogsDir(None, "pref", f"{PREF_LOGS_DIR}={override} is not a directory")
        return LogsDir(_logs_of(path) or path, "pref", f"{PREF_LOGS_DIR} preference")

    env = os.environ.get(ENV_LOGS_DIR, "")
    if env.strip():
        path = _as_dir(env)
        if path is None:
            return LogsDir(None, "env", f"{ENV_LOGS_DIR}={env} is not a directory")
        return LogsDir(_logs_of(path) or path, "env", f"{ENV_LOGS_DIR} override")

    candidates: list[tuple[Path, str, str]] = []
    try:
        install = registry.resolve_install_root(hint)
    except Exception:  # noqa: BLE001 - diagnostics must not raise
        install = None
    if install is not None and install.path is not None:
        logs = _logs_of(install.path)
        if logs is not None:
            candidates.append((logs, install.source.value, f"install dir {install.path}"))
    try:
        data = registry.le_data_dir()
    except Exception:  # noqa: BLE001
        data = None
    logs = _logs_of(data)
    if logs is not None:
        candidates.append((logs, "data-dir", f"LE data dir {data}"))

    for path, source, detail in candidates:
        if _has_logs(path):
            return LogsDir(path, source, detail)
    if candidates:
        path, source, detail = candidates[0]
        return LogsDir(path, source, detail + " (no live_editor logs yet)")
    return LogsDir(None, "unset", "Live Editor install not found (registry Install Dir/Dir unset)")


# --- choosing the file -----------------------------------------------------


def parse_log_name(name: str) -> tuple[str, date] | None:
    """``("editor"|"launcher", date)`` for an LE log file name, else ``None``."""
    match = _NAME_RE.match(name or "")
    if match is None:
        return None
    day, month, year = int(match.group(2)), int(match.group(3)), int(match.group(4))
    try:
        stamp = date(year, month, day)
    except ValueError:
        return None
    return (KIND_LAUNCHER if match.group(1) else KIND_EDITOR), stamp


def list_logs(logs_dir: Path | None, kind: str = KIND_EDITOR) -> list[tuple[date, Path]]:
    """Every LE log of ``kind`` in ``logs_dir``, oldest first."""
    if logs_dir is None:
        return []
    out: list[tuple[date, Path]] = []
    try:
        entries = list(logs_dir.iterdir())
    except OSError:
        return []
    for path in entries:
        parsed = parse_log_name(path.name)
        if parsed is not None and parsed[0] == kind:
            out.append((parsed[1], path))
    out.sort(key=lambda item: (item[0], item[1].name))
    return out


def current_log(logs_dir: Path | None, *, today: date, kind: str = KIND_EDITOR) -> Path | None:
    """The file LE is writing now: the newest-dated log not after tomorrow.

    Tomorrow is tolerated for clock skew; anything later is a mis-named file.
    A session started yesterday keeps writing to yesterday's name, so the
    newest file — not ``today``'s name — is the right answer.
    """
    limit = today + timedelta(days=1)
    dated = [item for item in list_logs(logs_dir, kind) if item[0] <= limit]
    return dated[-1][1] if dated else None


def log_date(path: Path | None) -> date | None:
    parsed = parse_log_name(path.name) if path is not None else None
    return parsed[1] if parsed else None


# --- opening without blocking LE -------------------------------------------


def open_shared(path: Path) -> BinaryIO:
    """Open ``path`` read-only, sharing read/write/delete with LE.

    On Windows this uses ``CreateFileW`` directly so the share mode is explicit
    (the CRT's ``open`` omits ``FILE_SHARE_DELETE``). Elsewhere, or if that
    fails for any reason other than the file being missing, plain ``open``.
    """
    if sys.platform == "win32":
        try:
            return _open_shared_win32(path)
        except FileNotFoundError:
            raise
        except OSError:
            pass
    return open(path, "rb")  # noqa: SIM115 - caller closes


def _open_shared_win32(path: Path) -> BinaryIO:  # pragma: no cover - Windows only
    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    SHARE_ALL = 0x1 | 0x2 | 0x4  # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    handle = create(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle is None or handle == INVALID:
        err = ctypes.get_last_error()
        if err in (2, 3):  # FILE_NOT_FOUND, PATH_NOT_FOUND
            raise FileNotFoundError(err, "file not found", str(path))
        raise OSError(err, "CreateFileW failed", str(path))
    try:
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")


# --- incremental tail ------------------------------------------------------


@dataclass(slots=True)
class TailChunk:
    """Complete lines read from one file in one poll."""

    path: Path
    file_date: date | None
    lines: list[str] = field(default_factory=list)
    #: ``""`` normally; ``attached`` / ``switched`` / ``truncated`` / ``rotated``
    #: when the reader (re)started at the beginning of a file.
    reset: str = ""
    at_eof: bool = True
    #: Bytes taken from disk for this chunk (counts against the poll budget).
    nbytes: int = 0


class LogTail:
    """Follow the current LE log by byte offset, a capped amount per poll.

    * Only complete lines are returned; a half-written last line waits for the
      next poll (or is flushed at a midnight switch / once it exceeds the cap).
    * A file that shrinks is *truncated*: the reader restarts at 0. A file whose
      first bytes change (or whose file id changes) was *rotated*: same.
    * When a newer-dated file appears the old one is drained to EOF first, then
      the reader switches and reads the new file from its start.
    * ``start="end"`` attaches near the end (``backlog_bytes``) so a big file
      does not flood the first poll; ``start="start"`` reads everything.
    """

    def __init__(
        self,
        *,
        kind: str = KIND_EDITOR,
        start: str = "end",
        max_bytes: int = MAX_READ_BYTES,
        backlog_bytes: int = BACKLOG_BYTES,
    ) -> None:
        self.kind = kind
        self.start = start
        self.max_bytes = max(1024, int(max_bytes))
        self.backlog_bytes = max(0, int(backlog_bytes))
        self.path: Path | None = None
        self.offset = 0
        self._remainder = b""
        self._head = b""
        self._ino = 0
        self._skip_partial = False
        self._pending_reset = ""

    # -- public ------------------------------------------------------------

    @property
    def file_date(self) -> date | None:
        return log_date(self.path)

    def poll(self, logs_dir: Path | None, *, today: date) -> list[TailChunk]:
        """Read at most ``max_bytes`` of new data. Never raises for I/O."""
        target = current_log(logs_dir, today=today, kind=self.kind)
        chunks: list[TailChunk] = []
        budget = self.max_bytes
        if target is None:
            return chunks
        if self.path is None:
            self._attach(target, initial=True)
        elif target != self.path:
            old = self._read(budget, final=True)
            if old is not None:
                budget -= old.nbytes
                if old.lines or old.reset:
                    chunks.append(old)
                if not old.at_eof:
                    return chunks  # finish the old file before switching
            self._attach(target, initial=False)
        if budget <= 0:
            return chunks
        chunk = self._read(budget, final=False)
        if chunk is not None:
            if chunk.lines or chunk.reset:
                chunks.append(chunk)
        return chunks

    def at_eof(self) -> bool:
        if self.path is None:
            return True
        try:
            return os.stat(self.path).st_size <= self.offset
        except OSError:
            return True

    # -- internals ---------------------------------------------------------

    def _attach(self, path: Path, *, initial: bool) -> None:
        self.path = path
        self._remainder = b""
        self._skip_partial = False
        self._head = b""
        self._ino = 0
        self.offset = 0
        self._pending_reset = "attached" if initial else "switched"
        try:
            st = os.stat(path)
        except OSError:
            return
        self._ino = int(getattr(st, "st_ino", 0) or 0)
        if initial and self.start == "end" and st.st_size > self.backlog_bytes:
            self.offset = st.st_size - self.backlog_bytes
            self._skip_partial = not self._byte_before_is_newline(path, self.offset)
        self._head = self._read_head(path)

    @staticmethod
    def _byte_before_is_newline(path: Path, offset: int) -> bool:
        try:
            with open_shared(path) as fh:
                fh.seek(max(0, offset - 1))
                return fh.read(1) == b"\n"
        except OSError:
            return False

    def _read_head(self, path: Path) -> bytes:
        try:
            with open_shared(path) as fh:
                return fh.read(_HEAD_BYTES)
        except OSError:
            return b""

    def _restart(self, reason: str) -> None:
        self.offset = 0
        self._remainder = b""
        self._skip_partial = False
        self._pending_reset = reason

    def _read(self, budget: int, *, final: bool) -> TailChunk | None:
        path = self.path
        if path is None:
            return None
        try:
            st = os.stat(path)
        except OSError:
            # Gone (deleted/rotated away): nothing more to drain from it.
            return TailChunk(path, log_date(path), [], self._take_reset(), True)
        size = int(st.st_size)
        ino = int(getattr(st, "st_ino", 0) or 0)
        if size < self.offset:
            self._restart("truncated")
        elif self._ino and ino and ino != self._ino:
            self._restart("rotated")
        elif self._head:
            current = self._read_head(path)
            if current[: len(self._head)] != self._head[: len(current)]:
                self._restart("rotated")
        self._ino = ino or self._ino
        if len(self._head) < _HEAD_BYTES and size > len(self._head):
            self._head = self._read_head(path)

        data = b""
        want = min(max(0, budget), max(0, size - self.offset))
        if want:
            try:
                with open_shared(path) as fh:
                    fh.seek(self.offset)
                    data = fh.read(want)
            except OSError:
                data = b""
        self.offset += len(data)
        at_eof = self.offset >= size
        buf = self._remainder + data
        self._remainder = b""
        if self._skip_partial:
            cut = buf.find(b"\n")
            if cut < 0:
                self._remainder = b""
                buf = b""
                # still inside the first partial line; keep skipping
            else:
                buf = buf[cut + 1:]
                self._skip_partial = False
        lines: list[str] = []
        if buf:
            cut = buf.rfind(b"\n")
            if cut >= 0:
                complete, self._remainder = buf[: cut + 1], buf[cut + 1:]
            else:
                complete, self._remainder = b"", buf
            if self._remainder and (len(self._remainder) >= self.max_bytes or (final and at_eof)):
                complete, self._remainder = complete + self._remainder, b""
            if complete:
                text = complete.decode("utf-8", errors="replace")
                lines = [line.rstrip("\r") for line in text.split("\n")]
                if lines and lines[-1] == "":
                    lines.pop()
        return TailChunk(path, log_date(path), lines, self._take_reset(), at_eof, len(data))

    def _take_reset(self) -> str:
        reason, self._pending_reset = self._pending_reset, ""
        return reason


__all__ = [
    "BACKLOG_BYTES",
    "ENV_LOGS_DIR",
    "KIND_EDITOR",
    "KIND_LAUNCHER",
    "LogTail",
    "LogsDir",
    "MAX_READ_BYTES",
    "PREF_LOGS_DIR",
    "TailChunk",
    "current_log",
    "list_logs",
    "log_date",
    "open_shared",
    "parse_log_name",
    "resolve_logs_dir",
]
