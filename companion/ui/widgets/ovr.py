"""The OVR chip and the OVR **delta** — the single most-read number in the app.

WHY this is its own module
--------------------------
§6.3 makes a specific claim: *"The delta form ``⟨86⟩ → ⟨91⟩`` colours each chip
by its own band, so an upgrade is visible as a colour jump, not just a number
change."* That is a real design decision with a real failure mode if it is
implemented casually — if both chips take the *after* colour (the obvious
shortcut, and what a single "delta chip" widget naturally does), the colour
jump disappears and the upgrade reads as two identically-gold numbers.

So the delta is modelled explicitly: :class:`OvrDelta` computes two independent
band lookups and exposes whether the *band* changed, not just the value. A
75→79 change (both "Gold low") and a 79→80 change (Gold low → Gold) are
different visual events and the model can tell them apart.

The value model is pure and has no Tk import path, so the band logic is tested
headlessly; only :func:`ovr_chip` and :func:`ovr_delta_chips` touch widgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import theme
from .primitives import ARROW, EM_DASH, ensure_ctk, mono_font, ui_font

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

#: (width, height, font size) per chip size.
_SIZES: dict[str, tuple[int, int, int]] = {
    "xs": (26, 20, 11),
    "sm": (32, 24, 12),
    "md": (40, 30, 15),
    "lg": (56, 42, 22),
}


def ovr_text(value: Any) -> str:
    """The digits, or an em dash when the value was never read (P3).

    ``0`` is a legitimate OVR-shaped value and must not be laundered into "—";
    only ``None`` means unknown.
    """
    if value is None:
        return EM_DASH
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return EM_DASH


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class OvrDelta:
    """A before/after OVR pair, with each side keeping its own band.

    ``before is None`` is the P3 case: we never read the game's value, so the
    delta cannot claim an improvement. :attr:`direction` reports ``"unknown"``
    rather than guessing, and :attr:`text` shows the em dash.
    """

    before: int | None
    after: int | None

    @property
    def known(self) -> bool:
        return self.before is not None and self.after is not None

    @property
    def changed(self) -> bool:
        return self.known and self.before != self.after

    @property
    def amount(self) -> int | None:
        if not self.known:
            return None
        return int(self.after) - int(self.before)  # type: ignore[arg-type]

    @property
    def direction(self) -> str:
        """``up`` | ``down`` | ``same`` | ``unknown`` — never a guess."""
        amount = self.amount
        if amount is None:
            return "unknown"
        if amount > 0:
            return "up"
        if amount < 0:
            return "down"
        return "same"

    @property
    def band_before(self) -> str:
        return theme.ovr_band(self.before)

    @property
    def band_after(self) -> str:
        return theme.ovr_band(self.after)

    @property
    def band_changed(self) -> bool:
        """True when the *colour* jumps, not merely the number."""
        return self.known and self.band_before != self.band_after

    @property
    def tone(self) -> str:
        return {"up": "ok", "down": "warn", "same": "muted"}.get(self.direction, "muted")

    @property
    def text(self) -> str:
        """``86 → 91`` — the form used in the diff and the Changes bar."""
        return f"{ovr_text(self.before)} {ARROW} {ovr_text(self.after)}"

    @property
    def signed(self) -> str:
        """``+5`` / ``-2`` / ``""`` — the compact form for a badge."""
        amount = self.amount
        if not amount:
            return ""
        return f"+{amount}" if amount > 0 else str(amount)


def ovr_delta(before: Any, after: Any) -> OvrDelta:
    """Build a delta from raw values of any shape."""
    return OvrDelta(before=_as_int(before), after=_as_int(after))


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


def ovr_chip(parent: Any, value: Any, *, size: str = "md", caption: str = "") -> Any:
    """One OVR value, filled and texted by its own band (§6.3).

    ``None`` renders in the unknown treatment, so a chip can honestly say "we
    have not read this player's rating" instead of showing a plausible number.
    """
    ensure_ctk()
    w, h, fs = _SIZES.get(size, _SIZES["md"])
    number = _as_int(value)
    fill, fg = theme.ovr_colors(number)
    frame = ctk.CTkFrame(
        parent,
        width=w,
        height=h,
        fg_color=fill,
        corner_radius=theme.R_XS,
        border_width=1,
        # Elite/Special get a bright ring; everything else a hairline.
        border_color=fg if (number is not None and number >= 90) else theme.BORDER,
    )
    frame.pack_propagate(False)
    ctk.CTkLabel(
        frame,
        text=ovr_text(number),
        font=mono_font(fs, bold=True),
        text_color=fg if number is not None else theme.MUTED_DIM,
    ).pack(expand=True)
    if caption:
        frame._caption = caption  # type: ignore[attr-defined]
    return frame


def ovr_delta_chips(
    parent: Any,
    before: Any,
    after: Any,
    *,
    size: str = "md",
    show_amount: bool = True,
) -> Any:
    """``⟨86⟩ → ⟨91⟩`` as two independently-coloured chips.

    Each chip calls ``theme.ovr_colors`` with **its own** value. That is the
    entire point of the widget; see the module docstring.
    """
    ensure_ctk()
    delta = ovr_delta(before, after)
    _w, _h, fs = _SIZES.get(size, _SIZES["md"])
    row = ctk.CTkFrame(parent, fg_color="transparent")

    ovr_chip(row, delta.before, size=size).pack(side="left")
    ctk.CTkLabel(
        row,
        text=ARROW,
        font=ui_font(max(11, fs - 2), bold=True),
        text_color=theme.MUTED,
        width=22,
    ).pack(side="left", padx=theme.SP1)
    ovr_chip(row, delta.after, size=size).pack(side="left")

    if show_amount and delta.signed:
        ctk.CTkLabel(
            row,
            text=delta.signed,
            font=ui_font(max(10, fs - 4), bold=True),
            text_color=theme.tone_fg(delta.tone),
        ).pack(side="left", padx=(theme.SP2, 0))

    row._delta = delta  # type: ignore[attr-defined]
    return row


def ovr_band_legend(parent: Any) -> Any:
    """The ramp, spelled out. Used by Settings and by design review.

    A5 says colour is never the only signal; a user who cannot separate "Gold"
    from "Gold high" needs somewhere the bands are named in words.
    """
    ensure_ctk()
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    for ceiling in (55, 65, 72, 77, 82, 87, 92, 97):
        cell = ctk.CTkFrame(frame, fg_color="transparent")
        cell.pack(side="left", padx=(0, theme.SP2))
        ovr_chip(cell, ceiling, size="xs").pack()
        ctk.CTkLabel(
            cell,
            text=theme.ovr_band(ceiling),
            font=ui_font(9),
            text_color=theme.MUTED_DIM,
        ).pack(pady=(2, 0))
    return frame
