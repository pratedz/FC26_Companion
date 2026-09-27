"""Shared CTk/Tk widget factories."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

try:
    import customtkinter as ctk
    import tkinter as tk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    tk = None  # type: ignore

from ..icons import ctk_icon
from ..ui_theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BORDER,
    BTN_H,
    CARD,
    CARD_HOVER,
    ENTRY_H,
    FONT_MONO,
    FONT_UI,
    LIST_BG,
    LIST_FG,
    LIST_SEL,
    MUTED,
    PANEL,
    RADIUS,
    RADIUS_SM,
    SUCCESS,
    SUCCESS_HOVER,
    TEXT,
    WARNING,
)
from .. import ui_theme as _theme

# Prefer theme tokens when present; keep hard fallbacks for older theme modules.
SECONDARY_BTN = getattr(_theme, "SECONDARY_BTN", "#243044")
SECONDARY_BTN_HOVER = getattr(_theme, "SECONDARY_BTN_HOVER", CARD_HOVER)
DANGER = getattr(_theme, "DANGER", "#f87171")
DANGER_HOVER = getattr(_theme, "DANGER_HOVER", "#fca5a5")
SP1 = getattr(_theme, "SP1", 4)
SP2 = getattr(_theme, "SP2", 8)
RADIUS_XS = getattr(_theme, "RADIUS_XS", 6)
SIZE_TINY = getattr(_theme, "SIZE_TINY", 10)
SIZE_META = getattr(_theme, "SIZE_META", 11)
SIZE_SECTION = getattr(_theme, "SIZE_SECTION", 14)
BADGE_BG = getattr(_theme, "BADGE_BG", "#0e749033")
MUTED_DIM = getattr(_theme, "MUTED_DIM", "#64748b")
PILL_LIVE_BG = getattr(_theme, "PILL_LIVE_BG", "#052e1c")
PILL_OFF_BG = getattr(_theme, "PILL_OFF_BG", "#422006")
PILL_BUSY_BG = getattr(_theme, "PILL_BUSY_BG", "#083344")


def ensure_ctk() -> None:
    if ctk is None:
        raise RuntimeError(
            "customtkinter is required.\nInstall: python -m pip install customtkinter pillow"
        )


def label(parent: Any, text: str, *, muted: bool = False, bold: bool = False, size: int = 12) -> Any:
    return ctk.CTkLabel(
        parent,
        text=text,
        text_color=MUTED if muted else TEXT,
        font=ctk.CTkFont(family=FONT_UI, size=size, weight="bold" if bold else "normal"),
        anchor="w",
    )


def btn(
    parent: Any,
    text: str,
    command: Callable[[], None],
    *,
    kind: str = "secondary",
    width: Optional[int] = None,
    height: int = BTN_H,
    icon: Optional[str] = None,
    icon_size: int = 18,
) -> Any:
    """Button factory. Icons only on primary/accent (ghost icons thrash CTk)."""
    if width is not None:
        w = width
    elif kind == "primary":
        w = 140
    elif kind == "ghost":
        w = 110
    else:
        w = 120

    # Ghost/secondary chrome: text only — PhotoImage on every ops button caused lag
    img = None
    if icon and kind in ("primary", "accent", "danger"):
        img = ctk_icon(icon, icon_size)
    if img is not None and width is None:
        w = w + 20

    kwargs: Dict[str, Any] = {
        "text": text,
        "command": command,
        "width": w,
        "height": height,
        "corner_radius": RADIUS_SM,
    }
    if img is not None:
        kwargs["image"] = img
        kwargs["compound"] = "left"

    if kind == "primary":
        kwargs.update(
            fg_color=SUCCESS,
            hover_color=SUCCESS_HOVER,
            text_color="#052e1c",
            font=ctk.CTkFont(family=FONT_UI, size=13, weight="bold"),
        )
    elif kind == "accent":
        kwargs.update(
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#042f2e",
            font=ctk.CTkFont(family=FONT_UI, size=13, weight="bold"),
        )
    elif kind == "danger":
        # Light coral fill — dark text for contrast (same pattern as primary/accent)
        kwargs.update(
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            text_color=BG,
            font=ctk.CTkFont(family=FONT_UI, size=13, weight="bold"),
        )
    elif kind == "ghost":
        kwargs.update(
            fg_color="transparent",
            hover_color=CARD_HOVER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family=FONT_UI, size=12),
        )
    else:
        # Secondary: mid-surface fill (hierarchy: below primary/accent, above ghost)
        kwargs.update(
            fg_color=SECONDARY_BTN,
            hover_color=SECONDARY_BTN_HOVER,
            border_width=0,
            text_color=TEXT,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
        )
    return ctk.CTkButton(parent, **kwargs)


def badge(parent: Any, text: str, *, tone: str = "accent") -> Any:
    """Small capsule label (LIVE, OVR, category, etc.)."""
    # (text_color, fg_color, border_color) — all from theme tokens
    tone_map: Dict[str, tuple[str, str, str]] = {
        "accent": (ACCENT, BADGE_BG, ACCENT),
        "success": (SUCCESS, PILL_LIVE_BG, SUCCESS),
        "warning": (WARNING, PILL_OFF_BG, WARNING),
        "danger": (DANGER, PILL_BUSY_BG, DANGER),
        "muted": (MUTED, CARD, BORDER),
    }
    fg, bg, bd = tone_map.get(tone, tone_map["accent"])
    frame = ctk.CTkFrame(
        parent,
        fg_color=bg,
        corner_radius=RADIUS_XS + 6,
        border_width=1,
        border_color=bd,
    )
    ctk.CTkLabel(
        frame,
        text=text,
        text_color=fg,
        font=ctk.CTkFont(family=FONT_UI, size=SIZE_TINY, weight="bold"),
    ).pack(padx=SP2, pady=SP1 // 2 + 1)
    return frame


def section_header(
    parent: Any,
    title: str,
    subtitle: str = "",
    icon: Optional[str] = None,
) -> Any:
    """Consistent H2 row: optional icon + title + muted subtitle."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    row = ctk.CTkFrame(frame, fg_color="transparent")
    row.pack(fill="x", anchor="w")

    img = ctk_icon(icon, 18) if icon else None
    if img is not None:
        ctk.CTkLabel(row, text="", image=img, width=22).pack(side="left", padx=(0, SP2))

    col = ctk.CTkFrame(row, fg_color="transparent")
    col.pack(side="left", fill="x", expand=True)

    ctk.CTkLabel(
        col,
        text=title,
        text_color=TEXT,
        font=ctk.CTkFont(family=FONT_UI, size=SIZE_SECTION, weight="bold"),
        anchor="w",
    ).pack(anchor="w")

    if subtitle:
        ctk.CTkLabel(
            col,
            text=subtitle,
            text_color=MUTED,
            font=ctk.CTkFont(family=FONT_UI, size=SIZE_META),
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

    return frame


def metric_chip(parent: Any, label: str, value: str) -> Any:
    """Compact labeled value chip (OVR / POT / SM / WF)."""
    frame = ctk.CTkFrame(
        parent,
        fg_color=CARD,
        corner_radius=RADIUS_XS,
        border_width=1,
        border_color=BORDER,
    )
    ctk.CTkLabel(
        frame,
        text=label,
        text_color=MUTED_DIM,
        font=ctk.CTkFont(family=FONT_UI, size=SIZE_TINY),
        anchor="center",
    ).pack(padx=SP2, pady=(SP1, 0))
    ctk.CTkLabel(
        frame,
        text=str(value),
        text_color=TEXT,
        font=ctk.CTkFont(family=FONT_UI, size=SIZE_META, weight="bold"),
        anchor="center",
    ).pack(padx=SP2, pady=(0, SP1))
    return frame


def entry(parent: Any, var: Any, *, width: int = 160, show: Optional[str] = None) -> Any:
    kw: Dict[str, Any] = {
        "textvariable": var,
        "width": width,
        "height": ENTRY_H,
        "corner_radius": RADIUS_SM,
        "fg_color": LIST_BG,
        "border_color": BORDER,
        "text_color": TEXT,
        "font": ctk.CTkFont(family=FONT_UI, size=13),
    }
    if show is not None:
        kw["show"] = show
    return ctk.CTkEntry(parent, **kw)


def panel(parent: Any) -> Any:
    return ctk.CTkFrame(
        parent,
        fg_color=PANEL,
        corner_radius=RADIUS,
        border_width=1,
        border_color=BORDER,
    )


def listbox(parent: Any, *, height: int = 6) -> Any:
    lb = tk.Listbox(
        parent,
        font=(FONT_MONO, 11),
        bg=LIST_BG,
        fg=LIST_FG,
        selectbackground=LIST_SEL,
        selectforeground="#ffffff",
        activestyle="none",
        highlightthickness=0,
        borderwidth=0,
        relief="flat",
        exportselection=False,
        height=height,
    )

    def _on_wheel(event: Any) -> str:
        """Native listbox wheel — multi-line step, no after() chains."""
        try:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return "break"
            # Larger step feels snappier; no reflow storm (plain Listbox)
            steps = -1 if delta > 0 else 1
            lb.yview_scroll(steps * 5, "units")
        except Exception:
            pass
        return "break"

    lb.bind("<MouseWheel>", _on_wheel)
    return lb
