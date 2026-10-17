"""Plan these players: shirt conflicts are per-player, fixable and never block the plan.

Covers the complete club shirt map (team_shirts), AI shirt awareness, per-item
Apply ("Apply the rest"), non-raising safe shirt writes, in-game shirt ops that
continue after one refusal, deterministic targeted shirt fixes, targeted AI
repair of one player and Rebuild invalidation after any issue.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.app import events as E
from companion.app.commands.squad_plan import (
    apply_squad_plan, plan_shirt_fix, apply_shirt_fix, resume_squad_plan, squad_plan_receipt,
)
from companion.app.commands.squad_plan_recovery import load_record
from companion.domain.squad_plan import (
    ShirtIssue, SquadMember, SquadPlan, blocked_playerids, build_apply_job, build_apply_jobs,
    issue_playerids, plan_from_rows, plan_without_blocked, safe_jersey_writes,
)
from companion.domain.squad_session import (
    build_session, parse_recipe, propose_payload, roster_from_members, shirt_map_for_ai, stage_per_player_plan,
)
from companion.domain.squad_shirts import fix_shirt, shirt_map_from_squad, swap_shirt
from companion.integrations import grok

from tests.v2.test_squad_durable_execution import Backend, Worker, _service

ROOT = Path(__file__).resolve().parents[2]
LOANED = 900  # Lucas Vázquez: on the club's link table, not in the senior list


# --------------------------------------------------------------------------- helpers


def _players():
    return [
        {"playerid": 1, "name": "Steven Gerrard", "position": "CM", "jerseynumber": 40, "teamid": 11},
        {"playerid": 2, "name": "Mohamed Salah", "position": "RW", "jerseynumber": 41, "teamid": 11},
        {"playerid": 3, "name": "Squad Keeper", "position": "GK", "jerseynumber": 1, "teamid": 11},
    ]


def _team_shirts():
    return [
        {"playerid": 1, "jerseynumber": 40, "senior": True},
        {"playerid": 2, "jerseynumber": 41, "senior": True},
        {"playerid": 3, "jerseynumber": 1, "senior": True},
        {"playerid": LOANED, "jerseynumber": 8, "senior": False, "loaned": True, "name": "Lucas Vázquez"},
    ]


def _plan(prompt_players=(1, 2)):
    rows = [row for row in _players() if row["playerid"] in prompt_players]
    plan = plan_from_rows(rows, source_save_uid="SAVE", source_teamid=11)
    book, names, hidden = shirt_map_from_squad(_players(), _team_shirts(), plan.members)
    return replace(plan, shirt_book=book, shirt_names=names, shirt_hidden=hidden)


def _stage(plan, prompt, patches=None):
    session = build_session(roster_from_members(plan.members), parse_recipe(prompt))
    return stage_per_player_plan(plan, session, patches or {}), session


# --------------------------------------------------------------------------- A. complete shirt map


def test_team_shirts_make_a_loaned_out_wearer_visible_and_hidden():
    book, names, hidden = shirt_map_from_squad(_players(), _team_shirts())
    assert book[LOANED] == 8 and names[LOANED] == "Lucas Vázquez"
    assert hidden == frozenset({LOANED})
    # Older cores export no link list: the senior export is the fallback.
    book, _names, hidden = shirt_map_from_squad(_players(), ())
    assert LOANED not in book and hidden == frozenset()


def test_shared_numbers_keep_every_holder_and_are_flagged():
    shirts = _team_shirts() + [{"playerid": 901, "jerseynumber": 8, "senior": False, "name": "Second Eight"}]
    book, names, _hidden = shirt_map_from_squad(_players(), shirts)
    assert book[LOANED] == 8 and book[901] == 8
    plan = replace(plan_from_rows(_players()[:2], source_save_uid="S", source_teamid=11),
                   shirt_book=book, shirt_names=names)
    staged, _ = _stage(plan, "gerrard jersey 8")
    assert any("#8 is worn by both" in note for note in staged.warnings)
    issue = staged.shirt_issue_for(1)
    assert issue is not None and issue.holder is None and "Pick another number" in issue.message


def test_hidden_holder_is_never_recommended_moved_or_written():
    staged, _ = _stage(_plan(), "arrange these players by jersey number as recommended")
    # Gerrard's #8 is worn by the loaned-out player: he gets his familiar #17.
    assert staged.jersey_numbers[1] == 17
    assert staged.jersey_numbers[2] == 11
    assert LOANED not in staged.jersey_numbers
    assert any("#8 is worn by Lucas Vázquez" in note and "#17" in note for note in staged.warnings)
    writes = [op.body for op in build_apply_job(staged).ops if op.body["table"] == "teamplayerlinks"]
    assert {op["key"]["value"]: op["fields"]["jerseynumber"] for op in writes} == {1: 17, 2: 11}


def test_typed_shirt_held_by_hidden_player_is_a_per_player_issue_not_a_failure():
    staged, _ = _stage(_plan(), "gerrard jersey 8 and salah jersey 11")
    assert staged.jersey_numbers == {2: 11}
    issue = staged.shirt_issue_for(1)
    assert issue == ShirtIssue(1, 8, LOANED, "Lucas Vázquez", "#8 is worn by Lucas Vázquez. Pick another number or swap.")
    assert not staged.blocking_issues
    # Apply still writes Salah's shirt.
    assert [op.id for op in build_apply_job(staged).ops] == ["jersey2-11"]


def test_squad_sync_keeps_team_shirts_and_planner_reads_them(tmp_path):
    from companion.ui.surfaces import planner
    svc = _service(tmp_path, Backend(2))
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=10, players=Backend(2).roster(),
                                     taken=svc.clock.now(), team_shirts=(
                                         {"playerid": 1, "jerseynumber": 1, "senior": True},
                                         {"playerid": 2, "jerseynumber": 2, "senior": True},
                                         {"playerid": 77, "jerseynumber": 9, "senior": False, "name": "On Loan"})))
    assert svc.store.snapshot().squad.team_shirts[-1]["playerid"] == 77
    plan = planner._with_squad_shirts(svc, plan_from_rows(Backend(2).roster()))
    assert plan.shirt_book[77] == 9 and plan.shirt_hidden == frozenset({77})


def test_export_squad_returns_every_club_link_row():
    lua = pytest.importorskip("lupa").LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        ops = { handlers = {} }
        function LEC_CAREER_SAVE_READY() return true end
        function GetUserSeniorTeamPlayerIDs() return {1, 2} end
        function GetPlayerName(pid) return pid == 900 and "Lucas Vazquez" or ("Player " .. pid) end
        function IsPlayerLoanedOut(pid) return pid == 900 end
        players = {}
        function players:records() local i = 0; return function() i = i + 1; if i <= 3 then return i end end end
        function players:get(field, rec)
            if field == "playerid" then return ({1, 2, 900})[rec] end
            return 70
        end
        rows = { {playerid=1, teamid=11, jerseynumber=7}, {playerid=2, teamid=11, jerseynumber=10},
                 {playerid=900, teamid=11, jerseynumber=17}, {playerid=900, teamid=55, jerseynumber=3},
                 {playerid=5, teamid=99, jerseynumber=17} }
        links = {}
        function links:records() local i = 0; return function() i = i + 1; return rows[i] end end
        function links:get(field, rec) return rec[field] end
        db = { field=function() return true end,
               open=function(name) if name == "players" then return players end
                                   if name == "teamplayerlinks" then return links end end }
        util = { now=function() return 1 end }
        function ok_result(data, counts) return { ok=true, data=data, counts=counts } end
        function fail_result(reason) return { ok=false, reason=reason } end
    ''')
    source = (ROOT / "ingame/le_companion/ops.lua").read_text(encoding="utf-8")
    code = "function ops.team_shirts" + source.split("function ops.team_shirts", 1)[1].split(
        "local function host_function", 1)[0]
    lua.execute(code)
    result = lua.globals().ops.handlers.export_squad(lua.table_from({"teamid": 11}), lua.table(), lua.table())
    assert result.ok
    shirts = [dict(result.data.team_shirts[i]) for i in range(1, len(result.data.team_shirts) + 1)]
    assert {row["playerid"]: row["jerseynumber"] for row in shirts} == {1: 7, 2: 10, 900: 17}
    hidden = next(row for row in shirts if row["playerid"] == 900)
    assert hidden["senior"] is False and hidden["loaned"] is True and hidden["name"] == "Lucas Vazquez"
    assert all(row["senior"] is True for row in shirts if row["playerid"] != 900)


# --------------------------------------------------------------------------- B. AI awareness


def _capture_requests(monkeypatch, *, shirt=None):
    calls = []

    def request(messages, model):
        body = json.loads(messages[1]["content"])
        calls.append({"system": messages[0]["content"], "body": body})
        items = [{"playerid": row["playerid"], "summary": "Faster",
                  "changes": [{"field": "stamina", "value": 90}]
                  + ([{"field": "jerseynumber", "value": shirt}] if shirt else [])}
                 for row in body["players"]]
        return {"choices": [{"message": {"content": json.dumps(
            {"selection_revision": body["selection_revision"], "players": items})}}]}

    monkeypatch.setattr(grok, "_request", request)
    return calls


def _members_with_stats(plan):
    return plan.with_member_bases({m.playerid: {"stamina": 70, "overallrating": 80, "potential": 85}
                                   for m in plan.members})


def test_payload_carries_the_club_shirt_map_and_the_no_steal_rule(monkeypatch):
    from companion.ui.surfaces.planner import session_propose_worker
    calls = _capture_requests(monkeypatch, shirt=23)
    plan = _members_with_stats(_plan())
    prompt = "improve stamina and give them good shirt numbers"
    session = build_session(roster_from_members(plan.members), parse_recipe(prompt))
    staged = []
    session_propose_worker(SimpleNamespace(cancelled=False), plan=plan, session=session,
                           on_success=staged.append, on_error=lambda exc: pytest.fail(str(exc)))
    assert len(calls) == 1
    assert calls[0]["body"]["squad_shirts"] == shirt_map_for_ai(plan)
    assert calls[0]["body"]["squad_shirts"]["8"] == "Lucas Vázquez"
    assert "never give a number worn by someone outside this selection" in calls[0]["system"]
    assert "8 words" in calls[0]["system"]
    assert '"reason"' not in calls[0]["system"]
    assert staged and staged[0].shirt_issues == ()


def test_recommended_mode_keeps_shirts_local_and_skips_the_map(monkeypatch):
    calls = _capture_requests(monkeypatch)
    plan = _members_with_stats(_plan())
    recipe = parse_recipe("improve stamina; arrange shirts as recommended")
    session = build_session(roster_from_members(plan.members), recipe)
    grok.propose_squad_players(players=propose_payload(plan, session), selection_revision=plan.selection_revision,
                               request=recipe.prompt, recipe=recipe, shirt_map=shirt_map_for_ai(plan))
    assert "squad_shirts" not in calls[0]["body"]
    assert "Never return jerseynumber" in calls[0]["system"]


def test_unrequested_ai_shirt_changes_are_dropped():
    plan = _members_with_stats(_plan())
    staged, _ = _stage(plan, "improve stamina", {1: {"stamina": 90, "jerseynumber": 23},
                                                 2: {"stamina": 90, "jerseynumber": 24}})
    assert staged.jersey_numbers == {} and staged.shirt_issues == ()
    assert staged.overrides[1]["stamina"] == 90


def test_requested_ai_shirt_never_moves_a_player_outside_the_selection():
    plan = _members_with_stats(_plan())
    staged, _ = _stage(plan, "improve stamina and give them good shirt numbers",
                       {1: {"stamina": 90, "jerseynumber": 1}, 2: {"stamina": 90, "jerseynumber": 23}})
    # Squad Keeper (not selected) wears #1: Gerrard gets an issue, the keeper stays.
    assert staged.jersey_numbers == {2: 23}
    assert staged.shirt_issue_for(1).holder == 3


# --------------------------------------------------------------------------- C. never block the plan


def test_safe_jersey_writes_drops_only_the_impossible_write():
    skipped = []
    writes = safe_jersey_writes({1: 10, 2: 20, 3: 3, 9: 30}, {1: 30, 2: 40, 3: 5}, skipped=skipped)
    assert dict(writes) == {2: 40, 3: 5}
    assert skipped == [(1, 30, "worn")]
    # A dependent write (2 wanted 1's old #10) is dropped too, nothing else.
    skipped.clear()
    writes = safe_jersey_writes({1: 10, 2: 20, 3: 3, 9: 30}, {1: 30, 2: 10, 3: 5}, skipped=skipped)
    assert dict(writes) == {3: 5}
    assert sorted(skipped) == [(1, 30, "worn"), (2, 10, "worn")]
    # Swap parking never uses #1.
    writes = safe_jersey_writes({1: 7, 2: 9}, {1: 9, 2: 7})
    assert all(number != 1 for _pid, number in writes)


def test_apply_the_rest_skips_only_blocked_items():
    plan = plan_from_rows([{"playerid": pid, "name": f"P{pid}", "position": "ST", "jerseynumber": pid,
                            "teamid": 11} for pid in (1, 2, 3)], source_save_uid="S", source_teamid=11)
    plan = plan.with_overrides({1: {"stamina": 90}, 2: {"stamina": 91}, 3: {"stamina": 92}},
                               jersey_numbers={1: 21}, blocking_issues=("P1: Missing required Quick Step+.",),
                               shirt_issues=(ShirtIssue(3, 8, 99, "Lucas Vázquez", "#8 is worn by Lucas Vázquez. "
                                                        "Pick another number or swap."),))
    assert blocked_playerids(plan) == {1}
    rest, skipped = plan_without_blocked(plan)
    assert set(rest.overrides) == {2, 3} and rest.jersey_numbers == {1: 21} and not rest.blocking_issues
    assert skipped == ["P1: player changes skipped until fixed.",
                       "P3: shirt skipped. #8 is worn by Lucas Vázquez. Pick another number or swap."]
    ops = [op.id for job in build_apply_jobs(rest) for op in job.ops]
    assert ops == ["p2", "p3", "jersey1-21"]
    whole = replace(plan, blocking_issues=("Starting XI is unknown. Choose your starters before protecting them.",))
    assert blocked_playerids(whole) is None
    with pytest.raises(Exception, match="whole plan"):
        plan_without_blocked(whole)


def test_apply_button_works_with_only_shirt_issues_and_offers_apply_the_rest(monkeypatch):
    from companion.ui.surfaces import planner
    reasons = []
    monkeypatch.setattr(planner, "set_disabled", lambda button, reason: reasons.append(reason))
    staged, _ = _stage(_plan(), "gerrard jersey 8 and salah jersey 11")
    planner._sync_session_apply({"apply_btn": object(), "plan": staged, "phase": "review"})
    assert reasons[-1] == ""  # shirt issues alone never disable Apply
    blocked = replace(staged, blocking_issues=("Steven Gerrard: Missing required Quick Step+.",),
                      overrides={1: {"stamina": 90}, 2: {"stamina": 90}})
    state = {"apply_btn": object(), "plan": blocked, "phase": "review"}
    planner._sync_session_apply(state)
    assert reasons[-1] == "Repair the affected players to complete this plan."
    assert planner.apply_rest_available(state)
    assert not planner.apply_rest_available({**state, "edits_dirty": True})


def test_shirt_ops_continue_on_error_on_the_wire():
    plan = replace(_plan(), jersey_numbers={1: 17, 2: 11})
    wire = build_apply_job(plan).to_wire(now=1)
    shirts = [op for op in wire["ops"] if op["table"] == "teamplayerlinks"]
    assert shirts and all(op["on_error"] == "continue" and op["displace"] is False for op in shirts)


class ShirtRefusingWorker(Worker):
    """Refuses one shirt write the way the core does, honouring per-op on_error."""

    refuse: set[tuple[int, int]] = set()

    def execute(self, job):
        if job.ops[0].op == "snapshot" or not any(op.body["table"] == "teamplayerlinks" for op in job.ops):
            return super().execute(job)
        backend = self.backend
        backend.write_jobs.append(job)
        from companion.core.transport.jobfile import atomic_write_json
        atomic_write_json(Path(job.snapshot_to), {"job_id": job.job_id, "save_uid": "SAVE", "rows": [
            {"table": op.body["table"], "key": op.body["key"], "existed": True, "teamid": 10,
             "fields": {"jerseynumber": backend.shirts[op.body["key"]["value"]]}} for op in job.ops]})
        ops, written, failed = [], 0, False
        for operation in job.ops:
            pid, number = operation.body["key"]["value"], operation.body["fields"]["jerseynumber"]
            taken = (pid, number) in self.refuse or any(
                other != pid and shirt == number for other, shirt in backend.shirts.items())
            if taken:
                failed = True
                ops.append({"id": operation.id, "op": "set_fields", "ok": False, "reason": "shirt_taken",
                            "counts": {"fields_requested": 1, "fields_written": 0}})
                if operation.on_error != "continue":
                    break
                continue
            backend.shirts[pid] = number
            written += 1
            ops.append({"id": operation.id, "op": "set_fields", "ok": True,
                        "counts": {"fields_requested": 1, "fields_written": 1}})
        state = "partial" if failed and written else "failed" if failed else "done"
        self.complete(job.job_id, {"state": state, "ok": not failed, "outcome": "applied" if not failed else state,
                                   "counts": {"fields_written": written}, "ops": ops})


def _refusing_service(tmp_path, backend, refuse):
    svc = _service(tmp_path, backend)
    worker = ShirtRefusingWorker(backend)
    worker.refuse = set(refuse)
    return replace(svc, transport=worker)


def _shirt_plan(svc, targets):
    squad = svc.store.snapshot().squad
    plan = plan_from_rows(squad.players).bind_source(squad.save_uid, squad.teamid, squad.taken.isoformat())
    return replace(plan, shirt_book={row["playerid"]: row["jerseynumber"] for row in squad.players}).with_overrides(
        {pid: {"skillmoves": 4} for pid in plan.playerids}, label="Shirts", jersey_numbers=targets)


def test_one_shirt_taken_does_not_stop_the_other_shirts_or_parts(tmp_path):
    backend = Backend(12)
    # #20 is refused in game (a wearer the export did not show).
    svc = _refusing_service(tmp_path, backend, {(1, 20)})
    targets = {pid: pid + 19 for pid in range(1, 11)}  # 10 shirt writes -> 2 shirt parts
    results = []
    plan_id = apply_squad_plan(svc, _shirt_plan(svc, targets), confirm_token="APPLY", on_result=results.append)
    record = load_record(svc, plan_id)
    assert all(part["status"] == "applied" for part in record["parts"])
    assert {pid: backend.shirts[pid] for pid in targets} == {**targets, 1: 1}
    report = squad_plan_receipt(svc, plan_id)
    assert [(item["playerid"], item["number"]) for item in report["shirt_conflicts"]] == [(1, 20)]
    assert report["verification"] == "verified" and report["status"] == "applied"
    assert results[-1].outcome.name == "PARTIAL"
    assert all(backend.players[pid]["skillmoves"] == 4 for pid in backend.players)
    # Resume never replays the refused write.
    sent = len(backend.write_jobs)
    resume_squad_plan(svc, plan_id)
    assert len(backend.write_jobs) == sent


def test_fix_shirts_replans_only_the_refused_shirt_and_queues_a_small_job(tmp_path):
    backend = Backend(4)
    svc = _refusing_service(tmp_path, backend, {(1, 12)})
    plan_id = apply_squad_plan(svc, _shirt_plan(svc, {1: 12, 2: 13}), confirm_token="APPLY")
    # The refresh shows who really wears #12: a loaned-out teammate.
    svc.clock.advance(1)
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=10, players=backend.roster(), taken=svc.clock.now(),
                                     team_shirts=tuple({"playerid": pid, "jerseynumber": n, "senior": True}
                                                       for pid, n in backend.shirts.items())
                                     + ({"playerid": LOANED, "jerseynumber": 12, "senior": False,
                                         "name": "Lucas Vázquez"},)))
    fix, notes = plan_shirt_fix(svc, plan_id)
    again, _ = plan_shirt_fix(svc, plan_id)
    assert fix.jersey_numbers == again.jersey_numbers  # deterministic
    assert set(fix.jersey_numbers) == {1}
    assert fix.jersey_numbers[1] not in {12, 1} and fix.jersey_numbers[1] not in backend.shirts.values()
    assert any("#12 is worn by Lucas Vázquez" in note for note in notes)
    svc.transport.refuse = set()
    sent = len(backend.write_jobs)
    fix_id = apply_shirt_fix(svc, plan_id, fix)
    new_jobs = backend.write_jobs[sent:]
    assert len(new_jobs) == 1 and [op.body["table"] for op in new_jobs[0].ops] == ["teamplayerlinks"]
    assert backend.shirts[1] == fix.jersey_numbers[1]
    assert squad_plan_receipt(svc, plan_id)["shirts_fixed_by"] == fix_id


def test_core_runner_continues_after_a_refused_shirt_op(tmp_path):
    from tests.v2.test_native_tick import Core, _posix, INGAME, _runtime
    import os
    lua = _runtime()
    queue = tmp_path / "queue"
    for sub in ("jobs", "claimed", "results", "state", "poison"):
        (queue / sub).mkdir(parents=True)
    g = lua.globals()
    g.ingame_root = _posix(INGAME)
    g.queue_dir = _posix(queue)
    g.py_dir_list = lambda path: lua.table_from(sorted(os.listdir(str(path)))) if Path(str(path)).is_dir() else None
    lua.execute('''
        CLOCK = 100.0
        _G.__mock_clock = function() return CLOCK end
        _G.__mock_time = function() return 1700000000 end
        IsInCM = function() return true end
        LEC_DIR_LIST = function(dir) return py_dir_list(dir) end
        package.loadlib = function() return nil, "stubbed", "open" end
        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end
        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir })
        assert(LEC.arm())
        attempted = {}
        LEC.ops.handlers["set_fields"] = function(op)
            attempted[#attempted + 1] = op.id
            if op.id == "jersey1-17" then
                return { ok = false, reason = "shirt_taken", detail = "taken", counts = {}, data = {},
                         failures = { { reason = "shirt_taken" } } }
            end
            return { ok = true, counts = { fields_requested = 1, fields_written = 1, found = 1 }, data = {} }
        end
    ''')
    core = Core(lua, queue)
    plan = replace(_plan(), jersey_numbers={1: 17, 2: 11, 3: 13})
    job = build_apply_job(plan)
    (queue / "jobs" / f"{job.job_id}.json").write_text(json.dumps(job.to_wire(now=1)), encoding="utf-8")
    result = queue / "results" / f"{job.job_id}.json"
    core.hub(100.0)  # a settled hub may drain writes itself
    if not result.exists():
        core.set_clock(102.0)
        assert core.eval("LEC.runner.tick_native_writes()") == "ok"
    attempted = [core.g.attempted[i] for i in range(1, core.eval("#attempted") + 1)]
    assert attempted == [op.id for op in job.ops] and len(attempted) == 3
    body = json.loads(result.read_text(encoding="utf-8"))
    assert body["state"] == "partial"
    failed = [(item["id"], item["reason"]) for item in body["ops"] if not item["ok"]]
    assert failed == [("jersey1-17", "shirt_taken")]


# --------------------------------------------------------------------------- D. targeted fixes


def test_targeted_shirt_fix_is_deterministic_and_never_moves_anyone_else():
    staged, _ = _stage(_plan(), "gerrard jersey 8 and salah jersey 11")
    fixed, note = fix_shirt(staged, 1)
    again, _ = fix_shirt(staged, 1)
    assert fixed.jersey_numbers == again.jersey_numbers == {1: 17, 2: 11}
    assert fixed.shirt_issues == () and note == "Steven Gerrard gets #17 instead of #8."
    assert LOANED not in fixed.jersey_numbers and 3 not in fixed.jersey_numbers
    # The explicit choice moves the wearer, and says so.
    swapped, note = swap_shirt(staged, 1)
    assert swapped.jersey_numbers == {1: 8, 2: 11, LOANED: 40}
    assert note == "Lucas Vázquez moves from #8 to #40 so Steven Gerrard can wear #8."
    writes = safe_jersey_writes(swapped.shirt_book, swapped.jersey_numbers)
    assert dict(writes) == {1: 8, 2: 11, LOANED: 40}


def test_targeted_shirt_fix_never_gives_number_one_to_an_outfielder():
    member = SquadMember(playerid=5, name="Unknown Winger", position="LW", base={"jerseynumber": 0})
    book = {5: 0, **{100 + number: number for number in range(2, 99)}}  # only #1 and #99 are free
    plan = SquadPlan(members=(member,), shirt_book=book,
                     shirt_issues=(ShirtIssue(5, 11, 11, "Eleven", "#11 is worn by Eleven."),))
    fixed, _note = fix_shirt(plan, 5)
    assert fixed.jersey_numbers == {5: 99}


def test_targeted_ai_repair_sends_one_player_with_the_error_and_keeps_the_others(monkeypatch):
    from companion.ui.surfaces.planner import player_issue_text, targeted_repair_worker
    rows = [{"playerid": pid, "name": f"Player {pid}", "position": "ST", "overallrating": 85, "potential": 88,
             "skillmoves": 1, "trait1": 0, "trait2": 0, "icontrait1": 0, "icontrait2": 0,
             "jerseynumber": pid, "teamid": 11} for pid in (1, 2, 3)]
    plan = plan_from_rows(rows, source_save_uid="S", source_teamid=11)
    prompt = "improve these players by adding playstyles and playstyles+"
    session = build_session(roster_from_members(plan.members), parse_recipe(prompt))
    from companion.domain.playstyles import encode_names
    good = {**encode_names(["Quick Step"], plus=False), **encode_names(["First Touch"], plus=True)}
    staged = stage_per_player_plan(plan, session, {1: dict(good), 3: dict(good)})
    assert issue_playerids(staged) == {2} and blocked_playerids(staged) == {2}
    reason = player_issue_text(staged, 2)
    assert reason and not reason.startswith("Player 2:")
    calls = []

    def request(messages, model):
        body = json.loads(messages[1]["content"])
        calls.append(body)
        return {"choices": [{"message": {"content": json.dumps({
            "selection_revision": body["selection_revision"],
            "players": [{"playerid": 2, "summary": "Quick feet", "playstyles": ["Quick Step"],
                         "playstyles_plus": ["First Touch"], "changes": []}]})}}]}

    monkeypatch.setattr(grok, "_request", request)
    out = []
    targeted_repair_worker(SimpleNamespace(cancelled=False), plan=staged, session=session, playerid=2,
                           reason=reason, on_success=out.append, on_error=lambda exc: pytest.fail(str(exc)))
    assert len(calls) == 1 and [row["playerid"] for row in calls[0]["players"]] == [2]
    assert f"Previous attempt rejected: {reason}. Fix only this." in calls[0]["user_request"]
    fixed = out[0]
    assert not fixed.blocking_issues
    assert fixed.overrides[1] == staged.overrides[1] and fixed.overrides[3] == staged.overrides[3]
    assert fixed.jersey_numbers == staged.jersey_numbers
    assert fixed.reasons[1] == staged.reasons[1]
    assert 2 in fixed.overrides


def test_repair_notes_bypass_the_cache_only_for_that_player(monkeypatch):
    from companion.integrations.squad_cache import SquadProposalCache
    calls = []

    def propose(chunk, **kwargs):
        calls.append((tuple(row["playerid"] for row in chunk), kwargs["request"]))
        return ({row["playerid"]: {"stamina": 90} for row in chunk}, {}, {})

    monkeypatch.setattr(grok, "_propose_squad_chunk", propose)
    cache = SquadProposalCache()
    kwargs = dict(players=[{"playerid": pid} for pid in (1, 2, 3)], request="improve stamina",
                  recipe=parse_recipe("improve stamina"), cache=cache, selection_revision="one")
    grok.propose_squad_players(**kwargs)
    calls.clear()
    grok.propose_squad_players(repair_notes={2: "stamina too low"}, **kwargs)
    assert calls == [((2,), "improve stamina\nPrevious attempt rejected: stamina too low. Fix only this.")]
    calls.clear()
    grok.propose_squad_players(**kwargs)
    assert calls == []  # the repaired proposal is cached under the normal key


def test_rebuild_invalidates_every_player_with_any_issue_and_refreshes_shirts(tmp_path, monkeypatch):
    from companion.ui.surfaces import planner
    staged, _ = _stage(_plan((1, 2, 3)), "gerrard jersey 8 and salah jersey 11")
    previous = replace(staged, warnings=staged.warnings + ("Squad Keeper: no applicable PlayStyles were staged.",))
    assert issue_playerids(previous) == {1, 3}
    invalidated = []
    monkeypatch.setattr(planner, "set_status", lambda *args, **kw: None)
    monkeypatch.setattr(planner, "_snapshot_preview_edits", lambda state: ([], {}))
    state = {"plan": previous, "original_plan": _plan((1, 2, 3)), "prompt": "",
             "proposal_cache": SimpleNamespace(invalidate_players=invalidated.append)}
    planner._start_session_propose(None, state, None, None, None, None, None)
    assert invalidated == [{1, 3}]

    class Widget:
        def configure(self, **kwargs):
            pass

    svc = _service(tmp_path, Backend(2))
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=11, players=_players(), taken=svc.clock.now(),
                                     team_shirts=_team_shirts()))
    monkeypatch.setattr("companion.integrations.ai_provider.block_reason", lambda: "")
    submitted = []
    monkeypatch.setattr(planner, "_submit", lambda *a, **kw: submitted.append(a[1]))
    original = plan_from_rows(_players()[:2], source_save_uid="SAVE", source_teamid=11)
    state = {"plan": original, "original_plan": original, "prompt": "gerrard jersey 8",
             "include_vars": {}, "pin_vars": {}}
    planner._start_session_propose(svc, state, None, None, Widget(), Widget(), Widget())
    assert submitted == ["squad.grok.session"]
    assert state["original_plan"].shirt_book[LOANED] == 8
    assert state["original_plan"].shirt_hidden == frozenset({LOANED})
    # The plan handed to the build carries the fresh map, so #8 is not free.
    staged = stage_per_player_plan(state["plan"], state["session"], {})
    assert staged.shirt_issue_for(1) is not None and staged.shirt_issue_for(1).holder == LOANED
    # A later refresh (the loanee returned his shirt) is picked up by the next Rebuild.
    svc.clock.advance(1)
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=11, players=_players(), taken=svc.clock.now(),
                                     team_shirts=[{**row, "jerseynumber": 0} if row["playerid"] == LOANED else row
                                                  for row in _team_shirts()]))
    state["busy"] = False  # the stubbed submit never finished the first build
    planner._start_session_propose(svc, state, None, None, Widget(), Widget(), Widget())
    staged = stage_per_player_plan(state["plan"], state["session"], {})
    assert staged.jersey_numbers == {1: 8} and staged.shirt_issues == ()



def test_explicit_swap_with_a_loaned_out_player_applies_and_verifies(tmp_path):
    backend = Backend(3)
    backend.shirts[LOANED] = 8  # on the link table only
    svc = _service(tmp_path, backend)

    def sync():
        svc.clock.advance(1)
        svc.store.dispatch(E.SquadSynced(
            save_uid="SAVE", teamid=10, players=backend.roster(), taken=svc.clock.now(),
            team_shirts=tuple({"playerid": pid, "jerseynumber": number, "senior": pid != LOANED,
                               **({"name": "Lucas Vázquez"} if pid == LOANED else {})}
                              for pid, number in backend.shirts.items())))

    sync()
    squad = svc.store.snapshot().squad
    plan = plan_from_rows(squad.players).bind_source(squad.save_uid, squad.teamid, squad.taken.isoformat())
    book, names, hidden = shirt_map_from_squad(squad.players, squad.team_shirts, plan.members)
    plan = replace(plan, shirt_book=book, shirt_names=names, shirt_hidden=hidden).with_overrides(
        {}, label="Shirts", shirt_issues=(ShirtIssue(2, 8, LOANED, "Lucas Vázquez", "#8 is worn by Lucas Vázquez."),))
    plan, note = swap_shirt(plan, 2)
    assert plan.jersey_numbers == {2: 8, LOANED: 2}
    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")
    report = squad_plan_receipt(svc, plan_id)
    assert report["status"] == "applied" and report["verification"] == "verified"
    assert backend.shirts[2] == 8 and backend.shirts[LOANED] == 2


# --------------------------------------------------------------------------- E. review copy


def test_review_shows_the_shirt_issue_on_the_player_with_fix_and_swap(monkeypatch):
    from unittest.mock import MagicMock
    import customtkinter as ctk
    from companion.ui.surfaces import planner_review as review
    staged, session = _stage(_plan(), "gerrard jersey 8 and salah jersey 11")
    widget = MagicMock()
    widget.winfo_children.return_value = []
    texts = []
    monkeypatch.setattr(ctk, "CTkFrame", lambda *a, **kw: widget)
    monkeypatch.setattr(ctk, "CTkEntry", lambda *a, **kw: widget)
    monkeypatch.setattr(review, "text_label", lambda host, text, **kw: texts.append(text) or widget)
    monkeypatch.setattr(review, "muted_label", lambda host, text, **kw: texts.append(text) or widget)
    buttons = {}
    monkeypatch.setattr(review, "button", lambda host, label, action, **kw: buttons.setdefault(label, action)
                        and MagicMock())
    fixed, swapped = [], []
    scroll = MagicMock()
    scroll.inner = widget
    state = {"plan": staged, "original_plan": staged, "session": session, "phase": "review",
             "review_detail": scroll, "fix_shirt": fixed.append, "swap_shirt": swapped.append,
             "pin_vars": {1: MagicMock()}}
    review.select_player(state, 1)
    assert "#8 is worn by Lucas Vázquez. Pick another number or swap." in texts
    buttons["Fix this shirt"]()
    buttons["Swap with Lucas Vázquez"]()
    assert fixed == [1] and swapped == [1]
    assert review.player_summary(staged, 1, ready=True).startswith("#8 is taken")


@pytest.mark.gui
def test_real_window_shirt_issue_flow_apply_skip_fix_and_apply_the_rest(tmp_path, monkeypatch):
    import customtkinter as ctk
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.core.clock import FakeClock
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport
    from companion.integrations import ai_provider
    from companion.ui.surfaces import planner

    root = ctk.CTk()
    root.geometry("1x1+5000+5000")
    monkeypatch.setattr(planner, "_raise_window", lambda _window: None)
    monkeypatch.setattr(planner, "_new_toplevel", lambda: ctk.CTkToplevel(root))
    monkeypatch.setattr(ai_provider, "block_reason", lambda: "")
    svc = Services(paths=TempAppPaths(tmp_path), store=Store(), clock=FakeClock(),
                   executor=InlineExecutor(), transport=FakeTransport())
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=11, players=_players(), taken=svc.clock.now(),
                                     team_shirts=_team_shirts()))
    original = plan_from_rows(_players()[:2], source_save_uid="SAVE", source_teamid=11)
    window = None
    try:
        planner._open_session_planner(svc, original, prompt="gerrard jersey 8 and salah jersey 11",
                                     club_name="Club", selected_rows=_players()[:2])
        window = next(child for child in root.winfo_children() if isinstance(child, ctk.CTkToplevel))
        state = window._planner_state
        state["propose_btn"].invoke()
        plan = state["plan"]
        assert plan.shirt_issue_for(1).holder == LOANED and plan.jersey_numbers == {2: 11}
        assert state["apply_btn"].cget("state") == "normal"
        assert state["apply_btn"].cget("text") == "Apply, skip 1 shirt…"
        assert "shirt number need a choice" in state["review_summary"].cget("text")
        state["fix_shirt"](1)
        assert state["plan"].jersey_numbers == {1: 17, 2: 11} and not state["plan"].shirt_issues
        assert state["apply_btn"].cget("text") == "Apply changes…"
        # A rejected player blocks Apply but not "Apply the rest".
        state["plan"] = replace(state["plan"], overrides={2: {"skillmoves": 4}},
                                blocking_issues=("Steven Gerrard: Missing required Quick Step+.",))
        planner._sync_session_apply(state)
        root.update()
        assert state["apply_btn"].cget("state") == "disabled"
        assert state["apply_rest_btn"].winfo_manager()
    finally:
        if window is not None:
            state["controller"].close(svc.executor)
            window.destroy()
        root.destroy()
