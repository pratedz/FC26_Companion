"""Keep the last real worker session across a transient session.json miss.

Lua replaces session.json by renaming it aside and then renaming the new
file in. One poll can observe no file. That is not a new Live Editor
session and must not clear the squad.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ...core.transport.v3 import Liveness, Pill

# One or two unreadable polls keep the last armed snapshot. A longer gap
# reports OFF but still carries the last session id, so ABC → "" → ABC is
# not a session change.
TRANSIENT_MISS_POLLS = 2
TRANSIENT_MISS_GRACE_SECONDS = 3.0


@dataclass
class LivenessHold:
    """Mutable poll memory. Lives on Services, not in AppState."""

    last: Liveness | None = None
    misses: int = 0
    miss_at: datetime | None = None


def stabilize_liveness(hold: LivenessHold, raw: Liveness, now: datetime) -> Liveness:
    """Return the liveness the reducer should see."""
    if raw.session_id:
        hold.last = raw
        hold.misses = 0
        hold.miss_at = None
        return raw
    if hold.last is None or not hold.last.session_id:
        return raw
    hold.misses += 1
    if hold.miss_at is None:
        hold.miss_at = now
    age = max(0.0, (now - hold.miss_at).total_seconds())
    if hold.misses <= TRANSIENT_MISS_POLLS and age <= TRANSIENT_MISS_GRACE_SECONDS:
        return replace(
            hold.last,
            message="Checking Live Editor… the session file blinked.",
        )
    return Liveness(
        pill=Pill.OFF,
        message="Live Editor session is not available.",
        armed=False,
        session_id=hold.last.session_id,
        core_version=hold.last.core_version,
        save_uid=hold.last.save_uid,
        pid=hold.last.pid,
        pid_alive=False,
        capabilities=dict(hold.last.capabilities or {}),
    )
