"""UI performance helpers — prefer native scroll, never fight the event loop.

History: a 90–165 Hz “smooth scroll” patch used after() + yview_scroll(units)
with large steps. On CustomTkinter that reflows huge widget trees every frame
and freezes the UI. We no longer install that patch.

Rules:
  1. Use stock CTkScrollableFrame wheel handling
  2. Never call root.update() from status paths
  3. Soft refresh is heavily throttled (idletasks only)
  4. Do not raise Windows timer to 1ms (wakes scheduler constantly)
"""

from __future__ import annotations

import time
from typing import Any

TARGET_FPS = 30  # unused for scroll; kept for debug/about
FRAME_MS = 33

# Public contracts (unit tests assert these)
SMOOTH_SCROLL_ENABLED = False
SOFT_MIN_INTERVAL = 0.35  # ~3 idletasks/sec max — scroll must not fight paints
TAB_PRELOAD_GAP_MS = 140  # between idle tab preloads
TAB_PRELOAD_HEAVY_GAP_MS = 320  # after Cards/Editor
BOOST_FIRST_BATCH = 3  # cards in first idle paint
BOOST_BATCH = 3
BOOST_BATCH_GAP_MS = 16  # one frame between batches
BRIDGE_TICK_MS = 5000
CHROME_PENDING_TTL = 2.0

_last_soft = 0.0
_SOFT_MIN_INTERVAL = SOFT_MIN_INTERVAL


def _prefer_windows_visuals(root: Any) -> None:
    try:
        root.option_add("*tearOff", False)
    except Exception:
        pass
    try:
        # Keep 1.0 — non-1.0 + CTkImage blurs and costs more
        root.tk.call("tk", "scaling", 1.0)
    except Exception:
        pass


def install_smooth_scroll(*, target_fps: int = TARGET_FPS, enabled: bool = False) -> None:
    """No-op: smooth-scroll monkey-patch disabled (caused scroll lag/freezes)."""
    del target_fps, enabled
    return


def tune_root(root: Any, *, target_fps: int = TARGET_FPS) -> None:
    """Call once after CTk root exists — light tweaks only (no timeBeginPeriod)."""
    del target_fps
    _prefer_windows_visuals(root)
    try:
        root._ui_target_fps = 30  # type: ignore[attr-defined]
        root._ui_smooth_scroll = False  # type: ignore[attr-defined]
    except Exception:
        pass


def soft_refresh(root: Any, *, force: bool = False) -> None:
    """Throttled idletasks flush — never full update()."""
    global _last_soft
    now = time.monotonic()
    if not force and (now - _last_soft) < _SOFT_MIN_INTERVAL:
        return
    _last_soft = now
    try:
        # Never root.update() — full event drain freezes scroll/tabs
        root.update_idletasks()
    except Exception:
        pass


def soft_refresh_uses_full_update() -> bool:
    """Contract: soft path must never call root.update()."""
    return False


def smooth_scroll_is_enabled() -> bool:
    """Contract: high-Hz after()+yview patch stays off."""
    return bool(SMOOTH_SCROLL_ENABLED)
