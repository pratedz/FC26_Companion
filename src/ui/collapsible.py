"""Accordion section with LAZY body build (critical for Editor performance).

Building 100s of CTkEntry at once freezes tab switch. Body factory runs
only on first open.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ..icons import ctk_icon
from ..ui_theme import (
    ACCENT,
    BORDER,
    CARD,
    FONT_UI,
    MUTED,
    PANEL,
    RADIUS,
    SP2,
    SP3,
    SP4,
    TEXT,
)


class CollapsibleSection(ctk.CTkFrame):
    """Header with chevron; body built on first open via body_builder."""

    def __init__(
        self,
        parent: Any,
        *,
        title: str,
        description: str = "",
        open: bool = False,
        body_builder: Optional[Callable[[Any], None]] = None,
        on_toggle: Optional[Callable[[bool], None]] = None,
        icon_name: Optional[str] = None,
        count: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("fg_color", PANEL)
        kwargs.setdefault("corner_radius", RADIUS)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", BORDER)
        super().__init__(parent, **kwargs)
        self._open = False
        self._built = False
        self._body_builder = body_builder
        self._on_toggle = on_toggle
        self._title = title

        head = ctk.CTkFrame(self, fg_color=CARD, corner_radius=RADIUS - 2, height=40)
        head.pack(fill="x", padx=2, pady=2)
        head.pack_propagate(False)
        head.bind("<Button-1>", lambda _e: self.toggle())

        self._chev = ctk.CTkLabel(
            head,
            text="▶",
            width=28,
            text_color=ACCENT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._chev.pack(side="left", padx=(SP3, 0))
        self._chev.bind("<Button-1>", lambda _e: self.toggle())

        if icon_name:
            img = ctk_icon(icon_name, size=16)
            if img is not None:
                icon_lbl = ctk.CTkLabel(head, text="", image=img, width=20)
                icon_lbl.pack(side="left", padx=(SP2, 0))
                icon_lbl.bind("<Button-1>", lambda _e: self.toggle())

        title_col = ctk.CTkFrame(head, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True, padx=SP2)
        title_col.bind("<Button-1>", lambda _e: self.toggle())
        ctk.CTkLabel(
            title_col,
            text=title,
            text_color=TEXT,
            font=ctk.CTkFont(family=FONT_UI, size=13, weight="bold"),
            anchor="w",
        ).pack(anchor="w")
        if description:
            ctk.CTkLabel(
                title_col,
                text=description[:90],
                text_color=MUTED,
                font=ctk.CTkFont(family=FONT_UI, size=10),
                anchor="w",
            ).pack(anchor="w")

        if count is not None:
            count_lbl = ctk.CTkLabel(
                head,
                text=str(count),
                text_color=MUTED,
                font=ctk.CTkFont(family=FONT_UI, size=11),
                width=36,
                anchor="e",
            )
            count_lbl.pack(side="right", padx=(0, SP3))
            count_lbl.bind("<Button-1>", lambda _e: self.toggle())

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        # Defer open until after body_builder is set by caller if needed
        if open:
            self.set_open(True)

    def is_open(self) -> bool:
        return self._open

    def _ensure_body(self) -> None:
        if self._built:
            return
        self._built = True
        if self._body_builder is not None:
            try:
                self._body_builder(self.body)
            except Exception:
                pass

    def _apply_border(self, open: bool) -> None:
        try:
            if open:
                self.configure(border_color=ACCENT, border_width=2)
            else:
                self.configure(border_color=BORDER, border_width=1)
        except Exception:
            pass

    def set_open(self, open: bool) -> None:
        open = bool(open)
        if open == self._open:
            if open and not self._built:
                self._ensure_body()
            return
        self._open = open
        try:
            self._chev.configure(text="▼" if open else "▶")
        except Exception:
            pass
        self._apply_border(open)
        try:
            if open:
                self._ensure_body()
                self.body.pack(fill="x", padx=SP4, pady=(0, SP3))
            else:
                self.body.pack_forget()
        except Exception:
            pass
        if self._on_toggle:
            try:
                self._on_toggle(open)
            except Exception:
                pass

    def toggle(self) -> None:
        self.set_open(not self._open)
