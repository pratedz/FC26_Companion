# PlayStyle squad plan correction — 2.10.40

## Actual failure on 27 September 2026

Job `01M3GCSCJB71HGM8YGCQCDHDFN`, 09:58 local, was part 1/4 of a
22-player plan. The worker genuinely returned PARTIAL, unlike the earlier
false-crash/timeout failures addressed in 2.10.39.

Five players completed. Player 190871 wrote five of six fields, but `trait2`
was 2097154 and the live schema permits only 0..131071. The worker stopped
before the remaining operations; parts 2–4 were never submitted. This part
also contained invalid later trait2 values 524320 and 1048584.

Squad exports identify Carvajal as 82407 and Manuel Neuer as 85308. Neither
was in the submitted first part. The later draft parts were not persisted,
so their exact abandoned proposals cannot be reconstructed from that job.
No-ceiling reached Apply: the first players had six or seven plus bits.
The failure was invalid bank encoding, not the count ceiling.

The AI contract had no maximum on all four masks and supplied no names-to-bits
map. The returned numbers also put goalkeeper/CPU/Career bits on outfield
players. A nonzero icontrait value alone does not prove a useful PlayStyle+.

## Changes

- Player schema validates bank 1 against the 30 defined bits and bank 2 against
  the observed 17-bit storage range. Validate before applying the normal count
  cap, so the cap cannot hide an invalid mask. No-ceiling retains valid bits.
- AI proposals now choose named PlayStyles from the local Live Editor enum;
  Python performs deterministic encoding. The six goalkeeper names map to
  bank-2 bits 0–5. CPU/Career flags are excluded from named PlayStyle+ choices.
- Validate each AI player, including appropriate keeper/outfield plus bits.
  Missing or rejected players get one bounded repair request; valid players
  are retained. No new game operations or worker changes are needed.
- An incomplete requested PlayStyle+ plan remains reviewable, with named
  blockers and Apply disabled. The job builder also rejects it independently
  of UI. Regular traits and Career flags cannot satisfy plus coverage.
- Review cards show proposed PlayStyle+ names and counts, wrapped for smaller
  windows. This is a targeted preview correction, not a layout redesign.

## Verification and limits

`tests/v2/test_squad_playstyle_contract.py` covers the three exact invalid
bank values, standard/unlimited validation, deterministic named masks,
missing/zero/unknown/wrong-role/Career-only proposals, repair isolation,
incomplete Apply blocking, and preserving every defined bit without a ceiling.

For 20, 21 and 22 players, a simulated provider first returns the actual bad
mask, then repairs only that player. The sequential apply simulation verifies
all player writes, including Neuer and Carvajal after part 1, and checks shirt
uniqueness at every intermediate write. Existing queue-race, split-job, group
failure, shift-selection and ceiling regressions are also run.

Focused regression run: **269 passed** (10 test modules), plus **45 passed**
for player flow, card imports and player AI feedback: **314 total**. Build log:
`logs/build_playstyle_fix_2_10_40.log`.

No live player changes are submitted by this fix, no process memory is read,
and no worker is installed while FC26 is running. Tests use fake provider and
worker responses; the rebuilt app still needs a fresh reviewed plan and an
in-game result to confirm this career save. Old incomplete plans are not resumed.
