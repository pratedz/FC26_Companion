"""ToastHost — transient feedback when the Changes bar is hidden.

Status text used to live only in the Changes summary, which disappears when
``dirty_count == 0``. Toasts keep those messages visible without turning the
Changes bar into a status dump.
"""

from __future__ import annotations

from typing import Any

from .. import theme
from .primitives import ensure_ctk, shade, text_label

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

_AUTO_DISMISS_MS = 4000
_MAX_VISIBLE = 3
_STICKY_TONES = frozenset({"error"})
_TONE_ICONS = {"ok": "success", "warn": "warning", "error": "warning", "info": "info"}


class ToastHost:
    """Bottom-right stack of tone-coloured toasts packed into the shell root."""

    def __init__(self, parent: Any) -> None:
        ensure_ctk()
        self._parent = parent
        # Leave this unplaced until a toast exists. An empty CTkFrame is
        # 200×200, and placing it at the bottom-right paints a black slab
        # over the squad grid.
        self._stack = ctk.CTkFrame(
            parent,
            fg_color="transparent",
            corner_radius=0,
            border_width=0,
        )
        # Above the page's own bottom action bar (Club, Sign), never on it.
        self._bottom_offset = 96
        self._placed = False
        self._items: list[Any] = []

    def set_bottom_offset(self, pixels: int) -> None:
        """Keep the stack above the Changes dock when that dock is visible."""
        self._bottom_offset = max(16, int(pixels))
        if not self._placed:
            return
        self._place_stack()

    def show(self, text: str, tone: str = "info") -> None:
        """Show a toast. ``error`` is sticky; other tones auto-dismiss."""
        message = str(text or "").strip()
        if not message:
            return
        ensure_ctk()
        while len(self._items) >= _MAX_VISIBLE:
            self._dismiss(self._items[0])

        from ..icons import ctk_icon

        fill, _fg = theme.tone_colors(tone)
        card = ctk.CTkFrame(
            self._stack,
            fg_color=theme.CARD_HOVER,
            corner_radius=theme.R_MD,
            border_width=1,
            border_color=shade(fill, 0.7) if tone != "muted" else theme.BORDER,
        )
        card.pack(fill="x", pady=(theme.SP2, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SP3, pady=theme.SP3)

        image = ctk_icon(_TONE_ICONS.get(tone, "info"), 18, tint=theme.tone_fg(tone))
        if image is not None:
            ctk.CTkLabel(inner, text="", image=image, width=20).pack(
                side="left", anchor="n", padx=(0, theme.SP2)
            )

        label = text_label(inner, message, size=theme.T_SMALL, color=theme.TEXT)
        label.configure(wraplength=320, justify="left", anchor="w")
        label.pack(side="left", fill="x", expand=True)

        sticky = str(tone or "info") in _STICKY_TONES
        close = ctk.CTkButton(
            inner,
            text="",
            image=ctk_icon("close", 12, tint=theme.MUTED),
            width=24,
            height=24,
            fg_color="transparent",
            hover_color=theme.CARD,
            corner_radius=theme.R_XS,
            command=lambda c=card: self._dismiss(c),
        )
        close.pack(side="right", anchor="n", padx=(theme.SP2, 0))

        self._items.append(card)
        self._place_stack()

        if not sticky:
            try:
                card.after(_AUTO_DISMISS_MS, lambda c=card: self._dismiss(c))
            except Exception:
                pass

    def _place_stack(self) -> None:
        try:
            self._stack.place(
                relx=1.0, rely=1.0, x=-theme.SP4, y=-self._bottom_offset, anchor="se",
            )
            self._stack.lift()
            self._placed = True
        except Exception:
            self._placed = False

    def _dismiss(self, card: Any) -> None:
        if card in self._items:
            self._items.remove(card)
        try:
            card.destroy()
        except Exception:
            pass
        if self._items or not self._placed:
            return
        try:
            self._stack.place_forget()
        except Exception:
            pass
        self._placed = False
