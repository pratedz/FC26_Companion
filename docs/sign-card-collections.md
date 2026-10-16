# Sign card collections

Sign shows which collection every Library card belongs to and filters by it.
Search shows the controls in a row under the search box. Suggestions has a
compact copy in its results toolbar. Both edit the same view state.

- **Best card / Base card / All versions**: one card per player and year
  (highest rated), one card per player and year (the plain card), or every
  version. All versions is the default.
- **Year**: Any year, or one FC year in the Library (newest first). The
  collection list follows the year, and a year change drops a collection or
  type that year does not have, with a note saying so.
- **Collection menu**: All collections, then All players / All Icons /
  All Heroes (only when that year has them), then groups:
  - *Base cards*: Base Gold, Base Silver, Base Bronze, Base Icons, Base & Prime Heroes.
  - *Every week*: TOTW and the other core weekly collections.
  - *FC{yy} promos*, one group per year: the main campaigns in release order,
    then smaller campaigns in a second column when the list is long. Picking a
    promo from a year's group also sets the Year.

In Suggestions the AI still chooses the players; the filters choose which of
their cards is offered, and changing a filter swaps cards without a new AI call.

## Where it lives

| Piece | File |
| --- | --- |
| Label to collection, kind and base flag; Best/Base pick; SQL filter | `companion/domain/card_collections.py` |
| Library search arguments `collection`, `kind`, `base_only`; `collections()` | `companion/domain/catalog.py` |
| Toolbar controls and the Suggestions re-pick | `companion/ui/surfaces/sign/card_filters.py` |
| Suggestions resolver honouring the filters | `companion/domain/add_player.py` |

A card's collection comes from its label (FUT.GG rarity name, Live Editor card
type, or `base` for Career rows), plus its club for Icons and Heroes and its OVR
for base gold (75+), silver (65-74) and bronze. `sql_filter` turns a collection
into exact label lists, so the 200/500 row limit applies after filtering. The
test `test_sql_filter_agrees_with_the_python_classifier` keeps the SQL filter
and `classify` the same.

## FC26 Ultimate Rewind and Pre-Season

FUT.GG files these cards under the promo they re-used. The FC26 Ultimate Rewind
items (squad 572, 2026-08-21 to 08-27) carry names such as "Winter Wildcards";
Pre-Season (from 2026-08-28) carries "FUTTIES". `campaign_label` adds the
campaign to the label when the Library is built (`tools/build_universe.py`) and
when the fallback catalog is filled (`tools/sync_futgg_database.py`). Raw FUT.GG
payloads are not changed.

## Data

`tools/sync_futgg_database.py --year 26|27` downloads, audits, installs and
verifies one complete FUT.GG year (rating partitions below the 10,000 cap).
`--verify-live` compares the dump with FUT.GG's current listing.
`tools/verify_card_collections.py <install>` checks that every FC26/FC27 label
is named and that each requested FC26 collection has cards.

Shapeshifters and Libertadores & Sudamericana have no FC26 items on FUT.GG
(they exist in FC23-FC25). The verifier fails if FUT.GG ever adds them, so the
mapping can be revisited.
