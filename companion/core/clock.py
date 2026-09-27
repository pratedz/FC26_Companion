"""The one clock. v1 had 36 raw ``time.time()`` sites across 13 modules and two
byte-identical ``_timestamp()`` helpers; no timeout was testable without really
sleeping. Everything that needs time takes a ``Clock``.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...           # tz-aware UTC, always
    def now_ts(self) -> float: ...           # unix seconds
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def stamp(self) -> str: ...              # the ONE ISO-8601 formatter


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_ts(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def stamp(self) -> str:
        return self.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FakeClock:
    """Deterministic clock for tests. ``sleep`` advances instead of blocking."""

    def __init__(self, start: float = 1_784_960_000.0) -> None:
        self._ts = float(start)
        self._mono = 0.0

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._ts, tz=timezone.utc)

    def now_ts(self) -> float:
        return self._ts

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def stamp(self) -> str:
        return self.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def advance(self, seconds: float) -> None:
        self._ts += seconds
        self._mono += seconds
