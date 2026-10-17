"""Squad Planner window — multi-select draft + Codex/preset template + Apply.

Opened from Club. Codex workers must accept the executor cancel token
(``fn(token)``) or the pool raises and the UI hangs on “proposing…”.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ...app.commands.squad_plan import apply_squad_plan
from ...domain.builds import preset
from ...domain.job import JobValidationError
from ...domain.squad_plan import (
    SquadPlan,
    plan_from_rows,
    selection_summary_for_ai,
    template_from_fields,
)
from ...domain.squad_session import (
    SquadSession,
    build_session,
    jersey_only,
    needs_live_base,
    parse_recipe,
    propose_payload,
    refine_session,
    relative_only,
    roster_from_rows,
    stage_per_player_plan,
)
from ...core.log import get_logger
from .. import theme
from ..widgets.primitives import (
    button,
    ensure_ctk,
    section_title,
    muted_label,
    panel,
    pill,
    set_disabled,
    text_label,
)
from ..widgets.table import Column, DataGrid, TableModel, inclusive_span
from ._common import set_status
from .planner_controller import PlannerController, REQUEST_PRESETS, load_requests, save_request
from .planner_shirts import shirt_conflict_text

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


_LOG = get_logger("ui.planner")

_PRESET_IDS = (
    ("balanced_85", "Balanced 85"),
    ("pace_monster", "Pace monster"),
    ("cb_wall", "CB wall"),
    ("playmaker", "Playmaker"),
    ("finisher", "Finisher"),
)


def _host_window() -> Any:
    try:
        import tkinter as tk

        getter = getattr(tk, "_get_default_root", None)
        if callable(getter):
            return getter()
        return getattr(tk, "_default_root", None)
    except Exception:
        return None


def _new_toplevel() -> Any:
    host = _host_window()
    try:
        if host is not None:
            return ctk.CTkToplevel(host)
    except Exception:
        pass
    return ctk.CTkToplevel()


def _plan_progress(
    state: dict[str, Any],
    text: str,
    *,
    mode: str = "idle",
    done: int = 0,
    total: int = 0,
) -> None:
    """Show Codex as a bar. ``wait`` pulses; ``ratio`` fills from finished groups."""
    label = state.get("progress_lbl")
    bar = state.get("progress_bar")
    if state.get("review_root"):
        view_phase = (state.get("phase"), bool(state.get("busy")), bool(state.get("interpreting")), bool(state.get("applying")))
        if view_phase != state.get("review_phase"):
            state["review_phase"] = view_phase
            from .planner_review import refresh_review
            refresh_review(state)
    try:
        if label is not None:
            tone = theme.SUCCESS if mode == "ready" else theme.CODEX if mode in {"wait", "ratio"} else theme.MUTED
            if any(word in text.casefold() for word in ("failed", "stopped", "need attention", "couldn't")):
                tone = theme.WARNING
            label.configure(text=text, text_color=tone)
    except Exception:
        pass
    if bar is None:
        return
    spinning = bool(state.get("progress_spinning"))
    try:
        if mode == "wait":
            bar.configure(progress_color=theme.CODEX)
            if not spinning:
                bar.start()
                state["progress_spinning"] = True
            return
        if spinning:
            bar.stop()
            state["progress_spinning"] = False
        if mode == "ready":
            bar.configure(progress_color=theme.SUCCESS)
            bar.set(1)
            return
        if mode == "ratio" and total > 0:
            bar.configure(progress_color=theme.CODEX)
            bar.set(max(0.04, min(1.0, done / total)))
            return
        bar.configure(progress_color=theme.CODEX)
        bar.set(0)
    except Exception:
        state["progress_spinning"] = False


def _raise_window(win: Any) -> None:
    """Keep the planner in front of Club. A bare Toplevel often opens behind."""
    host = _host_window()
    try:
        if host is not None:
            win.transient(host)
    except Exception:
        pass
    try:
        win.lift()
        win.focus_force()
        win.attributes("-topmost", True)
        win.after(450, lambda: win.attributes("-topmost", False))
    except Exception:
        pass


def open_planner(
    svc: Any,
    selected_rows: Sequence[Mapping[str, Any]],
    *,
    mode: str = "template",
    prompt: str = "",
    club_name: str = "",
) -> None:
    """Modal Squad Planner for the Club multi-select or a per-player Codex session."""
    ensure_ctk()
    try:
        plan = plan_from_rows(selected_rows)
        squad = svc.store.snapshot().squad
        if hasattr(plan, "bind_source"):
            plan = plan.bind_source(
                save_uid=squad.save_uid, teamid=squad.teamid,
                snapshot_revision=squad.taken.isoformat() if squad.taken else "",
            )
    except (JobValidationError, Exception) as exc:
        _LOG.exception("squad planner failed to build")
        set_status(svc, f"Squad Planner: {exc}")
        return
    if mode == "per_player":
        _open_session_planner(
            svc, plan, prompt=prompt, club_name=club_name, selected_rows=selected_rows
        )
        return
    _open_template_planner(svc, plan)


def _open_template_planner(svc: Any, plan: SquadPlan) -> None:
    """Same-patch template mode (presets / shared Codex / manual)."""
    state: dict[str, Any] = {"plan": plan, "busy": False}

    win = _new_toplevel()
    win.title("Squad Planner")
    win.geometry("720x560")
    win.configure(fg_color=theme.BG)
    _raise_window(win)

    header = panel(win, level=1)
    header.pack(fill="x", padx=theme.SP3, pady=theme.SP3)
    text_label(header, "Squad Planner", size=theme.T_TITLE, display=True).pack(
        anchor="w", padx=theme.SP3, pady=(theme.SP2, 2)
    )
    subtitle = muted_label(header, plan.blast_summary(), size=12)
    subtitle.pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))
    names = ", ".join(f"{m.name} ({m.position or '?'})" for m in plan.members[:12])
    if plan.player_count > 12:
        names += f" … +{plan.player_count - 12} more"
    muted_label(header, names, size=11).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    tools = panel(win, level=1)
    tools.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    text_label(
        tools, "Same numbers on every selected player — pick a preset or type fields", size=12, bold=True
    ).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 2))
    preset_row = ctk.CTkFrame(tools, fg_color="transparent")
    preset_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    for pid, label in _PRESET_IDS:
        button(
            preset_row, label,
            lambda p=pid: _apply_preset(svc, state, p, matrix_host, status_lbl, subtitle),
            kind="ghost", height=28,
        ).pack(side="left", padx=(0, theme.SP1))

    manual = ctk.CTkFrame(tools, fg_color="transparent")
    manual.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    muted_label(manual, "Or manual: field=value, field=value", size=10).pack(anchor="w")
    manual_entry = ctk.CTkEntry(manual, placeholder_text="acceleration=90, sprintspeed=90")
    manual_entry.pack(side="left", fill="x", expand=True, padx=(0, theme.SP2), pady=(2, 0))
    button(
        manual, "Stage",
        lambda: _stage_manual(svc, state, manual_entry.get(), matrix_host, status_lbl, subtitle),
        kind="secondary", height=28, width=70,
    ).pack(side="left", pady=(2, 0))

    status_lbl = muted_label(
        win, "Stage a preset or manual fields, then Apply.", size=11
    )
    status_lbl.pack(anchor="w", padx=theme.SP3)

    matrix_host = ctk.CTkScrollableFrame(win, fg_color="transparent")
    matrix_host.pack(fill="both", expand=True, padx=theme.SP3, pady=theme.SP2)
    _render_matrix(matrix_host, state["plan"])

    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    button(
        foot, "Clear template",
        lambda: _clear_template(state, matrix_host, status_lbl, subtitle),
        kind="ghost", height=32,
    ).pack(side="left")
    button(
        foot, "Apply to selection…",
        lambda: _apply(svc, state, win),
        kind="primary", height=32, width=160,
    ).pack(side="right")


def _with_squad_shirts(svc: Any, plan: SquadPlan) -> SquadPlan:
    """Every shirt the club wears now, from the latest squad export.

    Uses the complete club link list when the core exports it (players who
    are loaned out still wear their number), else the senior list. Called on
    open and again on every Build/Rebuild and shirt fix.
    """
    from ...domain.squad_shirts import with_shirt_map

    try:
        squad = svc.store.snapshot().squad
        players, team_shirts = tuple(squad.players or ()), tuple(getattr(squad, "team_shirts", ()) or ())
    except Exception:
        players, team_shirts = (), ()
    return with_shirt_map(plan, players, team_shirts)


def _open_session_planner(
    svc: Any, plan: SquadPlan, *, prompt: str, club_name: str,
    selected_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Compact request, split review and one persistent status/action area."""
    ensure_ctk()
    from .planner_review import mount_review, render_players
    from ...domain.team_plan import context_from_rows
    plan = _with_squad_shirts(svc, plan)
    controller = PlannerController(plan, request=(prompt or "").strip())
    from ...integrations.squad_cache import SquadProposalCache
    cache = SquadProposalCache(scope=":".join(str(getattr(plan, name, "")) for name in (
        "source_save_uid", "source_teamid", "source_snapshot_revision")))
    club_rows = tuple(getattr(svc.store.snapshot().squad, "players", ()) or selected_rows)
    state: dict[str, Any] = {
        "plan": plan, "busy": False, "mode": "per_player", "session": None,
        "prompt": (prompt or "").strip(), "club_name": club_name or "",
        "include_vars": {}, "pin_vars": {}, "phase": "interpret",
        "controller": controller, "original_plan": plan,
        "proposal_cache": cache,
        "original_selected_plan": plan, "whole_club_rows": club_rows,
        "team_context": context_from_rows(club_rows),
    }
    win = _new_toplevel()
    win.title("Plan these players")
    win.geometry("1020x740")
    win.minsize(800, 600)
    win.configure(fg_color=theme.BG)
    _raise_window(win)
    header = ctk.CTkFrame(win, fg_color="transparent")
    header.pack(fill="x", padx=20, pady=(16, 12))
    text_label(header, "Plan these players", size=22, bold=True).pack(side="left")
    subtitle = muted_label(header, f"{club_name or 'Squad'}  ·  {plan.player_count} selected", size=12)
    subtitle.pack(side="left", padx=16)
    tools = panel(win, level=1)
    tools.pack(fill="x", padx=16, pady=(0, 12))
    request_row = ctk.CTkFrame(tools, fg_color="transparent")
    request_row.pack(fill="x", padx=12, pady=(12, 8))
    prompt_entry = ctk.CTkEntry(request_row, placeholder_text="What should change? e.g. messi jersey 10",
                               height=38, fg_color=theme.BG, border_color=theme.BORDER,
                               text_color=theme.TEXT, placeholder_text_color=theme.MUTED)
    prompt_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    if state["prompt"]:
        prompt_entry.insert(0, state["prompt"])
    saved_path = svc.paths.root / "planner_requests.json"
    requests = {**REQUEST_PRESETS, **load_requests(saved_path)}
    def save_current_request():
        dialog = ctk.CTkInputDialog(text="Name this request so you can reuse it.", title="Save request")
        name = dialog.get_input()
        if name:
            try:
                save_request(saved_path, name, prompt_entry.get())
                requests[name] = prompt_entry.get().strip()
                preset_menu.configure(values=["Choose a request", *requests])
                set_status(svc, f"Saved request: {name}")
            except (ValueError, OSError) as exc:
                set_status(svc, str(exc))
    interpret_btn = button(request_row, "Save request", save_current_request,
                           kind="ghost", height=38, width=110)
    interpret_btn.pack(side="right")
    options = ctk.CTkFrame(tools, fg_color="transparent")
    options.pack(fill="x", padx=12, pady=(0, 10))
    def load_current_request(label):
        if label in requests:
            prompt_entry.delete(0, "end")
            prompt_entry.insert(0, requests[label])
            _request_changed(state)
    preset_menu = ctk.CTkOptionMenu(options, values=["Choose a request", *requests],
                                    command=load_current_request, width=180, height=28)
    preset_menu.set("Choose a request")
    preset_menu.pack(side="left")
    ceiling_var = ctk.BooleanVar(value=False)
    state["ceiling_unlimited"] = ceiling_var
    ceiling_switch = ctk.CTkSwitch(options, text="No PlayStyle+ ceiling", variable=ceiling_var,
                  progress_color=theme.ACCENT, button_color=theme.TEXT, text_color=theme.TEXT,
                  command=lambda: _ceiling_changed(state))
    ceiling_switch.pack(side="right")
    state.update(ceiling_switch=ceiling_switch, interpret_btn=interpret_btn)
    state.update(prompt_entry=prompt_entry, request_presets=preset_menu)
    prompt_entry.bind("<KeyRelease>", lambda _event: _request_changed(state))
    team_host = ctk.CTkFrame(tools, fg_color="transparent")
    def toggle_team():
        if team_host.winfo_manager():
            team_host.pack_forget()
        else:
            team_host.pack(fill="x", padx=12, pady=(0, 10))
    button(header, "Team plan", toggle_team, kind="ghost", height=28, width=86).pack(side="right", padx=6)
    from .planner_team import mount_team_controls
    mount_team_controls(team_host, state, on_dirty=lambda: _mark_review_dirty(state))
    settings_host = ctk.CTkFrame(tools, fg_color="transparent")
    def settings():
        if settings_host.winfo_manager():
            settings_host.pack_forget()
        else:
            if not settings_host.winfo_children():
                from ..widgets.ai_bar import mount_ai_provider_bar
                mount_ai_provider_bar(settings_host, svc)
            settings_host.pack(fill="x", padx=12, pady=(0, 10))
    button(header, "AI settings", settings, kind="ghost", height=28, width=92).pack(side="right")
    footer = ctk.CTkFrame(win, fg_color=theme.CHROME, corner_radius=0)
    footer.pack(side="bottom", fill="x")
    action_row = ctk.CTkFrame(footer, fg_color="transparent")
    action_row.pack(fill="x", padx=16, pady=(12, 12))
    progress = ctk.CTkFrame(action_row, fg_color="transparent")
    progress.pack(side="left", fill="x", expand=True, padx=(0, 16))
    status_lbl = muted_label(progress, "Ready to build a plan", size=12, anchor="w",
                              wraplength=520, justify="left")
    status_lbl.pack(fill="x")
    progress_bar = ctk.CTkProgressBar(progress, height=4, progress_color=theme.CODEX,
                                      fg_color=theme.BORDER)
    progress_bar.set(0)
    progress_bar.pack(fill="x", pady=(6, 0))
    state.update(progress_lbl=status_lbl, progress_bar=progress_bar)
    apply_btn = button(action_row, "Apply changes…", lambda: _apply(svc, state, win),
                        kind="primary", height=38, width=145,
                        disabled_reason="Build a plan to review first.")
    apply_btn.pack(side="right")
    def view_results():
        from .squad_receipts import open_receipt
        if state.get("receipt_id"):
            open_receipt(svc, state["receipt_id"])
    receipt_btn = button(action_row, "View results", view_results, kind="ghost", height=38, width=95)
    state["receipt_btn"] = receipt_btn
    # Packed next to Apply only when Apply could not finish (see _show_recovery).
    state["recovery_btn"] = button(action_row, "Try again", lambda: None, kind="secondary",
                                   height=38, width=170)
    propose_btn = button(action_row, "Build plan", lambda: _start_session_propose(
        svc, state, preview_host, matrix_host, status_lbl, subtitle, propose_btn),
        kind="secondary", height=38, width=120)
    propose_btn.pack(side="right", padx=(0, 8))
    # Packed next to Apply only while some players are blocked (see _sync_session_apply).
    state["apply_rest_btn"] = button(action_row, "Apply the rest…", lambda: _apply_rest(svc, state, win),
                                     kind="secondary", height=38, width=130)
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
    chip_host, preview_host, matrix_host = mount_review(body, state, lambda: _mark_review_dirty(state))
    state.update(apply_btn=apply_btn, propose_btn=propose_btn)
    def repair_player(pid):
        # "Fix this" for a rejected AI proposal: one player, one request.
        if not _start_targeted_repair(svc, state, int(pid), status_lbl, subtitle, matrix_host):
            state["retry_playerids"] = {int(pid)}
            _start_session_propose(svc, state, preview_host, matrix_host, status_lbl, subtitle, propose_btn)
    state["repair_player"] = repair_player
    state["fix_shirt"] = lambda pid: _fix_shirt_issue(svc, state, int(pid), status_lbl, subtitle, matrix_host)
    state["swap_shirt"] = lambda pid: _fix_shirt_issue(svc, state, int(pid), status_lbl, subtitle, matrix_host,
                                                      swap=True)
    win._planner_state = state
    def close():
        controller.close(getattr(svc, "executor", None))
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close)
    # A real selection is visible immediately, before any provider request.
    state["session"] = _draft_session(plan, state["prompt"], club_name, state.get("team_context"))
    state["draft_session"] = state["session"]
    ceiling_var.set(state["session"].recipe.unlimited_playstyles)
    state["phase"] = "preview"
    render_players(state)
    _sync_session_apply(state)
    _plan_progress(state, "Describe your changes above, then Build plan.")


def _request_changed(state: dict[str, Any]) -> None:
    controller = state.get("controller")
    entry = state.get("prompt_entry")
    if entry is not None and (controller is None or controller.update_request(entry.get())):
        _mark_review_dirty(state)


def _ceiling_changed(state: dict[str, Any]) -> None:
    state["ceiling_edited"] = True
    _mark_review_dirty(state)


def _mark_review_dirty(state: dict[str, Any]) -> None:
    if state.get("busy") or state.get("interpreting") or state.get("applying"):
        return
    state["edits_dirty"] = True
    if state.get("review_summary") is not None:
        state["review_summary"].configure(text="Preview needs a rebuild")
        from .planner_review import refresh_request_checks

        refresh_request_checks(state)
    _plan_progress(state, "Selection or targets changed. Rebuild to refresh the preview.")
    _sync_session_apply(state)


def session_interpret_worker(
    token: Any,
    *,
    roster: Sequence[Mapping[str, Any]],
    prompt: str,
    club_name: str,
    on_success: Any,
    on_error: Any,
) -> None:
    try:
        if getattr(token, "cancelled", False):
            return
        if not (prompt or "").strip():
            raise ValueError("Describe what you want to do with this squad.")
        from ...integrations.grok import interpret_squad_edit

        session = interpret_squad_edit(
            roster=roster, request=prompt, club_name=club_name
        )
        if getattr(token, "cancelled", False):
            return
        on_success(session)
    except Exception as exc:  # noqa: BLE001
        if not getattr(token, "cancelled", False):
            on_error(exc)


def session_propose_worker(
    token: Any,
    *,
    plan: SquadPlan,
    session: SquadSession,
    catalog: Any = None,
    on_progress: Any = None,
    on_success: Any = None,
    on_error: Any = None,
    cache: Any = None,
) -> None:
    try:
        if getattr(token, "cancelled", False):
            return
        from ...integrations.grok import library_hint_for, propose_squad_players

        if relative_only(session.recipe) or jersey_only(session.recipe):
            staged = stage_per_player_plan(plan, session, {})
            if not getattr(token, "cancelled", False) and on_success:
                on_success(staged)
            return
        hinted: list = []
        for row in propose_payload(plan, session):
            hint = row.get("library_hint") or ""
            if not hint and catalog is not None:
                hint = library_hint_for(
                    str(row.get("name") or ""),
                    str(row.get("position") or ""),
                    int(row.get("target_ovr") or 80),
                    catalog,
                    **({"cache": cache} if cache is not None else {}),
                )
            row = dict(row)
            row["library_hint"] = hint
            hinted.append(row)
        problems: list[str] = []
        patches, reasons, hints = propose_squad_players(
            players=hinted,
            selection_revision=plan.selection_revision,
            request=session.recipe.prompt,
            recipe=session.recipe,
            cancel_check=lambda: bool(getattr(token, "cancelled", False)),
            problems=problems,
            on_chunk=lambda cur, total: on_progress and on_progress(
                (
                    "Designing player changes…"
                    if cur <= 0
                    else f"Designing player changes · {round(100 * cur / total)}%"
                ),
                cur,
                total,
            ),
            **({"cache": cache} if cache is not None else {}),
            **_shirt_kwargs(plan, session),
        )
        if getattr(token, "cancelled", False):
            return
        hinted_session = session
        if problems:
            hinted_session = replace(
                hinted_session,
                warnings=hinted_session.warnings + tuple(problems),
            )
        if hints:
            hinted_session = replace(
                hinted_session,
                targets=tuple(
                    replace(item, library_hint=hints.get(item.playerid) or item.library_hint)
                    for item in hinted_session.targets
                ),
            )
        staged = stage_per_player_plan(
            plan, hinted_session, patches, reasons=reasons, library_hints=hints
        )
        if on_success:
            on_success(staged)
    except Exception as exc:  # noqa: BLE001
        if not getattr(token, "cancelled", False) and on_error:
            on_error(exc)


def _shirt_kwargs(plan: SquadPlan, session: SquadSession) -> dict[str, Any]:
    """The club shirt map for the AI, only when it may propose shirts."""
    from ...domain.squad_session import ai_shirts_wanted, shirt_map_for_ai

    if not ai_shirts_wanted(session.recipe):
        return {}
    shirt_map = shirt_map_for_ai(plan)
    return {"shirt_map": shirt_map} if shirt_map else {}


def player_issue_text(plan: SquadPlan, playerid: int) -> str:
    """The rejection text for one player, without the name prefix."""
    member = next((m for m in plan.members if m.playerid == int(playerid)), None)
    if member is None:
        return ""
    prefix = member.name + ":"
    lines = [issue[len(prefix):].replace("Rebuild this plan.", "").strip()
             for issue in plan.blocking_issues if issue.startswith(prefix)]
    return " ".join(line for line in lines if line) or "the proposal was missing or invalid"


def targeted_repair_worker(
    token: Any,
    *,
    plan: SquadPlan,
    session: SquadSession,
    playerid: int,
    reason: str,
    on_success: Any = None,
    on_error: Any = None,
    cache: Any = None,
) -> None:
    """Re-ask the AI for ONE player with the rejection fed back.

    Only that player's row is sent, the cache is bypassed for him, and his
    staged result is spliced into the reviewed plan; every other player,
    shirt decision and note stays exactly as it was.
    """
    try:
        if getattr(token, "cancelled", False):
            return
        from ...domain.squad_plan import splice_player
        from ...integrations.grok import propose_squad_players

        pid = int(playerid)
        rows = [dict(row) for row in propose_payload(plan, session) if int(row["playerid"]) == pid]
        if not rows:
            raise ValueError("This player is not part of the current plan.")
        rows[0]["library_hint"] = rows[0].get("library_hint") or plan.library_hints.get(pid, "")
        patches, reasons, hints = propose_squad_players(
            players=rows,
            selection_revision=plan.selection_revision,
            request=session.recipe.prompt,
            recipe=session.recipe,
            cancel_check=lambda: bool(getattr(token, "cancelled", False)),
            repair_notes={pid: reason},
            **({"cache": cache} if cache is not None else {}),
            **_shirt_kwargs(plan, session),
        )
        if getattr(token, "cancelled", False):
            return
        single = stage_per_player_plan(plan.keeping([pid]), session, {pid: patches.get(pid, {})},
                                       reasons=reasons, library_hints=hints)
        if on_success:
            on_success(splice_player(plan, single, pid))
    except Exception as exc:  # noqa: BLE001
        if not getattr(token, "cancelled", False) and on_error:
            on_error(exc)


def _codex_progress(svc: Any, state: dict[str, Any], status_lbl: Any):
    """Marshal a Codex group update onto the plan window."""

    controller = state.get("controller")
    generation = controller.generation if controller is not None else None
    def report(msg: str, done: int = 0, total: int = 0) -> None:
        def ui(message: str = msg, finished: int = done, groups: int = total) -> None:
            if controller is not None and not controller.accepts(generation):
                return
            try:
                status_lbl.configure(text=message)
            except Exception:
                pass
            _plan_progress(
                state,
                message,
                mode="wait" if finished <= 0 else "ratio",
                done=finished,
                total=groups,
            )

        _ui(svc, ui)

    return report


def _ui(svc: Any, fn: Any) -> None:
    if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "on_ui_thread"):
        svc.executor.on_ui_thread(fn)
    else:
        fn()


def _submit(svc: Any, name: str, work: Any, *, state: dict | None = None) -> None:
    controller = (state or {}).get("controller")
    if controller is not None:
        name = controller.task_key(name)
    if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "submit"):
        svc.executor.submit(name, work)
    else:
        work(type("T", (), {"cancelled": False})())


def _draft_session(plan: SquadPlan, prompt: str, club_name: str, team_context: Any = None) -> SquadSession:
    """Python ranking so the checklist appears before Codex returns."""
    recipe = parse_recipe(prompt, club_name)
    if team_context is not None:
        recipe = replace(recipe, starter_ids=tuple(team_context.starters), formation=team_context.formation,
                         planning_roles=tuple(sorted(team_context.roles.items())))
    roster = roster_from_rows(
        [
            {
                "playerid": member.playerid,
                "name": member.name,
                "pos": member.position,
                "ovr": member.ovr,
                "_raw": {
                    "playerid": member.playerid,
                    "name": member.name,
                    "position": member.position,
                    "overallrating": member.ovr,
                    "potential": member.base.get("potential"),
                },
            }
            for member in plan.members
        ]
    )
    session = build_session(roster, recipe)
    if team_context is not None:
        from ...domain.team_plan import apply_team_context
        session = apply_team_context(session, team_context)
    return session


def _snapshot_preview_edits(state: dict[str, Any]) -> tuple[set[int], dict[int, str]]:
    excluded: set[int] = set()
    pins: dict[int, str] = {}
    for pid, flag in (state.get("include_vars") or {}).items():
        try:
            draft = state.get("draft_session") or state.get("session")
            if not bool(flag.get()) and (draft is None or int(pid) in draft.playerids):
                excluded.add(int(pid))
        except (TypeError, ValueError, AttributeError):
            continue
    for pid, entry in (state.get("pin_vars") or {}).items():
        try:
            value = str(entry.get()).strip()
            target = state["session"].target_for(int(pid)) if state.get("session") else None
            if target is None or value != str(target.target_ovr):
                pins[int(pid)] = value
        except (TypeError, ValueError, AttributeError):
            continue
    return excluded, pins


def _sync_session_apply(state: dict[str, Any]) -> None:
    btn = state.get("apply_btn")
    if btn is None:
        return
    plan = state.get("plan")
    ready = (
        state.get("phase") == "review"
        and not state.get("applying")
        and not state.get("edits_dirty")
        and plan is not None
        and not getattr(plan, "blocking_issues", ())
        and bool(
            getattr(plan, "template", None)
            or getattr(plan, "overrides", None)
            or getattr(plan, "jersey_numbers", None)
        )
    )
    set_disabled(
        btn,
        "" if ready else (
            "Rebuild after changing the selection or targets." if state.get("edits_dirty") else
            "Repair the affected players to complete this plan."
            if getattr(plan, "blocking_issues", ()) else
            "Codex is still writing each build."
        ),
    )
    _sync_apply_rest(state, plan)
    if state.get("review_root"):
        shirts = len(getattr(plan, "shirt_issues", ()) or ())
        btn.configure(text="Applied" if state.get("phase") == "applied" else
                      f"Apply, skip {shirts} shirt{'s' if shirts != 1 else ''}…" if ready and shirts else
                      "Apply changes…")
        active = bool(state.get("busy") or state.get("interpreting") or state.get("applying"))
        control_state = "disabled" if active else "normal"
        repair_needed = bool(getattr(plan, "blocking_issues", ()) and not state.get("edits_dirty"))
        state["propose_btn"].configure(text="Repair affected players" if repair_needed else
                                      "Rebuild" if state.get("phase") in {"review", "applied"} else "Build plan", state=control_state)
        state["interpret_btn"].configure(state=control_state)
        state["ceiling_switch"].configure(state=control_state)
        for control in (state.get("prompt_entry"), state.get("request_presets"), *state.get("team_controls", ())):
            if control is not None:
                control.configure(state=control_state)


def apply_rest_available(state: dict[str, Any]) -> bool:
    """Some players are blocked, but the rest of the reviewed plan is valid."""
    from ...domain.squad_plan import plan_without_blocked

    plan = state.get("plan")
    if (plan is None or state.get("phase") != "review" or state.get("applying") or state.get("busy")
            or state.get("edits_dirty") or not getattr(plan, "blocking_issues", ())):
        return False
    try:
        plan_without_blocked(plan)
    except JobValidationError:
        return False
    return True


def _sync_apply_rest(state: dict[str, Any], plan: Any) -> None:
    btn = state.get("apply_rest_btn")
    if btn is None:
        return
    try:
        if apply_rest_available(state):
            if not btn.winfo_manager():
                btn.pack(side="right", padx=(0, 8), after=state["apply_btn"])
        elif btn.winfo_manager():
            btn.pack_forget()
    except Exception:
        pass


def _start_interpret(
    svc: Any,
    state: dict[str, Any],
    prompt: str,
    chip_host: Any,
    preview_host: Any,
    matrix_host: Any,
    status_lbl: Any,
    subtitle: Any,
    interpret_btn: Any,
    propose_btn: Any = None,
) -> None:
    request = (prompt or "").strip()
    if not request:
        try:
            status_lbl.configure(text="Type each overall in the box.")
        except Exception:
            pass
        return
    from ...integrations.ai_provider import block_reason

    blocked = block_reason()
    if blocked:
        try:
            status_lbl.configure(text=blocked)
        except Exception:
            pass
        set_status(svc, blocked)
        return
    if state.get("interpreting") or state.get("busy"):
        set_status(svc, "Codex is already working…")
        return
    state["interpreting"] = True
    controller = state.get("controller")
    generation = controller.begin() if controller is not None else None
    state["prompt"] = request
    state["phase"] = "interpret"
    _plan_progress(
        state,
        "Codex is reading the names in your request.",
        mode="wait",
    )
    excluded, pins = _snapshot_preview_edits(state)
    state["kept_excluded"] = excluded
    state["kept_pins"] = pins
    try:
        status_lbl.configure(text="Checking the names you typed…")
        interpret_btn.configure(state="disabled")
        if propose_btn is not None:
            propose_btn.configure(state="disabled")
    except Exception:
        pass
    _sync_session_apply(state)
    set_status(svc, "Checking the names you typed…")
    roster = [
        {
            "playerid": m.playerid,
            "name": m.name,
            "pos": m.position,
            "ovr": m.ovr,
            "pot": m.base.get("potential"),
            "_raw": {
                "playerid": m.playerid,
                "name": m.name,
                "position": m.position,
                "overallrating": m.ovr,
                "potential": m.base.get("potential"),
            },
        }
        for m in state["plan"].members
    ]
    try:
        draft = _draft_session(
            state["plan"], request, str(state.get("club_name") or "")
        )
        state["session"] = draft
        _render_session_preview(state, chip_host, preview_host, status_lbl, subtitle)
        if _named_pins_ready(draft):
            state["interpreting"] = False
            state["phase"] = "preview"
            _plan_progress(state, "Overalls are pinned. Rebuild when the list looks right.", mode="idle")
            try:
                interpret_btn.configure(state="normal")
                propose_btn.configure(state="normal") if propose_btn is not None else None
                status_lbl.configure(
                    text=_session_status("preview", draft) + " Then Rebuild."
                )
            except Exception:
                pass
            _sync_session_apply(state)
            set_status(svc, "Overalls are pinned. Rebuild when the list looks right.")
            return
    except Exception:
        _LOG.exception("python squad ranking failed")

    def on_success(session: SquadSession) -> None:
        def ui() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            state["interpreting"] = False
            excluded, pins = _snapshot_preview_edits(state)
            state["kept_excluded"] = excluded
            state["kept_pins"] = pins
            state["session"] = session
            state["phase"] = "preview"
            try:
                interpret_btn.configure(state="normal")
            except Exception:
                pass
            _render_session_preview(state, chip_host, preview_host, status_lbl, subtitle)
            try:
                if not state.get("review_root"):
                    for child in list(matrix_host.winfo_children()):
                        child.destroy()
            except Exception:
                pass
            _sync_session_apply(state)
            set_status(svc, _session_status("reading", session))
            if propose_btn is not None:
                _start_session_propose(
                    svc, state, preview_host, matrix_host, status_lbl, subtitle, propose_btn
                )

        _ui(svc, ui)

    def on_error(exc: BaseException) -> None:
        def err() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            state["interpreting"] = False
            _LOG.error("squad interpret failed: %s", exc)
            _plan_progress(state, f"Codex stopped: {exc}", mode="idle")
            try:
                interpret_btn.configure(state="normal")
                if propose_btn is not None:
                    propose_btn.configure(state="normal")
                status_lbl.configure(text=f"Interpret failed: {exc}")
            except Exception:
                pass
            _sync_session_apply(state)
            set_status(svc, f"Squad session interpret failed: {exc}")

        _ui(svc, err)

    def work(token: Any) -> None:
        session_interpret_worker(
            token,
            roster=roster,
            prompt=request,
            club_name=str(state.get("club_name") or ""),
            on_success=on_success,
            on_error=on_error,
        )

    try:
        _submit(svc, "squad.grok.interpret", work, state=state)
    except Exception as exc:  # noqa: BLE001
        state["interpreting"] = False
        _LOG.exception("could not start squad interpret")
        try:
            interpret_btn.configure(state="normal")
            status_lbl.configure(text=f"Could not start Codex: {exc}")
        except Exception:
            pass


def _named_pins_ready(session: SquadSession) -> bool:
    """True when every typed name already has its own pinned overall."""
    if not session.recipe.named_overalls or not session.targets:
        return False
    if any(str(note).startswith("Couldn't match") for note in session.warnings):
        return False
    return all(item.pinned for item in session.targets)


def _session_status(phase: str, session: SquadSession | None) -> str:
    """Plain status for the plan window. Names and overalls, not internal steps."""
    if phase == "review":
        return "Review each player, then Apply changes."
    if phase in {"reading", "proposing"}:
        return "Reading current player values…" if phase == "reading" else "Designing player changes…"
    return "Adjust the selection or overall targets, then Build plan."


def _render_session_preview(
    state: dict[str, Any], chip_host: Any, preview_host: Any,
    status_lbl: Any, subtitle: Any,
) -> None:
    del chip_host, preview_host, subtitle
    from .planner_review import render_players
    session = state.get("session")
    if session is None:
        return
    ceiling = state.get("ceiling_unlimited")
    if ceiling is not None:
        ceiling.set(bool(session.recipe.unlimited_playstyles))
    render_players(state)
    status_lbl.configure(text=_session_status(str(state.get("phase") or "preview"), session))


def _collect_session_edits(state: dict[str, Any]) -> SquadSession:
    session: SquadSession = state["session"]
    excluded: list[int] = []
    pins: dict[int, int] = {}
    for item in session.targets:
        flag = state.get("include_vars", {}).get(item.playerid)
        if flag is not None and not bool(flag.get()):
            excluded.append(item.playerid)
            continue
        entry = state.get("pin_vars", {}).get(item.playerid)
        if entry is None:
            continue
        try:
            typed = int(str(entry.get()).strip())
        except (TypeError, ValueError, AttributeError):
            raise JobValidationError(f"{item.name}: enter an overall between 1 and 99.")
        if not 1 <= typed <= 99:
            raise JobValidationError(f"{item.name}: enter an overall between 1 and 99.")
        if typed != item.target_ovr:
            pins[item.playerid] = typed
    return refine_session(session, excluded_ids=excluded, pins=pins)


def _start_session_propose(
    svc: Any,
    state: dict[str, Any],
    preview_host: Any,
    matrix_host: Any,
    status_lbl: Any,
    subtitle: Any,
    propose_btn: Any,
) -> None:
    if state.get("busy") or state.get("interpreting") or state.get("applying"):
        set_status(svc, "Codex is already working…")
        return
    controller = state.get("controller")
    generation = controller.begin() if controller is not None else None
    original = controller.original_plan if controller is not None else state.get("original_plan", state["plan"])
    previous = state["plan"]
    retry_ids = state.pop("retry_playerids", None)
    if retry_ids is None:
        # Any problem counts: rejected fields, shirt conflicts and staging
        # notes. Otherwise Rebuild returns the identical failing design.
        from ...domain.squad_plan import issue_playerids
        retry_ids = issue_playerids(previous)
    cache = state.get("proposal_cache")
    if retry_ids and cache is not None:
        cache.invalidate_players(retry_ids)
    if svc is not None:
        # Re-read the club's shirts from the latest squad snapshot every build.
        original = _with_squad_shirts(svc, original)
        if controller is not None:
            controller.original_plan = original
        state["original_plan"] = original
    excluded, pins = _snapshot_preview_edits(state)
    request = state["prompt_entry"].get().strip() if state.get("prompt_entry") is not None else state.get("prompt", "")
    if not request:
        set_status(svc, "Describe your changes above, then Build plan.")
        return
    try:
        from .planner_team import collect_team_context
        context = collect_team_context(state)
        state["session"] = _draft_session(original, request, str(state.get("club_name") or ""), context)
        state["draft_session"] = state["session"]
        state["prompt"] = request
        state["kept_excluded"], state["kept_pins"] = excluded, pins
        if controller is not None:
            controller.request = request
            if previous.overrides or previous.jersey_numbers:
                controller.last_proposal = previous
    except (ValueError, JobValidationError) as exc:
        set_status(svc, f"Could not build this request: {exc}")
        return
    session = state.get("session")
    ceiling = state.get("ceiling_unlimited")
    if session is not None and ceiling is not None:
        try:
            if not state.get("ceiling_edited"):
                ceiling.set(session.recipe.unlimited_playstyles)
            session = replace(
                session,
                recipe=replace(
                    session.recipe,
                    unlimited_playstyles=bool(ceiling.get()),
                ),
            )
            state["session"] = session
        except Exception:
            pass
    if session is None or state.get("interpreting"):
        set_status(svc, "Type each overall, then Update.")
        return
    if state.get("busy") or state.get("applying"):
        set_status(svc, "Codex is already working…")
        return
    from ...integrations.ai_provider import block_reason

    blocked = block_reason()
    if blocked:
        try:
            status_lbl.configure(text=blocked)
        except Exception:
            pass
        set_status(svc, blocked)
        return
    try:
        if state.get("review_root"):
            from .planner_review import render_players
            render_players(state)
        refined = _collect_session_edits(state)
        if context is not None:
            from ...domain.team_plan import apply_team_context
            refined = apply_team_context(refined, context)
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not apply pins: {exc}")
        return
    if not refined.targets:
        set_status(svc, "Nobody left to edit. Include at least one player.")
        return
    state["session"] = refined
    try:
        plan = original.keeping(refined.playerids)
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))
        return
    state["plan"] = plan
    missing = [m for m in plan.members if needs_live_base(m, refined.recipe)]
    state["busy"] = True
    state["phase"] = "reading" if missing else "proposing"
    state["edits_dirty"] = False
    _plan_progress(state, "Reading current player values…" if missing else "Designing player changes…", mode="wait")
    _sync_session_apply(state)
    try:
        propose_btn.configure(state="disabled")
        status_lbl.configure(text=_session_status(state["phase"], refined))
    except Exception:
        pass

    def after_bases(plan_ready: SquadPlan) -> None:
        if controller is not None and not controller.accepts(generation):
            return
        if controller is not None:
            controller.update_bases({m.playerid: m.base for m in plan_ready.members})
            state["original_plan"] = controller.original_plan
        state["plan"] = plan_ready
        state["phase"] = "proposing"
        try:
            status_lbl.configure(text=_session_status("proposing", state.get("session")))
        except Exception:
            pass
        set_status(svc, "Designing requested player changes…")

        def on_success(staged: SquadPlan) -> None:
            def ui() -> None:
                if controller is not None and not controller.accepts(generation):
                    return
                if controller is not None:
                    controller.last_proposal = staged
                state["busy"] = False
                state["phase"] = "review"
                _plan_progress(state, "Review each player, then Apply changes.", mode="ready")
                try:
                    propose_btn.configure(state="normal")
                except Exception:
                    pass
                _set_plan(
                    state, staged, matrix_host, status_lbl, subtitle,
                    f"Review {staged.blast_summary()}",
                )
                _sync_session_apply(state)
                set_status(svc, "Builds are ready. Look through the changes, then Apply.")

            _ui(svc, ui)

        def on_error(exc: BaseException) -> None:
            def err() -> None:
                if controller is not None and not controller.accepts(generation):
                    return
                state["busy"] = False
                if controller is not None and controller.last_proposal is not None:
                    state["plan"] = controller.last_proposal
                    state["phase"] = "review"
                    state["edits_dirty"] = True
                _plan_progress(state, f"Codex stopped: {exc}", mode="idle")
                try:
                    propose_btn.configure(state="normal")
                    status_lbl.configure(text=f"Propose failed: {exc}")
                except Exception:
                    pass
                _sync_session_apply(state)
                set_status(svc, f"Squad session propose failed: {exc}")

            _ui(svc, err)

        def work(token: Any) -> None:
            session_propose_worker(
                token,
                plan=plan_ready,
                session=state["session"],
                catalog=getattr(svc, "catalog", None),
                on_progress=_codex_progress(svc, state, status_lbl),
                on_success=on_success,
                on_error=on_error,
                cache=state.get("proposal_cache"),
            )

        try:
            _submit(svc, "squad.grok.session", work, state=state)
        except Exception as exc:  # noqa: BLE001
            state["busy"] = False
            try:
                propose_btn.configure(state="normal")
                status_lbl.configure(text=f"Could not start Codex: {exc}")
            except Exception:
                pass

    if not missing:
        try:
            status_lbl.configure(text=_session_status("proposing", state.get("session")))
        except Exception:
            pass
        after_bases(plan)
        return

    try:
        status_lbl.configure(text=f"Reading live stats 0/{len(missing)}")
        _plan_progress(
            state,
            f"Reading live stats 0/{len(missing)}",
            mode="ratio",
            done=0,
            total=len(missing),
        )
    except Exception:
        pass
    set_status(svc, "Reading live stats for the selected players…")

    def on_rows(rows: Mapping[int, Mapping[str, Any]]) -> None:
        def ui() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            filled = state["plan"].with_member_bases(rows)
            skipped = [m.name for m in filled.members if needs_live_base(m, refined.recipe)]
            if skipped:
                state["busy"] = False
                state["phase"] = "preview"
                message = (
                    f"Missing live values for {', '.join(skipped[:4])}"
                    + (f" and {len(skipped) - 4} more" if len(skipped) > 4 else "")
                    + ". Keep Career Mode open and rebuild; all selected players must be read."
                )
                _plan_progress(state, message, mode="idle")
                _sync_session_apply(state)
                set_status(svc, message)
                return
            try:
                status_lbl.configure(text=f"Reading live stats {len(rows)}/{len(missing)}")
                _plan_progress(
                    state,
                    f"Reading live stats {len(rows)}/{len(missing)}",
                    mode="ratio",
                    done=len(rows),
                    total=max(1, len(missing)),
                )
            except Exception:
                pass
            after_bases(filled)

        _ui(svc, ui)

    def on_error(exc: BaseException) -> None:
        def err() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            state["busy"] = False
            state["phase"] = "preview"
            _plan_progress(state, f"Live read failed: {exc}", mode="idle")
            _sync_session_apply(state)
            try:
                propose_btn.configure(state="normal")
                status_lbl.configure(text=f"Live read failed: {exc}")
            except Exception:
                pass
            set_status(svc, f"Squad session read failed: {exc}")

        _ui(svc, err)

    try:
        from ...app.commands.squad_session import snapshot_players

        snapshot_players(
            svc,
            [m.playerid for m in missing],
            on_rows=on_rows,
            on_error=on_error,
        )
    except Exception as exc:  # noqa: BLE001
        on_error(exc)


def grok_worker(
    token: Any,
    *,
    plan: SquadPlan,
    rev: str,
    prompt: str,
    on_success: Any,
    on_error: Any,
) -> None:
    """Executor-compatible worker: ``fn(token)``. Pure enough to unit-test."""
    try:
        if getattr(token, "cancelled", False):
            return
        if not (prompt or "").strip():
            raise ValueError("Describe the squad build you want (empty prompt).")
        from ...integrations.grok import propose_squad_template

        prop = propose_squad_template(
            selection=selection_summary_for_ai(plan),
            selection_revision=rev,
            request=prompt,
        )
        if getattr(token, "cancelled", False):
            return
        on_success(prop)
    except Exception as exc:  # noqa: BLE001
        if not getattr(token, "cancelled", False):
            on_error(exc)


def _render_matrix(host: Any, plan: SquadPlan) -> None:
    for child in list(host.winfo_children()):
        child.destroy()
    if not plan.template and not plan.overrides and not plan.jersey_numbers:
        muted_label(host, "No template staged yet.", size=12).pack(anchor="w")
        return
    for issue in plan.blocking_issues:
        muted_label(host, issue, size=11, color=theme.WARNING,
                    wraplength=520, justify="left").pack(anchor="w")
    for shirt_issue in plan.shirt_issues:
        muted_label(host, f"{_name_of(plan, shirt_issue.playerid)}: {shirt_issue.message}", size=11,
                    color=theme.WARNING, wraplength=520, justify="left").pack(anchor="w")
    if plan.chips:
        chip_row = ctk.CTkFrame(host, fg_color="transparent")
        chip_row.pack(fill="x", pady=(0, theme.SP2))
        for chip in plan.chips[:10]:
            pill(chip_row, chip, tone="info").pack(side="left", padx=(0, theme.SP1))
    if plan.overrides or plan.jersey_numbers:
        muted_label(host, plan.blast_summary(), size=11).pack(anchor="w", pady=(0, theme.SP2))
        for member in plan.members:
            reason = (plan.reasons or {}).get(member.playerid) or ""
            hint = (plan.library_hints or {}).get(member.playerid) or ""
            patch = plan.patch_for(member.playerid)
            before = member.ovr
            after = patch.get("overallrating", before)
            card = ctk.CTkFrame(
                host, fg_color=theme.CARD, corner_radius=theme.R_SM,
                border_width=1, border_color=theme.BORDER,
            )
            card.pack(fill="x", pady=2)
            before_fill, before_fg = theme.ovr_colors(before if isinstance(before, int) else None)
            after_value = after if isinstance(after, int) else before
            after_fill, after_fg = theme.ovr_colors(after_value if isinstance(after_value, int) else None)
            chips = (
                (before if before is not None else "—", before_fill, before_fg),
                (after_value if after_value is not None else "—", after_fill, after_fg),
            )
            for index, (value, fill, fg) in enumerate(chips):
                chip = ctk.CTkFrame(card, width=36, height=28, fg_color=fill, corner_radius=theme.R_SM)
                chip.pack(side="left", padx=(theme.SP2, 0), pady=theme.SP2)
                chip.pack_propagate(False)
                text_label(chip, str(value), size=12, bold=True, color=fg).pack(expand=True)
                if index == 0:
                    muted_label(card, "→", size=12, color=theme.CODEX).pack(side="left", padx=theme.SP1)
            words = ctk.CTkFrame(card, fg_color="transparent")
            words.pack(side="left", fill="x", expand=True, padx=theme.SP2, pady=theme.SP1)
            text_label(words, member.name, size=13, bold=True).pack(anchor="w")
            bits = []
            if "icontrait1" in patch or "icontrait2" in patch:
                from ...domain.playstyles import plus_names
                names = plus_names(patch)
                muted_label(words, f"{len(names)} PlayStyle+ · " + ", ".join(names),
                            size=10, color=theme.CODEX,
                            wraplength=420, justify="left").pack(anchor="w")
            new_shirt = plan.jersey_numbers.get(member.playerid)
            if new_shirt:
                old_shirt = plan.shirt_book.get(member.playerid) or member.base.get("jerseynumber") or "—"
                bits.append(f"#{old_shirt} → #{new_shirt}")
            if reason:
                bits.append(reason)
            if bits:
                muted_label(words, "  ·  ".join(bits), size=10).pack(anchor="w")
            if hint:
                muted_label(words, hint, size=10, color=theme.CODEX).pack(anchor="w")
        member_ids = {member.playerid for member in plan.members}
        for pid, number in plan.jersey_numbers.items():
            if pid in member_ids:
                continue
            old_shirt = plan.shirt_book.get(pid) or "—"
            name = plan.shirt_names.get(pid) or str(pid)
            moved = ctk.CTkFrame(
                host, fg_color=theme.BG, corner_radius=theme.R_SM,
                border_width=1, border_color=theme.BORDER,
            )
            moved.pack(fill="x", pady=2)
            text_label(moved, name, size=12, bold=True).pack(
                side="left", padx=theme.SP3, pady=theme.SP2,
            )
            muted_label(
                moved,
                f"#{old_shirt} → #{number}  ·  moved off the requested shirt",
                size=11,
            ).pack(side="left", padx=(0, theme.SP3))
    else:
        muted_label(
            host,
            f"Template ({plan.source}): "
            + ", ".join(f"{k}={v}" for k, v in sorted(plan.template.items())[:12]),
            size=11,
        ).pack(anchor="w", pady=(0, theme.SP2))
    if plan.summary:
        muted_label(host, plan.summary, size=11).pack(anchor="w")
    for w in plan.warnings:
        muted_label(host, f"⚠ {w}", size=10, color=theme.WARNING).pack(anchor="w")

    text_label(host, "What will change", size=11, bold=True, color=theme.MUTED).pack(
        anchor="w", pady=(theme.SP2, theme.SP1)
    )
    rows = [r for r in plan.matrix_rows() if r.get("changed")]
    if not rows:
        rows = list(plan.matrix_rows())[:40]
    display = []
    for index, row in enumerate(rows[:80]):
        delta = row.get("delta")
        display.append({
            "name": row.get("name") or "—",
            "field": row.get("field") or "—",
            "before": row.get("before"),
            "after": row.get("after"),
            "delta": f"{delta:+d}" if isinstance(delta, int) else "—",
            "_key": f"{row.get('name')}-{row.get('field')}-{index}",
        })
    model = TableModel(
        columns=(
            Column("name", "PLAYER", width=120, stretch=True),
            Column("field", "FIELD", width=110),
            Column("before", "BEFORE", width=70),
            Column("after", "AFTER", width=70),
            Column("delta", "Δ", width=50, numeric=True),
        ),
        rows=display,
        key_field="_key",
    )
    grid = DataGrid(host, model, show_checkboxes=False, visible_rows=10)
    grid.pack(fill="both", expand=True, pady=(0, theme.SP1))
    if len(plan.matrix_rows()) > 80:
        muted_label(host, f"… {len(plan.matrix_rows()) - 80} more cells", size=10).pack(
            anchor="w"
        )


def _set_plan(
    state: dict[str, Any],
    plan: SquadPlan,
    matrix_host: Any,
    status_lbl: Any,
    subtitle: Any,
    msg: str,
) -> None:
    state["plan"] = plan
    try:
        if not state.get("review_root"):
            subtitle.configure(text=plan.blast_summary())
        status_lbl.configure(text=msg)
    except Exception:
        pass
    if state.get("review_root"):
        from .planner_review import render_players
        render_players(state)
        text, mode = _review_progress(plan)
        _plan_progress(state, text, mode=mode)
    else:
        _render_matrix(matrix_host, plan)


def _review_progress(plan: SquadPlan) -> tuple[str, str]:
    """Plain status for a built plan, naming what still needs a choice."""
    shirts = len(plan.shirt_issues)
    shirt_text = f"{shirts} shirt number{'s' if shirts != 1 else ''}"
    if plan.blocking_issues:
        text = "Some players need a fix. Select one and press Fix this, or use Apply the rest."
        return (text + (f" {shirt_text} also need a choice." if shirts else "")), "idle"
    if shirts:
        return (f"{shirt_text} need a choice. Apply skips only those, or select the player and Fix this."), "ready"
    return "Review each player, then Apply changes.", "ready"


def _start_targeted_repair(svc: Any, state: dict[str, Any], pid: int,
                           status_lbl: Any, subtitle: Any, matrix_host: Any) -> bool:
    """Fix one rejected player with a single AI request. False if not possible."""
    plan, session = state.get("plan"), state.get("session")
    if (plan is None or session is None or state.get("phase") != "review" or state.get("edits_dirty")
            or state.get("busy") or state.get("applying") or pid not in plan.playerids):
        return False
    from ...integrations.ai_provider import block_reason

    blocked = block_reason()
    if blocked:
        set_status(svc, blocked)
        return True
    controller = state.get("controller")
    generation = controller.begin() if controller is not None else None
    name = next((m.name for m in plan.members if m.playerid == pid), str(pid))
    state["busy"] = True
    _plan_progress(state, f"Fixing {name} only…", mode="wait")
    _sync_session_apply(state)

    def on_success(fixed: SquadPlan) -> None:
        def ui() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            state["busy"] = False
            if controller is not None:
                controller.last_proposal = fixed
            _set_plan(state, fixed, matrix_host, status_lbl, subtitle, f"Fixed {name}. Review, then Apply.")
            _sync_session_apply(state)
            set_status(svc, f"{name} was rebuilt; the other players were not changed.")
        _ui(svc, ui)

    def on_error(exc: BaseException) -> None:
        def err() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            state["busy"] = False
            _plan_progress(state, f"Couldn't fix {name}: {exc}", mode="idle")
            _sync_session_apply(state)
            set_status(svc, f"Couldn't fix {name}: {exc}")
        _ui(svc, err)

    reason = player_issue_text(plan, pid)

    def work(token: Any) -> None:
        targeted_repair_worker(token, plan=plan, session=session, playerid=pid, reason=reason,
                               on_success=on_success, on_error=on_error, cache=state.get("proposal_cache"))

    try:
        _submit(svc, "squad.grok.repair", work, state=state)
    except Exception as exc:  # noqa: BLE001
        state["busy"] = False
        _sync_session_apply(state)
        set_status(svc, f"Could not start the fix: {exc}")
    return True


def _fix_shirt_issue(svc: Any, state: dict[str, Any], pid: int, status_lbl: Any, subtitle: Any,
                     matrix_host: Any, *, swap: bool = False) -> None:
    """Deterministic shirt fix for one player; no AI call, shown before Apply."""
    from ...domain.squad_shirts import fix_shirt, swap_shirt

    if state.get("busy") or state.get("applying"):
        return
    plan = _with_squad_shirts(svc, state["plan"])
    try:
        fixed, note = (swap_shirt if swap else fix_shirt)(plan, pid)
    except ValueError as exc:
        set_status(svc, str(exc))
        return
    controller = state.get("controller")
    if controller is not None:
        controller.last_proposal = fixed
    _set_plan(state, fixed, matrix_host, status_lbl, subtitle, note)
    _sync_session_apply(state)
    set_status(svc, note + " Review it, then Apply.")


def _apply_preset(
    svc: Any, state: dict, preset_id: str, matrix_host: Any, status_lbl: Any, subtitle: Any
) -> None:
    try:
        plan = state["plan"].with_template(preset(preset_id))
        _set_plan(
            state, plan, matrix_host, status_lbl, subtitle,
            f"Staged preset {preset_id} on {plan.player_count} players.",
        )
        set_status(svc, f"Squad plan: preset {preset_id}.")
    except Exception as exc:  # noqa: BLE001
        try:
            status_lbl.configure(text=f"Preset failed: {exc}")
        except Exception:
            pass
        set_status(svc, f"Preset failed: {exc}")


def _stage_manual(
    svc: Any, state: dict, text: str, matrix_host: Any, status_lbl: Any, subtitle: Any
) -> None:
    fields: dict[str, Any] = {}
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        fields[k.strip()] = v.strip()
    try:
        prop = template_from_fields(fields, source="manual", label="Manual squad template")
        plan = state["plan"].with_template(prop)
        _set_plan(
            state, plan, matrix_host, status_lbl, subtitle,
            f"Staged {len(prop.fields)} manual field(s).",
        )
        set_status(svc, "Squad plan: manual template staged.")
    except Exception as exc:  # noqa: BLE001
        try:
            status_lbl.configure(text=f"Manual template: {exc}")
        except Exception:
            pass
        set_status(svc, f"Manual template: {exc}")


def _propose_grok(
    svc: Any,
    state: dict,
    prompt: str,
    matrix_host: Any,
    status_lbl: Any,
    subtitle: Any,
    propose_btn: Any = None,
) -> None:
    plan: SquadPlan = state["plan"]
    rev = plan.selection_revision
    request = (prompt or "").strip()
    if not request:
        try:
            status_lbl.configure(text="Type what you want Codex to propose first.")
        except Exception:
            pass
        set_status(svc, "Describe the squad build you want.")
        return
    if state.get("busy"):
        set_status(svc, "Codex is already proposing…")
        return

    state["busy"] = True
    try:
        status_lbl.configure(
            text="Codex is working (up to ~2 min)… matrix updates when ready."
        )
        if propose_btn is not None:
            propose_btn.configure(state="disabled")
    except Exception:
        pass
    set_status(svc, "Codex: proposing squad template…")

    def on_success(prop: Any) -> None:
        def ui() -> None:
            state["busy"] = False
            try:
                if propose_btn is not None:
                    propose_btn.configure(state="normal")
            except Exception:
                pass
            current: SquadPlan = state["plan"]
            if current.selection_revision != rev:
                try:
                    status_lbl.configure(
                        text="Selection changed while Codex worked — proposal discarded."
                    )
                except Exception:
                    pass
                set_status(svc, "Player selection changed; Codex result discarded.")
                return
            new_plan = current.with_template(prop)
            _set_plan(
                state, new_plan, matrix_host, status_lbl, subtitle,
                f"Codex staged {len(prop.fields)} field(s): {prop.summary or prop.label}",
            )
            set_status(svc, "Squad plan: Codex template ready — review matrix.")

        if getattr(svc, "executor", None) is not None:
            svc.executor.on_ui_thread(ui)
        else:
            ui()

    def on_error(exc: BaseException) -> None:
        message = str(exc)

        def err() -> None:
            state["busy"] = False
            try:
                if propose_btn is not None:
                    propose_btn.configure(state="normal")
                status_lbl.configure(text=f"Codex failed: {message}")
            except Exception:
                pass
            set_status(svc, f"Codex squad propose failed: {message}")

        if getattr(svc, "executor", None) is not None:
            svc.executor.on_ui_thread(err)
        else:
            err()

    def work(token: Any) -> None:
        grok_worker(
            token,
            plan=plan,
            rev=rev,
            prompt=request,
            on_success=on_success,
            on_error=on_error,
        )

    try:
        if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "submit"):
            svc.executor.submit("squad.grok.template", work)
        else:
            work(type("T", (), {"cancelled": False})())
    except Exception as exc:  # noqa: BLE001
        state["busy"] = False
        try:
            if propose_btn is not None:
                propose_btn.configure(state="normal")
            status_lbl.configure(text=f"Could not start Codex: {exc}")
        except Exception:
            pass
        set_status(svc, f"Could not start Codex: {exc}")


def _clear_template(
    state: dict, matrix_host: Any, status_lbl: Any, subtitle: Any
) -> None:
    plan: SquadPlan = state["plan"]
    cleared = SquadPlan(members=plan.members)
    _set_plan(state, cleared, matrix_host, status_lbl, subtitle, "Template cleared.")


def _apply(svc: Any, state: dict, win: Any) -> None:
    if state.get("applying"):
        set_status(svc, "This squad plan is still applying. Keep Career Mode open.")
        return
    if state.get("mode") == "per_player" and state.get("phase") != "review":
        set_status(svc, "Codex is still writing each build.")
        return
    plan: SquadPlan = state["plan"]
    if state.get("edits_dirty"):
        set_status(svc, "Rebuild after changing the selection or targets.")
        return
    if plan.blocking_issues:
        hint = " Use Apply the rest to write everything else." if apply_rest_available(state) else ""
        set_status(svc, "Fix the affected players first: " + " ".join(plan.blocking_issues) + hint)
        return
    if not plan.template and not plan.overrides and not plan.jersey_numbers:
        set_status(svc, "Stage a template or per-player review before Apply.")
        return
    # Shirt problems never block: Apply writes everything else and says so.
    skipped = [f"{_name_of(plan, issue.playerid)}: shirt skipped. {issue.message}" for issue in plan.shirt_issues]
    if (plan.player_count > 1 or skipped) and not _confirm_apply(win, plan, skipped=skipped):
        set_status(svc, "Squad plan Apply cancelled.")
        return
    state["apply_plan"] = None
    _run_apply(svc, state, win)


def _name_of(plan: SquadPlan, pid: int) -> str:
    return next((m.name for m in plan.members if m.playerid == int(pid)), "") or str(
        plan.shirt_names.get(int(pid)) or pid)


def _apply_rest(svc: Any, state: dict, win: Any) -> None:
    """Apply every valid item and skip only the blocked players' fields and bad shirts."""
    from ...domain.squad_plan import plan_without_blocked

    if state.get("applying") or not apply_rest_available(state):
        set_status(svc, "Nothing else can be applied right now.")
        return
    try:
        rest, skipped = plan_without_blocked(state["plan"])
    except JobValidationError as exc:
        set_status(svc, str(exc))
        return
    if not _confirm_apply(win, rest, skipped=skipped):
        set_status(svc, "Squad plan Apply cancelled.")
        return
    state["apply_plan"] = rest
    _run_apply(svc, state, win)


def _confirm_apply(win: Any, plan: SquadPlan, *, skipped: Sequence[str] = ()) -> bool:
    """One deliberate click instead of typing APPLY; Undo covers the rest."""
    ensure_ctk()
    chosen = {"ok": False}
    dialog = ctk.CTkToplevel(win)
    dialog.title("Apply squad plan")
    dialog.configure(fg_color=theme.BG)
    dialog.resizable(False, False)
    try:
        dialog.transient(win)
    except Exception:
        pass
    body = ctk.CTkFrame(dialog, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=(22, 8))
    text_label(body, f"Apply changes to {plan.player_count} players?", size=18, bold=True).pack(anchor="w")
    shirts = len(plan.jersey_numbers)
    scope = f"{plan.field_count} player values"
    if shirts:
        scope += f" · {shirts} shirt number{'s' if shirts != 1 else ''}"
    text_label(body, scope, size=13).pack(anchor="w", pady=(8, 0))
    if skipped:
        text_label(body, f"Skipped for now ({len(skipped)})", size=13, bold=True).pack(anchor="w", pady=(10, 0))
        for line in list(skipped)[:8]:
            muted_label(body, line, size=11, wraplength=400, justify="left",
                        color=theme.WARNING).pack(anchor="w")
        if len(skipped) > 8:
            muted_label(body, f"… and {len(skipped) - 8} more", size=11).pack(anchor="w")
    muted_label(
        body,
        "Keep Career Mode open while it writes. If anything changed in your save since "
        "you built this plan, Companion re-reads the squad first. You can undo the whole "
        "plan later from View results.",
        size=12, wraplength=400, justify="left",
    ).pack(anchor="w", pady=(10, 0))
    actions = ctk.CTkFrame(dialog, fg_color="transparent")
    actions.pack(fill="x", padx=24, pady=(14, 22))

    def choose(ok: bool) -> None:
        chosen["ok"] = ok
        try:
            dialog.grab_release()
        except Exception:
            pass
        dialog.destroy()

    confirm = button(actions, f"Apply to {plan.player_count} players", lambda: choose(True),
                     kind="primary", height=38)
    confirm.pack(side="right")
    button(actions, "Cancel", lambda: choose(False), kind="ghost", height=38, width=90).pack(
        side="right", padx=(0, 8))
    dialog.bind("<Return>", lambda _event: choose(True))
    dialog.bind("<Escape>", lambda _event: choose(False))
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    try:
        dialog.update_idletasks()
        x = win.winfo_rootx() + (win.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = win.winfo_rooty() + (win.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.after(60, lambda: (dialog.grab_set(), confirm.focus_set()))
    except Exception:
        pass
    win.wait_window(dialog)
    return chosen["ok"]


def _show_recovery(state: dict, label: str, action: Any) -> None:
    """One obvious next step next to Apply whenever Apply could not finish."""
    btn = state.get("recovery_btn")
    if btn is None:
        return
    btn.configure(text=label, command=action)
    if not btn.winfo_manager():
        btn.pack(side="right", padx=(0, 8), after=state["apply_btn"])


def _hide_recovery(state: dict) -> None:
    btn = state.get("recovery_btn")
    if btn is not None and btn.winfo_manager():
        btn.pack_forget()


def _apply_failed(svc: Any, state: dict, win: Any, message: str, *, retry_label: str = "") -> None:
    """Say what stopped Apply. Offer a recovery button only when it differs from Apply."""
    state["applying"] = False
    _sync_session_apply(state)
    _plan_progress(state, f"Couldn't apply: {message}", mode="idle")
    set_status(svc, f"Squad plan apply failed: {message}")
    if retry_label:
        _show_recovery(state, retry_label, lambda: _refresh_then_apply(svc, state, win))


def _run_apply(svc: Any, state: dict, win: Any, *, refreshed: bool = False) -> None:
    """Queue the reviewed plan. A stale squad is re-read once, then Apply continues."""
    from ...app.commands.apply import ApplyError
    from ...app.commands.squad_plan_recovery import SquadRefreshNeeded

    plan: SquadPlan = state.get("apply_plan") or state["plan"]
    controller = state.get("controller")
    generation = controller.generation if controller is not None else None
    _hide_recovery(state)

    def finished(result: Any) -> None:
        def ui() -> None:
            if controller is not None and not controller.accepts(generation):
                return
            from ...app.commands.apply import _status_line

            text = _status_line(result)
            receipt = getattr(result, "raw", {}).get("plan_receipt", {})
            if receipt.get("plan_id"):
                state["receipt_id"] = receipt["plan_id"]
                if state.get("receipt_btn"):
                    state["receipt_btn"].pack(side="right", padx=(0, 8))
            if not result.outcome.is_terminal:
                _plan_progress(state, text, mode="wait")
                set_status(svc, text)
                return
            state["applying"] = False
            verified = receipt.get("verification") == "verified" if receipt else result.outcome.is_success
            notes = list(receipt.get("live_changes") or ()) if receipt else []
            if result.outcome.is_success and verified:
                state["phase"] = "applied"
                text = _applied_text(receipt, notes)
                _plan_progress(state, text, mode="ready")
                if not state.get("review_root") and not notes:
                    try:
                        win.after(600, win.destroy)
                    except Exception:
                        pass
            else:
                if receipt and receipt.get("verification") == "pending" and result.outcome.is_success:
                    state["applying"] = True
                    state["phase"] = "verifying"
                    text = "Player writes finished. Waiting for read-back verification."
                    _plan_progress(state, text, mode="wait")
                elif receipt.get("shirt_conflicts") and not receipt.get("shirts_fixed_by") \
                        and receipt.get("status") == "applied":
                    # Everything else is written; only refused shirts remain.
                    # Never replay them blindly: re-plan against a fresh squad.
                    state["phase"] = "attention"
                    text = shirt_conflict_text(receipt["shirt_conflicts"])
                    _plan_progress(state, text, mode="idle")
                    _show_recovery(state, "Fix shirts", lambda: _fix_failed_shirts(svc, state, win, receipt["plan_id"]))
                else:
                    state["phase"] = "attention"
                    _plan_progress(state, text, mode="idle")
                    if receipt.get("plan_id"):
                        _show_recovery(state, "Resume plan", lambda: _resume_plan(svc, state, win, receipt["plan_id"]))
            _sync_session_apply(state)
            set_status(svc, text)

        _ui(svc, ui)

    try:
        state["applying"] = True
        state["receipt_id"] = None
        btn = state.get("apply_btn")
        if btn is not None:
            btn.configure(state="disabled")
        _plan_progress(state, "Applying the plan. Keep Career Mode open.", mode="wait")
        jid = apply_squad_plan(svc, plan, confirm_token="APPLY", on_result=finished)
        state["receipt_id"] = state.get("receipt_id") or jid
        if state.get("receipt_btn"):
            state["receipt_btn"].pack(side="right", padx=(0, 8))
        set_status(svc, f"Squad plan queued ({jid[:8]}…) — {plan.blast_summary()}.")
    except SquadRefreshNeeded as exc:
        if refreshed:
            _apply_failed(svc, state, win, str(exc), retry_label="Refresh squad & apply")
        else:
            _refresh_then_apply(svc, state, win)
    except ApplyError as exc:
        _apply_failed(svc, state, win, str(exc))
    except Exception as exc:  # noqa: BLE001
        _apply_failed(svc, state, win, str(exc))


def _refresh_then_apply(svc: Any, state: dict, win: Any, timeout_s: float = 90.0) -> None:
    """Read the squad from Career Mode, then Apply without asking again."""
    import time

    from ...app.commands.squad import sync_squad

    _hide_recovery(state)
    state["applying"] = True
    _sync_session_apply(state)
    before = svc.store.snapshot().squad.taken
    try:
        sync_squad(svc)
    except Exception as exc:  # noqa: BLE001
        _apply_failed(svc, state, win, f"Could not read the squad: {exc}", retry_label="Refresh squad & apply")
        return
    _plan_progress(state, "Reading your squad from Career Mode, then applying…", mode="wait")
    set_status(svc, "Reading the live squad before applying the plan…")
    deadline = time.monotonic() + timeout_s

    def poll() -> None:
        squad = svc.store.snapshot().squad
        if squad.taken is not None and squad.taken != before and not squad.stale:
            _run_apply(svc, state, win, refreshed=True)
            return
        if time.monotonic() > deadline:
            _apply_failed(svc, state, win, "The squad read did not finish. Stay in the Career hub, then try again.",
                          retry_label="Refresh squad & apply")
            return
        try:
            win.after(500, poll)
        except Exception:
            state["applying"] = False

    try:
        win.after(500, poll)
    except Exception:
        state["applying"] = False


def _fix_failed_shirts(svc: Any, state: dict, win: Any, plan_id: str) -> None:
    """Refresh, re-plan only the refused shirts, confirm, queue a small shirt job."""
    from .planner_shirts import fix_shirts_flow

    _hide_recovery(state)
    state["applying"] = True
    _sync_session_apply(state)
    _plan_progress(state, "Reading your squad, then re-planning the refused shirts…", mode="wait")

    def done(message: str) -> None:
        state["applying"] = False
        queued = message.startswith("Shirt fix queued")
        _plan_progress(state, message, mode="ready" if queued else "idle")
        if not queued:
            _show_recovery(state, "Fix shirts", lambda: _fix_failed_shirts(svc, state, win, plan_id))
        _sync_session_apply(state)
        set_status(svc, message)

    fix_shirts_flow(svc, plan_id, win, on_done=done)


def _resume_plan(svc: Any, state: dict, win: Any, plan_id: str) -> None:
    from ...app.commands.squad_plan import resume_squad_plan

    _hide_recovery(state)
    try:
        state["applying"] = True
        _plan_progress(state, "Resuming the plan where it stopped…", mode="wait")
        resume_squad_plan(svc, plan_id)
        set_status(svc, "Squad plan resumed. Follow it in View results.")
    except Exception as exc:  # noqa: BLE001
        state["applying"] = False
        _plan_progress(state, f"Could not resume: {exc}", mode="idle")
        _show_recovery(state, "Resume plan", lambda: _resume_plan(svc, state, win, plan_id))
    finally:
        _sync_session_apply(state)


def _applied_text(receipt: Mapping[str, Any], notes: Sequence[str]) -> str:
    players = receipt.get("players_applied") or receipt.get("players_requested") or ""
    text = f"Plan applied to {players} players." if players else "Plan applied."
    skipped = [note for note in notes if note.endswith("skipped")]
    kept = [note for note in notes if note not in skipped]
    if skipped:
        text += f" Skipped {len(skipped)} who left the club."
    if kept:
        text += f" {len(kept)} value{'s' if len(kept) != 1 else ''} had changed in Career since the build; details in View results."
    return text
