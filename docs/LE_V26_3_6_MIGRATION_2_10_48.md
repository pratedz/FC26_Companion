# Companion 2.10.48 — Live Editor v26.3.6 migration

The active deployment is now:

`C:\Users\prated\Desktop\Fc26 LE\FC26_Companion\FC26_Companion.exe`

This checkout remains the development source and a backup of the previous
installation. Future executable updates should be deployed to the new folder.

## Compatibility

The new LE changelog and launcher log identify v26.3.6. All 41 official Lua
library files have identical text to the previous installation. Integration
tests exercise the new installation's actual table, field, memory-wrapper and
Career-event modules with simulated native memory. No native offsets or API
adapters needed a version-specific change.

Frozen discovery now uses the executable's directory when registry discovery
is unavailable. CLI worker installation now returns failure when its report is
unsuccessful.

## Deployment and data

`tools/create_companion_portable.py` creates a Companion-only folder with its
executable/runtime, the three card databases, profiles, preferences, signing
history, snapshots, planner requests, plans and protocol-v3 queue data. It uses
SQLite backup to include committed WAL data, rebases stored application paths,
marks copied squad caches stale and excludes old session/drain markers and the
legacy flat Lua queue. It does not overwrite an existing destination or change
the source installation.

The migration preserved 1,148 job records, 95 snapshots, one favorite and 13
preferences. Consumed free-agent IDs remain unchanged. It copied 1,620 JSON
artifacts and retained one unreadable historic result separately in
`migration_unreadable`.

LE's registry still uses `C:\FC 26 Live Editor` as its data directory, separately
from the new binary installation. The resident worker and v2 autorun were
installed there and now point to the new Companion queue. Signing remains
enabled. The previous worker is backed up under
`backups/worker_before_relocate_2_10_48`. All 43 pinned official LE binary/Lua
files remained unchanged.

## Validation

The full v2 suite passed: 1,049 tests, 30 skipped. The packaged 2.10.48 executable
passed version, Maradona card search, Madrid club search and offline installation
diagnostics from its new location. Worker/config/content hashes match.

FC26 and Live Editor were closed throughout installation. The remaining runtime
check requires starting the new LE launcher and FC26, loading a Career hub, and
using Refresh squad. No live-game success is claimed by the offline checks.

The new folder contains `README.txt`, `installation_report.json`,
`doctor_report.json`, `compatibility_report.json` and `migration.json`.
