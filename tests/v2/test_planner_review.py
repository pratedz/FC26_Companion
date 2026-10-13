"""Readable review and windowed controls without any provider/game requests."""
from dataclasses import replace

import pytest

from companion.domain.squad_plan import plan_from_rows
from companion.domain.squad_session import build_session, roster_from_members, parse_recipe, stage_per_player_plan
from companion.domain.playstyles import encode_names
from companion.ui.surfaces.planner_review import field_value_text, player_summary, request_checks, review_totals


def sample_plan():
    plan = plan_from_rows([
        {"playerid": 82407, "name": "Carvajal", "position": "RB", "overallrating": 86,
         "potential": 86, "teamid": 11, "jerseynumber": 2, "stamina": 85,
         "icontrait1": 0, "icontrait2": 0},
        {"playerid": 85308, "name": "Manuel Neuer", "position": "GK", "overallrating": 90,
         "potential": 90, "teamid": 11, "jerseynumber": 1, "icontrait1": 0, "icontrait2": 0},
    ])
    session = build_session(roster_from_members(plan.members), parse_recipe("Carvajal jersey 3 and improve playstyle+ no ceiling"))
    staged = stage_per_player_plan(plan, session, {
        82407: {**encode_names(["Whipped Pass", "Jockey", "Intercept", "Anticipate"], plus=True),
                "jerseynumber": 3, "stamina": 92},
        85308: encode_names(["GK Footwork", "GK Far Reach"], plus=True),
    })
    return plan, session, staged


def test_summary_hides_unchanged_overall_and_never_shows_old_proposal_during_build():
    _, _, staged = sample_plan()
    summary = player_summary(staged, 82407, ready=True)
    assert "#2 → #3" in summary and "6 PlayStyle+" in summary
    assert "OVR" not in summary and "icontrait" not in summary
    assert player_summary(staged, 82407, ready=False) == "Waiting for a proposal"
    assert review_totals(staged) == (2, 1, 2)


def test_review_displays_five_stars_and_exact_request_completion_with_keeper_exception():
    plan, _, _ = sample_plan()
    session = build_session(roster_from_members(plan.members), parse_recipe(
        "make all player 5 skill star move and Quick Step+. improve playstyle+ no ceiling"))
    staged = stage_per_player_plan(plan, session, {
        82407: encode_names(["Jockey"], plus=True),
        85308: encode_names(["GK Footwork"], plus=True),
    })
    assert field_value_text("skillmoves", 4) == "5★"
    assert field_value_text("skillmoves", None) == "—"
    assert "5★ skills" in player_summary(staged, 82407, ready=True)
    checks = request_checks(staged, session)
    assert "5★ skills 2/2" in checks
    assert "Quick Step+ 1/1 (1 position exception)" in checks
    assert any("no ceiling" in check for check in checks)


@pytest.mark.gui
def test_windowed_review_keeps_primary_actions_visible_and_selection_edits_block_apply(tmp_path, monkeypatch):
    import customtkinter as ctk
    from companion.ui.surfaces import planner
    from companion.app.store import Store
    from companion.core.clock import FakeClock
    from companion.core.executor import InlineExecutor
    from companion.core.paths import TempAppPaths
    from companion.core.transport.fake import FakeTransport
    from companion.app.services import Services
    from companion.ui.surfaces.planner_review import render_players, filter_rows

    root = ctk.CTk()
    root.geometry("1x1+5000+5000")
    monkeypatch.setattr(planner, "_raise_window", lambda _w: None)
    def toplevel():
        window = ctk.CTkToplevel(root)
        window.geometry("1020x740+5000+5000")
        return window
    monkeypatch.setattr(planner, "_new_toplevel", toplevel)
    monkeypatch.setattr(planner, "_start_interpret", lambda *a: None)
    plan, session, staged = sample_plan()
    svc = Services(paths=TempAppPaths(tmp_path), store=Store(), clock=FakeClock(),
                   executor=InlineExecutor(), transport=FakeTransport())
    win = None
    try:
        planner._open_session_planner(svc, plan, prompt="", club_name="Manchester United", selected_rows=[])
        win = next(w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel))
        state = win._planner_state
        state.update(plan=staged, session=session, phase="review")
        render_players(state)
        planner._sync_session_apply(state)
        for size in ("1020x740", "800x600"):
            win.geometry(size)
            root.update()
            for key in ("apply_btn", "propose_btn", "progress_lbl", "review_requirements"):
                widget = state[key]
                assert widget.winfo_ismapped()
                assert widget.winfo_rooty() + widget.winfo_height() <= win.winfo_rooty() + win.winfo_height()
            assert state["review_list"].winfo_height() >= 160
            assert state["review_detail"].winfo_width() > 300
        assert state["apply_btn"].cget("state") == "normal"
        # The provisional Python ranking must not pin unchanged values over
        # a later AI ranking when the user never edited the target.
        assert planner._snapshot_preview_edits(state)[1] == {}
        state["session"] = replace(session, targets=tuple(
            replace(target, target_ovr=89) if target.playerid == 82407 else target
            for target in session.targets))
        render_players(state)
        assert state["pin_vars"][82407].get() == "89"
        row_count = len(state["review_rows"])
        frames = [row["frame"] for row in state["review_rows"].values()]
        state["review_search"].insert(0, "Neuer")
        filter_rows(state)
        assert state["review_visible"] == [85308]
        assert len(state["review_rows"]) == row_count
        assert [row["frame"] for row in state["review_rows"].values()] == frames
        state["ceiling_unlimited"].set(True)
        planner._mark_review_dirty(state)
        assert state["apply_btn"].cget("state") == "disabled"
        assert not state["review_requirements"].winfo_manager()
        # The complete four-part request must fit the same compact review.
        full = plan_from_rows([{
            "playerid": pid, "name": f"Player {pid}", "position": "GK" if pid == 20 else "CM",
            "overallrating": 80, "potential": 84, "teamid": 11, "jerseynumber": pid,
            "skillmoves": 1, "trait1": 0, "trait2": 0, "icontrait1": 0, "icontrait2": 0,
        } for pid in range(1, 21)])
        full_session = build_session(roster_from_members(full.members), parse_recipe(
            "1. make all player 5 skill star move. 2. make all player have quick step+ playstyles+. "
            "3. arrange these player by jersey no as you recomended. "
            "4. improve thes player by adding playstyle and playstyle+ no limit ceiling"))
        full_staged = stage_per_player_plan(full, full_session, {
            pid: encode_names(["GK Footwork"] if pid == 20 else ["Anticipate"], plus=True)
            for pid in full.playerids})
        state.update(plan=full_staged, original_plan=full, draft_session=full_session,
                     session=full_session, phase="review", edits_dirty=False)
        render_players(state)
        planner._sync_session_apply(state)
        for size in ("1020x740", "800x600"):
            win.geometry(size)
            root.update()
            assert state["review_requirements"].winfo_ismapped()
            assert "5★ skills 20/20" in state["review_requirements"].cget("text")
            assert "Quick Step+ 19/19" in state["review_requirements"].cget("text")
            assert state["review_list"].winfo_height() >= 160
            assert state["apply_btn"].winfo_rooty() + state["apply_btn"].winfo_height() <= win.winfo_rooty() + win.winfo_height()
    finally:
        if win is not None:
            win.destroy()
        root.destroy()
