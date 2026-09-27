# v2 build — design decisions (reconciling the two blueprints)

The v2 design lives in two docs written across the design session:

- `docs/V2_ARCHITECTURE.md` — the Python architecture, the closed `ApplyOutcome`
  enum, and a **protocol v3** with ULID job ids, `jobs/`/`claimed/`/`results/`
  subdirectories, claim-by-rename, and pid-checked `session.json`/`drain.json`
  liveness.
- `docs/V2_INGAME_CORE.md` — the resident Lua core, with the most concrete Lua
  and the exact job-envelope + result JSON shapes (`schema`, `ops[]`, `counts`,
  `failures[]`, semantic `ok`).

They disagree on two mechanical points (job-id scheme, and drain-in-place vs
claim-by-rename) because they were written as parallel explorations. This build
**unifies them into one coherent wire protocol** so the Python and Lua sides
agree exactly. Where they conflicted, the choice and its reason:

| Question | Architecture doc | In-game doc | **Chosen** | Why |
|---|---|---|---|---|
| Job id | ULID | `[A-Za-z0-9_.-]{1,120}` | **ULID** (which *is* a legal `[A-Za-z0-9_.-]` name) | Sortable + collision-free; the charset validator still accepts it. |
| Directory | `jobs/`,`claimed/`,`results/` | `<id>.job.json` in root | **`jobs/`,`claimed/`,`results/`,`state/`,`poison/`,`archive/`** | Claim-by-rename needs a `claimed/` dir; keeps v1's flat `*.lua` files untouched. |
| Claim | rename→`claimed/` | drain in place | **rename→`claimed/`** | Atomic exactly-once + crash recovery (stale `claimed/` → `crashed` result). |
| Liveness | `session.json`+`drain.json`, pid-checked, no TTL | `_core_info.json` | **`session.json`+`drain.json`, pid-checked** | Fixes the #1 v1 bug: idle-but-armed read `OFF`. |
| Result verdict | `ApplyOutcome` closed enum | `state`+`ok`+`counts` | **Both**: Lua writes `state`/`ok`/`counts`/`failures`; Python maps to `ApplyOutcome` | Concrete wire format + typed domain model. |
| Envelope version | `"v": 3` | `"schema": 1` | **`"schema": 3`** | One number, matching `PROTOCOL_V = 3`. |

## Coexistence (the prime directive)

v1 keeps working. The v3 core uses only **new** paths:

- v1: `queue/*.lua`, `_pending.txt`, `_run_now.lua`, `_wake.txt`,
  `_last_result.txt`, `_job_status.txt`, `_bridge_alive.txt` — **never touched**.
- v3: `queue/jobs/`, `queue/claimed/`, `queue/results/`, `queue/state/`,
  `queue/poison/`, `queue/archive/`, `queue/session.json`, `queue/drain.json`.

Both arm from `lua/autorun/`; both can run in the same LE session; they own
disjoint file patterns and coordinate through nothing but the filesystem.

## Package location

- `companion/` — the v2 Python package (new architecture; v1 `src/` untouched).
- `ingame/le_companion/` — the resident Lua core source (installed into the LE
  data dir's `lua/scripts/le_companion/` by `companion/platform/le_install.py`).
- `docs/schema/{job,result}.schema.json` — the wire contract, machine-checkable.

## Safety invariants carried verbatim from v1 (SI-1…SI-8)

Enforced by code **and** by `tests/safety/`. See `docs/V2_ARCHITECTURE.md §1.3`.
The load-bearing ones this build implements now:

- **SI-5** `CreatePlayer` opt-in per job (`grants.allow_create_player`), id clamped
  to `[460000, 499999]`, rejected at validation before any write.
- **SI-7** every job-derived path confined under `queue/` on **both** sides.
- **SI-3** arming only via `lua/autorun/`; never patches `live_editor.lua`.
- **SI-4** no `ReloadPlayersManager` — the op registry has no encoding for it.
