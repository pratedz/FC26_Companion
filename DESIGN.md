---
name: FC26 Companion
description: A broadcast-graphics football tool for FC 26 Career Mode, rendered as a dark, dense desktop app.
colors:
  bg: "#0a0e14"
  chrome: "#0c121a"
  panel: "#0f1620"
  grid-header: "#121a26"
  card: "#141d2a"
  card-hover: "#1a2534"
  row-alt: "#0d1420"
  border: "#1e2a3a"
  border-strong: "#5a6f8c"
  accent: "#22d3ee"
  success: "#22c55e"
  warning: "#f59e0b"
  danger: "#ef4444"
  danger-text: "#f87171"
  codex: "#8b5cf6"
  text: "#e6edf5"
  muted: "#8b9bb0"
  muted-dim: "#7a8aa0"
  ovr-basic: "#3a4553"
  ovr-bronze: "#7a4a22"
  ovr-silver: "#5c6672"
  ovr-gold-low: "#836520"
  ovr-gold: "#b8901f"
  ovr-gold-high: "#e0b02a"
  ovr-elite: "#f0d264"
  ovr-special: "#0e7490"
typography:
  score:
    fontFamily: "Bahnschrift SemiBold, Segoe UI Semibold"
    fontSize: "28px"
  title:
    fontFamily: "Bahnschrift SemiBold, Segoe UI Semibold"
    fontSize: "22px"
  headline:
    fontFamily: "Bahnschrift SemiBold, Segoe UI Semibold"
    fontSize: "18px"
  heading:
    fontFamily: "Segoe UI"
    fontSize: "15px"
    fontWeight: 700
  body:
    fontFamily: "Segoe UI"
    fontSize: "13px"
  small:
    fontFamily: "Segoe UI"
    fontSize: "12px"
  caption:
    fontFamily: "Segoe UI"
    fontSize: "11px"
  micro:
    fontFamily: "Segoe UI"
    fontSize: "10px"
    fontWeight: 700
rounded:
  xs: "4px"
  sm: "6px"
  md: "10px"
  pill: "999px"
spacing:
  sp1: "4px"
  sp2: "8px"
  sp3: "12px"
  sp4: "16px"
  sp5: "24px"
  sp6: "32px"
  sp7: "48px"
components:
  button-primary:
    backgroundColor: "{colors.success}"
    textColor: "#04210f"
    rounded: "{rounded.sm}"
    height: "32px"
  button-accent:
    backgroundColor: "#083339"
    textColor: "{colors.accent}"
    rounded: "{rounded.sm}"
    height: "32px"
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    height: "32px"
  button-danger:
    backgroundColor: "#300e0e"
    textColor: "{colors.danger-text}"
    rounded: "{rounded.sm}"
    height: "32px"
  entry:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    height: "32px"
  grid-row:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    height: "30px"
  ovr-chip:
    typography: "{typography.body}"
    rounded: "{rounded.xs}"
    width: "34px"
---

# FC26 Companion design system

Source of truth in code: `companion/ui/theme.py` (every colour, size and the
CustomTkinter theme), `companion/ui/widgets/primitives.py` (buttons, entries,
search box, pills, headers), `companion/ui/widgets/table.py` (the data grid),
`companion/ui/nav.py` (sidebar and segmented control) and `assets/icons/`.
`tools/redesign_checks.py` enforces the rules marked *(checked)* below.

## Overview

**The broadcast desk.** The app sits beside FC 26, usually in the evening, and
reads like the graphics package of a football broadcast: a near-black ground, a
cyan accent, and rating numerals in the DIN face of football lower-thirds. Data
density drives the layout. The signature is the scoreboard: every rating is a
FUT-tier coloured chip set in Bahnschrift, so an upgrade reads as a colour jump
before the number is read. (North star chosen by the builder; the owner asked
not to be interviewed.)

## Colors

Restrained palette: tinted near-black neutrals plus one accent, with state
colours only where they mean something.

- Ground ladder, darkest to lightest: `bg` app, `chrome` header/sidebar/drawer,
  `panel`, `card`, `card-hover`. CTk has no shadows, so depth is one step up this
  ladder plus a 1 px `border`.
- `accent` cyan: selection, focus, information, tonal secondary actions, active
  navigation icon.
- `success` green: the one primary action per screen, and success states.
- `warning` amber and `danger`/`danger-text` red: caution and failure. Red text
  uses `danger-text` for contrast on dark grounds.
- `codex` purple: AI and Codex affordances only.
- OVR ramp (`ovr-*`): eight FUT tiers, each with its own text colour, used for
  every rating in the app.
- Every text and chip pairing is at least 4.5:1, borders on controls at least
  3:1 *(checked)*. No colour literal exists outside `theme.py` *(checked)*.

## Typography

Two voices, one scale of eight steps *(checked)*: 10, 11, 12, 13, 15, 18, 22, 28 px.

- Display voice, Bahnschrift SemiBold (falls back to Segoe UI Semibold): page
  titles (22), headlines and empty-state titles (18), stat tiles (28), and every
  OVR chip.
- UI voice, Segoe UI: section headings 15 bold or 13 bold inside panels, body 13,
  secondary 12, captions 11, table headers and chips 10 bold. Segoe UI figures are
  tabular, so numeric columns align without a monospace font.
- Consolas only for raw Lua, worker messages and error causes.
- Section titles are headings in `text` colour. No accent kickers, no all-caps
  micro labels above headings *(checked)*.

## Layout

- Window minimum 1040 x 720, default 1280 x 820. Sidebar 200 px; header 52 px;
  pages padded 16 px.
- Page anatomy: page header (title, live context line, actions on the right),
  then status strips only when something needs attention, then the work area,
  then a bottom action bar for selection actions.
- The Activity drawer is 360 px. It sits beside the page when the page keeps at
  least 760 px, otherwise it overlays the page's right edge.
- Grids re-lay out on every width change. Optional columns carry a `priority`
  and drop out narrowest-first; the stretch column carries a `max_width` so
  ratings stay near names on wide windows.
- Side panels (Club's selected player) collapse below 700 px of board width.
- Spacing scale 4/8/12/16/24/32/48. Panels pad 16 horizontally; dense rows 30 px.

## Elevation & Depth

Flat, tonal. Depth comes from stepping up the ground ladder and a hairline
border, never from shadows or glows. The only accent line is the 2 px rule above
the Changes bar, which marks that changes are staged.

## Shapes

Radius 4 for chips and OVR tiles, 6 for buttons, entries and rows, 10 for panels,
pills fully round. One corner language everywhere.

## Components

- **Buttons** (`button(kind=...)`): `primary` solid green, at most one per screen
  *(checked by `tools/check_layout.py`)*; `accent` tonal cyan for the main
  secondary action; `secondary` card fill; `ghost` outline; `danger` tonal red.
  Optional pack icon drawn in the label colour. Disabled buttons always carry a
  reason tooltip and render muted, icon included.
- **Entries** (`entry`, `search_box`): placeholders always visible, even with a
  bound variable *(checked)*. The search box has a magnifier, a clear control
  while it has text, Enter to submit and Esc to clear. Every entry shows a 2 px
  accent focus ring.
- **Data grid**: sortable headers (sorted column in `text`), OVR columns as
  centred tier chips, drawn checkboxes, cursor row tinted so the detail panel's
  subject is visible, accent focus ring, arrow/Space/Enter/Ctrl+A keys, optional
  empty-state text.
- **Navigation**: sidebar rows with pack icons tinted muted or accent, Settings
  pinned at the bottom, Ctrl+1 to Ctrl+6. In-page modes use the one segmented
  control (`segmented_rail`).
- **Chrome**: connection chip (drawn dot plus word: Connected, Live, Waiting,
  Stalled, Offline), loaded-squad context, selected-player chip, Ctrl K palette,
  Activity with in-flight count.
- **States**: empty states name the next action as a control when one exists;
  errors persist inline with the raw cause; loading uses a static row, never an
  animation loop.
- **Toasts**: tone icon, dismiss control, above the page's bottom bar; errors stay
  until dismissed.

## Do's and Don'ts

- Do put live context in a page subtitle (club, counts, freshness), not the
  page's purpose.
- Do say a queued change is queued, with its trigger.
- Don't add a second solid primary; make the extra action tonal.
- Don't use Unicode glyphs as icons; add or author an icon in the pack's 4 px
  stroke style (`assets/icons/medical.png` was authored this way) *(checked)*.
- Don't put a colored edge bar on a card, row or toast to signal state.
- Don't animate skeletons or loops; the v1 freeze came from that.
