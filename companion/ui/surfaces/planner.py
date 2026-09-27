"""Squad Planner window — multi-select draft + Codex/preset template + Apply.

Opened from Club. Codex workers must accept the executor cancel token
(``fn(token)``) or the pool raises and the UI hangs on “proposing…”.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ...app.commands.squad_plan import apply_squad_plan, confirm_squad_apply
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
    eyebrow,
    muted_label,
    panel,
    pill,
    set_disabled,
    text_label,
)
from ..widgets.table import Column, DataGrid, TableModel, inclusive_span
from ._common import set_status

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
    try:
        if label is not None:
            label.configure(text=text)
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
    text_label(header, "SQUAD PLANNER", size=16, bold=True).pack(
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
    """Remember every current shirt so a requested number can move its wearer."""
    book: dict[int, int] = {}
    names: dict[int, str] = {}
    try:
        players = svc.store.snapshot().squad.players
    except Exception:
        players = ()
    for row in players:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("playerid") or row.get("id"))
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        try:
            number = int(row.get("jerseynumber") or 0)
        except (TypeError, ValueError):
            number = 0
        book[pid] = number if 1 <= number <= 99 else 0
        names[pid] = str(row.get("name") or pid)
    for member in plan.members:
        if member.playerid not in book:
            try:
                number = int(member.base.get("jerseynumber") or 0)
            except (TypeError, ValueError):
                number = 0
            book[member.playerid] = number if 1 <= number <= 99 else 0
        names.setdefault(member.playerid, member.name)
    return replace(plan, shirt_book=book, shirt_names=names)


def _open_session_planner(
    svc: Any,
    plan: SquadPlan,
    *,
    prompt: str,
    club_name: str,
    selected_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Per-player Career session: interpret → pin/exclude → snapshot → propose → APPLY."""
    del selected_rows
    ensure_ctk()
    plan = _with_squad_shirts(svc, plan)
    state: dict[str, Any] = {
        "plan": plan,
        "busy": False,
        "mode": "per_player",
        "session": None,
        "prompt": (prompt or "").strip(),
        "club_name": club_name or "",
        "include_vars": {},
        "pin_vars": {},
        "phase": "interpret",
    }

    win = _new_toplevel()
    win.title("Plan these players")
    win.geometry("1080x860")
    try:
        win.minsize(940, 720)
    except Exception:
        pass
    win.configure(fg_color=theme.BG)
    _raise_window(win)

    header = ctk.CTkFrame(
        win, fg_color=theme.PANEL, corner_radius=theme.R_MD,
        border_width=1, border_color=theme.BORDER,
    )
    header.pack(fill="x", padx=theme.SP4, pady=(theme.SP3, theme.SP2))
    stripe = ctk.CTkFrame(header, width=4, height=8, fg_color=theme.CODEX, corner_radius=0)
    stripe.pack(side="left", fill="y", padx=(0, 0))
    head_copy = ctk.CTkFrame(header, fg_color="transparent")
    head_copy.pack(side="left", fill="x", expand=True, padx=theme.SP3, pady=theme.SP3)
    eyebrow(head_copy, "Codex squad session").pack(anchor="w")
    text_label(head_copy, "Plan these players", size=20, bold=True).pack(anchor="w", pady=(2, 0))
    subtitle = muted_label(
        head_copy,
        "One build per player. A number you type stays locked.",
        size=12,
    )
    subtitle.pack(anchor="w", pady=(2, 0))
    count_chip = ctk.CTkFrame(header, fg_color=theme.CARD, corner_radius=theme.R_SM)
    count_chip.pack(side="right", padx=theme.SP3, pady=theme.SP3)
    text_label(count_chip, str(plan.player_count), size=18, bold=True, color=theme.CODEX).pack(
        anchor="center", padx=theme.SP3, pady=(theme.SP2, 0)
    )
    muted_label(count_chip, "players", size=10).pack(anchor="center", padx=theme.SP3, pady=(0, theme.SP2))

    tools = panel(win, level=1)
    tools.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP2))
    from ..widgets.ai_bar import mount_ai_provider_bar

    mount_ai_provider_bar(tools, svc).pack_configure(padx=theme.SP3, pady=(theme.SP2, 0))
    grok_row = ctk.CTkFrame(tools, fg_color="transparent")
    grok_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    prompt_entry = ctk.CTkEntry(
        grok_row,
        placeholder_text="cr7 overall 88, messi jersey 10",
        height=40,
        fg_color=theme.BG,
        border_color=theme.CODEX,
        border_width=1,
        text_color=theme.TEXT,
        placeholder_text_color=theme.MUTED,
    )
    prompt_entry.pack(side="left", fill="x", expand=True, padx=(0, theme.SP2))
    if state["prompt"]:
        prompt_entry.insert(0, state["prompt"])
    interpret_btn = button(
        grok_row, "Update",
        lambda: _start_interpret(
            svc, state, prompt_entry.get(), chip_host, preview_host,
            matrix_host, status_lbl, subtitle, interpret_btn, propose_btn,
        ),
        kind="accent", height=40, width=96,
    )
    interpret_btn.pack(side="left")

    progress_host = ctk.CTkFrame(tools, fg_color="transparent")
    progress_host.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP2))
    progress_lbl = muted_label(progress_host, "Codex is idle.", size=11)
    progress_lbl.pack(anchor="w")
    progress_bar = ctk.CTkProgressBar(
        progress_host,
        height=8,
        corner_radius=theme.R_PILL,
        progress_color=theme.CODEX,
        fg_color=theme.BORDER,
    )
    progress_bar.set(0)
    progress_bar.pack(fill="x", pady=(theme.SP1, 0))
    state["progress_lbl"] = progress_lbl
    state["progress_bar"] = progress_bar
    ceiling_row = ctk.CTkFrame(tools, fg_color="transparent")
    ceiling_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    muted_label(ceiling_row, "PlayStyle+ ceiling", size=11).pack(side="left")
    ceiling_var = ctk.BooleanVar(value=False)
    state["ceiling_unlimited"] = ceiling_var
    ctk.CTkSwitch(
        ceiling_row,
        text="No ceiling",
        variable=ceiling_var,
        progress_color=theme.CODEX,
        button_color=theme.TEXT,
        button_hover_color=theme.MUTED,
        text_color=theme.TEXT,
    ).pack(side="left", padx=(theme.SP3, 0))

    chip_host = ctk.CTkFrame(tools, fg_color="transparent")
    chip_host.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))

    status_lbl = muted_label(win, "Checking the names you typed…", size=11)
    status_lbl.pack(anchor="w", padx=theme.SP4)

    foot = ctk.CTkFrame(win, fg_color="transparent", height=52)
    foot.pack(side="bottom", fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    try:
        foot.pack_propagate(False)
    except Exception:
        pass

    body = ctk.CTkScrollableFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=theme.SP3, pady=theme.SP2)
    preview_host = ctk.CTkFrame(body, fg_color="transparent")
    preview_host.pack(fill="x")
    matrix_host = ctk.CTkFrame(body, fg_color="transparent")
    matrix_host.pack(fill="both", expand=True, pady=(theme.SP2, 0))

    apply_btn = button(
        foot, "Apply these builds…",
        lambda: _apply(svc, state, win),
        kind="secondary", height=36, width=190,
        disabled_reason="Codex is still writing each build.",
    )
    apply_btn.pack(side="right")
    propose_btn = button(
        foot, "Rebuild",
        lambda: _start_session_propose(
            svc, state, preview_host, matrix_host, status_lbl, subtitle, propose_btn
        ),
        kind="primary", height=32, width=190,
    )
    propose_btn.pack(side="right", padx=(0, theme.SP2))
    button(
        foot, "Codex login",
        lambda: _connect_grok(svc, status_lbl),
        kind="ghost", height=32, width=110,
    ).pack(side="left")
    state["apply_btn"] = apply_btn
    _sync_session_apply(state)

    if state["prompt"]:
        _start_interpret(
            svc, state, state["prompt"], chip_host, preview_host,
            matrix_host, status_lbl, subtitle, interpret_btn, propose_btn,
        )
    else:
        try:
            status_lbl.configure(text="Type each overall, then Update.")
        except Exception:
            pass


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
                    f"Codex is writing {total} group{'s' if total != 1 else ''} of players."
                    if cur <= 0
                    else f"Codex finished {cur} of {total}."
                ),
                cur,
                total,
            ),
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


def _codex_progress(svc: Any, state: dict[str, Any], status_lbl: Any):
    """Marshal a Codex group update onto the plan window."""

    def report(msg: str, done: int = 0, total: int = 0) -> None:
        def ui(message: str = msg, finished: int = done, groups: int = total) -> None:
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


def _submit(svc: Any, name: str, work: Any) -> None:
    if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "submit"):
        svc.executor.submit(name, work)
    else:
        work(type("T", (), {"cancelled": False})())


def _draft_session(plan: SquadPlan, prompt: str, club_name: str) -> SquadSession:
    """Python ranking so the checklist appears before Codex returns."""
    recipe = parse_recipe(prompt, club_name)
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
    return build_session(roster, recipe)


def _snapshot_preview_edits(state: dict[str, Any]) -> tuple[set[int], dict[int, str]]:
    excluded: set[int] = set()
    pins: dict[int, str] = {}
    for pid, flag in (state.get("include_vars") or {}).items():
        try:
            if not bool(flag.get()):
                excluded.add(int(pid))
        except (TypeError, ValueError, AttributeError):
            continue
    for pid, entry in (state.get("pin_vars") or {}).items():
        try:
            pins[int(pid)] = str(entry.get()).strip()
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
            "Rebuild to complete every player's PlayStyle+ proposal."
            if getattr(plan, "blocking_issues", ()) else "Codex is still writing each build."
        ),
    )


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
        _submit(svc, "squad.grok.interpret", work)
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
    who = ""
    if session is not None and session.targets:
        who = ", ".join(
            f"{item.name} → {item.target_ovr}" for item in session.targets[:6]
        )
    if phase == "review":
        return "Builds are ready. Look through the changes, then Apply."
    if phase in {"reading", "proposing"}:
        if who:
            return f"Writing builds for {who}."
        return "Writing attributes and playstyles…"
    if who:
        return f"Check these overalls: {who}."
    return "Checking the names you typed…"


def _render_session_preview(
    state: dict[str, Any],
    chip_host: Any,
    preview_host: Any,
    status_lbl: Any,
    subtitle: Any,
) -> None:
    session: SquadSession | None = state.get("session")
    for host in (chip_host, preview_host):
        try:
            for child in list(host.winfo_children()):
                child.destroy()
        except Exception:
            return
    if session is None:
        return
    ceiling = state.get("ceiling_unlimited")
    if ceiling is not None:
        try:
            ceiling.set(bool(session.recipe.unlimited_playstyles))
        except Exception:
            pass
    for chip in session.chips[:8]:
        pill(chip_host, chip, tone="info").pack(side="left", padx=(0, theme.SP1), pady=(0, theme.SP1))
    include_vars: dict[int, Any] = {}
    pin_vars: dict[int, Any] = {}
    card = panel(preview_host, level=1)
    card.pack(fill="x")
    head = ctk.CTkFrame(card, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, 0))
    eyebrow(head, "These players").pack(side="left")
    muted_label(head, f"{len(session.targets)}", size=11).pack(side="right")
    muted_label(
        card,
        "Untick anyone to skip them. Change a number, then Rebuild.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(2, 0))
    for note in session.warnings:
        muted_label(card, note, size=11, color=theme.WARNING).pack(
            anchor="w", padx=theme.SP3, pady=(2, 0)
        )
    tools = ctk.CTkFrame(card, fg_color="transparent")
    tools.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, 0))

    def _set_all(value: bool) -> None:
        for var in include_vars.values():
            try:
                var.set(value)
            except Exception:
                pass

    button(tools, "All", lambda: _set_all(True), kind="ghost", height=24, width=48).pack(
        side="left"
    )
    button(tools, "None", lambda: _set_all(False), kind="ghost", height=24, width=56).pack(
        side="left", padx=(theme.SP1, 0)
    )
    kept_excluded = {int(pid) for pid in (state.get("kept_excluded") or set())}
    kept_pins = {
        int(pid): str(value)
        for pid, value in (state.get("kept_pins") or {}).items()
    }
    for item in session.targets:
        row = ctk.CTkFrame(
            card, fg_color=theme.BG, corner_radius=theme.R_SM,
            border_width=1, border_color=theme.BORDER,
        )
        row.pack(fill="x", padx=theme.SP3, pady=2)
        flag = ctk.BooleanVar(value=item.playerid not in kept_excluded)
        include_vars[item.playerid] = flag

        def _range_tick(event: Any, pid: int = item.playerid) -> None:
            shift = bool(int(getattr(event, "state", 0) or 0) & 0x0001)
            order = [target.playerid for target in session.targets]
            anchor = state.get("include_anchor")
            if not shift:
                state["include_anchor"] = pid
                return
            span = inclusive_span(order, anchor, pid)
            if span is None:
                state["include_anchor"] = pid
                return

            def apply(selected: tuple[int, ...] = span) -> None:
                for key in selected:
                    box = include_vars.get(key)
                    if box is not None:
                        box.set(True)

            try:
                event.widget.after_idle(apply)
            except Exception:
                apply()

        tick = ctk.CTkCheckBox(
            row,
            text="",
            variable=flag,
            width=28,
            height=22,
            checkbox_width=16,
            checkbox_height=16,
            fg_color=theme.CODEX,
            hover_color=theme.CARD_HOVER,
            border_color=theme.BORDER,
        )
        tick.pack(side="left", padx=(theme.SP2, 0), pady=theme.SP2)
        tick.bind("<Button-1>", _range_tick, add="+")
        row.bind("<Button-1>", _range_tick, add="+")
        before_fill, before_fg = theme.ovr_colors(item.ovr)
        before_chip = ctk.CTkFrame(
            row, width=40, height=28, fg_color=before_fill, corner_radius=theme.R_SM,
        )
        before_chip.pack(side="left", padx=(theme.SP1, theme.SP2), pady=theme.SP2)
        before_chip.pack_propagate(False)
        text_label(
            before_chip,
            str(item.ovr if item.ovr is not None else "—"),
            size=12,
            bold=True,
            color=before_fg,
        ).pack(expand=True)
        words = ctk.CTkFrame(row, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True, pady=theme.SP1)
        name_lbl = text_label(words, item.name, size=13, bold=True)
        name_lbl.pack(anchor="w")
        name_lbl.bind("<Button-1>", _range_tick, add="+")
        words.bind("<Button-1>", _range_tick, add="+")
        detail = item.position or "Player"
        if item.rung:
            detail = f"{detail}  ·  {item.rung}"
        if item.pinned:
            detail = f"{detail}  ·  overall locked"
        muted_label(words, detail, size=10).pack(anchor="w")
        muted_label(row, "→", size=14, color=theme.CODEX).pack(side="left", padx=(0, theme.SP2))
        after_fill, _after_fg = theme.ovr_colors(item.target_ovr)
        pin = ctk.CTkEntry(
            row,
            width=52,
            height=28,
            justify="center",
            fg_color=theme.CARD,
            border_color=after_fill,
            border_width=1,
            text_color=theme.TEXT,
        )
        pin.insert(0, kept_pins.get(item.playerid) or str(item.target_ovr))
        pin.pack(side="left", padx=(0, theme.SP3), pady=theme.SP2)
        pin_vars[item.playerid] = pin
    muted_label(card, "", size=4).pack(pady=(0, theme.SP1))
    state["include_vars"] = include_vars
    state["pin_vars"] = pin_vars
    try:
        status_lbl.configure(text=_session_status(str(state.get("phase") or "preview"), session))
        subtitle.configure(text=_session_status(str(state.get("phase") or "preview"), session))
    except Exception:
        pass


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
            continue
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
    session = state.get("session")
    ceiling = state.get("ceiling_unlimited")
    if session is not None and ceiling is not None:
        try:
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
        refined = _collect_session_edits(state)
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not apply pins: {exc}")
        return
    if not refined.targets:
        set_status(svc, "Nobody left to edit. Include at least one player.")
        return
    state["session"] = refined
    try:
        plan = state["plan"].keeping(refined.playerids)
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))
        return
    state["plan"] = plan
    state["busy"] = True
    state["phase"] = "reading"
    _plan_progress(state, "Codex is preparing this squad.", mode="wait")
    _sync_session_apply(state)
    try:
        propose_btn.configure(state="disabled")
        status_lbl.configure(text=_session_status("reading", refined))
    except Exception:
        pass

    def after_bases(plan_ready: SquadPlan) -> None:
        state["plan"] = plan_ready
        state["phase"] = "proposing"
        try:
            status_lbl.configure(text=_session_status("proposing", state.get("session")))
        except Exception:
            pass
        set_status(svc, "Writing attributes and playstyles…")

        def on_success(staged: SquadPlan) -> None:
            def ui() -> None:
                state["busy"] = False
                state["phase"] = "review"
                _plan_progress(state, "Builds are ready.", mode="ready")
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
                state["busy"] = False
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
            )

        try:
            _submit(svc, "squad.grok.session", work)
        except Exception as exc:  # noqa: BLE001
            state["busy"] = False
            try:
                propose_btn.configure(state="normal")
                status_lbl.configure(text=f"Could not start Codex: {exc}")
            except Exception:
                pass

    missing = [m for m in plan.members if needs_live_base(m)]
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
            filled = state["plan"].with_member_bases(rows)
            skipped = [m.name for m in filled.members if needs_live_base(m)]
            if skipped:
                try:
                    status_lbl.configure(
                        text=f"No live stats for {', '.join(skipped[:4])}. Editing the rest."
                    )
                except Exception:
                    pass
                keep = [m.playerid for m in filled.members if not needs_live_base(m)]
                if not keep:
                    state["busy"] = False
                    try:
                        propose_btn.configure(state="normal")
                    except Exception:
                        pass
                    set_status(svc, "Live read returned no usable players.")
                    return
                filled = filled.keeping(keep)
                state["session"] = refine_session(
                    state["session"],
                    excluded_ids=[
                        pid for pid in state["session"].playerids if pid not in set(keep)
                    ],
                )
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
            state["busy"] = False
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
        subtitle.configure(text=plan.blast_summary())
        status_lbl.configure(text=msg)
    except Exception:
        pass
    _render_matrix(matrix_host, plan)


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


def _connect_grok(svc: Any, status_lbl: Any = None) -> None:
    try:
        from ...integrations.grok import use_build_session

        st = use_build_session()
        msg = st.message
        if status_lbl is not None:
            try:
                status_lbl.configure(text=msg)
            except Exception:
                pass
        set_status(svc, msg)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if status_lbl is not None:
            try:
                status_lbl.configure(text=msg)
            except Exception:
                pass
        set_status(svc, msg)


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
    if plan.blocking_issues:
        set_status(svc, "Rebuild this plan: " + " ".join(plan.blocking_issues))
        return
    if not plan.template and not plan.overrides and not plan.jersey_numbers:
        set_status(svc, "Stage a template or per-player review before Apply.")
        return
    token = "APPLY"
    if plan.player_count > 1:
        kind = "per-player patches" if plan.overrides else "template"
        dialog = ctk.CTkInputDialog(
            text=(
                f"Apply {kind} to {plan.player_count} players "
                f"({plan.field_count} fields)? Type APPLY to confirm."
            ),
            title="Confirm squad plan",
        )
        token = dialog.get_input() or ""
        if not confirm_squad_apply(token):
            set_status(svc, "Squad plan Apply cancelled.")
            return
    def finished(result: Any) -> None:
        def ui() -> None:
            from ...app.commands.apply import _status_line

            text = _status_line(result)
            if not result.outcome.is_terminal:
                _plan_progress(state, text, mode="wait")
                set_status(svc, text)
                return
            state["applying"] = False
            _sync_session_apply(state)
            if result.outcome.is_success:
                _plan_progress(state, "Plan applied.", mode="ready")
                try:
                    win.after(600, win.destroy)
                except Exception:
                    pass
            else:
                _plan_progress(state, text, mode="idle")
            set_status(svc, text)

        _ui(svc, ui)

    try:
        state["applying"] = True
        btn = state.get("apply_btn")
        if btn is not None:
            btn.configure(state="disabled")
        _plan_progress(state, "Applying the plan. Keep Career Mode open.", mode="wait")
        jid = apply_squad_plan(
            svc,
            plan,
            confirm_token=token if plan.player_count > 1 else "APPLY",
            on_result=finished,
        )
        set_status(
            svc,
            f"Squad plan queued ({jid[:8]}…) — {plan.blast_summary()}.",
        )
    except Exception as exc:  # noqa: BLE001
        state["applying"] = False
        _sync_session_apply(state)
        _plan_progress(state, f"Squad plan apply failed: {exc}", mode="idle")
        set_status(svc, f"Squad plan apply failed: {exc}")
