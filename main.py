#!/usr/bin/env python3
"""FC 26 Live Editor Profile Executor — external control + dual-mode companion.

External program: config, profiles, card apply, queue jobs.
Inject-side: LECompanionInject.dll + Lua bridge (protocol drain).
LE Launcher owns FakeEAAC + FCLiveEditor.DLL inject — companion does not.

Usage examples:
  python main.py
  python main.py --gui
  python main.py --web
  python main.py --web --port 8765 --no-browser
  python main.py --list-profiles
  python main.py --dump-profile full_fitness
  python main.py --run-profile full_fitness
  python main.py --search-cards "Messi" --year local
  python main.py --apply-card --year local --query "Messi" --target-playerid 158023
  python main.py --protocol-self-check
  python main.py --install-worker
  python main.py --headless-check

Primary GUI is the CustomTkinter desktop app (--gui / double-click with no flags).
Optional web UI remains available via --web.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python main.py` from LE_Profile_Executor/ without installing the package.
_APP_ROOT = Path(__file__).resolve().parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from src import actions  # noqa: E402
from src import card_catalog  # noqa: E402
from src import card_to_lua  # noqa: E402
from src import companion_config  # noqa: E402
from src import inject_side  # noqa: E402
from src import le_apply  # noqa: E402
from src import lua_resolve  # noqa: E402
from src import paths  # noqa: E402
from src import profiles  # noqa: E402
from src import protocol  # noqa: E402
from src import target_players  # noqa: E402
from src.futbin_client import (  # noqa: E402
    fetch_player_url,
    import_from_html,
    import_from_json,
    probe_futbin,
)
from src import futgg_client  # noqa: E402
from src import product  # noqa: E402
from src import card_match  # noqa: E402
from src import snapshot_store  # noqa: E402


def _cmd_list_profiles(_: argparse.Namespace) -> int:
    for p in profiles.load_profiles():
        print(f"{p.id:32}  [{p.category}]  {p.label}")
        if p.description:
            print(f"  {p.description}")
    return 0


def _cmd_dump_profile(args: argparse.Namespace) -> int:
    lua = lua_resolve.dump_profile(args.dump_profile)
    sys.stdout.write(lua if lua.endswith("\n") else lua + "\n")
    return 0


def _cmd_run_profile(args: argparse.Namespace) -> int:
    """Always uses product turbo path (clear-stale, meta, short poll, history)."""
    r = product.run_profile_turbo(args.run_profile, wait=True)
    print(f"profile: {args.run_profile}  outcome={r.outcome}  applied={r.applied}")
    print(f"reason:  {r.reason}")
    if r.queue_file:
        print(f"queue:   {r.queue_file}")
    if args.clipboard and r.queue_file:
        try:
            lua = Path(r.queue_file).read_text(encoding="utf-8")
            actions.copy_to_clipboard(lua)
            print("clipboard: ok")
        except OSError:
            print("clipboard: failed (queue file unreadable)")
    return 0 if r.applied or r.outcome in ("queued_live", "applied") else 1


def _cmd_search_cards(args: argparse.Namespace) -> int:
    hits = card_catalog.search_cards(
        args.search_cards or args.query or "",
        year=args.year,
        ovr=args.ovr,
        limit=args.limit,
    )
    if not hits:
        print("No cards matched.")
        return 0
    for i, c in enumerate(hits):
        print(card_catalog.format_card_line(c, i))
    return 0


def _cmd_search_target(args: argparse.Namespace) -> int:
    q = args.search_target or ""
    print(target_players.squad_status_line())
    hits = target_players.search_target(
        q,
        limit=args.limit,
        include_catalog_fallback=True,
        year=args.year or "26",
    )
    if not hits:
        print("No target matches. Export squad from Career Mode first (profile: export_user_squad).")
        return 1
    for i, p in enumerate(hits):
        print(target_players.format_target_line(p, i))
    return 0


def _cmd_apply_card(args: argparse.Namespace) -> int:
    query = args.query or args.search_cards or ""
    if not query and args.year is None:
        print("error: --apply-card requires --query (and usually --year)", file=sys.stderr)
        return 2
    if args.target_playerid is None:
        print("error: --apply-card requires --target-playerid", file=sys.stderr)
        return 2

    hits = card_catalog.search_cards(
        query,
        year=args.year,
        ovr=args.ovr,
        limit=max(args.limit, (args.index or 0) + 1),
    )
    if not hits:
        print("No cards matched.", file=sys.stderr)
        return 1
    idx = int(args.index or 0)
    if idx < 0 or idx >= len(hits):
        print(f"error: --index {idx} out of range (0..{len(hits) - 1})", file=sys.stderr)
        return 1

    card = hits[idx]
    print("Selected:", card_catalog.format_card_line(card, idx))
    lua = card_to_lua.generate_apply_card_lua(card, int(args.target_playerid))

    if args.dump_only:
        sys.stdout.write(lua if lua.endswith("\n") else lua + "\n")
        return 0

    if getattr(args, "batch_targets", None):
        ids = [int(x.strip()) for x in str(args.batch_targets).split(",") if x.strip()]
        out = product.batch_apply_card(card, ids, wait_each=False, wait_last=True)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1

    # Turbo product path (snapshot + meta + clear stale + wait)
    r = product.apply_card_to_target(card, int(args.target_playerid), wait=True)
    print(f"outcome={r.outcome} applied={r.applied}")
    print(f"reason={r.reason}")
    if r.queue_file:
        print(f"queue={r.queue_file}")
    if args.clipboard:
        actions.copy_to_clipboard(lua)
        print("clipboard: ok")
    return 0 if r.applied or r.outcome in ("queued_live", "applied") else 1


def _cmd_add_to_team(args: argparse.Namespace) -> int:
    """Search card and queue add-to-team (create or dummy + transfer)."""
    from src import add_player
    from src import target_players

    query = args.query or args.search_cards or ""
    if not query:
        print("error: --add-to-team requires --query", file=sys.stderr)
        return 2
    hits = card_catalog.search_cards(
        query,
        year=args.year,
        ovr=args.ovr,
        limit=max(args.limit, (args.index or 0) + 1),
    )
    if not hits:
        print("No cards matched.", file=sys.stderr)
        return 1
    idx = int(args.index or 0)
    if idx < 0 or idx >= len(hits):
        print(f"error: --index {idx} out of range", file=sys.stderr)
        return 1
    card = hits[idx]
    squad = target_players.load_squad()
    tid = int(getattr(args, "teamid", None) or 0) or int(squad.get("teamid") or 0)
    mode = str(getattr(args, "add_mode", None) or "auto")
    if getattr(args, "plan_add_to_team", False) or args.dump_only:
        plan = add_player.describe_add_plan(
            card,
            teamid=tid,
            mode=mode,
            protected_ids=add_player.protected_squad_ids(squad),
            dummy_pool=hits,
        )
        print(json.dumps(plan, indent=2))
        if args.dump_only:
            lua = add_player.generate_add_to_team_lua(card, teamid=tid, mode=mode)
            sys.stdout.write(lua if lua.endswith("\n") else lua + "\n")
        return 0
    if tid <= 0:
        print("error: no teamid — export squad first or pass context via current_squad.json", file=sys.stderr)
        return 2
    print("Selected:", card_catalog.format_card_line(card, idx))
    print(f"teamid={tid} mode={mode}")
    r = product.add_card_to_user_team(card, teamid=tid, mode=mode, wait=True)
    print(f"outcome={r.outcome} applied={r.applied}")
    print(f"reason={r.reason}")
    from src import apply_service as _asvc

    js = _asvc.format_result_with_job_status(r)
    if js:
        print(f"job_status={js}")
    elif (r.meta or {}).get("job_status"):
        print(f"job_status={r.meta.get('job_status')}")
    if r.queue_file:
        print(f"queue={r.queue_file}")
    return 0 if r.applied or r.outcome in ("queued_live", "applied") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="LE_Profile_Executor",
        description=(
            "FC 26 LE Companion — external control/config. "
            "Inject-side DLL + Lua bridge; LE Launcher owns FakeEAAC."
        ),
    )
    p.add_argument(
        "--list-profiles",
        action="store_true",
        help="List action profiles from profiles.json",
    )
    p.add_argument(
        "--dump-profile",
        metavar="ID",
        help="Print resolved Lua for profile ID to stdout",
    )
    p.add_argument(
        "--run-profile",
        metavar="ID",
        help="Resolve profile and write Lua to queue/ and generated/",
    )
    p.add_argument(
        "--search-cards",
        metavar="QUERY",
        nargs="?",
        const="",
        help="Search card_db + local LE cards by name",
    )
    p.add_argument(
        "--search-target",
        metavar="NAME",
        nargs="?",
        const="",
        help="Search current_squad.json (and catalog fallback) for target playerid by name",
    )
    p.add_argument(
        "--apply-card",
        action="store_true",
        help="Generate Lua applying a matched card onto --target-playerid",
    )
    p.add_argument("--query", default=None, help="Card name query (for --apply-card)")
    p.add_argument(
        "--year",
        default=None,
        help="Card year filter (e.g. 18, 2018, local, fc26-local)",
    )
    p.add_argument("--ovr", type=int, default=None, help="Exact overall filter")
    p.add_argument(
        "--target-playerid",
        type=int,
        default=None,
        help="In-game playerid to overwrite with card stats",
    )
    p.add_argument(
        "--index",
        type=int,
        default=0,
        help="Which search hit to use (default 0)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Max search results (default 25)",
    )
    p.add_argument(
        "--clipboard",
        action="store_true",
        help="Also copy generated Lua to the clipboard",
    )
    p.add_argument(
        "--no-queue",
        action="store_true",
        help="Do not write to queue/",
    )
    p.add_argument(
        "--no-generated",
        action="store_true",
        help="Do not write to generated/",
    )
    p.add_argument(
        "--dump-only",
        action="store_true",
        help="With --apply-card, print Lua only (no queue/generated)",
    )
    p.add_argument(
        "--web",
        action="store_true",
        help="Open optional web UI (browser + local API)",
    )
    p.add_argument(
        "--web-host",
        default="127.0.0.1",
        help="Web UI bind host (default 127.0.0.1)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Web UI port (default 8765)",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="With --web, do not open a browser window",
    )
    p.add_argument(
        "--gui",
        action="store_true",
        help="Open CustomTkinter desktop GUI (default with no flags)",
    )
    p.add_argument(
        "--paths",
        action="store_true",
        help="Print resolved app/LE/queue paths",
    )
    p.add_argument(
        "--futbin-probe",
        action="store_true",
        help="Probe whether live Futbin is reachable",
    )
    p.add_argument(
        "--futbin-year",
        default="26",
        help="Year for Futbin probe/import (default 26)",
    )
    p.add_argument(
        "--import-futbin-html",
        metavar="PATH",
        help="Import a browser-saved Futbin player HTML into card_db/futbin",
    )
    p.add_argument(
        "--import-futbin-json",
        metavar="PATH",
        help="Import Futbin-like JSON card(s) into card_db/futbin",
    )
    p.add_argument(
        "--futbin-url",
        metavar="URL",
        help="Fetch a Futbin player URL if Cloudflare allows",
    )
    p.add_argument(
        "--sync-futgg",
        action="store_true",
        help="Bulk-download ALL FUT cards from FUT.GG (Futbin alternative; specials included)",
    )
    p.add_argument(
        "--years",
        default="23,24,25,26",
        help="Comma years for --sync-futgg (default 23,24,25,26)",
    )
    p.add_argument(
        "--sync-player",
        metavar="NAME",
        help="Download ALL FUT versions for one name from FUT.GG (e.g. Neymar)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional page cap for --sync-futgg (50 cards/page; API max ~200)",
    )
    p.add_argument(
        "--protocol-self-check",
        action="store_true",
        help="Headless: write job → protocol drain → assert result markers",
    )
    p.add_argument(
        "--native-self-check",
        action="store_true",
        help="Headless: drive LECompanionInject.dll ProcessQueue if built",
    )
    p.add_argument(
        "--install-worker",
        action="store_true",
        help="Install Lua bridge + inject DLL under LE lua/scripts (no FakeEAAC)",
    )
    p.add_argument(
        "--build-inject-dll",
        action="store_true",
        help="Build LECompanionInject.dll via native/le_companion_inject/build.bat",
    )
    p.add_argument(
        "--headless-check",
        action="store_true",
        help="Config round-trip + protocol self-check + integration status (no GUI)",
    )
    p.add_argument(
        "--save-config",
        action="store_true",
        help="Write companion_config.json (use with --target-playerid for default)",
    )
    p.add_argument(
        "--show-config",
        action="store_true",
        help="Print companion_config.json",
    )
    p.add_argument(
        "--integration-status",
        action="store_true",
        help="Print LE / FakeEAAC / inject-side / bridge install snapshot",
    )
    # Product / performance CLI
    p.add_argument("--list-packs", action="store_true", help="List product boost packs")
    p.add_argument("--run-pack", metavar="ID", help="Run product pack (matchday, squad_boost, …)")
    p.add_argument("--arm-status", action="store_true", help="LIVE/arm product status")
    p.add_argument(
        "--turbo-arm",
        action="store_true",
        help="Same as --inject-arm (SAFE LE Load auto-arm, no paste)",
    )
    p.add_argument(
        "--inject-arm",
        action="store_true",
        help="Install worker + SAFE auto-arm into LE Load (no Lua paste; restart LE once)",
    )
    p.add_argument(
        "--remove-inject-arm",
        action="store_true",
        help="Remove SAFE auto-arm patch from live_editor.lua",
    )
    p.add_argument("--force-drain-signal", action="store_true", help="Write turbo wake for LE worker")
    p.add_argument("--rebuild-catalog", action="store_true", help="Force rebuild card_db/catalog.sqlite")
    p.add_argument("--best-match", metavar="NAME", help="Rank FUT cards for a name")
    p.add_argument("--list-snapshots", action="store_true", help="List apply snapshot history")
    p.add_argument("--restore-snapshot", metavar="SID", nargs="?", const="last", help="Restore snapshot (sid or last)")
    p.add_argument(
        "--batch-targets",
        metavar="IDS",
        help="Comma playerids for batch apply with --apply-card",
    )
    p.add_argument(
        "--add-to-team",
        action="store_true",
        help="Create/add card (or AI shell) to Career user team (needs --query or --dump-only plan)",
    )
    p.add_argument(
        "--add-mode",
        default="auto",
        choices=["auto", "create", "dummy"],
        help="add-to-team strategy: auto (create then dummy), create only, dummy only",
    )
    p.add_argument(
        "--plan-add-to-team",
        action="store_true",
        help="Print offline add-to-team plan JSON (no queue / no game)",
    )
    p.add_argument(
        "--turbo-apply",
        action="store_true",
        help="Deprecated no-op: --apply-card / --run-profile always use product turbo",
    )
    p.add_argument(
        "--set-categories",
        metavar="LIST",
        help="Sticky apply categories CSV (attributes,ratings,skills,...)",
    )
    # Career ops (LE DOC — CE LE-safe parity)
    p.add_argument("--list-career-ops", action="store_true", help="List career ops (transfer/loan/…)")
    p.add_argument(
        "--career-transfer",
        action="store_true",
        help="TransferPlayer: needs --playerid --to-teamid [fee/wage/months]",
    )
    p.add_argument("--career-loan", action="store_true", help="LoanPlayer: --playerid --to-teamid")
    p.add_argument("--career-release", action="store_true", help="ReleasePlayerFromTeam: --playerid")
    p.add_argument("--career-set-budget", metavar="AMOUNT", type=int, help="SetTransferBudget amount")
    p.add_argument("--career-get-budget", action="store_true", help="Queue GetTransferBudget log job")
    p.add_argument("--career-terminate-loan", action="store_true", help="TerminateLoan: --playerid")
    p.add_argument("--playerid", type=int, default=None, help="Career ops playerid")
    p.add_argument("--to-teamid", type=int, default=None, help="Career ops destination team id")
    p.add_argument("--from-teamid", type=int, default=0, help="Career ops source team (0=auto)")
    p.add_argument("--transfersum", type=int, default=0, help="Transfer fee")
    p.add_argument("--wage", type=int, default=5000, help="Wage for transfer to user club")
    p.add_argument("--contract-months", type=int, default=60, help="Contract length months")
    p.add_argument("--loan-months", type=int, default=12, help="Loan length months")
    p.add_argument(
        "--recalc-ovr",
        action="store_true",
        help="Print CE-style OVR + Best-At for --query card (no game)",
    )
    p.add_argument("--dump-career-lua", action="store_true", help="Print career op Lua only (no queue)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.web:
        from src.web_server import run_server

        run_server(
            getattr(args, "web_host", None) or "127.0.0.1",
            int(getattr(args, "port", None) or 8765),
            open_browser=not bool(getattr(args, "no_browser", False)),
        )
        return 0

    if args.gui:
        from src.gui import run_gui

        run_gui()
        return 0

    if args.show_config:
        print(json.dumps(companion_config.load_config(), indent=2))
        return 0

    if args.save_config:
        kwargs: dict = {}
        if args.target_playerid is not None:
            kwargs["default_target_playerid"] = int(args.target_playerid)
        cfg = companion_config.update_config(**kwargs) if kwargs else companion_config.load_config()
        if not kwargs:
            companion_config.save_config(cfg)
        print(json.dumps(companion_config.load_config(), indent=2))
        print(f"config: {companion_config.config_path()}")
        return 0

    if args.integration_status:
        print(json.dumps(inject_side.integration_status(), indent=2))
        return 0

    if args.build_inject_dll:
        result = inject_side.build_dll()
        print(result.get("output") or "")
        print(json.dumps({k: v for k, v in result.items() if k != "output"}, indent=2))
        return 0 if result.get("ok") else (2 if result.get("toolchain_missing") else 1)

    if args.install_worker:
        out = le_apply.install_bridge()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if args.set_categories:
        cats = [c.strip() for c in str(args.set_categories).split(",") if c.strip()]
        print(json.dumps(product.set_sticky_categories(cats), indent=2))
        return 0

    if args.list_packs:
        for pck in product.list_packs():
            print(f"{pck['id']:16}  [{pck['category']}]  {pck['label']}  — {pck['description']}")
        return 0

    if getattr(args, "list_career_ops", False):
        from src import career_ops

        for op in career_ops.list_career_ops():
            print(f"{op['id']:16}  {op['label']}  — {op['description']}")
        return 0

    if getattr(args, "recalc_ovr", False):
        from src import card_catalog as _cc
        from src import ovr_formula
        from src import player_schema

        q = args.query or ""
        if not q:
            print("error: --recalc-ovr needs --query", file=sys.stderr)
            return 2
        hits = []
        try:
            hits = _cc.search_cards(q, year=args.year or "26", limit=1)
        except Exception:
            hits = []
        if hits:
            card = player_schema.normalize_player_card(dict(hits[0]))
        else:
            # Offline demo attrs so formula still runs without catalog hit
            card = player_schema.normalize_player_card(
                {
                    "name": q,
                    "preferredposition1": 25,
                    "acceleration": 90,
                    "sprintspeed": 90,
                    "positioning": 90,
                    "finishing": 90,
                    "shotpower": 85,
                    "longshots": 80,
                    "volleys": 80,
                    "penalties": 80,
                    "vision": 75,
                    "crossing": 70,
                    "shortpassing": 80,
                    "longpassing": 70,
                    "curve": 75,
                    "agility": 85,
                    "balance": 80,
                    "reactions": 88,
                    "ballcontrol": 90,
                    "dribbling": 90,
                    "composure": 85,
                    "interceptions": 40,
                    "headingaccuracy": 75,
                    "defensiveawareness": 35,
                    "standingtackle": 35,
                    "slidingtackle": 30,
                    "jumping": 80,
                    "stamina": 85,
                    "strength": 80,
                    "aggression": 70,
                    "gkdiving": 10,
                    "gkhandling": 10,
                    "gkkicking": 10,
                    "gkreflexes": 10,
                    "gkpositioning": 10,
                }
            )
        updated = ovr_formula.apply_calculated_ovr_to_card(card)
        print(f"name={updated.get('name')} pos={updated.get('preferredposition1')}")
        print(f"ovr={updated.get('overallrating')}")
        print(ovr_formula.format_best_at_line(updated))
        return 0

    def _career_dump_or_run(lua: str, runner) -> int:
        if getattr(args, "dump_career_lua", False) or args.dump_only:
            print(lua)
            return 0
        r = runner()
        print(
            json.dumps(
                {"applied": r.applied, "outcome": r.outcome, "reason": r.reason, "detail": r.detail},
                indent=2,
            )
        )
        return 0 if r.applied or r.outcome in ("queued_live", "applied") else 1

    if getattr(args, "career_transfer", False):
        from src import career_ops

        if args.playerid is None or args.to_teamid is None:
            print("error: --career-transfer needs --playerid and --to-teamid", file=sys.stderr)
            return 2
        lua = career_ops.generate_transfer_lua(
            playerid=int(args.playerid),
            to_teamid=int(args.to_teamid),
            transfersum=int(args.transfersum or 0),
            wage=int(args.wage or 5000),
            contract_months=int(getattr(args, "contract_months", None) or 60),
            from_teamid=int(args.from_teamid or 0),
        )
        return _career_dump_or_run(
            lua,
            lambda: product.career_transfer(
                playerid=int(args.playerid),
                to_teamid=int(args.to_teamid),
                transfersum=int(args.transfersum or 0),
                wage=int(args.wage or 5000),
                contract_months=int(getattr(args, "contract_months", None) or 60),
                from_teamid=int(args.from_teamid or 0),
                wait=True,
            ),
        )

    if getattr(args, "career_loan", False):
        from src import career_ops

        if args.playerid is None or args.to_teamid is None:
            print("error: --career-loan needs --playerid and --to-teamid", file=sys.stderr)
            return 2
        lua = career_ops.generate_loan_lua(
            playerid=int(args.playerid),
            to_teamid=int(args.to_teamid),
            length_months=int(getattr(args, "loan_months", None) or 12),
            from_teamid=int(args.from_teamid or 0),
        )
        return _career_dump_or_run(
            lua,
            lambda: product.career_loan(
                playerid=int(args.playerid),
                to_teamid=int(args.to_teamid),
                length_months=int(getattr(args, "loan_months", None) or 12),
                from_teamid=int(args.from_teamid or 0),
                wait=True,
            ),
        )

    if getattr(args, "career_release", False):
        from src import career_ops

        if args.playerid is None:
            print("error: --career-release needs --playerid", file=sys.stderr)
            return 2
        lua = career_ops.generate_release_lua(playerid=int(args.playerid))
        return _career_dump_or_run(
            lua,
            lambda: product.career_release(playerid=int(args.playerid), wait=True),
        )

    if getattr(args, "career_set_budget", None) is not None:
        from src import career_ops

        amt = int(args.career_set_budget)
        lua = career_ops.generate_set_transfer_budget_lua(amount=amt)
        return _career_dump_or_run(
            lua,
            lambda: product.career_set_budget(amount=amt, wait=True),
        )

    if getattr(args, "career_get_budget", False):
        from src import career_ops

        lua = career_ops.generate_get_transfer_budget_lua()
        return _career_dump_or_run(lua, lambda: product.career_get_budget(wait=True))

    if getattr(args, "career_terminate_loan", False):
        from src import career_ops

        if args.playerid is None:
            print("error: --career-terminate-loan needs --playerid", file=sys.stderr)
            return 2
        lua = career_ops.generate_terminate_loan_lua(playerid=int(args.playerid))
        return _career_dump_or_run(
            lua,
            lambda: product.career_terminate_loan(playerid=int(args.playerid), wait=True),
        )

    if args.run_pack:
        out = product.run_pack(args.run_pack, wait=True)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if args.arm_status:
        print(json.dumps(product.arm_status(), indent=2))
        return 0

    if getattr(args, "remove_inject_arm", False):
        out = product.remove_inject_autoarm()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if args.inject_arm or args.turbo_arm:
        out = product.one_click_inject_arm()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if args.force_drain_signal:
        print(json.dumps(product.force_turbo_drain_signal(), indent=2))
        return 0

    if args.rebuild_catalog:
        print(json.dumps(product.rebuild_catalog(progress=lambda f, m: print(f"{f:.0%} {m}")), indent=2))
        return 0

    if args.best_match is not None:
        year = args.year or "26"
        ranked = card_match.best_matches(args.best_match, year=year, limit=args.limit)
        for i, (sc, c) in enumerate(ranked):
            c = dict(c)
            c["_match_score"] = round(sc, 2)
            print(card_match.format_match_line(c, i))
        return 0 if ranked else 1

    if args.list_snapshots:
        for i, row in enumerate(snapshot_store.list_snapshots()):
            print(snapshot_store.format_snapshot_line(row, i))
        return 0

    if args.restore_snapshot is not None:
        sid = None if args.restore_snapshot in ("last", "") else args.restore_snapshot
        r = product.restore_snapshot(sid, wait=True)
        print(json.dumps({"applied": r.applied, "outcome": r.outcome, "reason": r.reason}, indent=2))
        return 0 if r.applied or r.outcome == "queued_live" else 1

    if args.protocol_self_check:
        # Isolated temp queue only — never production paths.queue_dir()
        report = protocol.protocol_self_check(None)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok") else 1

    if args.native_self_check:
        # Isolated temp queue + dry-run marker only
        report = inject_side.native_protocol_self_check(None)
        print(json.dumps(report, indent=2, default=str))
        if report.get("skipped"):
            return 0  # honest skip when toolchain/dll missing
        return 0 if report.get("ok") else 1

    if args.headless_check:
        # Config round-trip uses temp file; protocol uses isolated queue.
        # Production companion_config.json and queue/ are never written/drained.
        cfg = companion_config.config_round_trip()
        proto = protocol.protocol_self_check(None)
        status = inject_side.integration_status()
        iso_q = protocol.make_isolated_selfcheck_queue()
        lua = lua_resolve.dump_profile("full_fitness")
        issued = protocol.write_job(iso_q, lua, stem="headless_full_fitness")
        drain = protocol.process_queue_protocol(iso_q, require_dry_run=True)
        parsed = protocol.parse_result_line(drain.result_line)
        report = {
            "config_ok": bool(cfg.get("ok")) and bool(cfg.get("production_untouched")),
            "protocol_ok": bool(proto.get("ok")),
            "profile_apply_ok": bool(parsed.get("ok")) and int(parsed.get("processed") or 0) >= 1,
            "issued_job": issued.get("job_name"),
            "result_line": drain.result_line,
            "isolated_queue": str(iso_q),
            "config_path": cfg.get("path"),
            "integration": status,
            "le_owns_anticheat": True,
            "companion_swaps_fakeeaac": False,
            "production_queue_untouched": True,
        }
        print(json.dumps(report, indent=2, default=str))
        ok = report["config_ok"] and report["protocol_ok"] and report["profile_apply_ok"]
        return 0 if ok else 1

    if args.sync_futgg:
        years = [y.strip() for y in str(args.years).split(",") if y.strip()]
        print(f"Syncing FUT.GG catalog for years: {years}")
        print("(Futbin website is Cloudflare-blocked; FUT.GG provides the full FUT card DB.)")
        results = futgg_client.sync_years(
            years,
            max_pages=args.max_pages,
            progress=print,
        )
        print(json.dumps(results, indent=2))
        return 0

    if args.sync_player:
        rows = futgg_client.sync_player_all_versions(args.sync_player, progress=print)
        print(f"Cached {len(rows)} card version(s) for {args.sync_player!r}")
        for r in rows[:20]:
            print(
                f"  y={r.get('year')} OVR={r.get('overallrating')} "
                f"rev={r.get('revision')!r} name={r.get('name')!r} slug={r.get('slug')}"
            )
        return 0

    if args.futbin_probe:
        print(json.dumps(probe_futbin(args.futbin_year), indent=2))
        return 0

    if args.import_futbin_html:
        html = Path(args.import_futbin_html).read_text(encoding="utf-8", errors="replace")
        card = import_from_html(html, year=str(args.futbin_year), save=True)
        print(json.dumps(card, indent=2)[:3000])
        return 0

    if args.import_futbin_json:
        obj = json.loads(Path(args.import_futbin_json).read_text(encoding="utf-8"))
        cards = import_from_json(obj, year=str(args.futbin_year), save=True)
        print(f"Imported {len(cards)} card(s) into card_db/futbin")
        return 0

    if args.futbin_url:
        try:
            card = fetch_player_url(args.futbin_url, year=str(args.futbin_year), save=True)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 3
        print(json.dumps(card, indent=2)[:3000])
        return 0

    if args.paths:
        print(f"app_root:   {paths.app_root()}")
        print(f"le_root:    {paths.le_root()}")
        print(f"profiles:   {paths.profiles_path()}")
        print(f"queue:      {paths.queue_dir()}")
        print(f"generated:  {paths.generated_dir()}")
        print(f"card_db:    {paths.card_db_dir()}")
        print(f"cards.csv:  {paths.cards_csv_path()}")
        print(f"bridge:     {paths.bridge_dir() / 'le_profile_bridge.lua'}")
        return 0

    # Priority: explicit action flags
    if args.list_profiles:
        return _cmd_list_profiles(args)
    if args.dump_profile:
        return _cmd_dump_profile(args)
    if args.run_profile:
        return _cmd_run_profile(args)
    if args.search_target is not None:
        return _cmd_search_target(args)
    if getattr(args, "add_to_team", False) or getattr(args, "plan_add_to_team", False):
        return _cmd_add_to_team(args)

    if args.apply_card:
        return _cmd_apply_card(args)
    if args.search_cards is not None:
        return _cmd_search_cards(args)

    # No actionable flags → primary desktop GUI (double-click / no-args launch)
    user_argv = argv if argv is not None else sys.argv[1:]
    if not user_argv:
        from src.gui import run_gui

        run_gui()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
