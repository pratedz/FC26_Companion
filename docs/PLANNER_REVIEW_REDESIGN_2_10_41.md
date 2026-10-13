# Plan window review redesign — 2.10.41

The large selection checklist and repeated matrix/cards made real changes
hard to find. Several labels repeated the same progress, while the header
listed unchanged target overalls and internal scope chips.

The per-player plan window now uses a compact request area and two independent
panes: a searchable selection list and the selected player's actual changes.
Rows show proposed shirt moves, PlayStyle+ count and changed overall only.
Details show named plus additions/removals, regular PlayStyles and changed
ratings. Unchanged overall targets are accessible through a disclosure;
explicit requested overall targets remain immediately editable. Exact raw
values and plan notes are available on demand. Additional squad shirt moves
remain visible in the review.

One persistent footer contains progress, Build/Rebuild and Apply. Selection,
target or ceiling edits disable Apply until the preview is rebuilt. Controls
are disabled while reading, proposing or applying. The result remains visible
after a successful apply. Provider configuration is available through AI
settings rather than filling the initial viewport.

The window opens at 1020x740 and supports 800x600. Search hides/shows existing
rows; it does not recreate them. Scroll events use the existing coalesced
scroll helper. Progress does not rebuild the list on each chunk update.
Shift-click uses the visible order, and original player/field validation,
no-ceiling behavior and APPLY confirmation stay active.

Verification: 270 headless tests pass; the additional real Tk layout and
interaction test passes at both sizes. It checks action visibility, independent
panes, row reuse during search, edited-selection Apply blocking and preserving
new ranking targets when the user did not pin the provisional target.

Visual preview with sample data:
`feedback/screenshots/planner_review_2_10_41_final.png`.
Build log: `logs/build_planner_review_2_10_41.log`.
No live player changes are submitted during UI verification.
