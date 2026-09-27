"""The v2 command line. Headless, scriptable, and the fastest way to diagnose.

Every command here works with no GUI and, where possible, with no running
game — which is exactly what v1 lacked when something went wrong.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import CORE_VERSION, PROTOCOL_V, __version__
from .app.commands.apply import submit_job
from .app.presenters import job_report, liveness_view
from .bootstrap import build_services
from .domain.ids import is_ulid
from .domain.job import Job, Op, job_ping, op_budget
from .domain.pack_library import actions as pack_actions, build_job as build_pack_job, load_v1_packs
from .domain.profile_library import actions as profile_actions, build_job as build_profile_job
from .domain.teams import search_clubs


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_version(_args: argparse.Namespace) -> int:
    _print(
        {
            "app": __version__,
            "protocol": PROTOCOL_V,
            "core_expected": CORE_VERSION,
            "python": sys.version.split()[0],
        }
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    lv = svc.transport.liveness()
    view = liveness_view(lv)
    view["queue"] = {
        "pending": len(svc.transport.pending_ids()),
        "claimed": len(svc.transport.claimed_ids()),
    }
    view["queue_dir"] = str(svc.paths.queue)
    _print(view)
    return 0 if lv.armed else 1


def _submit_and_maybe_wait(
    svc: Any, job: Job, *, timeout: float, no_wait: bool
) -> tuple[str, Any | None]:
    """All mutating CLI paths go through apply.submit_job for one audit trail."""
    job_id = submit_job(svc, job, follow=False)
    print(f"submitted {job_id}", file=sys.stderr)
    if no_wait:
        return job_id, None
    result = svc.transport.await_result(job_id, timeout=timeout)
    return job_id, result


def cmd_ping(args: argparse.Namespace) -> int:
    """Submit a diag.ping and wait. The honest end-to-end liveness check."""
    svc = build_services(args.root, inline=True)
    job = job_ping(label="cli ping", origin="cli.ping")
    _job_id, result = _submit_and_maybe_wait(
        svc, job, timeout=args.timeout, no_wait=False
    )
    assert result is not None
    _print(job_report(result))
    return 0 if result.outcome.is_success else 2


def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a job from a JSON file of ops (developer/automation path)."""
    try:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"could not read job file: {exc}", file=sys.stderr)
        return 64
    body = raw if isinstance(raw, dict) else {}
    ops_raw = body.get("ops", raw if isinstance(raw, list) else [])
    if not isinstance(ops_raw, list):
        print("job file ops must be a JSON array", file=sys.stderr)
        return 64
    ops = []
    for i, o in enumerate(ops_raw):
        if not isinstance(o, dict) or not o.get("op"):
            print(f"ops[{i}] must be an object with an op", file=sys.stderr)
            return 64
        d = dict(o)
        name = d.pop("op")
        oid = d.pop("id", f"op{i}")
        ops.append(Op(name, oid, d))
    from .domain.job import Grants

    grants_raw = body.get("grants") or {}
    if not isinstance(grants_raw, dict):
        print("job grants must be an object", file=sys.stderr)
        return 64
    allowed_grants = {field.name for field in fields(Grants)}
    unknown_grants = set(grants_raw) - allowed_grants
    if unknown_grants:
        print(f"unknown job grants: {sorted(unknown_grants)}", file=sys.stderr)
        return 64

    job = Job(
        ops=tuple(ops),
        label=body.get("label", Path(args.file).stem),
        origin="cli.submit",
        dry_run=bool(args.dry_run or body.get("dry_run", False)),
        grants=Grants(**grants_raw),
    )
    svc = build_services(args.root, inline=True)
    job_id, result = _submit_and_maybe_wait(
        svc, job, timeout=args.timeout, no_wait=args.no_wait
    )
    if result is None:
        _print({"job_id": job_id, "state": "queued"})
        return 0
    _print(job_report(result))
    return 0 if result.outcome.is_success else 2


def cmd_result(args: argparse.Namespace) -> int:
    if not is_ulid(args.job_id):
        print(f"not a job id: {args.job_id}", file=sys.stderr)
        return 64
    svc = build_services(args.root, inline=True)
    result = svc.transport.result(args.job_id)
    if result is None:
        _print({"job_id": args.job_id, "state": "unknown", "note": "no result file yet"})
        return 3
    _print(job_report(result))
    return 0 if result.outcome.is_success else 2


def cmd_queue(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    _print(
        {
            "pending": svc.transport.pending_ids(),
            "claimed": svc.transport.claimed_ids(),
            "queue_dir": str(svc.paths.queue),
        }
    )
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Emit crashed results for jobs a dead session left claimed."""
    svc = build_services(args.root, inline=True)
    swept = svc.transport.sweep_crashed()
    _print({"swept": swept, "count": len(swept)})
    return 0


def cmd_force_drain(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    print(svc.transport.force_drain_snippet())
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install the resident Lua core + autorun stub into the LE data dir."""
    try:
        from .platform import le_install
    except ImportError as e:
        print(f"installer unavailable: {e}", file=sys.stderr)
        return 70
    svc = build_services(args.root, inline=True)
    try:
        report = le_install.install(queue_dir=svc.paths.queue, dry_run=args.dry_run)
    except Exception as e:  # noqa: BLE001 — surface the real reason
        print(f"install failed: {e}", file=sys.stderr)
        return 71
    _print(report if isinstance(report, dict) else {"result": str(report)})
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Everything the app knows about its own health, in one object."""
    from .app.commands import doctor

    svc = build_services(args.root, inline=True)
    report = doctor.run(svc).to_dict()
    report["app_version"] = __version__
    report["protocol"] = PROTOCOL_V
    report["paths"] = {
        "root": str(svc.paths.root),
        "queue": str(svc.paths.queue),
        "catalog": str(svc.paths.universe_db),
        "state_db": str(svc.paths.state_db),
    }
    _print(report)
    return 0 if report["healthy"] else 1


def cmd_migrate(args: argparse.Namespace) -> int:
    """Import v1's seven loose JSON files into state.sqlite. Idempotent."""
    from .app.commands import migrate

    svc = build_services(args.root, inline=True)
    report = migrate.run(svc.paths, write_report=not args.no_report, log=svc.log)
    _print(report.to_dict())
    return 0 if report.ok else 1


def _profiles_for(svc: Any) -> tuple[Any, ...]:
    return profile_actions(json.loads(svc.paths.profiles_file.read_text(encoding="utf-8")))


def cmd_profiles(args: argparse.Namespace) -> int:
    """List all v1 profiles and their truthful v2 migration state."""
    svc = build_services(args.root, inline=True)
    rows = _profiles_for(svc)
    _print({"count": len(rows), "profiles": [
        {"id": item.id, "label": item.label, "category": item.category,
         "mode": item.mode, "reason": item.reason}
        for item in rows
    ]})
    return 0


def cmd_run_profile(args: argparse.Namespace) -> int:
    """Run a typed v2 equivalent of a named v1 profile."""
    svc = build_services(args.root, inline=True)
    item = {row.id: row for row in _profiles_for(svc)}.get(args.profile_id)
    if item is None:
        print(f"unknown profile: {args.profile_id}", file=sys.stderr)
        return 64
    if item.mode == "pending":
        print(f"{item.label} is not available in v2 yet: {item.reason}", file=sys.stderr)
        return 69
    if item.mode == "native_mass" and not args.confirm_all:
        print("whole-save profile refused: pass --confirm-all explicitly", file=sys.stderr)
        return 64
    try:
        job = build_profile_job(item.id)
    except Exception as exc:  # noqa: BLE001
        print(f"could not build profile: {exc}", file=sys.stderr)
        return 65
    job_id, result = _submit_and_maybe_wait(
        svc, job, timeout=args.timeout, no_wait=args.no_wait
    )
    if result is None:
        _print({"job_id": job_id, "state": "queued", "profile": item.id})
        return 0
    _print(job_report(result))
    return 0 if result.outcome.is_success else 2


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .ui.shell import run_gui
    except ImportError as e:
        print(f"GUI unavailable: {e}", file=sys.stderr)
        return 70
    return int(run_gui(root=args.root) or 0)


def cmd_packs(args: argparse.Namespace) -> int:
    rows = pack_actions(load_v1_packs())
    _print({"count": len(rows), "packs": [
        {"id": p.id, "label": p.label, "category": p.category,
         "mode": p.mode, "reason": p.reason}
        for p in rows
    ]})
    return 0


def cmd_run_pack(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    item = {p.id: p for p in pack_actions(load_v1_packs())}.get(args.pack_id)
    if item is None:
        print(f"unknown pack: {args.pack_id}", file=sys.stderr)
        return 64
    if item.mode == "pending":
        print(f"{item.label} is not available in v2 yet: {item.reason}", file=sys.stderr)
        return 69
    if item.mode == "native_mass" and not args.confirm_all:
        print("whole-save pack refused: pass --confirm-all explicitly", file=sys.stderr)
        return 64
    try:
        job = build_pack_job(item.id)
    except Exception as exc:  # noqa: BLE001
        print(f"could not build pack: {exc}", file=sys.stderr)
        return 65
    job_id, result = _submit_and_maybe_wait(
        svc, job, timeout=args.timeout, no_wait=args.no_wait
    )
    if result is None:
        _print({"job_id": job_id, "state": "queued", "pack": item.id})
        return 0
    _print(job_report(result))
    return 0 if result.outcome.is_success else 2


def cmd_search_cards(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    if svc.catalog is None:
        print("catalog unavailable", file=sys.stderr)
        return 69
    hits = svc.catalog.search(
        args.query, year=args.year or "", ovr_min=args.ovr_min,
        ovr_max=args.ovr_max, limit=args.limit,
    )
    _print({"count": len(hits), "results": [dict(h) for h in hits]})
    return 0 if hits else 1


def cmd_best_match(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    if svc.catalog is None:
        print("catalog unavailable", file=sys.stderr)
        return 69
    target = {"name": args.name, "playerid": args.playerid}
    hits = svc.catalog.best_matches(target, year=args.year or "", limit=args.limit)
    _print({"count": len(hits), "results": [dict(h) for h in hits]})
    return 0 if hits else 1


def cmd_search_clubs(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    db = svc.paths.root / "card_db" / "teams.sqlite"
    hits = search_clubs(db, args.query, limit=args.limit)
    _print({"count": len(hits), "clubs": hits})
    return 0 if hits else 1


def cmd_sync_futgg(args: argparse.Namespace) -> int:
    """Bulk-download FUT.GG specials (promos/Icons/Heroes) for given years."""
    years = [y.strip() for y in str(args.years).split(",") if y.strip()]
    print(f"Syncing FUT.GG for years: {years}", file=sys.stderr)
    try:
        from src import futgg_client  # type: ignore
    except ImportError as exc:
        print(f"futgg_client unavailable: {exc}", file=sys.stderr)
        return 70
    results = futgg_client.sync_years(
        years,
        max_pages=args.max_pages,
        progress=lambda m: print(m, file=sys.stderr),
    )
    _print({"results": results, "years": years})
    return 0


def cmd_rebuild_catalog(args: argparse.Namespace) -> int:
    """Force rebuild card_db/catalog.sqlite from CSVs + FUT.GG dumps."""
    try:
        from src import product  # type: ignore
    except ImportError as exc:
        print(f"product.rebuild_catalog unavailable: {exc}", file=sys.stderr)
        return 70
    report = product.rebuild_catalog(progress=lambda f, m: print(f"{f:.0%} {m}", file=sys.stderr))
    _print(report if isinstance(report, dict) else {"result": str(report)})
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    svc = build_services(args.root, inline=True)
    action = "set" if args.amount is not None else "get"
    job = Job(
        ops=(op_budget("budget", action=action, transfer=args.amount, scope=args.scope),),
        label=f"budget {action}",
        origin="cli.budget",
        require_cm=True,
    )
    job_id, result = _submit_and_maybe_wait(
        svc, job, timeout=args.timeout, no_wait=args.no_wait
    )
    if result is None:
        _print({"job_id": job_id, "state": "queued"})
        return 0
    _print(job_report(result))
    return 0 if result.outcome.is_success else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="companion",
        description="FC 26 Career Companion v2 — works alongside the Live Editor.",
    )
    p.add_argument("--root", type=Path, default=None, help="app root (defaults to install dir)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn: Any, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)
        return sp

    add("version", cmd_version, "print versions")
    add("status", cmd_status, "bridge liveness (ARMED/LIVE/WAITING/STALLED/OFF)")
    add("doctor", cmd_doctor, "full health report")
    add("queue", cmd_queue, "list pending and claimed jobs")
    add("sweep", cmd_sweep, "report jobs a dead session abandoned")
    add("force-drain", cmd_force_drain, "print the Lua Engine escape-hatch snippet")

    sp = add("ping", cmd_ping, "submit a diag.ping and wait for the result")
    sp.add_argument("--timeout", type=float, default=45.0)

    sp = add("submit", cmd_submit, "submit a job from a JSON ops file")
    sp.add_argument("file", type=Path)
    sp.add_argument("--timeout", type=float, default=45.0)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-wait", action="store_true")

    sp = add("result", cmd_result, "print the result for a job id")
    sp.add_argument("job_id")

    sp = add("install", cmd_install, "install the resident Lua core into LE")
    sp.add_argument("--dry-run", action="store_true")

    sp = add("migrate", cmd_migrate, "import v1 user data into state.sqlite (idempotent)")
    sp.add_argument("--no-report", action="store_true", help="do not write logs/migrate-report.json")

    add("profiles", cmd_profiles, "list v1 profiles and v2 migration status")
    sp = add("run-profile", cmd_run_profile, "run a typed v2 equivalent of a v1 profile")
    sp.add_argument("profile_id")
    sp.add_argument("--confirm-all", action="store_true", help="allow an explicit whole-save profile")
    sp.add_argument("--timeout", type=float, default=45.0)
    sp.add_argument("--no-wait", action="store_true")

    add("packs", cmd_packs, "list v1 product packs and v2 migration status")
    sp = add("run-pack", cmd_run_pack, "run a typed v2 equivalent of a v1 product pack")
    sp.add_argument("pack_id")
    sp.add_argument("--confirm-all", action="store_true", help="allow an explicit whole-save pack")
    sp.add_argument("--timeout", type=float, default=45.0)
    sp.add_argument("--no-wait", action="store_true")

    sp = add("search-cards", cmd_search_cards, "search the local card universe/catalog")
    sp.add_argument("query")
    sp.add_argument("--year", default="")
    sp.add_argument("--ovr-min", type=int, default=None)
    sp.add_argument("--ovr-max", type=int, default=None)
    sp.add_argument("--limit", type=int, default=20)

    sp = add("best-match", cmd_best_match, "rank cards for a player name")
    sp.add_argument("name")
    sp.add_argument("--playerid", type=int, default=None)
    sp.add_argument("--year", default="")
    sp.add_argument("--limit", type=int, default=8)

    sp = add("search-clubs", cmd_search_clubs, "search teams.sqlite for a destination club")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=8)

    sp = add("budget", cmd_budget, "queue get/set transfer budget (Career Mode)")
    sp.add_argument("--amount", type=int, default=None, help="set this amount; omit to read")
    sp.add_argument("--scope", choices=("user", "cpu"), default="user")
    sp.add_argument("--timeout", type=float, default=45.0)
    sp.add_argument("--no-wait", action="store_true")

    sp = add("sync-futgg", cmd_sync_futgg, "download FUT.GG specials/promos into card_db/futgg/")
    sp.add_argument("--years", default="23,24,25,26", help="comma years (default 23-26)")
    sp.add_argument("--max-pages", type=int, default=None, help="optional page cap (50 cards/page)")

    add("rebuild-catalog", cmd_rebuild_catalog, "force rebuild card_db/catalog.sqlite search index")

    add("gui", cmd_gui, "launch the desktop UI")
    return p


def _force_utf8_stdio() -> None:
    """Windows consoles default to cp1252 and mangle the em-dashes in status
    messages. Reconfigure rather than degrading the copy."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
