# FC26 Companion 2.11.0 UI redesign: report

Date: 2026-10-14. Source: `FC26_Companion_src`, uncommitted on `main` (no commit was
requested). Deployed to `Desktop\Fc26 LE\FC26_Companion`; the previous build is in
`backups\companion_before_2.11.0`.

## 1. Product understanding

A Windows desktop companion (Python + CustomTkinter) for FC 26 Career Mode with Live
Editor. It reads the live squad, edits one player (card build or AI), signs cards from
a 238,000-card library, plans several players with AI, heals injuries, prepares the
squad for matchday, and tracks every queued job. Changes go through a queue to a Lua
worker inside Live Editor and are only called "applied" when the worker returns proof.
One power user plays it beside the game, in long evening sessions. See `PRODUCT.md`.

## 2. Initial audit

Twenty findings, ranked in `docs/redesign/AUDIT.md`. The ones that mattered most:

- Grid columns never re-laid out: ages clipped to "1"/"2" at the default size, every
  column but NAME vanished with the Activity drawer open, and offsets were double-scaled
  at UI scale above 1.0.
- Grid keyboard navigation never worked (keys were bound to CTk's inner canvas while
  focus went to the frame).
- Search boxes rendered blank: CTkEntry hides its placeholder whenever a variable is bound.
- Opening Activity and then switching page blanked the drawer.
- Club spent 80 px on four "all fine" cards; duplicate loud buttons; Unicode glyph icons;
  CTk's default blue in dropdowns; 17 font sizes; accent kicker labels; Sign showed
  three solid accent/green fills at once and a CTA that did not fix its empty state.

## 3. Design strategy

Established-world refinement in Operate mode, not a new identity: the dark broadcast
football look fits a tool used beside the game at night. Signature: the scoreboard.
Titles and every rating use Bahnschrift (the DIN face of football graphics, bundled
with Windows), on the existing FUT-tier OVR ramp. Rules: one solid primary per screen,
status then data then actions, empty states name their fix, icons from the in-house
pack, headings instead of kickers, layouts that adapt to the minimum window.
See `DESIGN.md`.

## 4. Implemented changes

- **Design system**: `theme.py` gains an 8-step type scale, display/num fonts,
  `BORDER_STRONG`, `FOCUS`, `DANGER_TEXT`, accessible `MUTED_DIM`, position-group
  colours, and a CustomTkinter theme built from tokens (no more default blue/Roboto).
- **Widget kit** (`primitives.py`): tonal `accent`/`danger` buttons, icons in buttons,
  `restyle_button`, `entry` and `search_box` with working placeholders, a global accent
  focus ring on every entry, `section_title`, display-face page headers, status pill,
  window branding (app icon + dark title bar on every dialog).
- **Grid** (`table.py`): re-layout on resize, scale-aware geometry, column `priority`
  (optional columns drop first) and `max_width`, centred OVR chips, position colours,
  drawn checkboxes, visible cursor row, focus ring, working keys, empty-state text,
  fewer repaints on scroll.
- **Shell**: header with connection chip, loaded-squad context, player chip only when
  a player is chosen, Ctrl K and Activity buttons; sidebar with pack icons, Settings
  pinned at the bottom, version; Ctrl+1 to Ctrl+6, Ctrl+, ; rebuilt palette (arrow
  keys, shortcuts, icons); responsive drawer (beside the page when it keeps 760 px,
  overlay otherwise); drawer no longer blanks on page switch; toasts with tone icons.
- **Club**: club as page title with average OVR; readiness strip only for failing
  steps, with Check connection; real search box; detail card with tier chips and
  collapse below 700 px; one action bar (Plan / Same change for all / Release, release
  tonal red); legible column set with priorities.
- **Player**: picker shares the Club grid style; workstation identity header with
  portrait tile and live OVR/POT deltas; one draft strip (count + Discard); unified
  segmented mode switch; card picker with search box, disabled Stage until a card is
  chosen, Back to results, empty-list hint; AI panel without nested cards and a busy
  state on Propose.
- **Sign**: segmented discovery tabs, search box, one style for active filters,
  empty state whose button fixes the cause (Show all faces), Checkout as the primary,
  clearer bag and readiness copy, result rows with pack icons and honest blocked state.
- **Also redesigned**: Injuries (header actions, one primary that moves from Scan to
  Heal, real empty state), Matchday (no kicker, scoreboard target tiles), Library
  (plain copy, search box, priorities, raw position ids mapped to names), Activity,
  Settings (no clipping, shortcuts listed), Review sheet (sentence-case groups, no
  empty headers, a real empty state instead of a disabled Apply), planner title.
- **Icon**: `assets/icons/medical.png`, authored in the pack's 4 px stroke style.

## 5. UX improvements

Squad data is readable at every size; keyboard users can drive grids, switch pages and
reach any command; every search box says what it searches; the user always sees which
single action is primary; the Review step is stated once, where it applies; blocked
actions say why; the drawer never squeezes a page into uselessness.

## 6. Design system

`DESIGN.md` (tokens in front matter, rules in prose). Enforced by
`tools/redesign_checks.py`: no colour literal outside `theme.py`, WCAG AA on 66 token
pairings, no hidden placeholders, no glyph icons in chrome, no kickers, every font size
on the scale.

## 7. Validation

All measured on 2026-10-14:

- `docs/redesign/GATES.md`: G1 to G9 met by `gate-check` (hex, contrast 66 pairs,
  placeholders, glyphs, kickers, fonts, layout, tests, docs).
- Layout gate (`tools/check_layout.py`): every surface at 1040x720, 1280x820 and
  1600x960, drawer open and closed, plus four scenarios. No clipped button, no column
  past its grid, at most one solid primary per surface.
- Tests: 1,548 passed, 37 failed, 178 skipped. The 37 failures are the pre-existing
  baseline (`docs/redesign/baseline_failures.txt`: missing recovered files, Lua
  contract strings, worker version); none is new. Baseline was 1,547 passed.
- GUI-only tests (`COMPANION_ALLOW_GUI_TESTS=1`): pass except
  `test_datagrid_scroll_configure_count_real_widgets`, whose synthetic wheel event
  also fails on the original code in this environment.
- Keyboard checked in the real UI: Down moves the grid cursor, Space ticks, Ctrl+A
  ticks all 39, Ctrl+3 opens Sign, the search box shows its hint and focus ring.
- Fresh-eyes review (general-purpose agent running Impeccable's finish-review brief;
  the shipped reviewer agent is not installed): round 1 `fix`, 10 findings; round 2
  `fix` with 5 resolved, 3 partial, 2 unresolved (see Limitations).
- Deployed exe launched, window responding, dark title bar and icon visible,
  0 errors in `logs/companion.jsonl`.

## 8. Before / after

`docs/redesign/screens/before/` and `after/` hold matching captures of the real app
with the real Real Madrid save (rendered by `tools/capture_ui.py`, which never touches
the game or the live queue). `after/deployed_exe_first_launch.png` is the shipped exe.

## 9. Remaining limitations

- The connection was simulated for captures; the live Career flow (read squad, apply,
  sign, heal) was not exercised against FC 26 in this session. Test it in your save.
- Reviewer items left open: POS shows coloured text, not the spec's filled chips;
  Injuries' empty state relies on the header Scan button; tooltips carry disabled
  reasons (not visible in screenshots); the Sign repair button keeps its accurate
  label "Repair when FC + LE close"; Review and Changes bar were not recaptured from
  one draft (both read the same staged fields, by code).
- Not yet built: player portraits and club crests in grids, an OVR chip in planner rows.
- Running the test suite rewrites `bridge/RUN_BRIDGE_ONCE.lua` (pre-existing test side
  effect); restore it with `git checkout -- bridge/RUN_BRIDGE_ONCE.lua`.

## 10. Changed files

- `companion/ui/theme.py`, `icons.py`, `nav.py`, `palette.py`, `shell.py`
- `companion/ui/widgets/primitives.py`, `table.py`, `states.py`, `toast.py`, `ovr.py`,
  `diff.py`, `ai_bar.py`, `__init__.py`
- `companion/ui/surfaces/`: `_common.py`, `club.py`, `player/*`, `sign/*`, `injury.py`,
  `matchday.py`, `library.py`, `activity.py`, `planner.py`, `planner_review.py`,
  `legacy_assets.py`, `signed_portrait.py`
- `companion/__init__.py` (2.11.0), `assets/icons/medical.png`
- Tests updated for intentional copy changes: `tests/v2/test_card_import.py`,
  `test_tier_a_polish.py`, `test_ui_shell.py`, `test_squad_grok_session.py`,
  `tests/test_companion_library_automations.py`
- New: `PRODUCT.md`, `DESIGN.md`, `docs/redesign/*`, `tools/capture_ui.py`,
  `tools/check_layout.py`, `tools/redesign_checks.py`, `tools/hidden_windows_plugin.py`

## 11. Assessment

The app now reads as one product: one type scale, one icon set, one button
hierarchy, and grids that hold up at the minimum window. The biggest wins are
functional, not cosmetic: the grid layout and keyboard fixes, the visible search
hints, the drawer behaviour. The ceiling not yet reached is real football imagery
(portraits, crests) and a broadcast-style score panel on Club and Matchday.
