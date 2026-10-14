# FC27 FUT.GG card coverage — 14 October 2026

The previous installed Universe held 10,000 FC27 observations. Its legacy catalog index had no FC27 entries. It included TOTW 1 and Season 1 OTW rewards but missed TOTWs 2–4, Destined for Glory, Future Stars, lower-rated players and most holographic variants.

The replacement source contains **20,425 distinct item IDs** for **19,657 players**. Every item has all 34 detailed attributes, separate player/item IDs, foot, skills, positions and PlayStyles. All 10,000 previous FC27 item IDs are retained. The catalogs are updated incrementally, so historical observations, record IDs and existing player identities are preserved. Existing FC27 observation IDs are reused when the same item is refreshed.

## Requested collection coverage

| Collection | Before | Now | Source |
|---|---:|---:|---|
| Ones to Watch (Season 1) | 15 | 15/15 | [FUT.GG](https://www.fut.gg/season-pass/) |
| TOTW 1 | 23 | 23/23 | [FUT.GG](https://www.fut.gg/squads/team-of-the-week/totw-1/) |
| TOTW 2 | 0 | 23/23 | [FUT.GG](https://www.fut.gg/squads/team-of-the-week/totw-2/) |
| Destined for Glory Team 1 | 0 | 15/15 | [FUT.GG](https://www.fut.gg/squads/destined-for-glory/destined-for-glory-1/) |
| TOTW 3 | 0 | 23/23 | [FUT.GG](https://www.fut.gg/squads/team-of-the-week/totw-3/) |
| Destined for Glory Team 2 | 0 | 15/15 | [FUT.GG](https://www.fut.gg/squads/destined-for-glory/destined-for-glory-2/) |
| TOTW 4 | 0 | 23/23 | [FUT.GG](https://www.fut.gg/squads/team-of-the-week/totw-4/) |
| Future Stars Team 1 | 0 | 15/15 | [FUT.GG](https://www.fut.gg/squads/future-stars/future-stars-1/) |

Season 1 currently has 18 player items across its Standard and Premium tracks; 15 are Ones to Watch. The full catalog includes 21 OTW items, covering the pass and the other published OTW rewards. FUT.GG's current pass page dates Season 1 from 11 September through 22 October 2026, rather than the 17–18 September date in the supplied list.

Destined for Glory contains 36 standard items: the 30 team cards and 6 additional published items. Future Stars contains 27 standard items: Team 1's 15 pack cards, 10 SBC items and 2 objective items. The dataset includes every published Academy-related card definition returned by the source. User-created Evolution combinations depend on each club's selected players and upgrade paths; they do not form a finite downloadable card collection.

## All published item variants

FUT.GG hides holographics by default. This dataset includes **379** holographic/pristine items as separate records and labels them explicitly in the revision name. The final verified listing uses `show_holographics=true`.

| Rarity family | All item variants, including holographics |
|---|---:|
| Rare | 19,574 |
| Base Icon | 272 |
| Team of the week | 184 |
| Base Hero | 175 |
| Destined for Glory | 66 |
| Future Stars | 42 |
| Debut International Icon | 36 |
| Squad Foundations | 23 |
| Base Hall of FUT | 21 |
| Ones to Watch | 21 |
| Partnerships | 5 |
| POTM Bundesliga | 1 |
| POTM LaLiga EA Sports | 1 |
| POTM Liga F | 1 |
| POTM Ligue 1 | 1 |
| POTM Premier League | 1 |
| POTM Serie A | 1 |

## Completeness evidence

FUT.GG caps each query at 10,000 items. The sync splits rating ranges until every query falls below the cap, follows all pages, and checks unique IDs against each uncapped total. It requires every requested definition and all detailed attributes before replacing a good source file. Page failures, repeated IDs, truncated batches, capped ranges and page limits fail the sync.

| Rating range | Independently reported item count |
|---|---:|
| 0–49 | 107 |
| 50–62 | 4,915 |
| 63–68 | 7,006 |
| 69–74 | 4,985 |
| 75–99 | 3,412 |

The committed `card_db/futgg/fc27_collections.json` stores the source URLs, complete squad item-ID sets, source-page hashes, uncapped listing totals, rarity counts, and source-file SHA-256. Every squad ID is present in the dataset. Both SQLite databases are integrity-checked and compared against every downloaded item. All non-FC27-FUT.GG rows are hashed before and after installation and must match exactly.

`skillmoves` in the normalized cache is FC's raw 0–4 encoding. The Universe stores external skill moves as 1–5 display stars, so the loader honors the explicit encoding marker; card imports convert to raw storage once. This prevents newly downloaded cards from losing a skill-move star.

## Refresh and install

From the source repository:

```powershell
python tools/sync_futgg_database.py --download
python tools/sync_futgg_database.py --verify-data
python tools/sync_futgg_database.py --install "C:/path/to/FC26_Companion"
python tools/sync_futgg_database.py --verify-install "C:/path/to/FC26_Companion"
```

Installation requires existing Universe and catalog databases. It stages the update, validates it, creates a SQLite backup under the installation's `backups` folder, and rolls back both live indexes if publishing or validation fails. It also writes an install report with historical row counts/hashes and the backup location. Personal save state and team data are not edited.

Restart Companion after the update to refresh any in-memory library counts. The versioned deliverable is the JSONL source and audit manifest; large generated SQLite indexes remain local and can be regenerated/updated by the installer.
