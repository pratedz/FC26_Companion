"""Live Editor log lines as typed entries, a capped in-memory buffer and an error tally.

Pure: no file or Tk access. ``platform/le_logs.py`` supplies the raw lines.

Line format written by LE (measured on v26.3.6)::

    01:56:51.705423<TAB>ERROR<TAB>Tottenham Hotspur Squad is full. Player in squad: 42/52, ...
    01:56:51.706073<TAB>ERROR<TAB>[LUA API] TransferPlayer Failed Target Team Squad is Full @ LE::LUA::...
    01:56:51.937413<TAB>INFO<TAB>[LUA API] [LEC] job 01M4YMC7KVVDBAV4SRWWZQP6RY -> partial ok=false

Two observations drive the model:

* LE's own errors for a worker call carry **no job id**; the worker's
  ``[LEC] job <id> -> ...`` line follows a few hundred ms later. An error
  burst is therefore grouped into an :class:`Incident`, and the next worker
  job line within :data:`JOB_LINK_SECONDS` names the job it belongs to
  (``job_inferred=True``).
* The worker prints ``drain skipped during ...`` thousands of times. Identical
  consecutive lines collapse into one entry with a ``repeat`` count.

Log text is data. Nothing here interprets a message as an instruction.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

# --- vocabulary --------------------------------------------------------------

SOURCE_WORKER = "worker"      # the Companion's Lua core ([LE_Companion] / [LEC])
SOURCE_LUA = "lua"            # LE's Lua runtime ([LUA API], LE::LUA::...)
SOURCE_LE = "le"              # LE itself (DLL, DirectX, file system)
SOURCE_LAUNCHER = "launcher"  # Launcher.exe's own log file

FILTER_WORKER = "worker"
FILTER_ERRORS = "errors"
FILTER_ALL = "all"
FILTERS = (FILTER_WORKER, FILTER_ERRORS, FILTER_ALL)

DEFAULT_CAPACITY = 500
#: Error lines closer together than this belong to one incident.
INCIDENT_GAP_SECONDS = 1.0
#: A worker job line this soon after an incident names the incident's job.
JOB_LINK_SECONDS = 3.0

_ERROR_LEVELS = frozenset({"ERROR", "ERR", "FATAL", "CRITICAL", "CRIT"})
_WARN_LEVELS = frozenset({"WARN", "WARNING"})

_LINE_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?[\t ]+([A-Za-z]{3,10})[\t ]+(.*)$")
_ULID_RE = re.compile(r"(?<![0-9A-Za-z])[0-9A-HJKMNP-TV-Z]{26}(?![0-9A-Za-z])")
_WORKER_RE = re.compile(r"\[LE_Companion\]|\[LEC\]|le_companion", re.IGNORECASE)
_LUA_RE = re.compile(r"\[LUA(?: API)?\]|LE::LUA::|LUA_ERR", re.IGNORECASE)
_EVENT_ERROR_RE = re.compile(r"\bevent\b.*\berror\b", re.IGNORECASE)
_TRACEBACK_RE = re.compile(r"stack traceback|LUA_ERR[A-Z]*", re.IGNORECASE)
_JOB_FAIL_RE = re.compile(r"->\s*(failed|partial|crashed|poison\w*)\b", re.IGNORECASE)
_LUA_SITE_RE = re.compile(r"([^\s\\/:]+\.lua:\d+:.*)$", re.IGNORECASE)


@dataclass(slots=True)
class LogEntry:
    """One log line (or a run of identical lines, see ``repeat``)."""

    seq: int
    clock: str                 # "HH:MM:SS.ffffff" exactly as written, "" if absent
    stamp: datetime | None     # file date + clock, rolled past midnight
    level: str                 # INFO / WARN / ERROR / ... ("" if unparsed)
    source: str                # SOURCE_* constant
    message: str
    job_id: str = ""
    job_inferred: bool = False  # job_id came from the following worker job line
    error: bool = False
    warn: bool = False
    repeat: int = 1

    @property
    def short_time(self) -> str:
        return self.clock[:8] if self.clock else "--:--:--"

    @property
    def tone(self) -> str:
        return "error" if self.error else "warn" if self.warn else "muted"

    @property
    def worker_related(self) -> bool:
        return self.source in (SOURCE_WORKER, SOURCE_LUA)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "time": self.clock,
            "stamp": self.stamp.isoformat() if self.stamp else "",
            "level": self.level,
            "source": self.source,
            "message": self.message,
            "job_id": self.job_id,
            "job_inferred": self.job_inferred,
            "error": self.error,
            "repeat": self.repeat,
        }


# --- parsing -----------------------------------------------------------------


def classify_source(message: str, *, launcher: bool = False) -> str:
    if launcher:
        return SOURCE_LAUNCHER
    if _WORKER_RE.search(message):
        return SOURCE_WORKER
    if _LUA_RE.search(message):
        return SOURCE_LUA
    return SOURCE_LE


def is_error(level: str, message: str) -> bool:
    if level.upper() in _ERROR_LEVELS:
        return True
    return bool(_TRACEBACK_RE.search(message) or _EVENT_ERROR_RE.search(message))


def extract_job_id(message: str, known_ids: Iterable[str] = ()) -> str:
    """A ULID-shaped token, else the first known job id found in the text."""
    match = _ULID_RE.search(message)
    if match is not None:
        return match.group(0)
    for job_id in known_ids:
        text = str(job_id or "")
        if len(text) >= 6 and text in message:
            return text
    return ""


def parse_line(
    line: str,
    *,
    seq: int = 0,
    file_date: date | None = None,
    launcher: bool = False,
    known_ids: Iterable[str] = (),
) -> LogEntry | None:
    """Parse one raw line. ``None`` for blank lines; never raises.

    A line without the ``time<TAB>LEVEL<TAB>`` prefix (a wrapped message) is
    kept as an entry with an empty clock so nothing written by LE is dropped.
    """
    text = str(line or "").rstrip("\r\n")
    if not text.strip():
        return None
    match = _LINE_RE.match(text)
    clock, level, message, stamp = "", "", text.strip(), None
    if match is not None:
        hh, mm, ss, frac, level, message = match.groups()
        level = level.upper()
        frac = frac or ""
        clock = f"{int(hh):02d}:{mm}:{ss}" + (f".{frac}" if frac else "")
        if file_date is not None:
            try:
                micro = int((frac + "000000")[:6]) if frac else 0
                stamp = datetime.combine(file_date, time(int(hh), int(mm), int(ss), micro))
            except ValueError:
                stamp = None
    source = classify_source(message, launcher=launcher)
    error = is_error(level, message)
    warn = (not error) and (level in _WARN_LEVELS or (source == SOURCE_WORKER and bool(_JOB_FAIL_RE.search(message))))
    return LogEntry(
        seq=seq,
        clock=clock,
        stamp=stamp,
        level=level,
        source=source,
        message=message,
        job_id=extract_job_id(message, known_ids),
        error=error,
        warn=warn,
    )


# --- incidents & tally -------------------------------------------------------


@dataclass(slots=True)
class Incident:
    """A burst of error lines that LE wrote for one failing call."""

    first_seq: int
    clock: str
    stamp: datetime | None
    last_stamp: datetime | None
    messages: list[str] = field(default_factory=list)
    worker: bool = False
    job_id: str = ""

    @property
    def short_time(self) -> str:
        return self.clock[:5] if self.clock else "--:--"

    @property
    def reason(self) -> str:
        return short_reason(self.messages)


@dataclass(slots=True)
class ErrorTally:
    """Error incidents seen since the current file was attached."""

    total: int = 0
    worker: int = 0
    last: Incident | None = None
    last_worker: Incident | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=50))

    @property
    def other(self) -> int:
        return self.total - self.worker


def _clean(message: str) -> str:
    text = message.strip()
    text = re.sub(r"^(\[[^\]]*\]\s*)+", "", text)       # [LUA API] [LE::...] prefixes
    text = re.sub(r"\s+@\s+LE::\S+\s*$", "", text)        # " @ LE::LUA::ScriptsManager::fn"
    return text.strip()


def short_reason(messages: Iterable[str], *, limit: int = 90) -> str:
    """One readable phrase for an incident, e.g. ``TransferPlayer squad full``."""
    lines = [str(m) for m in messages if str(m or "").strip()]
    if not lines:
        return ""
    joined = " ".join(lines).lower()
    failed = next((m for m in lines if re.search(r"\b\w+ Failed\b", m)), "")
    if failed:
        cleaned = _clean(failed)
        match = re.match(r"^(\w+) Failed\s*(.*)$", cleaned)
        if match:
            api, rest = match.groups()
            if "squad is full" in rest.lower() or "squad is full" in joined:
                text = f"{api} squad full"
            else:
                text = f"{api} failed: {rest}" if rest else f"{api} failed"
            return _truncate(text, limit)
    site = next((m for m in lines if _LUA_SITE_RE.search(m)), "")
    if site:
        found = _LUA_SITE_RE.search(site)
        return _truncate(found.group(1) if found else _clean(site), limit)
    return _truncate(_clean(lines[0]) or lines[0], limit)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


# --- ring buffer -------------------------------------------------------------


class GameLogBuffer:
    """The last ``capacity`` entries plus a per-file error tally.

    ``version`` changes whenever visible content changes (new entry, a repeat
    count bump or a back-filled job id), so a view can skip identical repaints.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self.capacity = max(1, int(capacity))
        self.entries: deque[LogEntry] = deque(maxlen=self.capacity)
        self.tally = ErrorTally()
        self.version = 0
        self.file_name = ""
        self.file_date: date | None = None
        self._seq = 0
        self._day_offset = 0
        self._last_secs: float | None = None
        self._open: Incident | None = None
        self._open_entries: list[LogEntry] = []
        self._link: Incident | None = None
        self._link_entries: list[LogEntry] = []
        self.lines_seen = 0

    # -- file lifecycle -------------------------------------------------------

    def begin_file(self, name: str, file_date: date | None, *, reset_tally: bool = True) -> None:
        """A new file (or the same file restarted) is being read from here on."""
        self.file_name = str(name or "")
        self.file_date = file_date
        self._day_offset = 0
        self._last_secs = None
        self._close_incident()
        self._link, self._link_entries = None, []
        if reset_tally:
            self.tally = ErrorTally()
        self.version += 1

    # -- ingest ---------------------------------------------------------------

    def ingest_lines(
        self,
        lines: Iterable[str],
        *,
        launcher: bool = False,
        known_ids: Iterable[str] = (),
    ) -> int:
        known = tuple(known_ids)
        added = 0
        for line in lines:
            entry = parse_line(
                line, seq=self._seq + 1, file_date=self.file_date,
                launcher=launcher, known_ids=known,
            )
            if entry is None:
                continue
            self.lines_seen += 1
            self._roll_day(entry)
            if self.ingest(entry):
                added += 1
        return added

    def ingest(self, entry: LogEntry) -> bool:
        """Add one entry. Returns ``False`` when it collapsed into the previous one."""
        self._track(entry)
        last = self.entries[-1] if self.entries else None
        if (
            last is not None
            and not entry.error
            and last.message == entry.message
            and last.level == entry.level
            and last.source == entry.source
            and not entry.job_id
        ):
            last.repeat += 1
            last.clock, last.stamp = entry.clock or last.clock, entry.stamp or last.stamp
            self.version += 1
            return False
        self._seq += 1
        entry.seq = self._seq
        self.entries.append(entry)
        self.version += 1
        return True

    def _roll_day(self, entry: LogEntry) -> None:
        """LE keeps one file per session start date; past midnight the clock wraps."""
        if entry.stamp is None:
            return
        secs = entry.stamp.hour * 3600 + entry.stamp.minute * 60 + entry.stamp.second
        if self._last_secs is not None and secs + 3600 < self._last_secs:
            self._day_offset += 1
        self._last_secs = float(secs)
        if self._day_offset:
            entry.stamp = entry.stamp + timedelta(days=self._day_offset)

    def _track(self, entry: LogEntry) -> None:
        if entry.error:
            if self._open is not None and _within(self._open.last_stamp, entry.stamp, INCIDENT_GAP_SECONDS):
                incident = self._open
            else:
                self._close_incident()
                incident = Incident(entry.seq, entry.clock, entry.stamp, entry.stamp)
                self._open, self._open_entries = incident, []
                self.tally.total += 1
                self.tally.last = incident
                self.tally.recent.append(incident)
            incident.last_stamp = entry.stamp or incident.last_stamp
            if len(incident.messages) < 6:
                incident.messages.append(entry.message)
            self._open_entries.append(entry)
            if entry.job_id and not incident.job_id:
                incident.job_id = entry.job_id
            if entry.worker_related and not incident.worker:
                incident.worker = True
                self.tally.worker += 1
            if incident.worker:
                self.tally.last_worker = incident
            return
        if self._open is not None and not _within(self._open.last_stamp, entry.stamp, INCIDENT_GAP_SECONDS):
            self._close_incident()
        # A worker job line shortly after an incident names the incident's job.
        if entry.job_id and entry.source == SOURCE_WORKER:
            pending = self._open or self._link
            entries = self._open_entries if self._open is not None else self._link_entries
            if pending is not None and not pending.job_id and _within(pending.last_stamp, entry.stamp, JOB_LINK_SECONDS):
                pending.job_id = entry.job_id
                for item in entries:
                    if not item.job_id:
                        item.job_id = entry.job_id
                        item.job_inferred = True
                self.version += 1
            self._close_incident()
            self._link, self._link_entries = None, []

    def _close_incident(self) -> None:
        if self._open is not None and not self._open.job_id:
            self._link, self._link_entries = self._open, self._open_entries
        self._open, self._open_entries = None, []

    # -- queries --------------------------------------------------------------

    def filtered(self, mode: str = FILTER_WORKER, *, newest_first: bool = True, limit: int | None = None) -> list[LogEntry]:
        items = [e for e in self.entries if matches(e, mode)]
        if newest_first:
            items.reverse()
        return items[:limit] if limit is not None else items

    def for_job(self, job_id: str) -> list[LogEntry]:
        """Entries that name ``job_id`` (directly or via the following job line), oldest first."""
        key = str(job_id or "")
        if not key:
            return []
        return [e for e in self.entries if e.job_id == key]

    def clear(self) -> None:
        self.entries.clear()
        self.tally = ErrorTally()
        self.version += 1


def _within(earlier: datetime | None, later: datetime | None, seconds: float) -> bool:
    if earlier is None or later is None:
        return earlier is None and later is None
    delta = (later - earlier).total_seconds()
    return -0.001 <= delta <= seconds


def matches(entry: LogEntry, mode: str) -> bool:
    """Worker = the Companion's lines plus every error (LE's squad-full errors carry no tag)."""
    if mode == FILTER_ALL:
        return True
    if mode == FILTER_ERRORS:
        return entry.error
    return entry.error or entry.source == SOURCE_WORKER


# --- presentation (shared by UI, CLI and doctor) ----------------------------

_SOURCE_TAG = {SOURCE_WORKER: "LEC", SOURCE_LUA: "LUA", SOURCE_LE: "LE ", SOURCE_LAUNCHER: "LCH"}


def format_row(entry: LogEntry, *, width: int | None = None) -> str:
    """``HH:MM:SS ERR LEC message ×N [job]`` — one compact, fixed-prefix line."""
    level = "ERR" if entry.error else "WRN" if entry.warn else (entry.level[:3] or "---")
    text = " ".join(entry.message.split())
    if text.startswith("[LUA API] ") and entry.source != SOURCE_LUA:
        text = text[len("[LUA API] "):]  # every worker print carries it; the tag says LEC
    if entry.job_inferred and entry.job_id:
        text += f"  [job {entry.job_id}]"
    if entry.repeat > 1:
        text += f"  ×{entry.repeat}"
    row = f"{entry.short_time} {level:<3} {_SOURCE_TAG.get(entry.source, '?  ')} {text}"
    if width is not None and len(row) > width:
        row = row[: max(1, width - 1)] + "…"
    return row


def summarize(tally: ErrorTally, *, file_name: str = "", is_today: bool = True) -> str:
    """The doctor line, e.g. ``Game log: 6 worker errors today, last: TransferPlayer squad full (09:03)``."""
    when = "today" if is_today else (f"in {file_name}" if file_name else "in the last log")
    if tally.worker:
        noun = "error" if tally.worker == 1 else "errors"
        last = tally.last_worker
        tail = f", last: {last.reason} ({last.short_time})" if last is not None else ""
        other = f"; {tally.other} other LE error{'s' if tally.other != 1 else ''}" if tally.other else ""
        return f"Game log: {tally.worker} worker {noun} {when}{tail}{other}"
    if tally.total:
        last = tally.last
        tail = f", last: {last.reason} ({last.short_time})" if last is not None else ""
        noun = "error" if tally.total == 1 else "errors"
        return f"Game log: no worker errors {when}; {tally.total} other LE {noun}{tail}"
    return f"Game log: no worker errors {when}"


__all__ = [
    "DEFAULT_CAPACITY",
    "ErrorTally",
    "FILTERS",
    "FILTER_ALL",
    "FILTER_ERRORS",
    "FILTER_WORKER",
    "GameLogBuffer",
    "Incident",
    "LogEntry",
    "SOURCE_LAUNCHER",
    "SOURCE_LE",
    "SOURCE_LUA",
    "SOURCE_WORKER",
    "classify_source",
    "extract_job_id",
    "format_row",
    "is_error",
    "matches",
    "parse_line",
    "short_reason",
    "summarize",
]
