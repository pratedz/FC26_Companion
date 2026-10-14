# UI audit and redesign decisions (2.11 redesign)

Baseline: Companion 2.10.52 source at `fec6bd8`, rendered with `tools/capture_ui.py`
against a read-only copy of the owner's live install (Real Madrid save, 39 players,
238k-card catalogue). Captures cover every surface at 1040x720, 1280x820 and with the
Activity drawer open, plus scenarios: player selected, players ticked, player open,
staged edits, review sheet, Library search, Sign search, planner, disconnected state.

Severity: Critical (blocks a job or risks the save), High (frequent, slows or misleads),
Medium (friction or inconsistency), Low (polish). Frequency is per session for the
owner's real usage (Club, Player and Matchday every session; Sign and Library often).

## Findings

| # | Area | Finding | Sev | Freq | Root cause | Fix | Cost |
|---|------|---------|-----|------|------------|-----|------|
| 1 | Squad grid (Club, Player, Library, Sign) | Columns never re-lay out after the first paint. At 1280x820 the Club AGE column shows "1" and "2" (ages clipped). With the Activity drawer open, every column except NAME disappears. At UI scale above 1.0 the offsets are scaled twice. | High | Every session | `DataGrid._paint` lays cells out from the width at paint time and nothing repaints on `<Configure>`; offsets mix device pixels with CTk-scaled `place(x=)`. | Repaint geometry on width change; compute in unscaled units; give columns a minimum and drop optional columns before squeezing the name. | M |
| 2 | Every search box | Club filter, Player picker search and the command palette render as blank boxes with no hint. | High | Every session | `CTkEntry` hides its placeholder whenever a `textvariable` is passed. | One `entry()` primitive that keeps the variable in sync without disabling the placeholder; focus ring on every entry. | S |
| 3 | Club | Four "Worker / FC 26 / Career Mode / Connection" cards take ~80 px on every visit and say "all good", repeating the header pill. The squad, the reason the screen exists, starts at y=330. | High | Every session | Setup checklist kept on the connected board. | Show the checklist only when a step needs attention; when healthy, one quiet line in the club header. | S |
| 4 | Club | Two buttons do the same thing with two loud fills ("Open in Player Page" solid cyan, "Open Carlos Espí" solid green). "Release from club" is a solid red block next to the green primary. | Medium | Often | Each panel picked its own button kind. | One primary per screen; release becomes a danger-tone outline; detail card uses one quiet open action. | S |
| 5 | Club detail card | OVR and POT render in success green whatever the rating, so 61 and 91 look alike. | Medium | Often | Hard-coded `SUCCESS` colour. | Use the OVR tier chips used everywhere else. | S |
| 6 | Chrome | Header message is truncated mid-word ("Jobs run on the next Career Mode t…"). "No player selected" sits in a 220 px button. The palette button reads "⌘K", a macOS key on a Windows app. | Medium | Every session | Fixed-width caption, glyph labels. | Connection chip + short context; player chip only when a player is selected; real icons with "Ctrl K" hint. | M |
| 7 | Navigation and chrome | Unicode glyphs (⌂ ◉ + ✚ ⟳ ▤ ☰ ⧉) stand in for icons. They render at different weights; the Matchday glyph falls back to a box when bold. The app ships a 96-icon pack that the UI never uses. | Medium | Every session | Glyph strings in `nav.SURFACES`. | Icons from `assets/icons`, tinted per state through one loader; an authored first-aid icon for Injuries in the same stroke. | S |
| 8 | Every screen | Competing solid fills: Sign shows a solid cyan tab, solid cyan Search and solid green "Focus search" together; Library pairs a green Search with a green "Open Club"; Injuries uses solid cyan for Scan. | Medium | Every session | `accent` buttons are solid; tabs use solid fills. | `accent` becomes a tonal button; tabs and segmented controls use one quiet style; one solid primary per screen. | S |
| 9 | Typography | 17 font sizes in use (9 to 30) and accent-coloured micro labels above headings ("CURRENT CAREER SQUAD · 39 PLAYERS", "Find a card", "Selected", "LAST PREPARATION", "NOT TOUCHED"). Numerics use Consolas, which reads as code. | Medium | Every session | No type scale token; `eyebrow()` helper. | Seven-step type scale in `theme`; section titles replace kickers; Bahnschrift (DIN, ships with Windows 10) for titles and scoreboard numerals; Segoe UI tabular figures for table numbers. | M |
| 10 | Dropdowns, switches, checkboxes | CustomTkinter's default blue (#1F6AA5) leaks into option menus ("Choose a request", "Automatic") and the Roboto default font falls back unpredictably. | Medium | Often | No app theme registered with CustomTkinter. | Register a token-built CTk theme before the window is created. | S |
| 11 | Player workstation | The Review/Apply rule is explained three times on one screen; "← Back" goes nowhere obvious; the card list has headers and no empty state; "Stage card & review now" is enabled with nothing selected. | Medium | Very often | Copy accreted per release. | One explanation line; empty state with the next action; stage button disabled with a reason until a card is chosen. | M |
| 12 | Player picker | A second copy of the squad list with four columns over half the width and three blocks of instructions. | Medium | Often | Built separately from the Club grid. | Same columns as Club; one instruction line. | S |
| 13 | Sign | "Verified only" filter empties the results, and the empty state's CTA is "Focus search" while the copy says "Switch to All faces". Two "Focus search" buttons on one screen. | Medium | Often | CTA not tied to the cause of emptiness. | Empty state CTA names the fix ("Show all faces"); bag empty state has no button that repeats the search box. | S |
| 14 | Review sheet | With nothing staged it still says "This change cannot be undone automatically." Section labels are all caps; empty "NOT TOUCHED" header. | Medium | Every apply | Static footer. | Empty review explains and offers Close; undo note only with changes; sentence-case group titles. | S |
| 15 | Toasts | Coloured 4 px left bar (decorative), toast duplicates inline counts ("37 card variant(s) found." next to the same line on the page), overlaps the Club action bar. | Low | Often | Every status goes to a toast. | Tone icon instead of the bar; placed above the bottom chrome. | S |
| 16 | Settings | "Clear" clipped to "Clea", key field placeholder clipped, paths truncated mid-word. | Low | Rare | Fixed widths in a 560 px dialog. | Wider dialog, wrapped rows. | S |
| 17 | Command palette | Centred button labels, no hint text, no arrow-key movement, macOS glyph. | Low | Power use | Built from generic buttons. | Left-aligned rows with kind and shortcut, arrow keys + Enter, Esc closes. | S |
| 18 | Keyboard | No visible focus on entries or buttons; surfaces cannot be switched from the keyboard. | Medium | Power use | CTk has no focus ring. | Accent focus ring on entries and buttons; Ctrl+1…6 switch surfaces. | S |
| 19 | Library | Top strip copy is jargon ("Lock a Club player to stage a card build, or Send to Sign with no lock."); the empty state CTA "Open Club" sends the user away from search. | Low | Often | Copy written from the mechanism. | Plain copy; empty state focuses on searching. | S |
| 20 | Injuries | The explanation card under the actions is a dead end that repeats the action panel. | Low | Often | Two panels for one state. | One empty state holding the Scan action. | S |

Accessibility measurements are in `tools/redesign_checks.py contrast`. Before the
redesign `MUTED_DIM` text (#5b6a7d) on panels measured about 3.2:1, below the 4.5:1
the spec requires for the "unknown value" dash and for helper text that used it.

## Strategy

Mode: Operate. The owner opens this beside a running game, usually at night, many
times per session. The established world is right for that scene and stays: dark,
dense, broadcast football graphics, OVR tiers as the first thing read. This is an
established-world refinement plus UX restructuring, not a new visual identity.

Signature move: the scoreboard. Titles and every rating number use Bahnschrift, the
DIN face of football broadcast lower-thirds, on the existing FUT-tier OVR ramp. All
other UI text stays Segoe UI, the platform face, with its tabular figures in tables.

Rules applied everywhere:

1. One solid primary per screen. Secondary emphasis is tonal (tinted fill, accent
   text), never a second solid block.
2. Status first, then data, then actions, then explanation. Explanations appear once.
3. Every empty, error and loading state names its next action as a control.
4. Icons come from the in-house pack, tinted by state, never from Unicode.
5. Section titles are headings, not kickers.
6. Layout adapts at the minimum window and with the drawer open; optional columns
   drop before names are squeezed.
