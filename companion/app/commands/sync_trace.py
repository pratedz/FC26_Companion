"""Optional squad-sync trace. Off unless COMPANION_SYNC_DEBUG=1.

This is diagnostic output for one real session. It is not a product feature
and it does not run during normal launches.
"""

from __future__ import annotations

import os

from ...core.log import get_logger

_TRUE = {"1", "true", "yes", "on"}


def sync_debug_enabled() -> bool:
    return os.environ.get("COMPANION_SYNC_DEBUG", "").strip().lower() in _TRUE


def sync_log(prefix: str, message: str) -> None:
    if not sync_debug_enabled():
        return
    log = get_logger("companion.sync")
    if log.level == 0 or log.level > 20:
        log.setLevel(20)
    log.info("[%s] %s", prefix, message)
