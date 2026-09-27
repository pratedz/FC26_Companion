# v1 → v2 parity matrix

Status values:

- `FOUNDATION`: underlying v2 mechanism exists, but user flow is incomplete.
- `MISSING`: not implemented in the canonical v2 runtime.
- `LIVE-GATE`: implemented offline but not trusted until disposable-save proof.
- `PARITY`: verified against v1 behavior.

No item is `PARITY` merely because a button or op name exists.

| Capability | v1 reference | v2 baseline | Phase |
|---|---|---:|---:|
| Worker install and automatic arm | `src/le_apply.py` | FOUNDATION | 1 |
| Honest connection/liveness | v1 heartbeat | FOUNDATION | 1 |
| Queue claim and one-result-per-job | v1 flat Lua queue | FOUNDATION | 1 |
| Semantic write result/read-back | apply generators | FOUNDATION / LIVE-GATE | 1 |
| Persistent jobs/preferences/squad | JSON files | MISSING | 1 |
| Pre-write snapshot and rollback | `snapshot_store.py`, `undo_apply.py` | MISSING | 1–2 |
| Reactive desktop surfaces | v1 tab callbacks | MISSING | 1 |
| Full live player read | Editor load-from-target | FOUNDATION / LIVE-GATE | 2 |
| Full player schema editor | `player_schema.py` | FOUNDATION | 2 |
| Card variant search and filters | `card_catalog.py`, Cards tab | FOUNDATION | 2 |
| Best match / compare / favorites | card modules | FOUNDATION | 2 |
| Cross-year import | `import_player.py`, universe DB | FOUNDATION / LIVE-GATE | 2 |
| Squad grid and target lock | Squad tab | FOUNDATION / LIVE-GATE | 2 |
| Team picker | none (v1 gap) | MISSING | 3 |
| Transfer / loan / release / budget | `career_ops.py` | FOUNDATION / LIVE-GATE | 3 |
| Safe add-to-team | `add_player.py`, `add_team_lua.py` | FOUNDATION / LIVE-GATE | 4 |
| 51 profiles | `profiles.json` | MISSING | 3 |
| 19 workflow packs | `product.py` | MISSING | 3 |
| Activity queue/history/health | ops bar modules | FOUNDATION | 3 |
| Undo/redo and restore points | snapshots/undo modules | FOUNDATION / LIVE-GATE | 3 |
| AI editing and credential UI | `grok_client.py` | MISSING | 3 |
| Catalog source management | Catalog tab | MISSING | 3 |
| CLI parity | `main.py` | MISSING | 3 |
| Web parity | `web_api.py`, `web/` | MISSING | 3 |

Future feature documents (`V2_NEW_FEATURES.md` and `V2_FEATURE_PORT.md`) are a
post-parity roadmap. They are not acceptance evidence for the current build.
