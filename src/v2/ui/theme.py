"""v2 design tokens.

Single source of truth for every colour, space and radius in the v2 UI. The
rule from the UX spec is that no hex value may appear outside this module —
v1 drifted because tabs hand-rolled their own colours, and the result was nine
equally-loud primary buttons on one screen with no visual hierarchy.

Two token groups are load-bearing rather than decorative:

* ``SUCCESS`` is *the* primary action colour and is allowed at most once per
  screen. If a screenshot shows two green fills, that screen is wrong.
* The provenance colours implement "never display a value you did not read".
  A field the app never read renders in ``PROV_UNKNOWN`` as an em dash and is
  structurally excluded from any write — v1 showed blank boxes that looked
  populated and then silently wrote three fields into a career save.
"""

from __future__ import annotations

from typing import Tuple

# ── surfaces ─────────────────────────────────────────────────────────
# CustomTkinter has no shadows, so elevation is a background step plus a 1px
# border rather than a drop shadow.
BG = "#0a0e14"
PANEL = "#0f1620"
CARD = "#141d2a"
CARD_HOVER = "#1a2534"
ROW_ALT = "#0d1420"
BORDER = "#1e2a3a"
SKELETON = "#1a2433"

# ── semantics ────────────────────────────────────────────────────────
ACCENT = "#22d3ee"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
DANGER = "#ef4444"
TEXT = "#e6edf5"
MUTED = "#8b9bb0"
MUTED_DIM = "#5b6a7d"

# ── provenance (see module docstring) ────────────────────────────────
PROV_READ = TEXT
PROV_EDITED = ACCENT
PROV_UNKNOWN = MUTED_DIM
UNKNOWN_GLYPH = "—"

# ── spacing / radius ─────────────────────────────────────────────────
SP1, SP2, SP3, SP4, SP5, SP6, SP7 = 4, 8, 12, 16, 24, 32, 48
R_XS, R_SM, R_MD, R_PILL = 4, 6, 10, 999

# ── typography ───────────────────────────────────────────────────────
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"
FS_XS, FS_SM, FS_MD, FS_LG, FS_XL, FS_XXL = 10, 11, 12, 14, 18, 24

# ── chrome metrics ───────────────────────────────────────────────────
CONNECTION_BAR_H = 40
NAV_W = 172
DRAWER_W = 380
ROW_H = 30
AVATAR_GRID = 24
AVATAR_HEADER = 96
AVATAR_CARD = 160


# ── OVR ramp ─────────────────────────────────────────────────────────
# OVR is the first number a player reads, so it is colour-coded everywhere it
# appears. The delta form colours each chip by its own band, which makes an
# upgrade read as a colour jump rather than a number that got bigger.
_OVR_BANDS: Tuple[Tuple[int, str, str, str], ...] = (
    (59, "Basic", "#3a4553", "#c9d4e2"),
    (69, "Bronze", "#7a4a22", "#ffd9b3"),
    (74, "Silver", "#5c6672", "#eef3f8"),
    (79, "Gold low", "#8a6b1f", "#ffeeb8"),
    (84, "Gold", "#b8901f", "#1a1200"),
    (89, "Gold high", "#e0b02a", "#1a1200"),
    (94, "Elite", "#f0d264", "#1a1200"),
    (99, "Special", "#0e7490", "#e8f4ff"),
)


def ovr_band(value: object) -> Tuple[str, str, str]:
    """(name, fill, text) for an OVR. Unknown values fall back to Basic."""
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ("Unknown", SKELETON, MUTED_DIM)
    for ceiling, name, fill, text in _OVR_BANDS:
        if v <= ceiling:
            return (name, fill, text)
    return _OVR_BANDS[-1][1:]


def ovr_fill(value: object) -> str:
    return ovr_band(value)[1]


def ovr_text(value: object) -> str:
    return ovr_band(value)[2]


# ── position colours ─────────────────────────────────────────────────
POSITION_GROUPS = {
    "GK": ("GK",),
    "DEF": ("CB", "LB", "RB", "LWB", "RWB", "RCB", "LCB", "SW"),
    "MID": ("CDM", "CM", "CAM", "LM", "RM", "LDM", "RDM", "LCM", "RCM", "LAM", "RAM"),
    "ATT": ("LW", "RW", "CF", "ST", "LF", "RF", "LS", "RS"),
}

_POSITION_COLOURS = {
    "GK": ("#eab308", "#1a1200"),
    "DEF": ("#3b82f6", "#f0f6ff"),
    "MID": ("#22c55e", "#04240f"),
    "ATT": ("#f43f5e", "#fff0f3"),
}

# LE stores positions as ids 0-27, not names.
POSITION_NAMES = (
    "GK", "SW", "RWB", "RB", "RCB", "CB", "LCB", "LB", "LWB",
    "RDM", "CDM", "LDM", "RM", "RCM", "CM", "LCM", "LM",
    "RAM", "CAM", "LAM", "RF", "CF", "LF", "RW", "RS", "ST", "LS", "LW",
)


def position_name(pos: object) -> str:
    """Accepts an LE position id or a name; returns a display name."""
    if isinstance(pos, str) and pos.strip():
        return pos.strip().upper()
    try:
        idx = int(pos)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if 0 <= idx < len(POSITION_NAMES):
        return POSITION_NAMES[idx]
    return "?"


def position_group(pos: object) -> str:
    name = position_name(pos)
    for group, members in POSITION_GROUPS.items():
        if name in members:
            return group
    return "MID"


def position_colours(pos: object) -> Tuple[str, str]:
    """(fill, text) for a position chip."""
    return _POSITION_COLOURS.get(position_group(pos), _POSITION_COLOURS["MID"])


# ── button tones ─────────────────────────────────────────────────────
# `primary` is deliberately the only filled-green tone. Anything that is not
# the single most important action on a screen must use another tone.
BUTTON_TONES = {
    "primary": {"fg": SUCCESS, "hover": "#16a34a", "text": "#04240f", "border": SUCCESS},
    "accent": {"fg": ACCENT, "hover": "#0891b2", "text": "#04242b", "border": ACCENT},
    "secondary": {"fg": CARD, "hover": CARD_HOVER, "text": TEXT, "border": BORDER},
    "ghost": {"fg": "transparent", "hover": CARD, "text": MUTED, "border": "transparent"},
    "danger": {"fg": "transparent", "hover": "#3a1a1e", "text": DANGER, "border": DANGER},
}


# ── connection states ────────────────────────────────────────────────
# v1 conflated "worker loaded", "worker armed" and "events are flowing" into a
# single 90-second heartbeat, so a healthy armed worker displayed as OFF and
# every apply was hard-blocked. These five states keep them distinct.
CONNECTION_STATES = {
    "live": (SUCCESS, "LIVE", "Worker armed, game responding"),
    "armed": (ACCENT, "ARMED", "Worker loaded — runs at the next Career Mode event"),
    "waiting": (WARNING, "WAITING", "Game not running, or Career Mode not loaded"),
    "setup": (MUTED, "SETUP", "Worker not installed yet"),
    "error": (DANGER, "ERROR", "Something is wrong — open the Activity drawer"),
}


def connection_colour(state: str) -> str:
    return CONNECTION_STATES.get(state, CONNECTION_STATES["setup"])[0]


def connection_label(state: str) -> str:
    return CONNECTION_STATES.get(state, CONNECTION_STATES["setup"])[1]


def connection_hint(state: str) -> str:
    return CONNECTION_STATES.get(state, CONNECTION_STATES["setup"])[2]
