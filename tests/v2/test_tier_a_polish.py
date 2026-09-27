"""Tier A polish: pack unify, club freshness, undo, palette."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from companion.app.presenters import SQUAD_TTL_SECONDS, squad_view
from companion.app.state import AppState, SquadState
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.pack_library import build_job, load_v1_packs, quick_rail
from companion.ui.palette import dispatch_palette, filter_commands, palette_commands
from companion.ui.surfaces.activity import confirm_undo


ROOT = Path(__file__).parents[2]


def test_quick_rail_and_pack_library_share_job_shape_for_shared_ids():
    rail = {p.id: p for p in quick_rail()}
    assert "match_ready" in rail
    assert "pre_match" in rail
    # Automations quick path and pack-library builder are the same function.
    for pid in ("match_ready", "club_refresh", "signing_settle", "pre_match", "squad_boost"):
        wire = build_job(pid).to_wire()
        ops = [o["op"] for o in wire["ops"]]
        assert ops, pid
        if pid == "match_ready":
            assert "export_squad" in ops and "career.set" in ops
            career = next(o for o in wire["ops"] if o["op"] == "career.set")
            assert career["fitness"] == 100
            assert career["sharpness"] == 100
        if pid == "squad_boost":
            # Product pack semantics (career fitness pack), not ad-hoc attr bulk.
            assert "career.set" in ops


def test_pre_match_alias_matches_pre_match_full_fields():
    a = build_job("pre_match").to_wire()
    b = build_job("pre_match_full").to_wire()
    assert [o["op"] for o in a["ops"]] == [o["op"] for o in b["ops"]]
    ca = next(o for o in a["ops"] if o["op"] == "career.set")
    cb = next(o for o in b["ops"] if o["op"] == "career.set")
    for key in ("fitness", "form", "morale", "sharpness"):
        assert ca.get(key) == cb.get(key)


def test_squad_view_with_fixed_now_reports_age_and_ttl_stale():
    taken = datetime(2026, 1, 1, 12, 0, 0)
    now = taken + timedelta(seconds=30)
    state = AppState(
        squad=SquadState(
            players=({"playerid": 1, "name": "A"},),
            taken=taken,
            stale=False,
            session_id="sess-a",
        ),
    )
    # Attach a liveness with matching session
    from companion.app.state import BridgeState

    state = AppState(
        squad=SquadState(
            players=({"playerid": 1, "name": "A"},),
            taken=taken,
            stale=False,
            session_id="sess-a",
        ),
        bridge=BridgeState(
            liveness=Liveness(
                pill=Pill.ARMED, message="ok", armed=True, session_id="sess-a",
            )
        ),
    )
    view = squad_view(state, now=now)
    assert view["count"] == 1
    assert view["age_seconds"] == pytest.approx(30.0)
    assert "as of" in view["caption"]
    assert view["ttl_stale"] is False
    assert view["stale"] is False

    aged = squad_view(state, now=taken + timedelta(seconds=SQUAD_TTL_SECONDS + 5))
    assert aged["ttl_stale"] is True
    assert aged["stale"] is True
    assert aged["needs_refresh"] is True
    assert "aged" in aged["caption"]


def test_squad_view_session_mismatch_is_stale():
    taken = datetime(2026, 1, 1, 12, 0, 0)
    from companion.app.state import BridgeState

    state = AppState(
        squad=SquadState(
            players=({"playerid": 1},),
            taken=taken,
            stale=False,
            session_id="old",
        ),
        bridge=BridgeState(
            liveness=Liveness(
                pill=Pill.LIVE, message="ok", armed=True, session_id="new",
            )
        ),
    )
    view = squad_view(state, now=taken + timedelta(seconds=10))
    assert view["session_stale"] is True
    assert view["stale"] is True


def test_confirm_undo_gate():
    assert confirm_undo("UNDO") is True
    assert confirm_undo("undo") is False
    assert confirm_undo("ALL") is False


def test_club_surface_passes_clock_into_squad_view():
    src = (ROOT / "companion" / "ui" / "surfaces" / "club.py").read_text(encoding="utf-8")
    assert "squad_view(state, now=" in src or "squad_view(state, now=now)" in src
    assert "Refresh squad" in src


def test_palette_commands_cover_surfaces_and_dispatch():
    cmds = palette_commands()
    ids = {c.id for c in cmds}
    assert "nav.club" in ids
    assert "nav.library" in ids
    assert "nav.add_player" in ids
    assert "activity" in ids
    assert "settings" in ids
    assert "doctor" in ids
    hits = filter_commands("club")
    assert any(c.id == "nav.club" for c in hits)
    seen: list[str] = []
    msg = dispatch_palette(
        next(c for c in cmds if c.id == "nav.player"),
        navigate=lambda k: seen.append(k),
    )
    assert seen == ["player"]
    assert "Player" in msg or "player" in msg.lower()


def test_shell_open_palette_is_not_stub_status_only():
    src = (ROOT / "companion" / "ui" / "shell.py").read_text(encoding="utf-8")
    assert "Command palette isn't wired" not in src
    assert "dispatch_palette" in src or "filter_commands" in src


def test_activity_refresh_rebuilds_and_undo_confirms():
    src = (ROOT / "companion" / "ui" / "surfaces" / "activity.py").read_text(
        encoding="utf-8"
    )
    assert "confirm_undo" in src
    assert "Type UNDO" in src
    assert "rebuild" in src
    assert "_render_body" in src
    assert "waiting for FC" not in src
    assert "Force Drain" in src
    assert "In Live Editor" in src


def test_palette_squad_planner_copy_is_honest():
    cmds = palette_commands()
    planner = next(c for c in cmds if c.id == "nav.club.planner")
    assert "multi-select" in planner.label.lower()
    assert "hint" not in planner.label.lower()


def test_player_review_is_on_the_tab_and_library_stage_copy_is_one_line():
    workstation = (
        ROOT / "companion" / "ui" / "surfaces" / "player" / "workstation.py"
    ).read_text(encoding="utf-8")
    assert '"Review changes"' in workstation or "'Review changes'" in workstation
    library = (ROOT / "companion" / "ui" / "surfaces" / "library.py").read_text(
        encoding="utf-8"
    )
    assert "Send to Sign with no lock" in library
    automations = (ROOT / "companion" / "ui" / "surfaces" / "automations.py").read_text(
        encoding="utf-8"
    )
    assert "refresh_automation_queue" in automations


def test_cli_mutators_use_submit_job_helper():
    src = (ROOT / "companion" / "cli.py").read_text(encoding="utf-8")
    assert "_submit_and_maybe_wait" in src
    assert "submit_job(svc, job, follow=False)" in src
    # Direct transport.submit for mutating profile/pack/budget/ping should be gone
    # from those command bodies (helper owns it).
    assert "from .app.commands.apply import submit_job" in src
