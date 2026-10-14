"""Design tokens. No hex code appears anywhere outside this module.

Brief: a broadcast-graphics football product rendered as a dark, dense desktop
tool. Data density, not decoration, drives the layout.

Colour roles: green = the one primary action per screen and success; cyan
accent = navigation, selection, information and tonal secondary actions;
purple (``CODEX``) = AI/Codex affordances only; amber/red = warning/error.

Type: two voices. ``DISPLAY`` (Bahnschrift, the DIN face of football broadcast
graphics, bundled with Windows 10+) sets page titles and every rating numeral.
``FONT`` (Segoe UI) sets everything else; its figures are tabular, so numeric
table columns align without a monospace costume. ``MONO`` is for raw code, IDs
in error causes and Lua only.

The presenters emit *semantic tone names* (``ok``/``warn``/``error``/``info``/
``muted``); this module is the only place a tone becomes a colour. That
indirection is what lets the apply report say "this is a warning" without the
domain layer knowing what amber is.
"""

from __future__ import annotations

from typing import Any, Final

# ---- surfaces (elevation ladder: CTk has no shadows, so bg steps + 1px border)
BG: Final = "#0a0e14"
CHROME: Final = "#0c121a"       # header, sidebar, drawer, Changes bar
PANEL: Final = "#0f1620"
GRID_HEADER: Final = "#121a26"  # DataGrid header strip
CARD: Final = "#141d2a"
CARD_HOVER: Final = "#1a2534"
ROW_ALT: Final = "#0d1420"
BORDER: Final = "#1e2a3a"       # decorative hairline between panels
BORDER_STRONG: Final = "#5a6f8c"  # input and control outlines (>= 3:1, WCAG 1.4.11)
CHROME_LINE: Final = "#1a3a44"  # teal-tinted drawer edge
SKELETON: Final = "#1a2433"

# ---- semantics
ACCENT: Final = "#22d3ee"
SUCCESS: Final = "#22c55e"   # THE primary action colour — at most once per screen
WARNING: Final = "#f59e0b"
DANGER: Final = "#ef4444"
DANGER_TEXT: Final = "#f87171"  # red text on dark grounds
CODEX: Final = "#8b5cf6"    # AI / Codex affordances only (not a second primary)
TEXT: Final = "#e6edf5"
MUTED: Final = "#8b9bb0"
MUTED_DIM: Final = "#7a8aa0"  # quieter than MUTED, still 4.5:1 on cards
FOCUS: Final = ACCENT

# ---- position groups (spec 6.5), as text colours that hold 4.5:1 on cards
POS_GK: Final = "#facc15"
POS_DEF: Final = "#60a5fa"
POS_MID: Final = "#4ade80"
POS_ATT: Final = "#fb7185"

_POS_GROUPS: Final[dict[str, str]] = {
    "GK": POS_GK,
    **{p: POS_DEF for p in ("CB", "LB", "RB", "LWB", "RWB", "LCB", "RCB", "SW")},
    **{p: POS_MID for p in ("CDM", "CM", "CAM", "LM", "RM", "LCM", "RCM", "LDM", "RDM", "LAM", "RAM")},
    **{p: POS_ATT for p in ("LW", "RW", "CF", "ST", "LF", "RF", "LS", "RS")},
}


def pos_color(position: str) -> str:
    """Text colour for a position, by its group. The first listed position decides."""
    first = str(position or "").replace("/", ",").split(",")[0].strip().upper()
    return _POS_GROUPS.get(first, TEXT)


# ---- provenance (P3: never display a value you did not read)
PROV_READ: Final = TEXT
PROV_EDITED: Final = ACCENT
PROV_UNKNOWN: Final = MUTED_DIM

# ---- spacing / radius
SP1, SP2, SP3, SP4, SP5, SP6, SP7 = 4, 8, 12, 16, 24, 32, 48
R_XS, R_SM, R_MD, R_PILL = 4, 6, 10, 999

# ---- control heights
BTN_SM, BTN_MD, BTN_LG = 28, 32, 36

# ---- type
FONT = "Segoe UI"
DISPLAY = "Bahnschrift SemiBold"
DISPLAY_FALLBACK = "Segoe UI Semibold"
MONO = "Consolas"

#: The whole type scale, in pixels. Nothing in the UI uses a size outside it.
T_MICRO, T_CAPTION, T_SMALL, T_BODY, T_HEADING, T_HEADLINE, T_TITLE, T_SCORE = (
    10, 11, 12, 13, 15, 18, 22, 28,
)
TYPE_SCALE: Final = (T_MICRO, T_CAPTION, T_SMALL, T_BODY, T_HEADING, T_HEADLINE, T_TITLE, T_SCORE)

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
        "error": DANGER_TEXT,
        "info": ACCENT,
        "muted": MUTED,
    }.get(tone, MUTED)


# ---- OVR ramp: the number users read first, colour-coded everywhere
_OVR_BANDS: Final[tuple[tuple[int, str, str, str], ...]] = (
    (59, "Basic", "#3a4553", "#c9d4e2"),
    (69, "Bronze", "#7a4a22", "#ffd9b3"),
    (74, "Silver", "#5c6672", "#eef3f8"),
    (79, "Gold low", "#836520", "#ffeeb8"),
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


# ---- CustomTkinter theme ----------------------------------------------------


def ctk_theme() -> dict[str, dict[str, Any]]:
    """Token values for every CustomTkinter widget default.

    Without this, any widget a surface builds without explicit colours falls
    back to CustomTkinter's blue theme and Roboto, which is how option menus
    shipped in #1F6AA5 next to a cyan accent.
    """

    def both(value: Any) -> list[Any]:
        return [value, value]

    return {
        "CTk": {"fg_color": both(BG)},
        "CTkToplevel": {"fg_color": both(BG)},
        "CTkFrame": {
            "corner_radius": R_SM, "border_width": 0,
            "fg_color": both(PANEL), "top_fg_color": both(CARD), "border_color": both(BORDER),
        },
        "CTkButton": {
            "corner_radius": R_SM, "border_width": 0,
            "fg_color": both(CARD), "hover_color": both(CARD_HOVER),
            "border_color": both(BORDER_STRONG), "text_color": both(TEXT),
            "text_color_disabled": both(MUTED_DIM),
        },
        "CTkLabel": {"corner_radius": 0, "fg_color": "transparent", "text_color": both(TEXT)},
        "CTkEntry": {
            "corner_radius": R_SM, "border_width": 1,
            "fg_color": both(CARD), "border_color": both(BORDER_STRONG),
            "text_color": both(TEXT), "placeholder_text_color": both(MUTED),
        },
        "CTkCheckBox": {
            "corner_radius": R_XS, "border_width": 2,
            "fg_color": both(ACCENT), "border_color": both(BORDER_STRONG),
            "hover_color": both(ACCENT), "checkmark_color": both(BG),
            "text_color": both(TEXT), "text_color_disabled": both(MUTED_DIM),
        },
        "CTkSwitch": {
            "corner_radius": 1000, "border_width": 3, "button_length": 0,
            "fg_color": both(BORDER_STRONG), "progress_color": both(ACCENT),
            "button_color": both(TEXT), "button_hover_color": both(MUTED),
            "text_color": both(TEXT), "text_color_disabled": both(MUTED_DIM),
        },
        "CTkRadioButton": {
            "corner_radius": 1000, "border_width_checked": 6, "border_width_unchecked": 2,
            "fg_color": both(ACCENT), "border_color": both(BORDER_STRONG),
            "hover_color": both(ACCENT), "text_color": both(TEXT),
            "text_color_disabled": both(MUTED_DIM),
        },
        "CTkProgressBar": {
            "corner_radius": 1000, "border_width": 0,
            "fg_color": both(CARD_HOVER), "progress_color": both(ACCENT),
            "border_color": both(BORDER),
        },
        "CTkSlider": {
            "corner_radius": 1000, "button_corner_radius": 1000, "border_width": 6,
            "button_length": 0, "fg_color": both(CARD_HOVER), "progress_color": both(ACCENT),
            "button_color": both(TEXT), "button_hover_color": both(MUTED),
        },
        "CTkOptionMenu": {
            "corner_radius": R_SM,
            "fg_color": both(CARD), "button_color": both(CARD_HOVER),
            "button_hover_color": both(BORDER), "text_color": both(TEXT),
            "text_color_disabled": both(MUTED_DIM),
        },
        "CTkComboBox": {
            "corner_radius": R_SM, "border_width": 1,
            "fg_color": both(CARD), "border_color": both(BORDER_STRONG),
            "button_color": both(CARD_HOVER), "button_hover_color": both(BORDER),
            "text_color": both(TEXT), "text_color_disabled": both(MUTED_DIM),
        },
        "CTkScrollbar": {
            "corner_radius": 1000, "border_spacing": 4,
            "fg_color": "transparent", "button_color": both(BORDER),
            "button_hover_color": both(BORDER_STRONG),
        },
        "CTkSegmentedButton": {
            "corner_radius": R_SM, "border_width": 2,
            "fg_color": both(PANEL), "selected_color": both(CARD_HOVER),
            "selected_hover_color": both(CARD_HOVER), "unselected_color": both(PANEL),
            "unselected_hover_color": both(CARD), "text_color": both(TEXT),
            "text_color_disabled": both(MUTED_DIM),
        },
        "CTkTextbox": {
            "corner_radius": R_SM, "border_width": 1,
            "fg_color": both(CARD), "border_color": both(BORDER_STRONG),
            "text_color": both(TEXT), "scrollbar_button_color": both(BORDER),
            "scrollbar_button_hover_color": both(BORDER_STRONG),
        },
        "CTkScrollableFrame": {"label_fg_color": both(CARD)},
        "DropdownMenu": {
            "fg_color": both(CARD), "hover_color": both(CARD_HOVER), "text_color": both(TEXT),
        },
        "CTkFont": {
            "macOS": {"family": FONT, "size": T_BODY, "weight": "normal"},
            "Windows": {"family": FONT, "size": T_BODY, "weight": "normal"},
            "Linux": {"family": FONT, "size": T_BODY, "weight": "normal"},
        },
    }


def apply_ctk_theme() -> None:
    """Install :func:`ctk_theme` into CustomTkinter. Call before any widget exists."""
    try:
        import sys

        import customtkinter as ctk
    except ImportError:  # pragma: no cover
        return
    current = ctk.ThemeManager.theme
    for widget, values in ctk_theme().items():
        if widget == "CTkFont":
            platform = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")
            current.setdefault("CTkFont", {}).update(values[platform])
            continue
        current.setdefault(widget, {}).update(values)
