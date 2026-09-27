"""Sep 27 live failure: trait2=2097154 stopped part 1 before Neuer/Carvajal."""
from dataclasses import replace
import json

import pytest

from companion.domain.player import FIELD_SPECS, PlayerValidationError, normalize_patch
from companion.domain.playstyles import GOALKEEPER, OUTFIELD, encode_names, plus_names
from companion.domain.job import JobValidationError
from companion.domain.squad_plan import build_apply_jobs, plan_from_rows
from companion.domain.squad_session import (
    build_session, parse_recipe, propose_payload, roster_from_members,
    sanitize_patch, stage_per_player_plan,
)
from companion.integrations import grok

PROMPT = "arrange these players jersey no by recommendation and improve playstyle+ no limit ceiling"
DEFENDER_PLUS = ["Whipped Pass", "Jockey", "Intercept", "Anticipate", "Slide Tackle", "Relentless"]


def squad(count=22):
    rows = [{"playerid": i, "name": f"P{i}", "position": "CM", "overallrating": 80,
             "potential": 80, "jerseynumber": i, "teamid": 11,
             "trait1": 0, "trait2": 0, "icontrait1": 0, "icontrait2": 0}
            for i in range(1, count + 1)]
    # Both real players are after the first attribute part, as in the failed run.
    rows[8].update(playerid=85308, name="Manuel Neuer", position="GK")
    rows[9].update(playerid=82407, name="Carvajal", position="RB")
    plan = plan_from_rows(rows)
    session = build_session(roster_from_members(plan.members), parse_recipe(PROMPT))
    return plan, session


def reply(body, items):
    return {"choices": [{"message": {"content": json.dumps({
        "selection_revision": body["selection_revision"], "players": items,
    })}}]}


def named_player(row):
    names = list(GOALKEEPER) if row["position"] == "GK" else DEFENDER_PLUS
    return {"playerid": row["playerid"], "summary": "Position-specific build",
            "playstyles": names, "playstyles_plus": names,
            "changes": [{"field": "jerseynumber", "value": 3}]}


@pytest.mark.parametrize("field,value", [("trait2", 2097154), ("trait2", 524320),
    ("trait2", 1048584), ("icontrait2", 262144), ("icontrait1", 1 << 30)])
@pytest.mark.parametrize("unlimited", [False, True])
def test_invalid_masks_rejected_even_before_standard_cap(field, value, unlimited):
    _, session = squad()
    with pytest.raises(PlayerValidationError):
        normalize_patch({field: value})
    with pytest.raises(PlayerValidationError):
        sanitize_patch({field: value}, recipe=replace(session.recipe, unlimited_playstyles=unlimited),
                       target=session.targets[0], current={})


def test_encoding_uses_live_editor_bank_positions_and_counts_real_plus_only():
    assert encode_names(["Rapid", "Whipped Pass"]) == {"trait1": 2097152 | 4096, "trait2": 0}
    assert encode_names(list(GOALKEEPER), plus=True) == {"icontrait1": 0, "icontrait2": 63}
    assert encode_names(list(OUTFIELD), plus=True)["icontrait1"] == (1 << 30) - 1
    assert plus_names({"icontrait1": 0, "icontrait2": 2048 | 8192}) == ()
    with pytest.raises(ValueError, match="Unknown PlayStyle"):
        encode_names(["Injury Prone"], plus=True)


@pytest.mark.parametrize("count", [20, 21, 22])
def test_exact_invalid_bank_repaired_then_every_player_reaches_apply(monkeypatch, tmp_path, count):
    plan, session = squad(count)
    calls = []
    def request(messages, model):
        body = json.loads(messages[1]["content"])
        calls.append(body)
        assert body["editable_field_contract"]["trait2"]["max"] == 131071
        assert "GK Footwork" in body["playstyle_catalog"]["goalkeeper"]
        items = [named_player(row) for row in body["players"]]
        if "Repair these rejected" not in body["user_request"]:
            for item in items:
                if item["playerid"] == 3:
                    item.pop("playstyles")
                    item["changes"].append({"field": "trait2", "value": 2097154})
        return reply(body, items)
    monkeypatch.setattr(grok, "_request", request)
    patches, reasons, hints = grok.propose_squad_players(
        players=propose_payload(plan, session), selection_revision=plan.selection_revision,
        request=PROMPT, recipe=session.recipe,
    )
    repairs = [call for call in calls if "Repair these rejected" in call["user_request"]]
    assert len(repairs) == 1 and [p["playerid"] for p in repairs[0]["players"]] == [3]
    assert set(patches) == set(plan.playerids)
    staged = stage_per_player_plan(plan, session, patches, reasons=reasons, library_hints=hints)
    assert not staged.blocking_issues
    assert set(plus_names(staged.overrides[85308])) == set(GOALKEEPER)
    assert set(plus_names(staged.overrides[82407])) == set(DEFENDER_PLUS)
    assert len(plus_names(staged.overrides[82407])) == 6  # OVR 80, no 2-bit ceiling
    jobs = build_apply_jobs(staged)
    ops = [op for job in jobs for op in job.ops if op.body["table"] == "players"]
    assert {op.body["key"]["value"] for op in ops} == set(plan.playerids)
    for op in ops:
        assert plus_names(op.body["fields"])
        normalize_patch(op.body["fields"])
    assert all(job.budget_ms <= 5000 for job in jobs)
    # Run every sequential part through a read-back simulator. The two named
    # players must actually be written after part 1, not just exist in preview.
    from companion.app import events as E
    from companion.app.commands.squad_plan import apply_squad_plan
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.core.clock import FakeClock
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport
    from companion.domain.outcome import ApplyOutcome

    clock, store, transport = FakeClock(), Store(), FakeTransport()
    store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=11, players=(), taken=clock.now()))
    svc = Services(paths=TempAppPaths(tmp_path), clock=clock, store=store,
                   transport=transport, executor=InlineExecutor())
    written, worn = {}, {member.playerid: member.base["jerseynumber"] for member in plan.members}
    def follow(jid, **kwargs):
        job = transport.job(jid)
        for op in job.ops:
            pid, fields = op.body["key"]["value"], op.body["fields"]
            if op.body["table"] == "players":
                written[pid] = normalize_patch(fields)
            else:
                shirt = fields["jerseynumber"]
                assert not any(other != pid and number == shirt for other, number in worn.items())
                worn[pid] = shirt
        transport.complete(jid, {"job_id": jid, "ok": True, "state": "done", "outcome": "applied",
                                 "counts": {"fields_written": sum(len(op.body["fields"]) for op in job.ops)}})
        return transport.result(jid)
    transport.await_result = follow
    results = []
    apply_squad_plan(svc, staged, confirm_token="APPLY", on_result=results.append)
    assert len(transport.submitted) == len(jobs)
    assert set(written) == set(plan.playerids)
    assert written[85308]["icontrait2"] == 63
    assert set(plus_names(written[82407])) == set(DEFENDER_PLUS)
    assert results[-1].outcome is ApplyOutcome.APPLIED


@pytest.mark.parametrize("failure", ["missing", "zero", "career", "wrong_role", "unknown_name"])
def test_incomplete_neuer_is_repaired_without_losing_other_player(monkeypatch, failure):
    plan, session = squad()
    rows = [row for row in propose_payload(plan, session) if row["playerid"] in (85308, 82407)]
    seen = []
    def request(messages, model):
        body = json.loads(messages[1]["content"])
        seen.append(body)
        items = [named_player(row) for row in body["players"]]
        if len(seen) == 1:
            item = next(i for i in items if i["playerid"] == 85308)
            if failure == "missing":
                items.remove(item)
            elif failure == "zero":
                item["playstyles_plus"] = []
            elif failure == "career":
                item.pop("playstyles_plus")
                item["changes"].append({"field": "icontrait2", "value": 2048})
            elif failure == "wrong_role":
                item["playstyles_plus"] = ["Finesse Shot"]
            else:
                item["playstyles_plus"] = ["Made up trait"]
        return reply(body, items)
    monkeypatch.setattr(grok, "_request", request)
    patches, _, _ = grok._propose_squad_chunk(rows, selection_revision=plan.selection_revision,
                                            request=PROMPT, recipe=session.recipe, model="test")
    assert set(patches) == {85308, 82407}
    assert len(seen) == 2 and [r["playerid"] for r in seen[1]["players"]] == [85308]
    assert patches[85308]["icontrait2"] == 63


def test_twice_rejected_player_keeps_valid_draft_but_blocks_incomplete_apply(monkeypatch):
    plan, session = squad()
    def request(messages, model):
        body = json.loads(messages[1]["content"])
        items = [named_player(row) for row in body["players"]]
        for item in items:
            if item["playerid"] == 85308:
                item["playstyles_plus"] = []
        return reply(body, items)
    monkeypatch.setattr(grok, "_request", request)
    problems = []
    patches, reasons, _ = grok.propose_squad_players(
        players=propose_payload(plan, session), selection_revision=plan.selection_revision,
        request=PROMPT, recipe=session.recipe, problems=problems,
    )
    assert len(patches) == 21 and 82407 in patches
    staged = stage_per_player_plan(plan, session, patches, reasons=reasons)
    assert any("Manuel Neuer" in issue for issue in staged.blocking_issues)
    with pytest.raises(JobValidationError, match="Manuel Neuer"):
        build_apply_jobs(staged)


def test_missing_plus_is_not_satisfied_by_regular_traits_or_career_bits():
    plan, session = squad()
    patches = {pid: encode_names(DEFENDER_PLUS, plus=True) for pid in plan.playerids}
    patches[85308] = {"trait1": 1, "trait2": 63, "icontrait1": 0, "icontrait2": 2048}
    staged = stage_per_player_plan(plan, session, patches)
    assert any("Manuel Neuer" in issue for issue in staged.blocking_issues)
    with pytest.raises(JobValidationError):
        build_apply_jobs(staged)


def test_no_ceiling_can_encode_every_defined_playstyle_without_trimming():
    plan, session = squad()
    all_plus = encode_names(list(OUTFIELD) + list(GOALKEEPER), plus=True)
    clean = sanitize_patch(all_plus, recipe=session.recipe, target=session.targets[0], current={})
    assert len(plus_names(clean)) == 36
    assert FIELD_SPECS["icontrait2"].coerce(clean["icontrait2"]) == 63


def test_apply_button_explains_incomplete_proposal_and_unblocks_after_rebuild(monkeypatch):
    from companion.ui.surfaces import planner
    plan, session = squad()
    incomplete = stage_per_player_plan(plan, session, {})
    seen = []
    monkeypatch.setattr(planner, "set_disabled", lambda button, reason: seen.append(reason))
    state = {"apply_btn": object(), "plan": incomplete, "phase": "review"}
    planner._sync_session_apply(state)
    assert "Rebuild" in seen[-1]
    patches = {pid: encode_names(DEFENDER_PLUS, plus=True) for pid in plan.playerids}
    patches[85308] = encode_names(list(GOALKEEPER), plus=True)
    state["plan"] = stage_per_player_plan(plan, session, patches)
    planner._sync_session_apply(state)
    assert seen[-1] == ""
