"""Shell + surface-registry tests.

Split in two on purpose:

* The registry half is **pure** — it proves that a surface which fails to import
  is reported rather than swallowed (the v1 dead-Catalog-tab bug) without
  needing a window. The ErrorState factory is injected, so no Tk is involved.
* The widget half opens a real ``CTk`` window and is therefore marked
  ``@pytest.mark.gui``: per ``tests/v2/conftest.py`` it is skipped unless
  ``COMPANION_ALLOW_GUI_TESTS=1``, because the user plays FC 26 on the primary
  monitor while this suite runs.

Every window these tests open is withdrawn immediately, so even with GUI tests
enabled nothing flashes over a match.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from companion.app import events as E
from companion.app.presenters import editor_view, liveness_view
from companion.app.services import Services
from companion.app.state import AppState, BridgeState
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.ui import nav, theme
from companion.ui.shell import _jobs_only_changed, _surface_needs_refresh

# ---------------------------------------------------------------------------
# Surface registry — no Tk
# ---------------------------------------------------------------------------


def test_connection_copy_refreshes_club_without_rebuilding_for_age_only():
    before = AppState(
        bridge=BridgeState(
            Liveness(
                pill=Pill.STALLED,
                message="Old worker session ignored.",
                armed=False,
                last_drain_age=10.0,
            )
        )
    )
    age_only = replace(
        before,
        bridge=BridgeState(replace(before.bridge.liveness, last_drain_age=11.0)),
    )
    armed = replace(
        before,
        bridge=BridgeState(
            replace(
                before.bridge.liveness,
                pill=Pill.ARMED,
                message="Worker is armed.",
                armed=True,
            )
        ),
    )

    assert not _surface_needs_refresh("club", before, age_only)
    assert _surface_needs_refresh("club", before, armed)
    assert _surface_needs_refresh("automations", before, armed)
    assert not _surface_needs_refresh("player", before, armed)


def test_sign_jobs_only_change_does_not_require_a_full_rebuild_helper():
    from companion.app.state import JobView, JobsState, SquadState
    from companion.domain.outcome import ApplyOutcome

    before = AppState()
    jobs_only = before.with_(
        jobs=JobsState(
            active={"jid": JobView("jid", "Add Player: One", ApplyOutcome.QUEUED)}
        )
    )
    squad_too = jobs_only.with_(squad=SquadState(teamid=1))
    assert _jobs_only_changed(before, jobs_only)
    assert _surface_needs_refresh("add_player", before, jobs_only)
    assert not _jobs_only_changed(before, squad_too)
    assert not _jobs_only_changed(before, before)
    from companion.app.state import TargetState

    target_too = jobs_only.with_(target=TargetState(playerid=1, name="X"))
    assert not _jobs_only_changed(before, target_too)
    assert _surface_needs_refresh("automations", before, jobs_only)


def test_shell_soft_refreshes_automations_on_jobs_only():
    src = Path(__file__).parents[2].joinpath("companion", "ui", "shell.py").read_text(
        encoding="utf-8"
    )
    assert 'key in ("add_player", "automations")' in src
    assert "refresh_automation_queue" in src
    assert "Open Activity." in src


def _record_error(parent, *, message, cause="", action_label="", action=None):
    """Stand-in for ``states.error_state`` so the registry is testable headlessly."""
    return {"parent": parent, "message": message, "cause": cause,
            "action_label": action_label, "action": action}


def test_default_registry_has_the_five_surfaces_plus_the_drawer():
    registry = nav.default_registry()
    assert registry.keys()[:6] == (
        "club", "player", "add_player", "injury", "automations", "library"
    )
    assert nav.DRAWER.key in registry
    assert registry.title_of("club") == "Club"


def test_every_registered_surface_module_is_importable():
    """Regression: missing companion.ui.surfaces broke every tab at runtime."""
    registry = nav.default_registry()
    for key in registry.keys():
        builder = registry.resolve(key)
        assert callable(builder), key
        # Image #1 was ModuleNotFoundError for companion.ui.surfaces*
        assert builder.__module__.startswith("companion.ui.surfaces"), (
            f"{key} resolved to {builder.__module__}"
        )


def test_registry_surface_modules_exist_on_disk():
    """Structural guard: every Surface.module path is a real package module."""
    import importlib.util

    for surface in (*nav.SURFACES, nav.DRAWER):
        spec = importlib.util.find_spec(surface.module)
        assert spec is not None, f"missing module {surface.module} for {surface.key}"


def test_number_keys_do_not_switch_surfaces():
    """Tabs are click-only; single-digit hotkeys are not registered."""
    assert nav.default_registry().hotkeys() == {}
    assert all(not s.hotkey or len(s.hotkey) != 1 for s in nav.SURFACES)


def test_every_surface_states_its_job():
    """P1: navigation names what the user wants to achieve."""
    for surface in nav.SURFACES:
        assert surface.purpose.endswith("?") or surface.purpose.endswith(".")


def test_resolve_reports_a_missing_module_instead_of_swallowing_it():
    registry = nav.SurfaceRegistry([
        nav.Surface("ghost", "Ghost", "companion.ui.surfaces.does_not_exist")
    ])
    with pytest.raises(nav.SurfaceError) as exc:
        registry.resolve("ghost")
    assert "Ghost could not be loaded" in str(exc.value)
    assert exc.value.detail                      # traceback is kept for the UI


def test_resolve_reports_a_module_without_a_builder():
    registry = nav.SurfaceRegistry([
        nav.Surface("odd", "Odd", "companion.ui.theme", attr="build")
    ])
    with pytest.raises(nav.SurfaceError, match="no callable"):
        registry.resolve("odd")


def test_unknown_key_is_an_error_not_a_none():
    with pytest.raises(nav.SurfaceError):
        nav.SurfaceRegistry().resolve("nope")


def test_the_error_detail_drops_interpreter_plumbing():
    """H4 keeps the raw cause — but an import failure is 14 lines of
    ``importlib._bootstrap`` wrapping one useful sentence."""
    registry = nav.SurfaceRegistry([
        nav.Surface("ghost", "Ghost", "companion.ui.surfaces.does_not_exist")
    ])
    with pytest.raises(nav.SurfaceError) as exc:
        registry.resolve("ghost")
    detail = exc.value.detail
    assert "ModuleNotFoundError" in detail
    assert "importlib._bootstrap" not in detail
    assert "<frozen" not in detail
    assert not detail.startswith("Traceback")     # the headline already said it
    assert len(detail.splitlines()) <= nav._TRACE_LINES


def test_a_broken_surface_renders_an_error_state_and_does_not_raise():
    """v1 swallowed exactly this into a dead Catalog tab with no message."""
    registry = nav.SurfaceRegistry([
        nav.Surface("catalog", "Catalog", "companion.ui.surfaces.missing")
    ])
    widget = registry.build("catalog", parent=object(), svc=None,
                            error_factory=_record_error, retry=lambda: None)
    assert "Catalog could not be loaded" in widget["message"]
    assert widget["action_label"] == "Retry"      # H5: never a dead end
    assert callable(widget["action"])
    assert "catalog" in registry.errors


def test_a_surface_that_raises_while_building_is_isolated(monkeypatch):
    registry = nav.SurfaceRegistry([nav.Surface("boom", "Boom", "companion.ui.theme")])
    monkeypatch.setattr(registry, "resolve",
                        lambda key: (_ for _ in ()).throw(ZeroDivisionError("boom")))
    widget = registry.build("boom", parent=object(), svc=None,
                            error_factory=_record_error)
    assert "Boom failed to open" in widget["message"]
    assert "ZeroDivisionError" in widget["cause"]


def test_a_builder_returning_none_is_treated_as_a_failure():
    """A surface that builds nothing is the dead tab, just with no exception."""
    registry = nav.SurfaceRegistry([nav.Surface("void", "Void", "companion.ui.theme")])
    registry.resolve = lambda key: (lambda parent, svc, vm: None)  # type: ignore[assignment]
    widget = registry.build("void", parent=object(), svc=None,
                            error_factory=_record_error)
    assert "built nothing" in widget["message"]


def test_a_good_surface_is_cached_and_forget_clears_it():
    registry = nav.SurfaceRegistry([nav.Surface("ok", "Ok", "companion.ui.theme")])
    sentinel = object()
    registry.resolve = lambda key: (lambda parent, svc, vm: sentinel)  # type: ignore[assignment]
    assert registry.build("ok", parent=None, svc=None) is sentinel
    assert registry.built("ok") is sentinel
    assert registry.errors == {}
    registry.forget("ok")
    assert registry.built("ok") is None


def test_one_broken_surface_does_not_stop_the_others():
    registry = nav.SurfaceRegistry([
        nav.Surface("good", "Good", "companion.ui.theme", order=1),
        nav.Surface("bad", "Bad", "companion.ui.surfaces.nope", order=2),
    ])
    registry.build("bad", parent=object(), svc=None, error_factory=_record_error)
    assert registry.keys() == ("good", "bad")     # the app still has its nav


# ---------------------------------------------------------------------------
# Real-Tk half
# ---------------------------------------------------------------------------


_HAS_CTK = importlib.util.find_spec("customtkinter") is not None


def needs_tk(fn):
    """Mark a test that needs a real toplevel.

    Two independent gates, deliberately: ``gui`` defers to the suite-wide
    opt-in in ``conftest.py``, and the skipif keeps the file importable on a
    machine with no customtkinter at all. Note that neither gate *constructs*
    anything — probing for a display by opening a Tk root at collection time
    would flash a window on every run, which is the thing we are avoiding.
    """
    fn = pytest.mark.skipif(not _HAS_CTK, reason="customtkinter not installed")(fn)
    return pytest.mark.gui(fn)


@pytest.fixture
def svc(tmp_path: Path) -> Services:
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
    )


@pytest.fixture
def shell_factory(svc):
    """Build a ``Shell``, withdrawn, and guarantee it is closed.

    Withdrawing matters even under ``COMPANION_ALLOW_GUI_TESTS=1``: the shell's
    own window is the one thing in this file big enough to cover a match.
    """
    from companion.ui.shell import Shell

    made: list = []

    def build(service=None, registry=None):
        shell = Shell(service or svc, registry=registry)
        try:
            shell.root.withdraw()
        except Exception:
            pass
        made.append(shell)
        return shell

    yield build
    for shell in made:
        shell.close()


@pytest.fixture
def tk_root():
    import customtkinter as ctk

    root = ctk.CTk()
    root.withdraw()
    try:
        yield root
    finally:
        # Flush CTk's own deferred callbacks (it schedules a titlebar-icon fix
        # ~200 ms after construction) before tearing the interpreter down.
        for step in (root.update, root.destroy):
            try:
                step()
            except Exception:
                pass


@needs_tk
def test_widget_kit_constructs(tk_root):
    """Every factory builds against a real Tk without raising."""
    from companion.ui.widgets import (
        badge, button, card, divider, empty_state, error_state, hairline, icon_button,
        key_value_row, loading_row, ovr_chip, ovr_delta_chips, panel, pill,
        section_header, skeleton, skeleton_table, stat_chip,
    )

    for build in (
        lambda p: panel(p),
        lambda p: card(p, hover=True, on_click=lambda: None),
        lambda p: section_header(p, "Squad", "45 players"),
        lambda p: divider(p),
        lambda p: hairline(p),
        lambda p: button(p, "Apply", lambda: None, kind="primary"),
        lambda p: button(p, "Nope", None, kind="ghost", disabled_reason="Pick a player."),
        lambda p: icon_button(p, "⧉", None, badge_count=3),
        lambda p: pill(p, "ARMED", tone="ok"),
        lambda p: badge(p, "26", tone="info"),
        lambda p: stat_chip(p, "OVR", 86),
        lambda p: stat_chip(p, "POT", None),
        lambda p: key_value_row(p, "Stamina", None, provenance="unknown"),
        lambda p: ovr_chip(p, 91),
        lambda p: ovr_chip(p, None),
        lambda p: ovr_delta_chips(p, 86, 91),
        lambda p: skeleton(p, rows=4),
        lambda p: skeleton_table(p, rows=3),
        lambda p: loading_row(p, "Reading squad…", detail="4 s"),
        lambda p: empty_state(p, headline="No squad yet", body="Read it.",
                              action_label="Read my squad", action=lambda: None),
        lambda p: error_state(p, message="Couldn't read your squad.",
                              cause="lua:63: nil", action=lambda: None),
    ):
        widget = build(tk_root)
        assert widget is not None
        widget.destroy()


@needs_tk
def test_empty_and_error_states_refuse_to_build_a_dead_end(tk_root):
    from companion.ui.widgets import DeadEndError, empty_state, error_state

    with pytest.raises(DeadEndError):
        empty_state(tk_root, headline="Nothing here", body="…")
    with pytest.raises(DeadEndError):
        error_state(tk_root, message="Broken", action=None)


def test_club_empty_squad_state_has_a_real_read_action():
    """The compact no-squad screen keeps a named next action without a dead-end card."""
    from companion.ui.surfaces import club
    import inspect

    source = inspect.getsource(club._build_setup)
    assert '"Read my squad"' in source
    assert "lambda: _read_squad(svc)" in source
    assert "states.empty_state" not in source


def test_club_waiting_state_has_a_real_connection_action():
    """The no-squad, Live-Editor-waiting state must offer a safe way forward."""
    from companion.ui.surfaces import club
    import inspect

    source = inspect.getsource(club._build_setup)
    assert '"Check connection"' in source
    assert "lambda: _refresh_connection(svc)" in source


def test_check_connection_polls_liveness_and_returns_to_club(monkeypatch):
    """The waiting-state CTA is a real liveness poll, never a decorative no-op."""
    from types import SimpleNamespace
    from companion.ui.surfaces import club

    refreshed: list[object] = []
    navigated: list[str] = []
    events: list[object] = []
    svc = SimpleNamespace(
        store=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                bridge=SimpleNamespace(
                    liveness=SimpleNamespace(armed=False, message="Career save is not open yet.")
                )
            ),
            dispatch=events.append,
        ),
        ui=SimpleNamespace(navigate=navigated.append),
    )
    monkeypatch.setattr(
        "companion.app.commands.apply.refresh_liveness",
        lambda actual_svc: refreshed.append(actual_svc),
    )

    club._refresh_connection(svc)

    assert refreshed == [svc]
    assert navigated == ["club"]
    assert any(isinstance(event, E.StatusSet) for event in events)


@needs_tk
def test_data_grid_paints_and_selects(tk_root):
    from companion.ui.widgets import Column, DataGrid, TableModel

    rows = [{"playerid": i, "name": f"Player {i}", "ovr": 60 + (i % 40)}
            for i in range(500)]
    model = TableModel(
        (Column("name", "NAME", width=200, stretch=True),
         Column("ovr", "OVR", width=50, numeric=True)),
        rows,
        key_field="playerid",
    )
    clicked: list[dict] = []
    grid = DataGrid(tk_root, model, visible_rows=12,
                    on_row_click=clicked.append)
    grid.pack(fill="both", expand=True)
    tk_root.update_idletasks()

    # P-8 / §7.1: widget count is O(visible), not O(rows).
    assert len(grid._rows) == 12
    assert model.visible_count == 500

    grid._on_click(3, type("E", (), {"state": 0})())
    assert clicked and clicked[0]["playerid"] == 3

    grid._scroll_to(400)
    assert grid.first_index == 400
    grid._scroll_to(10**6)
    assert grid.first_index == 500 - 12          # clamped to the last window

    grid._sort("ovr")
    assert model.sort_key == "ovr" and model.sort_desc is True
    grid.set_filter("Player 4")
    assert model.visible_count < 500
    grid.widget.destroy()


@needs_tk
def test_data_grid_cursor_only_click_does_not_change_checked_selection(tk_root):
    """Roster browsing must not accidentally turn into a bulk selection."""
    from companion.ui.widgets import Column, DataGrid, TableModel

    model = TableModel(
        (Column("name", "NAME", stretch=True),),
        ({"playerid": 1, "name": "One"}, {"playerid": 2, "name": "Two"}),
        key_field="playerid",
    )
    grid = DataGrid(tk_root, model, select_on_click=False)
    grid.pack(fill="both", expand=True)
    tk_root.update_idletasks()

    grid._on_click(1, type("E", (), {"state": 0})())
    assert model.cursor == 1
    assert model.selected_count == 0
    grid._nav(-1)
    assert model.cursor == 0
    assert model.selected_count == 0
    grid._key_toggle()
    assert model.selected_count == 1
    grid.widget.destroy()


@needs_tk
def test_tab_bar_stays_compact_and_leaves_room_for_the_chip(tk_root):
    """An unsized CTkFrame requests 200 px; that widened every tab column and
    pushed the connection chip off the right of the chrome."""
    registry = nav.default_registry()
    bar = nav.tab_bar(tk_root, registry, active="club", on_select=lambda k: None)
    bar.pack()
    tk_root.update_idletasks()
    per_tab = bar.winfo_reqwidth() / len(nav.SURFACES)
    # Button + the underline's own padding + the column gap. The bug produced
    # ~216 px per tab, because the unsized rule requested CTk's 200 px default.
    assert per_tab <= nav.TAB_WIDTH + 2 * theme.SP2 + theme.SP1 + 4, (
        f"{per_tab:.0f}px per tab"
    )
    bar.set_active("library")               # repaint, never rebuild
    bar.destroy()


@needs_tk
def test_apply_stays_visible_at_the_minimum_window_size(shell_factory):
    """A7: 1040 × 720 with the Apply button fully visible."""
    from companion.ui.shell import MIN_SIZE

    shell = shell_factory()
    shell.root.geometry(f"{MIN_SIZE[0]}x{MIN_SIZE[1]}")
    shell.root.update_idletasks()
    shell._render()
    shell.root.update_idletasks()

    # Withdrawn windows do not have meaningful screen coordinates on Windows;
    # assert the required chrome widths fit inside the supported minimum.
    action_width = shell.review_btn.winfo_reqwidth() + shell.apply_btn.winfo_reqwidth()
    assert action_width + 2 * 24 <= MIN_SIZE[0]
    assert shell.tabs.winfo_reqwidth() + shell.status_holder.winfo_reqwidth() <= MIN_SIZE[0]


@needs_tk
def test_diff_view_renders(tk_root):
    from companion.ui.widgets import build_diff, diff_view

    model = build_diff(
        {"overallrating": (86, 91), "stamina": (None, 88)},
        subject="Cole Palmer", subject_id=257534, undo_available=True,
    )
    view = diff_view(tk_root, model, on_view_raw=lambda: None)
    assert view is not None
    view.destroy()


# ---- the shell itself ------------------------------------------------------


@needs_tk
def test_shell_opens_with_no_surfaces_available(shell_factory):
    """A build with zero surface modules must still give the user a window."""
    broken = nav.SurfaceRegistry(
        (nav.Surface("club", "Club", "companion.ui.surfaces.does_not_exist"),)
    )
    shell = shell_factory(registry=broken)
    shell.root.update_idletasks()
    assert shell.active == "club"
    # The Club "surface" is an ErrorState, and that is recorded, not hidden.
    assert "club" in shell.registry.errors
    assert shell._surfaces["club"] is not None


@needs_tk
def test_shell_chrome_renders_armed_as_a_success_tone(svc, shell_factory):
    """P5 / §3.5: ARMED with an empty queue is success, never OFF.

    The shell primes liveness during construction, so the very first paint
    already reflects the transport instead of the OFF default.
    """
    shell = shell_factory()
    shell._render()
    shell.root.update_idletasks()
    assert shell._pill is not None
    assert svc.store.snapshot().bridge.pill is Pill.ARMED
    assert liveness_view(svc.store.snapshot().bridge.liveness)["tone"] == "ok"
    assert shell.target_label.cget("text") == "No player selected"


@needs_tk
def test_changes_bar_is_the_single_apply_affordance(svc, shell_factory):
    """P2: one verb, one meaning, one button — and P4 gates it behind a diff."""
    shell = shell_factory()
    # Nothing staged: quiet strip, Apply explains why it is off (A9).
    shell._render()
    assert shell.changes_title.cget("text") == "CHANGES"
    assert "No changes staged" in shell.changes_summary.cget("text")
    assert str(shell.apply_btn.cget("state")) == "disabled"

    svc.store.dispatch(E.TargetLocked(playerid=257534, name="Cole Palmer"))
    svc.store.dispatch(E.EditorLoaded(base={"overallrating": 86}))
    svc.store.dispatch(E.FieldEdited("overallrating", 91))
    shell._render()
    shell.root.update_idletasks()

    assert shell.changes_title.cget("text") == "CHANGES (1)"
    assert "Overall 86 → 91" in shell.changes_summary.cget("text")
    assert str(shell.apply_btn.cget("state")) == "normal"

    model = shell._diff_model()
    assert model.apply_label == "Apply 1"
    assert model.subject == "Cole Palmer"


@needs_tk
def test_apply_is_blocked_with_a_reason_when_the_worker_is_off(svc, shell_factory):
    """A9: a disabled control states why. P5: the reason is the bridge's own copy."""
    svc.transport.set_liveness(
        Liveness(pill=Pill.OFF, message="LE isn't running.", armed=False)
    )
    shell = shell_factory()
    svc.store.dispatch(E.TargetLocked(playerid=1, name="X"))
    svc.store.dispatch(E.FieldEdited("overallrating", 91))
    shell._render()
    assert str(shell.apply_btn.cget("state")) == "disabled"
    # The tooltip text is the transport's own sentence, not an invented one.
    assert editor_view(svc.store.snapshot())["blocked_reason"] == "LE isn't running."


@needs_tk
def test_in_flight_count_badges_the_drawer_toggle(svc, shell_factory):
    shell = shell_factory()
    shell._render()
    assert shell.drawer_btn.cget("text") == "⧉"
    svc.store.dispatch(E.JobSubmitted(job_id="01J", label="Apply 1 field"))
    shell._render()
    assert shell.drawer_btn.cget("text") == "⧉ 1"


@needs_tk
def test_drawer_toggles_without_a_surface_module(shell_factory):
    shell = shell_factory()
    shell.toggle_drawer()
    assert shell.drawer_open is True
    shell.toggle_drawer()
    assert shell.drawer_open is False


@needs_tk
def test_switching_surfaces_never_raises(shell_factory):
    shell = shell_factory()
    for key in shell.registry.keys():
        if key == nav.DRAWER.key:
            continue
        shell._show(key)
        assert shell.active == key
    shell._rebuild("club")               # the ErrorState's Retry path
    assert shell.active == "club"


@needs_tk
def test_palette_hook_is_callable_and_defaults_to_saying_so(svc, shell_factory):
    shell = shell_factory()
    shell.open_palette()
    assert "palette" in svc.store.snapshot().status.lower()
    seen: list[int] = []
    shell.on_palette = lambda: seen.append(1)
    shell.open_palette()
    assert seen == [1]


@needs_tk
def test_close_shuts_the_executor_down(svc, monkeypatch, shell_factory):
    shut: list[int] = []
    monkeypatch.setattr(svc.executor, "shutdown", lambda: shut.append(1))
    shell = shell_factory()
    shell.close()
    # Idempotent: WM_DELETE_WINDOW can fire twice, and the fixture closes again.
    shell.close()
    assert shut == [1]


def test_run_gui_is_exported_with_the_signature_the_cli_calls():
    """``companion/cli.py::cmd_gui`` calls ``run_gui(root=...)`` exactly."""
    import inspect

    from companion.ui.shell import run_gui

    params = inspect.signature(run_gui).parameters
    assert list(params) == ["root"]
    assert params["root"].default is None
