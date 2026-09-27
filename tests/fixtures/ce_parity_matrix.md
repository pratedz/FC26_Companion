# CE → LE Profile Executor parity matrix

Reference: `FC 25 CT v25.1.6` (xAranaktu) vs `LE_Profile_Executor` (FC 26 LE queue companion).

| CE area | Status | Mapping in Profile Executor |
|---------|--------|------------------------------|
| Players Editor (attrs/DB) | already-had + ported | Editor tab + Apply; **Recalc OVR** / Best-At via `src/ovr_formula.py` |
| OVR formula + Best At | **ported** | `ovr_formula.preferred_position_ovr`, `best_at_positions`; CLI `--recalc-ovr`; Editor button |
| FUT card clone / import | already-had | Cards tab, Import, FutGG/Futbin, `product.import_card` |
| Teams Editor GUI | non-portable (GUI) / LE-API-only | Squad board + target pick; no full team form clone |
| Transfer HUB (memory approach flags) | **non-portable** | Memory AOB only |
| TransferPlayer / Loan / Release | **ported** | `career_ops` + `product.career_*` + Boost Career ops panel + CLI |
| Transfer budget get/set | **ported** | `generate_*_transfer_budget_lua` + CLI + UI |
| Terminate loan | **ported** | `career_terminate_loan` / CLI |
| Mass fitness/form/morale/sharpness | already-had | snippets + packs `matchday`, `squad_boost` |
| Auto daily fitness/sharpness events | already-had | stock scripts auto_max_* profiles |
| Extend user contracts | already-had | profile `extend_contracts` |
| Extend CPU contracts | **ported** (wired) | profile `extend_cpu_contracts` |
| Never retire / force retire | already-had + wired | `never_retire`, `force_retire` |
| Mass edit age | **ported** (wired) | `mass_edit_age` → `lua/scripts/mass_edit_age.lua` |
| Mass squad role | **ported** (wired) | `mass_edit_squadrole` |
| 99 pot / 99 ovr scripts | **ported** (wired + new `99pot.lua`) | `pot_99_all`, `ovr_99_pot_99`, team variants |
| Fix head models | **ported** (wired) | `fix_heads` |
| Custom head/tattoo maps | **ported** (wired) | `custom_headasset_map`, `custom_tattoos_map` |
| Untuck / socks / visual | **ported** (wired) | `untuck_shirts`, `medium_socks`; pack `mass_visual` |
| Tight shirts (jerseyfit) | **ported** | profile `tight_shirts` → `lua/scripts/tight_shirts.lua` |
| Randomize shoe models | **ported** | profile `randomize_shoe_models` → `lua/scripts/randomize_shoe_models.lua`; pack `shoe_random` |
| Set generic heads (list) | **ported** | `set_generic_heads`, `set_generic_heads_alt` (edit playerid list in script) |
| Players list never-retire | **ported** | `players_list_retiring` (edit listed playerids) |
| Custom headasset → manager | **ported** | `custom_headasset_to_manager` (edit teamid→head map) |
| Unlock boots / mgr clothes | **ported** (wired) | `unlock_boots`, `unlock_mgr_clothes`; pack `unlocks_all` |
| Export season stats | **ported** (wired) | `export_season_stats`; pack `export_season` |
| Export transfer history | **ported** (wired) | `export_transfer_history` |
| Export fixtures / list players | **ported** (wired) | profiles |
| Delete generated / transfer ban / small squad | **ported** (wired) | career_cleanup category |
| Play As Player playstyles / VPro fitness | **ported** (wired) | `pap_all_playstyles`, `auto_max_vpro_fitness` |
| Youth Academy memory hooks | **non-portable** | AOB / mode managers |
| Hire scout free 5/5 | **non-portable** | memory |
| Job offers / negotiation status | **non-portable** | memory |
| GTN reveal scouting | **non-portable** | memory |
| Match timer / weather / stadium | **non-portable** | Gameplay memory |
| Side changer / unlimited subs | **non-portable** | Gameplay memory |
| Don’t pause AltTab | **non-portable** | memory |
| Camera pointers | **non-portable** | memory |
| Training XP multiplier | **non-portable** | growth pointer XP (not DOC API) |
| Randomize all attrs | **non-portable** (risk) | CE warns career break; not shipped as default profile |
| CE headshot CDN cache UI | non-goal | functional parity only |

## CLI entry points
- `--list-profiles` / `--run-profile <id>`
- `--list-packs` / `--run-pack <id>`
- `--list-career-ops`
- `--career-transfer --playerid N --to-teamid T`
- `--career-loan --playerid N --to-teamid T`
- `--career-release --playerid N`
- `--career-set-budget AMOUNT` / `--career-get-budget`
- `--recalc-ovr --query Name`
- `--dump-career-lua` (print only)

## Static safety
- No CE AOB / FakeEAAC in `career_ops.py` or `ovr_formula.py` (asserted by tests).
