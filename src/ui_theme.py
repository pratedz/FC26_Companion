"""Design tokens for LE Companion — dark sports-tech (refined hierarchy).

Spacing rhythm: 4 / 8 / 12 / 16 / 24 / 32 (tighten within groups, loosen between).
Primary action = solid green success; accent cyan for secondary/info; ghost for tertiary.
"""

from __future__ import annotations

# ── Spacing scale (px) ──────────────────────────────────────────────
SP1 = 4
SP2 = 8
SP3 = 12
SP4 = 16
SP5 = 24
SP6 = 32
SP7 = 48  # section breaks

# ── Surfaces ────────────────────────────────────────────────────────
BG = "#0a0e14"
PANEL = "#111827"
CARD = "#182230"
CARD_HOVER = "#1e2a3a"
BORDER = "#243044"
BORDER_SOFT = "#1a2433"
LIST_BG = "#0f1620"
LIST_FG = "#e2e8f0"
LIST_SEL = "#0e7490"
LIST_SEL_FG = "#ffffff"
EMPTY_BG = "#0c1219"
ROW_ALT = "#0d1420"  # optional zebra for dense lists
SKELETON = "#1a2433"
ELEVATION = (BG, PANEL, CARD, CARD_HOVER)  # surface ladder
SHADOW_NONE = True  # CTk has no real elevation; use border + bg steps only

# ── Accents & semantics ─────────────────────────────────────────────
ACCENT = "#22d3ee"
ACCENT_HOVER = "#67e8f9"
ACCENT_DIM = "#0e7490"
SUCCESS = "#34d399"
SUCCESS_HOVER = "#6ee7b7"
WARNING = "#fbbf24"
DANGER = "#f87171"
DANGER_HOVER = "#fca5a5"
INFO = "#38bdf8"
FOCUS_RING = ACCENT  # keyboard focus border where settable
BADGE_BG = "#0e749033"  # soft cyan wash

# ── Status pills ────────────────────────────────────────────────────
PILL_LIVE_BG = "#052e1c"
PILL_LIVE_BORDER = SUCCESS
PILL_OFF_BG = "#422006"
PILL_OFF_BORDER = WARNING
PILL_BUSY_BG = "#083344"

# ── Buttons ─────────────────────────────────────────────────────────
SECONDARY_BTN = "#243044"
SECONDARY_BTN_HOVER = CARD_HOVER

# ── Text ────────────────────────────────────────────────────────────
TEXT = "#f1f5f9"
MUTED = "#94a3b8"
MUTED_DIM = "#64748b"

# ── Typography ──────────────────────────────────────────────────────
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"
SIZE_TITLE = 20
SIZE_SECTION = 14
SIZE_BODY = 13
SIZE_META = 11
SIZE_TINY = 10

# ── Radii & control sizes ───────────────────────────────────────────
RADIUS = 12
RADIUS_SM = 8
RADIUS_XS = 6
BTN_H = 36
BTN_H_SM = 30
ENTRY_H = 36
ENTRY_H_SM = 28

# ── Chrome ──────────────────────────────────────────────────────────
HEADER_H = 64
OPS_H = 40
DOCK_H_COMPACT = 64
DOCK_H_EXPANDED = 78
