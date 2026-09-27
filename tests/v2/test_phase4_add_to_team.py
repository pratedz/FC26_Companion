from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import CORE_VERSION, SIGN_CORE_MIN
from companion.domain.job import (
    Grants,
    Job,
    JobValidationError,
    op_add_to_team,
    op_export_squad,
)
from companion.domain.safe_dummy_candidates import REAL_FREE_AGENT_CANDIDATE_IDS
from companion.platform import le_install
from companion.app.commands import team
from companion.app.state import AppState, BridgeState, Prefs, SquadState
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.catalog import ImportPlan
from companion.core.clock import FakeClock


ROOT = Path(__file__).resolve().parents[2]


def test_builder_is_disabled_without_explicit_experimental_opt_in() -> None:
    with pytest.raises(JobValidationError, match="disabled by default"):
        op_add_to_team("add", teamid=2, dummy_pool=[85308])


def test_job_requires_separate_add_to_team_grant() -> None:
    op = op_add_to_team(
        "add",
        teamid=2,
        dummy_pool=[85308],
        player={"fields": {"overallrating": 93}},
        experimental=True,
    )
    with pytest.raises(JobValidationError, match="allow_add_to_team"):
        Job(ops=(op,)).validate()
    wire = Job(
        ops=(op,),
        grants=Grants(allow_add_to_team=True),
    ).to_wire(now=1)
    assert wire["grants"]["allow_add_to_team"] is True
    assert wire["ops"][0]["op"] == "add_to_team"
    assert wire["ops"][0]["v"] == 4


def test_install_config_defaults_phase4_off() -> None:
    payload = le_install._config_payload(  # type: ignore[attr-defined]
        queue_dir=ROOT / "queue",
        core_version="2.1.0",
        core_sha256="abc",
        app_version="2.1.0",
    )
    assert payload["core_ops"] == []


def test_worker_toggle_is_narrow_atomic_and_reversible(tmp_path: Path) -> None:
    core = le_install.installed_core_dir(tmp_path)
    core.mkdir(parents=True)
    config = core / "config.json"
    config.write_text(
        json.dumps({"v": 3, "queue_dir": "C:/q", "core_ops": ["unknown"]}),
        encoding="utf-8",
    )
    enabled = le_install.set_experimental_add_to_team(True, data_dir=tmp_path)
    assert enabled["enabled"] is True
    assert json.loads(config.read_text(encoding="utf-8"))["core_ops"] == ["add_to_team"]
    disabled = le_install.set_experimental_add_to_team(False, data_dir=tmp_path)
    assert disabled["enabled"] is False
    assert json.loads(config.read_text(encoding="utf-8"))["core_ops"] == []
    assert not config.with_name("config.json.tmp").exists()


def test_repair_replaces_a_malformed_worker_config_and_enables_add_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = le_install.installed_core_dir(tmp_path)
    core.mkdir(parents=True)
    config = core / "config.json"
    # This is the exact kind of interrupted config that makes a restart loop
    # forever: the loader sees no valid opt-in even though the desktop UI has
    # already remembered the feature was enabled.
    config.write_text('{"v": 3, "core_ops": []\n"broken": true}', encoding="utf-8")

    monkeypatch.setattr(le_install, "_assert_game_and_live_editor_stopped", lambda: None)
    repaired = le_install.repair_experimental_add_to_team(
        queue_dir=tmp_path / "queue", data_dir=tmp_path
    )

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert repaired["enabled"] is True
    assert repaired["restart_required"] is True
    assert payload["core_ops"] == ["add_to_team"]
    assert payload["core_version"] == CORE_VERSION


def test_repair_quarantines_legacy_generated_add_player_jobs(tmp_path: Path) -> None:
    job_id = "01KZ0JN1F5RJP1NDYDYWKQ6EGE"
    queue = tmp_path / "queue"
    (queue / "jobs").mkdir(parents=True)
    (queue / "claimed").mkdir()
    (queue / "state").mkdir()
    (queue / "results").mkdir()
    legacy = {
        "job_id": job_id,
        "label": "Add Player: Legacy",
        "ops": [{"op": "add_to_team", "strategy": "create"}],
    }
    (queue / "jobs" / f"{job_id}.json").write_text(json.dumps(legacy), encoding="utf-8")
    (queue / "state" / f"{job_id}.state.json").write_text("{}", encoding="utf-8")

    quarantined = le_install._quarantine_unsafe_add_player_jobs(queue)  # type: ignore[attr-defined]

    assert quarantined == (job_id,)
    assert not (queue / "jobs" / f"{job_id}.json").exists()
    assert (queue / "poison" / f"{job_id}.json").is_file()
    assert (queue / "poison" / f"{job_id}.state.json").is_file()
    result = json.loads((queue / "results" / f"{job_id}.json").read_text(encoding="utf-8"))
    assert result["state"] == "poisoned"
    assert result["failures"][0]["reason"] == "create_strategy_disabled"


def test_lua_has_ten_named_steps_and_truthful_verification() -> None:
    source = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    for name in (
        "resolve_team",
        "pick_dummy",
        "write_fields",
        "write_face",
        "preserve_name_dictionary",
        "reserve_dummy",
        "transfer",
        "verify_team",
        "reapply_fields",
        "write_verified_name",
    ):
        assert f'"{name}"' in source
    assert '"team_not_applied"' in source
    assert 'fail_after_custom_detach("name_not_visible"' in source
    assert "create_strategy_disabled" in source
    assert "consumed_free_agent_dummies_" in source
    assert "safe_free_agent" in source
    assert "dummy_team_unknown" in source
    assert "pick_dummy_tries" in source
    assert 'if #pool ~= 1 then' not in source
    assert "Add Player needs at least one reviewed free-agent dummy" in source
    assert "CreatePlayer" not in source
    # editedplayernames is small; GetDBTableRows is the proven LE name path.
    # Never walk the full players table from this op.
    assert 'GetDBTableRows", "players"' not in source
    assert 'pcall(_G.GetDBTableRows, "editedplayernames")' in source
    assert "slot_consumed" in source
    assert "verify_team_tries" in source
    assert "verify_name_tries" in source
    assert "wait_event" in source
    assert "protect_potential" in source
    # Post-transfer name rewrite is mandatory so Career does not keep a blank name.
    assert source.index("if step == 10 then") < source.index(
        "write_names(state.playerid, player.names or {})",
        source.index("if step == 10 then"),
    )


def test_lua_name_path_proves_durable_content_not_shell_rows() -> None:
    """Blank Career names came from detach + success without durable content."""
    source = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    # Content verification after every insert/update path.
    assert "function durable_name_ok" in source or "local function durable_name_ok" in source
    assert "name_row_content_matches" in source
    assert 'return false, "name_not_applied", 0' in source
    assert 'return false, "insert_rejected", 0' in source
    # Update every matching editedplayernames row (anti stale-first-row).
    assert "edited_rows_for_pid" in source
    assert "for i = 1, #matches do" in source
    # The original dictionary remains live when the name row cannot be
    # materialised: the write now happens before the one-and-only detach.
    assert "prev_nameids" in source
    assert "snapshot_name_dict" in source
    assert "restore_name_dict" in source
    assert "name_dictionary_snapshot_missing" in source
    # Component matching allows Iniesta / Andrés Iniesta but cannot make an
    # embedded dummy substring (Ben / Benedict) look like a valid identity.
    assert "contains_sequence" in source
    assert "got:find(want" not in source
    # Step 10: custom fallback keeps durable-row ordering, but a matching FC
    # host-visible name is the terminal gate for both custom and native paths.
    step10 = source.split("if step == 10 then", 1)[1].split(
        'return fail("bad_resume_step"', 1
    )[0]
    assert 'strategy == "native_ids"' in step10
    assert 'strategy == "custom"' in step10
    assert "write_names(state.playerid, player.names or {})" in step10
    assert "host_player_name" in step10
    assert "visible_name_matches(host_name, expected)" in step10
    assert 'waiting_for = "visible_name"' in step10
    assert 'fail_after_custom_detach("name_not_visible"' in step10
    assert "visible_name_verified = true" in step10
    assert step10.index("write_names(state.playerid, player.names or {})") < step10.index(
        "if player.detach_name_dictionary ~= false then"
    )
    # A worker can never claim the signing is verified before this guard.
    assert step10.index("visible_name_matches(host_name, expected)") < step10.index(
        "visible_name_verified = true"
    )
    # A custom fallback restores its original dictionary identity on its final
    # visible-name failure, so it cannot leave a new blank Squad Hub label.
    assert "fail_after_custom_detach" in step10
    assert "restored the original FC name identity" in step10


def test_lua_rejects_pre_v3_add_player_contract_before_writes() -> None:
    source = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    guard = source.split("function M.step(op, job, ctx)", 1)[1].split(
        "if step == 1 then", 1
    )[0]
    assert 'tonumber(op.v or 1) < 3' in guard
    assert 'fail("stale_add_player_contract"' in guard
    assert guard.index('fail("stale_add_player_contract"') < guard.index(
        "if op.strategy ~= nil"
    )


def test_lua_defers_name_dictionary_until_after_transfer_verification() -> None:
    """Face/stats may arrive early; the custom name must not go blank meanwhile."""
    source = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    step5 = source.split("if step == 5 then", 1)[1].split("if step == 6 then", 1)[0]
    step6 = source.split("if step == 6 then", 1)[1].split("if step == 7 then", 1)[0]
    step9 = source.split("if step == 9 then", 1)[1].split("if step == 10 then", 1)[0]
    step10 = source.split("if step == 10 then", 1)[1].split(
        'return fail("bad_resume_step"', 1
    )[0]
    for early_step in (step5, step6, step9):
        assert "firstnameid = 0" not in early_step
        assert "write_names(state.playerid" not in early_step
    assert "firstnameid = 0" in step10
    assert "write_names(state.playerid, player.names or {})" in step10
    # FC resolves a label when dictionary ids are cleared. The durable custom
    # row must exist first or Squad Hub can cache the blank transition.
    assert step10.index("write_names(state.playerid, player.names or {})") < step10.index(
        "firstnameid = 0"
    )
    assert "custom name was saved but could not be activated" in step10


def test_lua_team_verifier_falls_back_when_the_host_returns_unknown() -> None:
    """A -1 host sentinel must not strand an already-transferred signing."""
    source = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    assert "local function verify_team_membership" in source
    assert "GetUserSeniorTeamPlayerIDs" in source
    assert "GetPlayerIDSForTeam" in source
    assert "GetPlayerIDsForTeam" in source
    assert 'db.open("teamplayerlinks")' in source
    assert "actual ~= 111592" in source
    step8 = source.split("if step == 8 then", 1)[1].split("if step == 9 then", 1)[0]
    assert "verify_team_membership" in step8
    assert "verify_source = source" in step8
    assert '"team_verify_unavailable"' in step8
    assert "membership_sources" in step8


def test_runner_persists_progress_and_hard_yields_after_transfer() -> None:
    runner = (ROOT / "ingame/le_companion/runner.lua").read_text(encoding="utf-8")
    op = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    assert "write_progress(job_id, result.progress" in runner
    assert 'body.state = awaiting and "awaiting_event" or "running"' in runner
    assert "res.awaiting_event = true" in op
    assert 'util.remove(qjoin("state", job_id .. ".state.json"))' in runner
    assert "if attempts > max_attempts then" in runner
    assert '"max_attempts"' in runner
    assert "tonumber(previous.attempts)" in runner


def test_lua_dispatch_has_config_gate_in_addition_to_job_grant() -> None:
    source = (ROOT / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    block = source.split('ops.handlers["add_to_team"]')[1].split(
        "-------------------------------------------------------------------------------", 1
    )[0]
    assert "ctx.cfg.core_ops.add_to_team == true" in block
    assert "experimental_disabled" in block


def test_export_squad_uses_only_a_bounded_live_candidate_probe() -> None:
    source = (ROOT / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    block = source.split("local function collect_ids", 1)[1].split(
        "-- Helpers are the normal", 1
    )[0]
    assert block.index("local pid = tonumber(value)") < block.index(
        "pid = tonumber(key)"
    )
    assert "free_agent_candidates" in source
    assert "free_agent_template" in source
    assert "math.min(#raw_template, 30)" in source
    assert "GetTeamIdFromPlayerId" in source
    assert "live_unlinked_scan" not in source
    assert "career_playercontract" not in source
    assert 'links:get("leagueid"' not in source
    assert 'collect_ids("GetPlayerIDSForTeam", 0' not in source
    assert '"age", "nationality"' not in source


def test_export_candidate_wire_is_bounded_and_real_player_only() -> None:
    assert len(REAL_FREE_AGENT_CANDIDATE_IDS) == 30
    assert len(set(REAL_FREE_AGENT_CANDIDATE_IDS)) == 30
    assert all(0 < playerid <= 459999 for playerid in REAL_FREE_AGENT_CANDIDATE_IDS)
    wire = op_export_squad(
        "squad", free_agent_candidates=REAL_FREE_AGENT_CANDIDATE_IDS
    ).to_wire()
    assert wire["free_agent_candidates"] == list(REAL_FREE_AGENT_CANDIDATE_IDS)
    with pytest.raises(JobValidationError, match="at most 30"):
        op_export_squad("squad", free_agent_candidates=range(1, 32))


def test_add_worker_refuses_a_career_team_switch_before_writing_a_dummy() -> None:
    source = (ROOT / "ingame/le_companion/ops/add_to_team.lua").read_text(
        encoding="utf-8"
    )
    assert "queued team %s does not match active user team %s" in source
    assert 'return fail("team_changed"' in source


def test_runner_and_export_reject_zero_host_readiness_values() -> None:
    runner = (ROOT / "ingame/le_companion/runner.lua").read_text(encoding="utf-8")
    export = (ROOT / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    assert "return value == true or tonumber(value) == 1" in runner
    assert "if ok then return host_true(value) end" in runner
    assert "not teamid or teamid <= 0" in export
    assert 'fail_result("no_team", "Career Mode is not ready' in export


class _Store:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def snapshot(self) -> AppState:
        return self.state


def _team_service(
    *, enabled: bool = True, capability: bool = True, tmp_path: Path | None = None
):
    clock = FakeClock()
    free_agents = (
        {"playerid": 10, "overallrating": 1, "source": "free_agent_template"},  # protected
        {"playerid": 85308, "overallrating": 52, "source": "free_agent_template"},
        {"playerid": 91234, "overallrating": 48, "source": "free_agent_template"},
    )
    state = AppState(
        bridge=BridgeState(
            Liveness(
                pill=Pill.ARMED,
                message="ready",
                armed=True,
                capabilities={"add_to_team": capability},
                core_version=CORE_VERSION,
                session_id="session-test",
            )
        ),
        squad=SquadState(
            teamid=2,
            players=({"playerid": 10},),
            free_agents=free_agents,
            taken=clock.now(),
            stale=False,
            save_uid="save-test",
            session_id="session-test",
        ),
        prefs=Prefs(
            core_ops=frozenset({"add_to_team"} if enabled else set()),
            values={"core_ops": ["add_to_team"] if enabled else []},
        ),
    )
    catalog = SimpleNamespace(
        prepare_cross_year_import=lambda card, target_playerid: ImportPlan(
            target_playerid=target_playerid,
            fields={"overallrating": 93},
            source={"name": card["name"]},
        )
    )
    root = tmp_path if tmp_path is not None else Path(".")
    queue = root / "queue"
    (queue / "state").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        store=_Store(state),
        catalog=catalog,
        db=None,
        clock=clock,
        paths=SimpleNamespace(queue=queue, root=root),
    )


@pytest.mark.parametrize(
    ("card", "expect_common", "expect_first", "expect_sur"),
    [
        (
            {"name": "Luka Modrić"},
            "Luka Modrić",
            "Luka",
            "Modrić",
        ),
        (
            {"name": "Pelé"},
            "Pelé",
            "",
            "Pelé",
        ),
        (
            {
                "name": "Ignored",
                "firstname": "David",
                "surname": "Beckham",
                "commonname": "Beckham",
            },
            "Beckham",
            "David",
            "Beckham",
        ),
        (
            {"name": "Marco van Basten"},
            "Marco van Basten",
            "Marco",
            "van Basten",
        ),
    ],
)
def test_add_player_name_payload_always_has_nonempty_commonname(
    card: dict, expect_common: str, expect_first: str, expect_sur: str
) -> None:
    names = team._names(card)  # shipped builder used by prepare_card_for_team
    assert names["commonname"] == expect_common
    assert names["commonname"].strip() != ""
    assert names["firstname"] == expect_first
    assert names["surname"] == expect_sur
    assert names["playerjerseyname"].strip() != ""


def test_add_job_wire_includes_names_detach_and_name_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def submit(_svc, job):
        captured["wire"] = job.to_wire(now=1)
        return job.job_id

    monkeypatch.setattr(team, "submit_job", submit)
    monkeypatch.setattr(team, "resolve_card_base_profile", lambda _card: None)
    team.add_card_to_team(_team_service(), {"name": "Luka Modrić"})
    op = captured["wire"]["ops"][0]
    player = op["player"]
    assert player["detach_name_dictionary"] is True
    assert player["name_strategy"] == "custom"
    assert player["names"]["commonname"] == "Luka Modrić"
    assert player["names"]["firstname"] == "Luka"
    assert player["names"]["surname"] == "Modrić"
    assert player["names"]["playerjerseyname"].strip() != ""
    assert op["verify"]["name"] is True
    assert op["v"] == 4
    # This is a resumable ten-step operation; a normal frame-sized budget can
    # otherwise turn a completed name write into a false partial result.
    assert captured["wire"]["budget_ms"] == 5000


def test_ui_command_uses_only_verified_unprotected_free_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def submit(_svc, job):
        captured["wire"] = job.to_wire(now=1)
        return job.job_id

    monkeypatch.setattr(team, "submit_job", submit)
    jid = team.add_card_to_team(_team_service(), {"name": "Marco van Basten"})
    assert len(jid) == 26
    op = captured["wire"]["ops"][0]
    assert op["dummy_pool"][0] == 91234
    assert 10 not in op["dummy_pool"]
    assert set(op["dummy_pool"]).issubset({91234, 85308})
    assert captured["wire"]["grants"]["allow_add_to_team"] is True
    assert captured["wire"]["requires"]["core_version"] == SIGN_CORE_MIN
    assert captured["wire"]["grants"]["allow_create_player"] is False
    assert op["dummy_filter"]["must_be_unused_in_save"] is True


def test_batch_builder_reserves_distinct_singleton_dummies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(team, "resolve_card_base_profile", lambda _card: None)
    svc = _team_service()
    _preview, jobs = team.build_add_card_jobs(
        svc,
        (
            {"name": "One", "person_id": 1},
            {"name": "Two", "person_id": 2},
        ),
    )
    assert len(jobs) == 1
    op = jobs[0].to_wire(now=1)["ops"][0]
    assert op["v"] == 4
    batch = op["batch"]
    assert len(batch) == 2
    pools = [tuple(member["dummy_pool"]) for member in batch]
    assert [pool[0] for pool in pools] == [91234, 85308]
    assert set(pools[0]).isdisjoint(set(pools[1]))
    assert jobs[0].label.startswith("Sign 2:")
    assert "One" in jobs[0].label
    assert "Two" in jobs[0].label


def test_batch_builder_stripes_disjoint_backup_dummies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(team, "resolve_card_base_profile", lambda _card: None)
    svc = _team_service()
    extras = (
        {"playerid": 66040, "overallrating": 60, "source": "free_agent_template"},
        {"playerid": 66371, "overallrating": 61, "source": "free_agent_template"},
        {"playerid": 66388, "overallrating": 62, "source": "free_agent_template"},
    )
    svc.store.state = svc.store.state.with_(
        squad=replace(
            svc.store.state.squad,
            free_agents=(*svc.store.state.squad.free_agents, *extras),
        )
    )
    _preview, jobs = team.build_add_card_jobs(
        svc,
        (
            {"name": "One", "person_id": 1},
            {"name": "Two", "person_id": 2},
        ),
    )
    assert len(jobs) == 1
    batch = jobs[0].to_wire(now=1)["ops"][0]["batch"]
    pools = [tuple(member["dummy_pool"]) for member in batch]
    assert pools[0][0] == 91234
    assert pools[1][0] == 85308
    assert len(pools[0]) > 1
    assert len(pools[1]) > 1
    assert set(pools[0]).isdisjoint(set(pools[1]))
    used = [pid for pool in pools for pid in pool]
    assert len(used) == len(set(used))


def test_batch_builder_refuses_to_create_or_overwrite_when_no_free_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(team, "resolve_card_base_profile", lambda _card: None)
    svc = _team_service()
    svc.store.state = svc.store.state.with_(
        squad=replace(svc.store.state.squad, free_agents=())
    )
    with pytest.raises(JobValidationError, match="will not create or overwrite a club player"):
        team.build_add_card_jobs(
            svc,
            (
                {"name": "One", "person_id": 1},
                {"name": "Two", "person_id": 2},
            ),
        )


def test_create_strategy_is_rejected_at_the_domain_boundary() -> None:
    with pytest.raises(JobValidationError, match="cannot create a new Career player"):
        op_add_to_team(
            "add",
            teamid=2,
            strategy="create",  # type: ignore[arg-type]
            player={"id_pool": [460000]},
            experimental=True,
        )


def test_batch_builder_rejects_duplicate_people(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(team, "resolve_card_base_profile", lambda _card: None)
    with pytest.raises(JobValidationError, match="Duplicate selection"):
        team.build_add_card_jobs(
            _team_service(),
            (
                {"name": "One", "person_id": 1},
                {"name": "One special", "person_id": 1},
            ),
        )


def test_typed_batch_confirmation_is_exact() -> None:
    assert team.confirm_team_add(True, 11)
    assert not team.confirm_team_add(False, 11)
    assert team.confirm_team_add(ask=lambda: True)
    assert not team.confirm_team_add(ask=lambda: False)


@pytest.mark.parametrize(
    ("enabled", "capability", "message"),
    [
        (False, True, "Sign support is off"),
        (True, False, "Restart Live Editor"),
    ],
)
def test_ui_command_requires_both_opt_ins(
    enabled: bool, capability: bool, message: str
) -> None:
    with pytest.raises(JobValidationError, match=message):
        team.add_card_to_team(
            _team_service(enabled=enabled, capability=capability),
            {"name": "X"},
        )
