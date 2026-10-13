"""Rebuild scope, stale callbacks and explicit recipe constraints without game/AI."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from companion.domain.card_import import forced_birthdate_for_age
from companion.domain.player import ATTRIBUTE_GROUPS
from companion.domain.squad_plan import plan_from_rows
from companion.domain.squad_session import (
    build_session, filter_roster, lock_python_scope, merge_recipe, parse_recipe,
    roster_from_members, sanitize_patch, stage_per_player_plan,
)
from companion.ui.surfaces.planner_controller import PlannerController, load_requests, save_request


def rows(count=3):
    return [{
        **{field: 80 for _group, attributes in ATTRIBUTE_GROUPS for field, _name in attributes},
        "playerid": pid, "name": ("Messi" if pid == 1 else "Ronaldo" if pid == 2 else f"Player {pid}"),
        "position": "GK" if pid == 3 else "ST", "overallrating": 80, "potential": 85,
        "acceleration": 80, "sprintspeed": 80, "finishing": 80, "shotpower": 80,
        "shortpassing": 80, "longpassing": 80, "vision": 80, "defensiveawareness": 60,
        "standingtackle": 60, "interceptions": 60, "stamina": 80, "strength": 80,
        "skillmoves": 1, "trait1": 0, "trait2": 0, "icontrait1": 0, "icontrait2": 0,
        "teamid": 11, "jerseynumber": pid, "birthdate": forced_birthdate_for_age(25),
    } for pid in range(1, count + 1)]


def test_controller_task_names_and_generation_are_window_owned():
    first = PlannerController(plan_from_rows(rows()))
    second = PlannerController(first.original_plan)
    assert first.task_key("propose") != second.task_key("propose")
    generation = first.begin()
    assert first.accepts(generation)
    assert first.update_request("make all players 5 star skill moves")
    assert not first.accepts(generation)
    generation = first.generation
    cancelled = []
    first.close(SimpleNamespace(cancel=cancelled.append))
    assert cancelled == list(first.tasks)
    assert not first.accepts(generation)
    assert not second.closed


def test_controller_original_members_survive_partial_preview_and_bases():
    original = plan_from_rows(rows())
    controller = PlannerController(original)
    controller.last_proposal = original.keeping((1, 2))
    controller.update_bases({1: {"acceleration": 91}})
    assert controller.original_plan.playerids == (1, 2, 3)
    assert controller.original_plan.members[0].base["acceleration"] == 91
    assert controller.original_plan.keeping((1, 2, 3)).playerids == (1, 2, 3)


def test_saved_requests_round_trip_and_ignore_malformed_files(tmp_path):
    path = tmp_path / "planner_requests.json"
    assert load_requests(path) == {}
    save_request(path, "Skills", "Give all players 5 star skill moves")
    save_request(path, "Shirts", "arrange jerseys as recommended")
    assert set(load_requests(path)) == {"Skills", "Shirts"}
    path.write_text("broken", encoding="utf-8")
    assert load_requests(path) == {}


def test_negated_families_win_over_positive_keywords_and_ai():
    recipe = parse_recipe("prime 2005, improve skills and playstyles. Do not change age, body, identity or ratings.")
    assert recipe.wants_skills and recipe.wants_playstyles
    assert not recipe.wants_age and not recipe.wants_body and not recipe.wants_identity
    assert "attrs" not in recipe.families
    merged = lock_python_scope(recipe, merge_recipe(recipe, {"families": ["attrs", "age", "body", "identity"]}))
    assert merged.families == recipe.families
    plan = plan_from_rows(rows())
    session = build_session(roster_from_members(plan.members), recipe)
    patch = sanitize_patch({"overallrating": 99, "acceleration": 99, "birthdate": 123,
                            "height": 210, "nationality": 5, "skillmoves": 4},
                           recipe=recipe, target=session.targets[0], current=plan.members[0].base)
    assert patch["skillmoves"] == 4
    assert not {"overallrating", "potential", "acceleration", "birthdate", "height", "nationality"}.intersection(patch)


def test_requested_age_is_exact_and_negation_has_priority():
    plan = plan_from_rows(rows())
    recipe = parse_recipe("set age to 19 and improve skills")
    session = build_session(roster_from_members(plan.members), recipe)
    patch = sanitize_patch({"birthdate": forced_birthdate_for_age(28)}, recipe=recipe,
                           target=session.targets[0], current=plan.members[0].base)
    assert patch["birthdate"] == forced_birthdate_for_age(19)
    from companion.ui.surfaces.planner_review import field_value_text, request_checks
    staged = stage_per_player_plan(plan, session, {})
    assert any("Age 19 at" in text and "season baseline" in text for text in request_checks(staged, session))
    assert "-" in field_value_text("birthdate", patch["birthdate"])
    recipe = parse_recipe("set age to 19. Do not change age.")
    assert not recipe.wants_age and recipe.requested_age is None
    with pytest.raises(ValueError, match="16 and 40"):
        parse_recipe("set age to 12")


def test_exclusions_beat_named_targets_and_keepers_are_not_reintroduced():
    roster = roster_from_members(plan_from_rows(rows()).members)
    roster = tuple(replace(player, name="Buffon") if player.playerid == 3 else player for player in roster)
    session = build_session(roster, parse_recipe("Messi overall 99, Ronaldo overall 99; skip Messi."))
    assert session.playerids == (2,)
    session = build_session(roster, parse_recipe("improve all players except Messi and Ronaldo"))
    assert session.playerids == (3,)
    session = build_session(roster, parse_recipe("Buffon overall 99; exclude goalkeepers"))
    assert session.playerids == ()


def test_starters_are_user_owned_and_never_the_top_eleven():
    plan = plan_from_rows(rows(30))
    roster = roster_from_members(plan.members)
    recipe = parse_recipe("improve all players but protect starters")
    kept, protected, truncated = filter_roster(roster, recipe)
    assert len(kept) == 30 and protected == () and not truncated
    session = build_session(roster, recipe)
    assert any("Starting XI is unknown" in note for note in session.warnings)
    staged = stage_per_player_plan(plan, session, {pid: {"stamina": 90} for pid in plan.playerids})
    assert staged.blocking_issues
    session = build_session(roster, replace(recipe, starter_ids=(2, 7, 15)))
    assert session.protected_ids == (2, 7, 15)
    assert len(session.targets) == 27
    assert 1 in session.playerids


class Widget:
    def __init__(self, value=""):
        self.value = value
        self.options = {}
    def get(self):
        return self.value
    def set(self, value):
        self.value = value
    def configure(self, **kwargs):
        self.options.update(kwargs)


def test_one_rebuild_uses_current_text_and_can_restore_excluded_members(tmp_path, monkeypatch):
    from companion.ui.surfaces import planner
    from companion.core.executor import InlineExecutor
    from companion.app.store import Store
    from companion.integrations import ai_provider
    monkeypatch.setattr(ai_provider, "block_reason", lambda: "")
    monkeypatch.setattr(planner, "_plan_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner, "_set_plan", lambda state, plan, *args: state.update(plan=plan))
    original = plan_from_rows(rows())
    controller = PlannerController(original, request="old request")
    state = {
        "plan": original.keeping((1, 2)), "original_plan": original, "controller": controller,
        "session": planner._draft_session(original, "improve skills", "Club"),
        "prompt": "old request", "prompt_entry": Widget("make all players 5 star skill moves"),
        "include_vars": {1: Widget(True), 2: Widget(True), 3: Widget(False)},
        "pin_vars": {}, "phase": "review", "apply_btn": Widget(),
    }
    captured = []
    def propose(token, **kwargs):
        captured.append((kwargs["session"].recipe.prompt, kwargs["plan"].playerids))
        kwargs["on_success"](stage_per_player_plan(kwargs["plan"], kwargs["session"], {}))
    monkeypatch.setattr(planner, "session_propose_worker", propose)
    svc = SimpleNamespace(executor=InlineExecutor(), store=Store(), catalog=None)
    planner._start_session_propose(svc, state, None, None, Widget(), Widget(), Widget())
    assert captured[-1] == ("make all players 5 star skill moves", (1, 2))
    state["include_vars"][3].set(True)
    state["prompt_entry"].set("make all players 4 star skill moves")
    planner._start_session_propose(svc, state, None, None, Widget(), Widget(), Widget())
    assert captured[-1] == ("make all players 4 star skill moves", (1, 2, 3))
    assert controller.original_plan.playerids == (1, 2, 3)
    assert all(plan["skillmoves"] == 3 for plan in state["plan"].overrides.values())
    assert len({key.split(".")[1] for key in svc.executor.calls}) == 1


def test_review_matrix_is_indexed_once_per_immutable_plan(monkeypatch):
    from companion.domain.squad_plan import SquadPlan
    from companion.ui.surfaces.planner_review import player_summary, review_totals, _REVIEW_INDEX
    plan = plan_from_rows(rows(30)).with_overrides({pid: {"stamina": 90} for pid in range(1, 31)})
    calls = []
    original = SquadPlan.matrix_rows
    def matrix(self):
        calls.append(self)
        return original(self)
    monkeypatch.setattr(SquadPlan, "matrix_rows", matrix)
    for pid in plan.playerids:
        player_summary(plan, pid, ready=True)
    review_totals(plan)
    assert len(calls) == 1
    for _ in range(6):
        review_totals(replace(plan, label=str(_)))
    assert len(_REVIEW_INDEX) <= 4


def test_queued_progress_cannot_update_closed_or_superseded_window():
    from companion.ui.surfaces.planner import _codex_progress
    queued = []
    controller = PlannerController(plan_from_rows(rows()))
    controller.begin()
    label = Widget()
    state = {"controller": controller}
    svc = SimpleNamespace(executor=SimpleNamespace(on_ui_thread=queued.append))
    _codex_progress(svc, state, label)("Old proposal", 1, 2)
    controller.begin()
    queued.pop()()
    assert label.options == {}
    _codex_progress(svc, state, label)("After close", 2, 2)
    controller.close()
    queued.pop()()
    assert label.options == {}


@pytest.mark.gui
def test_real_review_rebuild_restores_selection_and_consumes_text_and_starter_roles(tmp_path, monkeypatch):
    import customtkinter as ctk
    from companion.ui.surfaces import planner
    from companion.ui.surfaces.planner_review import dirty, render_players
    from companion.ui.surfaces.planner_team import set_player_role
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.core.clock import FakeClock
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport
    from companion.integrations import ai_provider

    root = ctk.CTk()
    root.geometry("1x1+5000+5000")
    monkeypatch.setattr(planner, "_raise_window", lambda _window: None)
    def toplevel():
        window = ctk.CTkToplevel(root)
        window.geometry("1020x740+5000+5000")
        return window
    monkeypatch.setattr(planner, "_new_toplevel", toplevel)
    monkeypatch.setattr(ai_provider, "block_reason", lambda: "")
    monkeypatch.setattr(planner, "_start_interpret", lambda *args: pytest.fail("Build has one action; opening does not query AI."))
    captured = []
    def propose(token, **kwargs):
        captured.append((kwargs["session"].recipe, kwargs["plan"].playerids))
        kwargs["on_success"](stage_per_player_plan(kwargs["plan"], kwargs["session"], {}))
    monkeypatch.setattr(planner, "session_propose_worker", propose)
    svc = Services(paths=TempAppPaths(tmp_path), store=Store(), clock=FakeClock(),
                   executor=InlineExecutor(), transport=FakeTransport())
    original = plan_from_rows(rows(30))
    window = None
    try:
        planner._open_session_planner(svc, original, prompt="make all players 5 star skill moves",
                                     club_name="Club", selected_rows=rows(30))
        window = next(child for child in root.winfo_children() if isinstance(child, ctk.CTkToplevel))
        state = window._planner_state
        assert captured == []
        state["include_vars"][3].set(False)
        dirty(state)
        state["propose_btn"].invoke()
        assert len(captured[-1][1]) == 29
        assert len(state["review_rows"]) == 30
        assert 3 in state["include_vars"]
        state["include_vars"][3].set(True)
        dirty(state)
        state["propose_btn"].invoke()
        assert len(captured[-1][1]) == 30
        previous = state["plan"]
        state["prompt_entry"].delete(0, "end")
        state["prompt_entry"].insert(0, "make all players 4 star skill moves")
        planner._request_changed(state)
        assert state["plan"] is previous
        assert state["apply_btn"].cget("state") == "disabled"
        state["propose_btn"].invoke()
        assert captured[-1][0].forced_skill_stars == 4
        assert all(patch["skillmoves"] == 3 for patch in state["plan"].overrides.values())
        set_player_role(state, 3, "Starter")
        state["prompt_entry"].delete(0, "end")
        state["prompt_entry"].insert(0, "make all players 5 star skill moves; protect starters")
        planner._request_changed(state)
        state["propose_btn"].invoke()
        assert captured[-1][0].starter_ids == (3,)
        assert 3 not in captured[-1][1] and 1 in captured[-1][1]
        assert len(state["review_rows"]) == 30
        for size in ("1020x740", "800x600"):
            window.geometry(size)
            root.update()
            assert state["review_list"].winfo_height() >= 160
            assert state["apply_btn"].winfo_rooty() + state["apply_btn"].winfo_height() <= window.winfo_rooty() + window.winfo_height()
    finally:
        if window is not None:
            state["controller"].close(svc.executor)
            window.destroy()
        root.destroy()
