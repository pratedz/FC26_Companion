"""Static skeleton loaders for LE Companion.

Perf rule: no after() animation loops — static grey bars only.
"""

from __future__ import annotations

from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from .. import ui_theme
from ..ui_theme import RADIUS_XS, SP2

# Prefer theme token when Phase-1 tokens land; else design-doc default.
SKELETON = getattr(ui_theme, "SKELETON", "#1a2433")


def skeleton_block(parent: Any, *, width: int = 200, height: int = 14) -> Any:
    """Single static placeholder bar (text/content stand-in)."""
    frame = ctk.CTkFrame(
        parent,
        width=width,
        height=height,
        fg_color=SKELETON,
        corner_radius=RADIUS_XS,
        border_width=0,
    )
    frame.pack_propagate(False)
    return frame


def skeleton_list(parent: Any, *, rows: int = 5, row_height: int = 18) -> Any:
    """Stack of static skeleton bars for list loading states.

    Caller is responsible for packing/placing the returned host.
    No animation — bars are fixed SKELETON-colored frames.
    """
    host = ctk.CTkFrame(parent, fg_color="transparent")
    n = max(0, int(rows))
    for i in range(n):
        # Slight width variation reads as content without motion.
        bar_w = 280 if i % 2 == 0 else 220
        bar = skeleton_block(host, width=bar_w, height=row_height)
        pady = (0, SP2) if i < n - 1 else (0, 0)
        bar.pack(anchor="w", fill="x", pady=pady)
    return host
