# Companion 2.10.50 — signed identities and LE 26.3.6 injury support

The injury scan in the screenshot fell through to unsupported API/database
fallbacks because `ops/fitness_injuries.lua` admitted only LE `v26.3.5`.
LE v26.3.6 still uses the same native injury structures. Offline inspection
of its DLL verified the getters at RVAs 0x2C9760 and 0x2C98B0: manager vectors
+0x3F38 and +0x3F58, with strides 20 and 12. The signed-byte injury comparison
is at 0x396536. Full-fitness and recovery resets match byte-for-byte at
0x48C700 and 0x48C720. The inspected DLL SHA256 is
`0af955fcf7a34ec4055a11e18d3c621b4fa877cd6013776cfaa78a279b9308d9`.
`tools/verify_injury_26_3_6.py` reproduces the comparison, and
`injury_26_3_6_evidence.json` records the matches.

Core 2.6.31 admits the verified LE version while retaining the game manager
vtable, vector, player ID, date, duplicate, original-byte and rollback guards
documented in INJURY_LAYOUT.md. Unknown versions remain unsupported. Injury
requests now allow 5 seconds, matching other squad reads. An older resident
worker gives a precise restart message before queueing, rather than the
misleading missing-API fallback. Save your Career and restart FC with LE to
load the new resident scripts. No live injury healing is performed by this
release verification.

New signing requests record the card's base person ID as `source_playerid` in
the audited player payload. This metadata is not written to player database
fields. Verified signing receipts map the requested name and source portrait
to the actual Career ID, including backup dummy selection and batch order.
The existing save-scoped name correction remains in place. For old receipts,
source recovery requires matching all four native name IDs and the saved
real-face asset against an authoritative FC26 base row. Unverifiable older
signings show an unavailable source rather than a guessed portrait.

After squad refresh, Companion fetches portraits for verified signed players
present in that Career roster. Each source is deduplicated and cached under
`portraits/sources/<source-id>/`. Existing verified asset exports are reused.
Pending exports recover from their audit receipts after restart or queue
rotation. Missing assets can be retried through **Load signed portrait**.
Exports wait for a safe Career event. They do not change game state.

Club's selected-player card and the Player summary show the signed source
image; their fallback is a monogram. Injury scan names also use the verified
signing identity for the current save.

In Club, select a signed player and click **Install portrait in game** to opt
in. The destination is Live Editor's resolved mods folder under
`legacy/data/ui/imgAssets/heads/p<actual-career-id>.DDS`. Source DDS must decode
as 180×180 BC3/DXT5 and match its verified fingerprint. Existing overrides are
backed up before atomic replacement. **Restore previous portrait** restores
those exact bytes, or removes only the override Companion installed when no
previous file existed. External edits and changed backups stop restoration.
The receipt journal also protects the previous file after an interrupted
installation. Installation requires the same live save/session and current
squad membership.

LE portrait overrides affect that player ID across saves and require LE to be
running. They change menu images, not the 3D face. Reopen the squad screen;
restart FC with LE if the game retains a cached image. This release never
automatically installs or restores portraits.

The previously queued native export completed successfully on LE v26.3.6:
database 46,167,344 bytes, Maradona portrait 32,528 bytes, decoded at 180×180.
Its receipt is `01M3YBT7PP0W3T9SJ9DWAGD4T4`, retained in the active portable
queue. The source preview has been cached without installing an override.

Active deployment: `C:\Users\prated\Desktop\Fc26 LE\FC26_Companion`.
The previous runtime/worker and a consistent SQLite backup are retained under
the development checkout's `backups/companion_before_2_10_50`.
