"""Regression coverage for the compact V2 Automations workspace."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import CORE_VERSION
from companion.domain.automation_guide import trusted_cached_teamid
from companion.domain.pack_library import (
    actions as pack_actions,
    build_job as build_pack_job,
    load_v1_packs,
)
from companion.domain.profile_library import (
    actions as profile_actions,
    build_job as build_profile_job,
)
from companion.core.clock import FakeClock


ROOT = Path(__file__).resolve().parents[2]


def _state(*, stale: bool = False, squad_session: str = "session-a", live_session: str = "session-a", age: int = 0):
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        squad=SimpleNamespace(
            stale=stale,
            players=({"playerid": 1},),
            teamid=21,
            session_id=squad_session,
            taken=now - timedelta(seconds=age),
        ),
        bridge=SimpleNamespace(liveness=SimpleNamespace(session_id=live_session)),
    ), now


def test_fresh_same_session_team_hint_is_emitted_for_career_boosts():
    state, now = _state()
    assert trusted_cached_teamid(state, now=now) == 21

    profile = build_profile_job("full_fitness", teamid=21).to_wire()
    assert profile["ops"][0]["op"] == "career.set"
    assert profile["ops"][0]["teamid"] == 21

    pack = build_pack_job("squad_boost", teamid=21).to_wire()
    assert pack["ops"][0]["op"] == "career.set"
    assert pack["ops"][0]["teamid"] == 21


def test_stale_or_other_session_squad_never_supplies_a_team_hint():
    stale, now = _state(stale=True)
    assert trusted_cached_teamid(stale, now=now) is None

    old_session, now = _state(squad_session="old", live_session="new")
    assert trusted_cached_teamid(old_session, now=now) is None

    old_read, now = _state(age=121)
    assert trusted_cached_teamid(old_read, now=now) is None

    no_live_session, now = _state(live_session="")
    assert trusted_cached_teamid(no_live_session, now=now) is None


def test_career_worker_uses_the_same_confirmed_membership_and_setters_as_v1():
    source = (ROOT / "ingame" / "le_companion" / "ops.lua").read_text(encoding="utf-8")
    career_block = source.split('ops.handlers["career.set"]', 1)[1].split('ops.handlers[', 1)[0]
    # V1 snippets require this helper; it defines both GetUserSeniorTeamPlayerIDs
    # and the concrete SetPlayer* APIs in the installed Live Editor helpers.lua.
    assert 'pcall(require, "imports/career_mode/helpers")' in source
    assert "GetUserSeniorTeamPlayerIDs" in source
    for name in ("SetPlayerFitness", "SetPlayerSharpness", "SetPlayerForm", "SetPlayerMorale"):
        assert name in source
    for wrong in ("PlayerSetFitness", "PlayerSetSharpness", "PlayerSetForm", "PlayerSetMorale"):
        assert wrong not in source
    assert "team_changed" in source
    assert "user_senior_playerids" in source
    assert "targets = user_senior_playerids()" in career_block
    assert "requested_teamid or live_teamid" not in career_block
    # A generic bulk edit for another club must not fall back to the user's
    # host helper simply because its own teamplayerlinks rows are absent.
    assert "active_teamid and tonumber(teamid) == active_teamid" in source


def test_career_boosts_require_the_target_safe_worker_operation_version():
    wire = build_profile_job("full_fitness").to_wire()
    assert wire["ops"][0]["op"] == "career.set"
    assert wire["ops"][0]["v"] == 2
    version = (ROOT / "ingame" / "le_companion" / "version.lua").read_text(encoding="utf-8")
    assert f'version.VERSION = "{CORE_VERSION}"' in version
    assert '["career.set"]    = 2' in version


def test_automation_workspace_is_fixed_first_and_uses_the_shared_queue():
    source = (ROOT / "companion" / "ui" / "surfaces" / "automations.py").read_text(encoding="utf-8")
    assert "CTkScrollableFrame" not in source
    assert "CTkTabview" not in source
    assert "Activity" in source
    assert "Match ready" in source
    assert "Other matchday actions" in source
    assert "Customize individual boosts" in source
    assert "PRIORITY_PROFILES" in source
    assert "follow=False" in source
    assert "waiting for a safe career event" in source.lower()
    assert "retry_recorded_automation" in source
    assert source.index("_priority_workspace(root") < source.index("_queue_panel(queue_host")
    assert "refresh_automation_queue" in source
    assert 'list(view["active"])[:1]' in source

    shell = (ROOT / "companion" / "ui" / "shell.py").read_text(encoding="utf-8")
    assert '"automations": ("target", "squad", "bridge_view", "jobs")' in shell
    assert "bind_activity(self.open_drawer)" in shell
    ui_actions = (ROOT / "companion" / "app" / "ui_actions.py").read_text(encoding="utf-8")
    assert "def open_activity" in ui_actions


def test_every_visible_native_automation_builds_a_real_worker_job():
    """A visible Queue button may never point at a missing/placeholder job."""
    packs = pack_actions(load_v1_packs())
    raw_profiles = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
    profiles = profile_actions(raw_profiles)

    for item in packs:
        if item.mode != "native":
            continue
        job = build_pack_job(item.id, teamid=21)
        wire = job.to_wire()
        assert wire["ops"], item.id
        assert all(op.get("op") for op in wire["ops"]), item.id

    for item in profiles:
        if item.mode != "native":
            continue
        job = build_profile_job(item.id, teamid=21)
        wire = job.to_wire()
        assert wire["ops"], item.id
        assert all(op.get("op") for op in wire["ops"]), item.id


def test_priority_submission_reports_success_after_the_job_is_written(monkeypatch):
    """Regression for the UI's old post-submit ``set_status`` TypeError."""
    from companion.ui.surfaces import automations

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    state = SimpleNamespace(
        bridge=SimpleNamespace(
            armed=True,
            liveness=SimpleNamespace(session_id="session-a", core_version="2.6.4"),
        ),
        squad=SimpleNamespace(
            stale=True, players=(), teamid=None, session_id="", taken=now,
        ),
    )
    events: list[object] = []
    svc = SimpleNamespace(
        store=SimpleNamespace(snapshot=lambda: state, dispatch=events.append),
        clock=FakeClock(),
    )
    submitted: list[object] = []
    monkeypatch.setattr(
        automations,
        "submit_job",
        lambda _svc, job, *, follow: submitted.append((job, follow)) or "01KYYTEST00000000000000000",
    )

    automations._submit_automation(
        svc,
        "Fitness",
        "full_fitness",
        lambda teamid: build_profile_job("full_fitness", teamid=teamid),
    )

    assert submitted and submitted[0][1] is False
    assert any("Fitness queued" in getattr(event, "text", "") for event in events)


def test_automation_surface_constructs_with_real_ctk_widgets(tmp_path):
    """Catch widget call-signature regressions that source-grep cannot see."""
    ctk = pytest.importorskip("customtkinter")
    from companion.bootstrap import build_services
    from companion.ui.surfaces import automations

    try:
        root = ctk.CTk()
    except Exception as exc:  # pragma: no cover - headless CI only
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    svc = build_services(tmp_path, inline=True)
    try:
        page = automations.build(root, svc)
        page.pack(fill="both", expand=True)
        root.update_idletasks()
        assert page.winfo_exists()
    finally:
        root.destroy()
