"""Reader for ``queue/native_status.json`` — the lec_native plugin's heartbeat.

The native plugin runs inside the game and rewrites this file about once a
second (tmp + rename). It is a diagnostic, like ``drain.json``: it never
changes the Pill, it only tells the user whether "instant mode" (a native tick
that runs jobs on a settled Career hub without waiting for a hub event) is on.

A file left behind by a closed game is *stale*, not absent: it is live only
while its mtime is under ``FRESH_AGE`` seconds old and, when ``session.json``
carries a pid, the pids agree. Missing, partial or non-object JSON reads as
``None`` and never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .jobfile import read_json

NATIVE_STATUS_FILE = "native_status.json"
FRESH_AGE = 5.0  # seconds; the plugin writes about once per second

KNOWN_REASONS = (
    "not_opened",
    "le_base_not_found",
    "le_build_mismatch_header",
    "le_build_mismatch_code",
    "waiting_for_career_event",
    "set_timer_failed",
    "ok",
    "tick_error_stop",
)


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            return default
    return default


def _opt_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _bool(value: Any) -> bool:
    return value is True  # JSON booleans only; "false"/1 are not trusted


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): _int(v) for k, v in value.items() if not isinstance(v, (dict, list))}


@dataclass(frozen=True, slots=True)
class NativeStatus:
    schema: int
    plugin: str
    version: str
    le_build: str
    pid: int
    verified: bool
    reason: str
    le_base: str = ""
    le_base_via: str = ""
    pe_header: str = ""
    disabled: bool = False
    error_stop: bool = False
    tick_live: bool = False
    tick_ms: int = 0
    timer_kind: str = ""
    threads: Mapping[str, int] = field(default_factory=dict)
    same_thread: bool = False
    same_state: bool = False
    counts: Mapping[str, int] = field(default_factory=dict)
    last_tick_age_ms: int | None = None
    last_career_age_ms: int | None = None
    last_status: int = 0
    uptime_ms: int = 0
    # Staleness, decided by the reader — not part of the wire file.
    file_age: float = 0.0       # seconds since the file's mtime
    stale: bool = False
    stale_reason: str = ""      # "" | "old" | "pid_mismatch"

    @property
    def live(self) -> bool:
        return not self.stale

    @property
    def instant(self) -> bool:
        """Jobs can run on the native tick right now."""
        return (
            self.live
            and self.verified
            and self.tick_live
            and not self.disabled
            and not self.error_stop
            and self.reason == "ok"
        )

    @classmethod
    def from_wire(
        cls,
        data: Mapping[str, Any],
        *,
        file_age: float = 0.0,
        stale: bool = False,
        stale_reason: str = "",
    ) -> "NativeStatus":
        return cls(
            schema=_int(data.get("schema")),
            plugin=_str(data.get("plugin")),
            version=_str(data.get("version")),
            le_build=_str(data.get("le_build")),
            pid=_int(data.get("pid")),
            verified=_bool(data.get("verified")),
            reason=_str(data.get("reason")) or "unknown",
            le_base=_str(data.get("le_base")),
            le_base_via=_str(data.get("le_base_via")),
            pe_header=_str(data.get("pe_header")),
            disabled=_bool(data.get("disabled")),
            error_stop=_bool(data.get("error_stop")),
            tick_live=_bool(data.get("tick_live")),
            tick_ms=_int(data.get("tick_ms")),
            timer_kind=_str(data.get("timer_kind")),
            threads=_int_map(data.get("threads")),
            same_thread=_bool(data.get("same_thread")),
            same_state=_bool(data.get("same_state")),
            counts=_int_map(data.get("counts")),
            last_tick_age_ms=_opt_int(data.get("last_tick_age_ms")),
            last_career_age_ms=_opt_int(data.get("last_career_age_ms")),
            last_status=_int(data.get("last_status")),
            uptime_ms=_int(data.get("uptime_ms")),
            file_age=float(file_age),
            stale=bool(stale),
            stale_reason=stale_reason,
        )


def read_native_status(
    path: Path,
    *,
    now_ts: float,
    session_pid: int | None = None,
    fresh_age: float = FRESH_AGE,
) -> NativeStatus | None:
    """Parse ``path``; ``None`` when absent or not a lec_native status object.

    ``session_pid`` is ``session.json``'s ``le_pid``; pass ``None`` / ``0``
    when unknown and only the mtime decides freshness.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    data = read_json(path)
    if data is None:
        return None
    plugin = data.get("plugin")
    if plugin is not None and plugin != "lec_native":
        return None
    if "reason" not in data and "tick_live" not in data:
        return None  # not a status file we understand
    age = now_ts - mtime
    stale_reason = ""
    if age >= fresh_age:
        stale_reason = "old"
    else:
        native_pid = _int(data.get("pid"))
        if session_pid and session_pid > 0 and native_pid > 0 and native_pid != session_pid:
            stale_reason = "pid_mismatch"
    return NativeStatus.from_wire(
        data,
        file_age=max(0.0, age),
        stale=bool(stale_reason),
        stale_reason=stale_reason,
    )
