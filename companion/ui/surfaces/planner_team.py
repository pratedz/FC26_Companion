"""Optional team planning controls, separate from the primary request/review flow."""
from __future__ import annotations

from dataclasses import replace

from ...domain.team_plan import FORMATIONS, TeamContext
from ..widgets.primitives import button, muted_label


def mount_team_controls(host, state, *, on_dirty):
    import customtkinter as ctk

    row = ctk.CTkFrame(host, fg_color="transparent")
    row.pack(fill="x", pady=4)
    muted_label(row, "Planning formation", size=11).pack(side="left", padx=(0, 8))
    def formation(value):
        state["team_context"] = replace(state["team_context"], formation=value)
        on_dirty()
    menu = ctk.CTkOptionMenu(row, values=list(FORMATIONS), command=formation, width=105, height=28)
    menu.set(state["team_context"].formation)
    menu.pack(side="left")
    muted_label(row, "Overall range", size=11).pack(side="left", padx=(16, 8))
    low = ctk.CTkEntry(row, width=50, height=28, placeholder_text="Min")
    low.pack(side="left")
    muted_label(row, "to", size=11).pack(side="left", padx=5)
    high = ctk.CTkEntry(row, width=50, height=28, placeholder_text="Max")
    high.pack(side="left")
    for entry in (low, high):
        entry.bind("<KeyRelease>", lambda _event: on_dirty())
    state.update(team_min_entry=low, team_max_entry=high)
    def scope(value):
        controller = state["controller"]
        if value == "Whole club":
            rows = state["whole_club_rows"]
            if not rows:
                state["progress_lbl"].configure(text="Sync the club before planning the whole squad.")
                return
            controller.original_plan = state["original_selected_plan"].reselect(rows)
        else:
            controller.original_plan = state["original_selected_plan"]
        state["original_plan"] = controller.original_plan
        state["include_vars"] = {}
        state["pin_vars"] = {}
        state["kept_excluded"] = set()
        from .planner import _draft_session
        from .planner_review import render_players
        state["session"] = _draft_session(controller.original_plan, state["prompt_entry"].get(),
                                          state["club_name"], state["team_context"])
        state["draft_session"] = state["session"]
        render_players(state)
        on_dirty()
    scope_menu = ctk.CTkOptionMenu(row, values=["Selected players", "Whole club"],
                                  command=scope, width=145, height=28)
    scope_menu.set("Selected players")
    scope_menu.pack(side="right")
    muted_label(host, "Assign starter, rotation or prospect in each player's details. Formation guides the plan; it does not change your tactics.",
                size=10, wraplength=740, justify="left").pack(anchor="w", pady=(4, 2))
    state["team_controls"] = [menu, low, high, scope_menu]


def collect_team_context(state):
    context = state.get("team_context")
    if context is None:
        return None
    low, high = state.get("team_min_entry"), state.get("team_max_entry")
    if low is not None and high is not None:
        left, right = low.get().strip(), high.get().strip()
        if bool(left) != bool(right):
            raise ValueError("Enter both ends of the overall range, or leave both blank.")
        try:
            lower, upper = int(left) if left else None, int(right) if right else None
        except ValueError as exc:
            raise ValueError("Use whole numbers between 1 and 99 for the overall range.") from exc
        context = replace(context, overall_min=lower, overall_max=upper)
        state["team_context"] = context
    return context


def set_player_role(state, pid, label):
    context = state.get("team_context", TeamContext())
    roles = dict(context.roles)
    role = label.casefold()
    if role == "automatic":
        roles.pop(pid, None)
    else:
        roles[pid] = role
    # Selecting another role clears previous starter status for this player.
    starters = tuple(value for value in context.starters if value != pid)
    state["team_context"] = replace(context, roles=roles, starters=starters)
