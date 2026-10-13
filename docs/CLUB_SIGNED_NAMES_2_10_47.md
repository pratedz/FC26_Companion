# Club signing names — Companion 2.10.47

Refreshing the squad now uses the chosen signing name for each verified signed
player, even if Live Editor returns the original free agent's cached name.
Native-name signings do not necessarily have an `editedplayernames` row, so
checking that table alone did not cover every signing.

The refresh reads the saved signing request and its completed worker receipt.
It pairs batch member names with the actual returned Career player IDs, including
backup dummy IDs. It updates the squad row before publishing and caching it, so
Club, player selection, and planner handoffs share the same name.

Identity recovery is scoped to the Career save. Queued, unverified, dry-run,
failed, and mismatched results cannot supply a name. The lookup includes older
signings beyond the recent activity limit and survives Companion restarts.
This desktop display fix requires reopening Companion and refreshing the squad;
it does not require signing the player again or reinstalling the in-game worker.

Validation: 236 checks passed and 5 skipped across squad refresh, signing
identity, signing handoff, database, player signing, apply, and Club release
tests. Live FC 26 behavior was not exercised during this change.
