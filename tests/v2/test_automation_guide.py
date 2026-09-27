"""Automations intent groups + empty-team preflight / failure copy."""

from __future__ import annotations

from types import SimpleNamespace

from companion.domain.automation_guide import (
    EMPTY_TEAM_HINT,
    NO_TEAM_HINT,
    SECTIONS,
    TEAM_SCOPED_IDS,
    group_items,
    map_failure_reason,
    preflight_team_scope,
    primary_rail_ids,
    section_for,
    team_scoped,
)
from companion.domain.pack_library import PackAction, QUICK_RAIL_IDS


def _pack(
    pid: str,
    *,
    category: str = "workflow",
    mode: str = "native",
) -> PackAction:
    return PackAction(
        id=pid,
        label=pid.replace("_", " ").title(),
        description="test",
        category=category,
        mode=mode,
    )


def _state(*, players=(), teamid=None, armed=False, core_version="2.6.4"):
    return SimpleNamespace(
        bridge=SimpleNamespace(armed=armed, liveness=SimpleNamespace(core_version=core_version)),
        squad=SimpleNamespace(players=tuple(players), teamid=teamid),
    )


def test_section_for_known_and_fallback():
    assert section_for("match_ready") == "matchday"
    assert section_for("growth_sync") == "growth"
    assert section_for("ping") == "diag"
    assert section_for("never_retire") == "advanced"
    assert section_for("unknown_xyz", "workflow") == "matchday"
    assert section_for("unknown_xyz", "mass") == "advanced"
    assert section_for("totally_new") == "other"


def test_group_items_orders_sections_and_buckets():
    items = [
        _pack("match_ready"),
        _pack("growth_sync"),
        _pack("ping", category="diag"),
        _pack("never_retire", category="mass"),
        _pack("custom_pack", category="misc"),
    ]
    grouped = group_items(items)
    keys = [k for k, *_ in grouped]
    section_order = [k for k, *_ in SECTIONS]
    # Relative order matches SECTIONS order.
    assert keys == sorted(keys, key=section_order.index)
    by_key = {k: acts for k, _t, _b, acts in grouped}
    assert [a.id for a in by_key["matchday"]] == ["match_ready"]
    assert [a.id for a in by_key["growth"]] == ["growth_sync"]
    assert [a.id for a in by_key["diag"]] == ["ping"]
    assert [a.id for a in by_key["advanced"]] == ["never_retire"]
    assert [a.id for a in by_key["other"]] == ["custom_pack"]


def test_primary_rail_ids_match_pack_library():
    assert primary_rail_ids() == tuple(QUICK_RAIL_IDS)


def test_team_scoped_covers_matchday_not_mass():
    assert team_scoped("match_ready") is True
    assert team_scoped("growth_sync") is True
    assert "match_ready" in TEAM_SCOPED_IDS
    assert team_scoped("never_retire") is False
    assert team_scoped("ping") is False


def test_preflight_allows_current_career_team_when_local_cache_is_empty():
    empty = _state(players=(), teamid=None)
    assert preflight_team_scope(empty, "match_ready") is None

    # Mass / non-team packs always free to queue.
    assert preflight_team_scope(empty, "never_retire") is None
    assert preflight_team_scope(empty, "ping") is None


def test_preflight_empty_cache_allows_every_worker_checked_action():
    """The worker, not a stale app cache, resolves the current FC team."""
    empty = _state(players=(), teamid=None)
    assert preflight_team_scope(empty, "export_squad") is None
    assert preflight_team_scope(empty, "export_user_squad") is None
    assert preflight_team_scope(empty, "list_players") is None
    assert preflight_team_scope(empty, "ping") is None
    assert preflight_team_scope(empty, "match_ready") is None
    assert preflight_team_scope(empty, "growth_sync") is None
    assert preflight_team_scope(empty, "squad_boost") is None


def test_preflight_does_not_trust_an_empty_cached_team_over_live_career_mode():
    state = _state(players=(), teamid=131)
    assert preflight_team_scope(state, "squad_boost") is None
    assert preflight_team_scope(state, "export_squad") is None


def test_preflight_allows_when_squad_loaded():
    state = _state(
        players=({"playerid": 1, "name": "A"},),
        teamid=1,
        armed=True,
    )
    assert preflight_team_scope(state, "match_ready") is None
    assert preflight_team_scope(state, "export_squad") is None


def test_preflight_refuses_career_boost_on_old_worker():
    old = _state(armed=True, core_version="2.6.3")
    msg = preflight_team_scope(old, "full_fitness")
    assert msg is not None
    assert "2.6.4" in msg
    assert preflight_team_scope(old, "ping") is None


def test_map_failure_reason_empty_and_no_team():
    assert map_failure_reason("empty_team") == EMPTY_TEAM_HINT
    assert "has no players" in map_failure_reason("", "team has no players").lower() or (
        map_failure_reason("", "team has no players") == EMPTY_TEAM_HINT
    )
    assert map_failure_reason("no_team") == NO_TEAM_HINT
    assert "Career Mode" in map_failure_reason("no_career")
    assert "Failed: boom" in map_failure_reason("boom", "x")


def test_map_job_failure_reads_failures_when_error_is_generic():
    """Wire shape: error.message='failed', failures[0].reason='empty_team'."""
    from companion.domain.automation_guide import map_job_failure
    from companion.domain.outcome import ApplyOutcome, Failure, JobResult

    result = JobResult(
        job_id="j1",
        outcome=ApplyOutcome.FAILED,
        failures=(Failure(op_id="op1", reason="empty_team", detail="team has no players"),),
        error={"message": "failed"},
        diagnostic="",
    )
    assert map_job_failure(result) == EMPTY_TEAM_HINT

    no_team = JobResult(
        job_id="j2",
        outcome=ApplyOutcome.FAILED,
        failures=(Failure(op_id="op1", reason="no_team", detail="GetUserTeamId failed"),),
        error={"message": "failed", "reason": "failed"},
    )
    assert map_job_failure(no_team) == NO_TEAM_HINT


def test_automations_surface_wires_live_team_and_core_preflight():
    from pathlib import Path

    src = (
        Path(__file__).parents[2]
        / "companion"
        / "ui"
        / "surfaces"
        / "automations.py"
    ).read_text(encoding="utf-8")
    assert "preflight_team_scope" in src
    assert "_automation_disabled_reason" in src
    assert "Open Club" in src
    assert "group_items" not in src
    assert "_native_chooser" not in src
