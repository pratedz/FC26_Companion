"""Design tokens. No hex code appears anywhere outside this module.

Brief: a broadcast-graphics football product rendered as a dark, dense desktop
tool. Data density, not decoration, drives the layout.

Colour roles: green = ready/safe/live primary actions; blue/cyan accent =
navigation/info; purple (``CODEX``) = AI/Codex affordances only; amber/red =
warning/error.

The presenters emit *semantic tone names* (``ok``/``warn``/``error``/``info``/
``muted``); this module is the only place a tone becomes a colour. That
indirection is what lets the apply report say "this is a warning" without the
domain layer knowing what amber is.
"""

from __future__ import annotations

from typing import Final

# ---- surfaces (elevation ladder: CTk has no shadows, so bg steps + 1px border)
BG: Final = "#0a0e14"
CHROME: Final = "#0c121a"       # header, drawer, Changes, Sign footer
PANEL: Final = "#0f1620"
GRID_HEADER: Final = "#121a26"  # DataGrid ticker strip
CARD: Final = "#141d2a"
CARD_HOVER: Final = "#1a2534"
ROW_ALT: Final = "#0d1420"
BORDER: Final = "#1e2a3a"
CHROME_LINE: Final = "#1a3a44"  # teal-tinted drawer edge
SKELETON: Final = "#1a2433"

# ---- semantics
ACCENT: Final = "#22d3ee"
SUCCESS: Final = "#22c55e"   # THE primary action colour — at most once per screen
WARNING: Final = "#f59e0b"
DANGER: Final = "#ef4444"
CODEX: Final = "#8b5cf6"    # AI / Codex affordances only (not a second primary)
TEXT: Final = "#e6edf5"
MUTED: Final = "#8b9bb0"
MUTED_DIM: Final = "#5b6a7d"

# ---- provenance (P3: never display a value you did not read)
PROV_READ: Final = TEXT
PROV_EDITED: Final = ACCENT
PROV_UNKNOWN: Final = MUTED_DIM

# ---- spacing / radius
SP1, SP2, SP3, SP4, SP5, SP6, SP7 = 4, 8, 12, 16, 24, 32, 48
R_XS, R_SM, R_MD, R_PILL = 4, 6, 10, 999

# ---- control heights
BTN_SM, BTN_MD, BTN_LG = 28, 32, 36

FONT = "Segoe UI"
MONO = "Consolas"

# ---- tone -> (fill, text)
_TONES: Final[dict[str, tuple[str, str]]] = {
    "ok": (SUCCESS, "#04210f"),
    "warn": (WARNING, "#2a1a00"),
    "error": (DANGER, "#2a0808"),
    "info": (ACCENT, "#04222a"),
    "muted": (BORDER, MUTED),
}


def tone_colors(tone: str) -> tuple[str, str]:
    """(fill, text) for a presenter tone name."""
    return _TONES.get(tone, _TONES["muted"])


def tone_fg(tone: str) -> str:
    """Foreground-only colour, for text that carries the tone itself."""
    return {
        "ok": SUCCESS,
        "warn": WARNING,
        "error": DANGER,
        "info": ACCENT,
        "muted": MUTED,
    }.get(tone, MUTED)


# ---- OVR ramp: the number users read first, colour-coded everywhere
_OVR_BANDS: Final[tuple[tuple[int, str, str, str], ...]] = (
    (59, "Basic", "#3a4553", "#c9d4e2"),
    (69, "Bronze", "#7a4a22", "#ffd9b3"),
    (74, "Silver", "#5c6672", "#eef3f8"),
    (79, "Gold low", "#8a6b1f", "#ffeeb8"),
    (84, "Gold", "#b8901f", "#1a1200"),
    (89, "Gold high", "#e0b02a", "#1a1200"),
    (94, "Elite", "#f0d264", "#1a1200"),
    (99, "Special", "#0e7490", "#e8f4ff"),
)


def ovr_colors(ovr: int | None) -> tuple[str, str]:
    """(fill, text) for an OVR. An upgrade shows as a colour jump, not just digits."""
    if ovr is None:
        return (BORDER, MUTED_DIM)
    for ceiling, _name, fill, text in _OVR_BANDS:
        if ovr <= ceiling:
            return (fill, text)
    return _OVR_BANDS[-1][2], _OVR_BANDS[-1][3]


def ovr_band(ovr: int | None) -> str:
    if ovr is None:
        return "Unknown"
    for ceiling, name, _f, _t in _OVR_BANDS:
        if ovr <= ceiling:
            return name
    return _OVR_BANDS[-1][1]
