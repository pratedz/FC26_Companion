"""shadcn-inspired empty states for CustomTkinter.

UX principle: empty states are onboarding — name what's missing + one CTA.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from .. import icons
from ..ui_theme import (
    ACCENT,
    BORDER,
    EMPTY_BG,
    FONT_UI,
    MUTED,
    SP2,
    SP3,
    SP4,
    TEXT,
)
from . import widgets as w

# Visual weight roughly matches the previous 28pt glyph.
_ICON_PX = 40


def empty_state(
    parent: Any,
    *,
    title: str,
    body: str,
    action_label: Optional[str] = None,
    action: Optional[Callable[[], None]] = None,
    icon_text: str = "◇",
    icon_name: Optional[str] = None,
) -> Any:
    frame = ctk.CTkFrame(
        parent,
        fg_color=EMPTY_BG,
        corner_radius=10,
        border_width=1,
        border_color=BORDER,
    )
    frame.pack(fill="both", expand=True, padx=SP4, pady=SP4)

    inner = ctk.CTkFrame(frame, fg_color="transparent")
    inner.place(relx=0.5, rely=0.48, anchor="center")

    img = icons.ctk_icon(icon_name, _ICON_PX) if icon_name else None
    if img is not None:
        ctk.CTkLabel(
            inner,
            text="",
            image=img,
        ).pack(pady=(0, SP2))
    else:
        ctk.CTkLabel(
            inner,
            text=icon_text,
            font=ctk.CTkFont(family=FONT_UI, size=28),
            text_color=ACCENT,
        ).pack(pady=(0, SP2))
    ctk.CTkLabel(
        inner,
        text=title,
        font=ctk.CTkFont(family=FONT_UI, size=16, weight="bold"),
        text_color=TEXT,
    ).pack(pady=(0, SP2))
    ctk.CTkLabel(
        inner,
        text=body,
        font=ctk.CTkFont(family=FONT_UI, size=12),
        text_color=MUTED,
        wraplength=380,
        justify="center",
    ).pack(pady=(0, SP4))
    if action_label and action:
        w.btn(inner, action_label, action, kind="accent", width=160, height=32).pack()
    return frame
