# Sign redesign implementation report — Companion 2.10.37

## Result

The Sign tab is a discovery workspace with four modes and one authoritative Signing Bag/Checkout flow. It uses **no player photos, card artwork, club logos, generated imagery, or fake player attributes**. Filters and details are derived from the actual local catalog and verified signing history.

| Before | After |
| --- | --- |
| Dense search/actions strip | Search input, a compact filter toolbar, and expandable additional filters |
| Single catalog grid with optional AI strip | Search / Recommendations / Favourites / Recently Added discovery modes |
| Bag beside the grid, Checkout in a separate footer | Bag above selected-player details; Checkout docked inside the bag |
| Small flat table cells | Monograms, real rating/position chips, face verification, per-row favourite and Add controls |
| Limited history text | Successful, verified additions for the current Career save/team, newest first |

## Real data and filters

Inspected `LocalCatalog`, its universe/catalog fallback implementations, normalized search rows, the actual `card_db/universe.sqlite` schema, Library favourites, Sign helpers, app state/UI actions, Shell soft refresh, and the existing Checkout/team command code before implementing.

| Control | Actual field/API | Reason |
| --- | --- | --- |
| Name | `LocalCatalog.search` / normalized person name | Existing local search; retains three-character minimum and debouncing |
| Year | observation `year`, normalized `year`/`game_year` | Narrow historical/current card versions through existing indexed query |
| Overall range | observation `overall` → `overallrating` | Existing catalog query supports minimum/maximum |
| Position | normalized `positions_text` | Immediate narrowing of fetched results; best-card reduction runs after position filtering |
| Source | `source_kind` / existing `search(source=...)` | Actual installed universe kinds: `career`, `fut`, `ea_official`, `le_base` |
| Verified only | Existing local base-profile/face annotation | Only verified cards can enter the bag; Checkout revalidates |
| Best card | Existing `shop_cards` ranking by real overall, then year, per person | Reduces repeated years/promos; all versions remain available |

Search fetches at most 200 catalog matches. Position and best-card reduction apply to those fetched matches; the existing capped-results feedback tells users to narrow large searches. This does not promise an exhaustive ranking of the whole catalog.

Potential, club, nationality, card variant and source are shown in details only when present. They are not fabricated into filters unsupported by the current query API. Card year is labelled **Year**, not age. Details omit missing/null fields and unset potential.

## Favourites

Sign calls the existing `LocalCatalog.is_favorite`, `toggle_favorite` and `favorites` APIs backed by the same `state.sqlite` favourite table as Library. No second database or favourite identity system was introduced. Row stars and the detail-panel favourite action update this shared source. Saved cards remain selectable and can enter the normal bag.

## Recently Added

Uses persisted `Database.job_records` and the actual original job/result JSON. Only applied `add_to_team` jobs with a verified result, matching the current save and club, are included. Dry runs, other saves/teams, queued jobs, failures and unverified results are excluded. Each batch member uses the stored player name and applied fields; results are sorted by completion time, newest first.

History does not retain exact original catalog-card provenance, so historical rows are **inspection-only**. They do not offer Add or Favourite, and do not invent a year/card variant/face badge. It is signing history, not recent searches.

## Recommendations

Reuses `SquadAssistantPanel`, the existing AI provider integration, `grok_library_worker` and the existing local recommendation resolver. The request adds a small snapshot of actual available squad names, ratings and positions. No guessed player values are sent.

AI output remains name/reason suggestions. Only resolved real local cards become result rows; unresolved names are reported as **Not found in local Library**. A recommendation Add goes through the same verified-face gate, bag and Checkout. Exact-formation validation and retrying missing suggestions remain available. Search and AI result caches are separate, so changing discovery mode does not replace another mode's results.

External AI responses were mocked in the tests; no live paid AI request was made during implementation.

## State and performance

Presentation state stays in the existing Sign surface VM, not global AppState: mode, query, filters, per-mode rows/checks/selection, sorting/page, bag, and recommendation results/feedback. Shell's existing job-progress soft refresh remains intact. A full surface rebuild restores cached state.

The result list mounts at most 20 rows per page. Selection and checkbox changes update existing row widgets, rather than rebuilding the whole page. Results, bag and details use separate themed scroll regions; the existing descendant-aware mouse-wheel handling is reused. The right rail has a bounded width and its Checkout dock remains visible at the supported minimum size.

## Signing safety / engine changes

No changes to `companion/domain/add_player.py`, `companion/app/commands/team.py`, job protocol, safe dummy selection/reservation, Lua signing, face validation, or `sign/review.py`.

The redesigned UI adds an explicit history/verified-face check before adding a result to the bag, and retains existing duplicate prevention and bag limit. Checkout still invokes `preview_cards_for_team`, checks verified faces and formation validity, then opens the existing `_open_review_window`; only that existing reviewed flow can queue signings.

## Files changed

- `companion/ui/surfaces/sign/__init__.py` — workspace composition and shared callbacks.
- `companion/ui/surfaces/sign/search.py` — compact controls, source filter, state/cache integration and result adapter.
- `companion/ui/surfaces/sign/basket.py` — bag rail, monograms and docked Checkout.
- `companion/ui/surfaces/sign/readiness.py` — compact presentation with existing readiness conditions/actions retained.
- `companion/ui/surfaces/sign/assistant.py` — recommendations discovery mode, actual squad context, persisted results and unresolved feedback.
- `companion/ui/surfaces/sign/discovery.py` (new) — mode selection, shared favourites, verified history and squad context.
- `companion/ui/surfaces/sign/details.py` (new) — real optional fields and detail favourite control.
- `companion/ui/surfaces/sign/results.py` (new) — bounded result rows, chips, checks, row Add/favourites, sorting and paging.
- `companion/__init__.py` — app version 2.10.37; core version unchanged.
- `tests/v2/test_sign_discovery.py` (new) — catalog/favourite/history/AI and real-widget interaction checks.
- `tests/v2/test_add_player_surface.py`, `tests/v2/test_sign_results_visible.py` — update outdated layout guards while retaining safety assertions.

## Verification

**78 passed** across these focused suites, with real Tk tests explicitly enabled:

- `test_sign_discovery.py`
- `test_sign_results_visible.py`
- `test_add_player_surface.py`
- `test_phase4_add_to_team.py`

Widget tests cover 1040×720, 1180×820 and 1600×900, with space reserved for the Shell header and bottom Changes bar. They exercise search, verified-face blocking, best/all versions, selecting details, adding/removing/clearing, shared favourites, mode switching, state restoration, the actual AI-to-local-card boundary with a mocked provider, and routing into the existing Checkout preflight. Engine tests continue to cover authoritative signing checks. No game signing was applied during these tests.

Python compilation passed. A later headless compatibility rerun passed 39 tests, with its one GUI test skipped by the default opt-in guard.

## Concept elements omitted

Player photos, card artwork, club logos, invented age/attributes, fake AI suggestions, and unsupported filter fields were omitted. A separate tile/gallery view was omitted because the dataset provides no dependable imagery. The screenshot's hardcoded 20/20 capacity and example team/player values were not copied; real readiness data remains authoritative.

## Packaging / screenshots

Version 2.10.37 was packaged and reopened successfully. Screenshot capture was interrupted by the user.

## Responsiveness follow-up — 2.10.38

The follow-up targets scrolling, typing a new search, changing pages and refreshing the squad:

- Result rows use a reusable pool capped at 20. Page changes and searches rebind those widgets instead of destroying and rebuilding them. Selection and bag changes update only controls whose state changed.
- Rows use a lighter layout: name and overall on the first line, year and positions beneath, with persistent favourite and Add actions. Club information remains responsive to available width; recommendations retain their explanation.
- Sign scrolling batches wheel events over 16 ms with fixed 12-pixel increments. Wheel handlers are attached once per pooled row instead of walking every descendant on each layout event. Other scroll surfaces retain their existing wheel behavior.
- Catalog search and disk-backed face annotation run in the existing worker executor. A shared cancellation key supersedes previous work, and the result generation changes as soon as the query text changes, including during debounce.
- Existing results remain visible while searching; loading text is inline instead of producing repeated status toasts. New completed searches return to the first page. Position typing is also debounced.
- Squad/state refresh updates readiness and signing progress without destroying the Sign workspace, preserving focus, discovery mode, selection and scroll position.
- Details compare only displayed fields; selection changes no longer copy every catalog payload. Bag sizing skips unchanged dimensions. Recently Added avoids database reads when the relevant signing state is unchanged.

The review, verified-face enforcement and live signing pipeline remain authoritative and unchanged. This release changes no Lua core files or game memory logic.

Validation for this follow-up: Python compilation completed successfully. The 78-test result above belongs to 2.10.37; tests were not rerun for this follow-up. No frame-time benchmark or live-game signing validation was performed, so zero lag is not a measured guarantee. Build status is recorded in `logs/build_sign_responsiveness_2_10_38.log`.

Packaging completed successfully for 2.10.38 on 27 September 2026. The root executable, desktop runtime and portable bundle were rebuilt; the root executable was reopened.
