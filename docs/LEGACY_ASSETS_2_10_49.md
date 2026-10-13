# Companion 2.10.49 — LE v26.3.6 asset tools

Open **Activity → Assets**. This page contains:

- Check database: probes `data/db/fifa_ng_db.db` using `LegacyFileExist`.
- Export database: exports the raw game database with `LegacyFileExport`, verifies a nonempty file, then records its size, SHA-256 and first 32 header bytes.
- Player portrait: enter a game player ID to check/export `data/ui/imgAssets/heads/p<ID>.DDS` and its `youthheads` equivalent. Available DDS/BC3 files are decoded into transparent PNG previews by Pillow, off the UI thread. Missing portraits remain explicitly missing.
- Other game asset: check/export a validated `data/...` legacy path.
- Recent results: persistent reports, previews and an Open export folder action. Late results and app restarts recover using the recorded job request.

The raw database is not a Career save backup. This feature does not parse/import the EA database into the Library or replace the card catalog. Portraits are identified by the requested game ID; a free-agent ID reused for signing may still resolve to the original free agent's portrait. Assets are not reassigned inside the game.

## Runtime contract

App 2.10.49 ships resident core 2.6.30 and the typed `assets.legacy` v1 operation. Session capabilities report the actual availability of `LegacyFileExist` and `LegacyFileExport`, rather than assuming the functions exist because a wrapper or version string is present. Requests require an updated worker, supported host functions and an open Career hub.

Checks can use the existing read timer. Exports run on a normal safe Career event, since the native call can block while writing a file. Advance to a safe Career screen if an export remains queued. Requests are bounded to eight checks or two exports, use one attempt, and have a five-second job budget. The native call itself cannot be preempted by Lua.

Each job gets `queue/assets/<job-id>/<item-index>/`. Python creates the folders before submission; Lua never uses a caller-supplied output path. The exporter supports hosts interpreting the optional output location as a directory or as a filename, and verifies the resulting file rather than treating a successful call as sufficient evidence. Existing destinations are refused. Dry runs check existence without exporting. File inspection/opening remains confined to the specific request folder.

Game assets are classified as reads of game state and do not invalidate squad snapshots or create Career undo entries. No memory allocation, jump patching or native hooks are added.

## CLI

```
FC26_Companion.exe assets check data/db/fifa_ng_db.db
FC26_Companion.exe assets database
FC26_Companion.exe assets portrait 190045
FC26_Companion.exe assets export data/ui/imgAssets/heads/p190045.DDS
FC26_Companion.exe assets reports
```

Check/export/database/portrait commands accept `--no-wait` and `--timeout`. Global `--root` selects a different installation. Doctor reports game asset API availability for an armed worker.

## Sources

- [Official legacy file API](https://github.com/xAranaktu/FC-26-Live-Editor/wiki/LUA-API-v2-Legacy-Files)
- [Official miniface format and paths](https://github.com/xAranaktu/FC-26-Live-Editor/wiki/Importing-Miniface)
- [Official generated portrait paths](https://github.com/xAranaktu/FC-26-Live-Editor/wiki/Generating-Miniface)

The feature's native entry points require an in-game test against the installed LE build. Automated coverage uses the real Companion Lua handler with simulated native file APIs, including folder/nested/filename exports, false-return and empty-file failures, missing assets, old APIs, dry runs, path confinement, recovery, and real DDS/BC3 decoding.

Active portable deployment: `C:\Users\prated\Desktop\Fc26 LE\FC26_Companion`. The older checkout remains the development source and backup; release bundles must be deployed to the new folder.

Final validation: **1,076 passed, 30 skipped** in the v2 suite, followed by 97 passing UI regression tests (16 skipped) after the compact drawer adjustment. The invisible disabled desktop smoke verified the actual 360px drawer geometry and preview; the deployed executable recovered a saved worker result and decoded a real DDS/BC3 fixture. Existing 1,148 job records, 95 snapshots, one favorite, 13 preferences and 45 save snapshots were preserved, with preference values unchanged. All 43 pinned official LE files remained unchanged. Prior runtime, worker and SQLite backup are in `backups/companion_before_2_10_49`.

FC26 was subsequently started. Its v26.3.6 session loaded core 2.6.30, advertised both file APIs, and passed every Doctor check. A real `LegacyFileExist` request found the database and Maradona's normal `p190045.DDS` portrait; the youth portrait was correctly reported absent. This check and an export verification request add two audit records to Activity. The export request `01M3YBT7PP0W3T9SJ9DWAGD4T4` is queued for the next safe Career event. Native export verification remains pending; the export logic and DDS decoding have passed simulated-host and packaged-fixture tests.
