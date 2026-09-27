# FC26 injury layout — Live Editor 26.3.5

Verified on 25 September 2026 against the installed FCLiveEditor.DLL.
SHA256: `0412BEE62D1C8A6DC9B220E5696B227627D023208914EDD90BF6B9A9156611A2`.

## Native evidence

All addresses below are RVAs in that DLL. SetPlayerFitness (0x3F6540)
calls 0x350AF0, which resolves manager type 47. The fitness getter
0x2C6CA0 reads a vector at manager + 0x3F38 (begin), +0x3F40 (end),
+0x3F48 (capacity), with 20-byte records.

| Record offset | Value |
| --- | --- |
| 0x00 | Player ID, uint32 |
| 0x04 | Team ID, uint32 |
| 0x08 | Recovery date, uint32 YYYYMMDD |
| 0x0E | Fitness byte |
| 0x0F | Signed status byte; greater than 1 means injured |
| 0x11 | Injury type byte |

The Player Editor comparison is at 0x390D26. Low fitness alone is not an injury.
Absent records use the native default of fit/uninjured.

Recovery getter 0x2C6DF0 reads a second vector at +0x3F58/+0x3F60/+0x3F68,
with 12-byte records: player ID at 0, recovery date at 4, state at 8.

Native full-fitness healing resets an existing recovery record with 0x47DBE0
(ID = 0xFFFFFFFF, date = 20080101, remaining bytes retained), and fitness
record with 0x47DBC0 (player and team IDs = 0xFFFFFFFF, date = 20080101,
fitness = 100, other trailing bytes zero). The reader mirrors these resets;
it does not allocate records or call unknown native functions.

## Runtime guards

The supported profile requires LE_VERSION v26.3.5 and manager vtable at game
module base + 0xB7A2460. Vector ordering, strides, sizes, player IDs, fitness,
dates, duplicate squad records and unchanged headers are checked. Reads are
bounded to 128 records per block. Healing rechecks current squad membership,
manager identity, headers and original bytes. Writes are read back; failed
operations attempt rollback and stop before the next player.

A future game/Live Editor update requires a newly verified profile. The DLL
hash above records the inspected artifact; the Lua guard uses the version and
manager vtable fingerprint rather than computing the DLL hash in-game.

## Live validation

The read-only probe found 396 records and 42 current squad players. Fourteen
had explicit fitness records. Carlos Alberto (73669) was the only injured
player: fitness 100, status 2, injury type 31, return date 20270401. Pelé
(fitness 60) and Steven Gerrard (fitness 50) were correctly excluded.
No healing was applied during this diagnostic.

`tools/inspect_injury_native.py` reproduces offline disassembly references.
`tools/injury_verified_probe.lua` is the read-only diagnostic. Isolated Lua
tests in `tests/v2/test_fitness_injuries.py` cover decoding, guards, reset
bytes, squad filtering and rollback without accessing the game process.
