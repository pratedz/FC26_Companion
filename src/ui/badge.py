"""Badge and status-pill primitives for LE Companion.

Small capsule chips (OVR, year, category) and header-style LIVE / OFF pills.
Colors come from ``ui_theme`` tokens; no stray hex in call sites.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from .. import ui_theme as theme
from ..ui_theme import (
    ACCENT,
    ACCENT_DIM,
    BORDER,
    CARD,
    DANGER,
    FONT_UI,
    MUTED,
    MUTED_DIM,
    SIZE_TINY,
    SP1,
    SP2,
    SUCCESS,
    WARNING,
)

# Optional phase-1 tokens (present after token pass; getattr for safety)
_PILL_LIVE_BG = getattr(theme, "PILL_LIVE_BG", None)
_PILL_LIVE_BORDER = getattr(theme, "PILL_LIVE_BORDER", None)
_PILL_OFF_BG = getattr(theme, "PILL_OFF_BG", None)
_PILL_OFF_BORDER = getattr(theme, "PILL_OFF_BORDER", None)
_PILL_BUSY_BG = getattr(theme, "PILL_BUSY_BG", None)
_BADGE_BG = getattr(theme, "BADGE_BG", None)
_RADIUS_PILL = 20

# Soft solid washes (CTk wants #RRGGBB; alpha hex is unreliable)
_SOFT_ACCENT = "#0c2a32"
_SOFT_SUCCESS = "#052e1c"
_SOFT_WARNING = "#422006"
_SOFT_DANGER = "#3f1219"
_SOFT_MUTED = CARD
_SOFT_INFO = "#083344"

__all__ = ["badge", "status_pill"]


def _tone_style(tone: str) -> Tuple[str, str, str]:
    """Return (fg_color, border_color, text_color) for a badge tone."""
    t = (tone or "accent").strip().lower()
    styles: Dict[str, Tuple[str, str, str]] = {
        "accent": (_BADGE_BG if _is_solid_hex(_BADGE_BG) else _SOFT_ACCENT, ACCENT_DIM, ACCENT),
        "success": (_SOFT_SUCCESS, SUCCESS, SUCCESS),
        "warning": (_SOFT_WARNING, WARNING, WARNING),
        "danger": (_SOFT_DANGER, DANGER, DANGER),
        "muted": (_SOFT_MUTED, BORDER, MUTED),
        "live": (
            _PILL_LIVE_BG or _SOFT_SUCCESS,
            _PILL_LIVE_BORDER or SUCCESS,
            SUCCESS,
        ),
        "off": (
            _PILL_OFF_BG or _SOFT_WARNING,
            _PILL_OFF_BORDER or WARNING,
            WARNING,
        ),
    }
    return styles.get(t, styles["accent"])


def _is_solid_hex(value: Optional[str]) -> bool:
    """True if value looks like a solid #RGB / #RRGGBB (CTk-safe)."""
    if not value or not isinstance(value, str):
        return False
    s = value.strip()
    if not s.startswith("#"):
        return False
    body = s[1:]
    return len(body) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in body)


def badge(parent: Any, text: str, *, tone: str = "accent") -> Any:
    """Small capsule: soft background + bold tiny text.

    Tones: ``accent``, ``success``, ``warning``, ``danger``, ``muted``,
    ``live``, ``off``.
    """
    if ctk is None:
        raise RuntimeError(
            "customtkinter is required.\nInstall: python -m pip install customtkinter pillow"
        )
    bg, border, fg = _tone_style(tone)
    frame = ctk.CTkFrame(
        parent,
        fg_color=bg,
        corner_radius=_RADIUS_PILL,
        border_width=1,
        border_color=border,
    )
    ctk.CTkLabel(
        frame,
        text=str(text or "").strip() or "—",
        font=ctk.CTkFont(family=FONT_UI, size=SIZE_TINY, weight="bold"),
        text_color=fg,
    ).pack(padx=SP2, pady=SP1)
    return frame


def _status_style(state: str) -> Tuple[str, str, str, str, Optional[str]]:
    """Return (bg, border, text_color, label, icon_name) for a status state."""
    s = (state or "off").strip().lower()
    # Prefer dedicated pill tokens when phase-1 landed; else semantic accents.
    if s in ("live", "on", "ok", "online"):
        return (
            _PILL_LIVE_BG or CARD,
            _PILL_LIVE_BORDER or SUCCESS,
            SUCCESS,
            "LIVE",
            "status_online",
        )
    if s in ("off", "offline", "idle_worker", "worker_off"):
        return (
            _PILL_OFF_BG or CARD,
            _PILL_OFF_BORDER or WARNING,
            WARNING,
            "WORKER OFF",
            "status_offline",
        )
    if s in ("busy", "applying", "working"):
        return (
            _PILL_BUSY_BG or _SOFT_INFO,
            ACCENT,
            ACCENT,
            "APPLYING",
            None,
        )
    if s in ("error", "err", "fail", "failed"):
        return (
            _SOFT_DANGER,
            DANGER,
            DANGER,
            "ERROR",
            None,
        )
    # Unknown → muted off-like
    return (CARD, BORDER, MUTED_DIM, str(state).upper()[:16] or "—", None)


def status_pill(parent: Any, state: str) -> Any:
    """Header-style status pill for LIVE / OFF / BUSY / ERROR.

    Uses ``PILL_*`` tokens from ``ui_theme`` when present; otherwise
    SUCCESS / WARNING / DANGER / ACCENT. Optional ``status_online`` /
    ``status_offline`` icons via ``icons.ctk_icon`` when available.
    """
    if ctk is None:
        raise RuntimeError(
            "customtkinter is required.\nInstall: python -m pip install customtkinter pillow"
        )
    bg, border, fg, label, icon_name = _status_style(state)
    frame = ctk.CTkFrame(
        parent,
        fg_color=bg,
        corner_radius=_RADIUS_PILL,
        border_width=2,
        border_color=border,
    )
    row = ctk.CTkFrame(frame, fg_color="transparent")
    row.pack(padx=SP2 + 2, pady=SP1 + 2)

    img = None
    if icon_name:
        try:
            from ..icons import ctk_icon

            img = ctk_icon(icon_name, 14)
        except Exception:
            img = None

    label_kw: Dict[str, Any] = {
        "text": f"  {label}  " if img is None else f" {label} ",
        "font": ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
        "text_color": fg,
    }
    if img is not None:
        label_kw["image"] = img
        label_kw["compound"] = "left"

    ctk.CTkLabel(row, **label_kw).pack(side="left")
    # Keep references so CTkImage is not GC'd
    frame._badge_icon = img  # type: ignore[attr-defined]
    frame._badge_state = (state or "").strip().lower()  # type: ignore[attr-defined]
    frame._badge_label_text = label  # type: ignore[attr-defined]
    return frame
