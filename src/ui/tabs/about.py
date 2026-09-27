"""About tab."""

from __future__ import annotations

from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import __version__
from ... import paths
from ...ui_theme import (
    ACCENT,
    BG,
    FONT_MONO,
    FONT_UI,
    MUTED,
    RADIUS_SM,
    SUCCESS,
    TEXT,
)
from .. import widgets as w

def _build_about_tab(app: Any) -> None:
    box = w.panel(app.tab_about)
    box.pack(fill="both", expand=True, padx=8, pady=8)

    w.label(box, "LE Companion", bold=True, size=18).pack(anchor="w", padx=20, pady=(20, 4))
    w.label(
        box,
        "Companion for FC 26 Live Editor (xAranaktu).",
        muted=True,
        size=12,
    ).pack(anchor="w", padx=20, pady=(0, 12))

    # Safety badges
    badges = ctk.CTkFrame(box, fg_color="transparent")
    badges.pack(anchor="w", padx=20, pady=(0, 16))
    ctk.CTkLabel(
        badges,
        text="  No inject  ",
        font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
        text_color="#042f2e",
        fg_color=ACCENT,
        corner_radius=RADIUS_SM,
    ).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        badges,
        text="  Queue worker  ",
        font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
        text_color="#052e1c",
        fg_color=SUCCESS,
        corner_radius=RADIUS_SM,
    ).pack(side="left")

    w.label(
        box,
        "Lua only, through the Live Editor queue. Start on Home — boost, apply a card, build/AI edit, or download catalog.",
        muted=True,
        size=12,
    ).pack(anchor="w", padx=20, pady=(0, 16))

    # Definition-style rows
    defs = (
        ("Version", f"v{__version__}"),
        ("App path", str(paths.app_root())),
        ("LE root", str(paths.le_root())),
        ("Queue path", str(paths.queue_dir())),
    )
    for key, val in defs:
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(
            row,
            text=key,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            text_color=MUTED,
            width=100,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            row,
            text=val,
            font=ctk.CTkFont(family=FONT_MONO, size=12),
            text_color=TEXT,
            anchor="w",
            wraplength=900,
            justify="left",
        ).pack(side="left", fill="x", expand=True, padx=(12, 0))


