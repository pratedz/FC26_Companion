"""Human-readable squad review, with bounded rows and independent scrolling."""
from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
from typing import Any

from ...domain.player import FIELD_SPECS
from ...domain.playstyles import plus_names
from ...domain.squad_plan import SquadPlan
from .. import theme
from ..widgets.primitives import button, muted_label, text_label, themed_scroll
from ..widgets.table import inclusive_span


_REVIEW_INDEX: OrderedDict[int, tuple[SquadPlan, dict]] = OrderedDict()


def _review_index(plan: SquadPlan) -> dict:
    """Index each immutable proposal once; keep at most four recent previews."""
    key = id(plan)
    cached = _REVIEW_INDEX.get(key)
    if cached is not None and cached[0] is plan:
        _REVIEW_INDEX.move_to_end(key)
        return cached[1]
    changed: dict[int, list[dict[str, Any]]] = {}
    for row in plan.matrix_rows():
        if row["changed"]:
            changed.setdefault(row["playerid"], []).append(row)
    index = {"members": {member.playerid: member for member in plan.members}, "changes": changed}
    _REVIEW_INDEX[key] = (plan, index)
    while len(_REVIEW_INDEX) > 4:
        _REVIEW_INDEX.popitem(last=False)
    return index


def changes_for(plan: SquadPlan, pid: int) -> list[dict[str, Any]]:
    return list(_review_index(plan)["changes"].get(pid, ()))


def field_value_text(field: str, value: Any) -> str:
    if value is None:
        return "—"
    if field == "birthdate":
        from ...domain.card_import import EA_DATE_EPOCH
        try:
            return (EA_DATE_EPOCH + timedelta(days=int(value))).isoformat()
        except (ValueError, OverflowError, TypeError):
            return str(value)
    return f"{int(value) + 1}★" if field == "skillmoves" else str(value)


def request_checks(plan: SquadPlan, session: Any) -> tuple[str, ...]:
    """Visible completion checks for the user's explicit bulk request."""
    from ...domain.playstyles import GOALKEEPER, OUTFIELD
    from ...domain.squad_session import position_group, wants_recommended_shirts

    recipe = session.recipe
    checks = []
    if recipe.requested_age is not None and recipe.wants_age:
        from ...domain.card_import import FC26_SEASON_START
        checks.append(f"Age {recipe.requested_age} at {FC26_SEASON_START.isoformat()} (season baseline)")
    if recipe.forced_skill_stars is not None:
        satisfied = sum(plan.patch_for(pid).get("skillmoves") == recipe.forced_skill_stars - 1
                        for pid in plan.playerids)
        checks.append(f"{recipe.forced_skill_stars}★ skills {satisfied}/{plan.player_count}")
    for name in recipe.forced_playstyle_plus:
        members = [m for m in plan.members if name in (
            GOALKEEPER if position_group(m.position) == "gk" else OUTFIELD)]
        satisfied = sum(name in plus_names(plan.patch_for(m.playerid)) for m in members)
        exceptions = plan.player_count - len(members)
        checks.append(f"{name}+ {satisfied}/{len(members)}"
                      + (f" ({exceptions} position exception{'s' if exceptions != 1 else ''})" if exceptions else ""))
    if wants_recommended_shirts(recipe.prompt):
        shirts = [plan.jersey_numbers.get(m.playerid,
                  plan.shirt_book.get(m.playerid, m.base.get("jerseynumber", 0))) for m in plan.members]
        valid = len(set(shirts) - {None, 0})
        checks.append(f"Unique shirts {valid}/{plan.player_count}")
    if recipe.unlimited_playstyles and recipe.wants_playstyles:
        counts = [len(plus_names(plan.patch_for(pid))) for pid in plan.playerids]
        if counts:
            spread = str(min(counts)) if min(counts) == max(counts) else f"{min(counts)}–{max(counts)}"
            checks.append(f"PlayStyle+ {spread} per player · no ceiling")
    return tuple(checks)


def player_summary(plan: SquadPlan, pid: int, *, ready: bool) -> str:
    if not ready:
        return "Waiting for a proposal"
    member = _review_index(plan)["members"][pid]
    patch = plan.patch_for(pid)
    bits = []
    shirt = plan.jersey_numbers.get(pid)
    old_shirt = plan.shirt_book.get(pid) or member.base.get("jerseynumber")
    if shirt is not None and shirt != old_shirt:
        bits.append(f"#{old_shirt or '—'} → #{shirt}")
    names = plus_names(patch)
    if "icontrait1" in patch or "icontrait2" in patch:
        bits.append(f"{len(names)} PlayStyle+")
    ovr = patch.get("overallrating")
    if ovr is not None and ovr != member.ovr:
        bits.append(f"OVR {member.ovr or '—'} → {ovr}")
    if "skillmoves" in patch:
        bits.append(f"{field_value_text('skillmoves', patch['skillmoves'])} skills")
    attributes = [r for r in changes_for(plan, pid) if r["field"] not in {
        "trait1", "trait2", "icontrait1", "icontrait2", "overallrating", "jerseynumber", "skillmoves"}]
    if attributes:
        bits.append(f"{len(attributes)} other changes")
    return " · ".join(bits) or "No changes proposed"


def review_totals(plan: SquadPlan) -> tuple[int, int, int]:
    changed = set(_review_index(plan)["changes"])
    changed.update(plan.jersey_numbers)
    plus = sum(bool(plus_names(plan.patch_for(pid))) for pid in plan.playerids)
    return len(changed), len(plan.jersey_numbers), plus


def mount_review(parent: Any, state: dict, on_dirty: Any) -> tuple[Any, Any, Any]:
    import customtkinter as ctk

    shell = ctk.CTkFrame(parent, fg_color="transparent")
    shell.pack(fill="both", expand=True)
    summary = ctk.CTkFrame(shell, fg_color=theme.PANEL, corner_radius=theme.R_MD,
                           border_width=1, border_color=theme.BORDER)
    summary.pack(fill="x", pady=(0, theme.SP2))
    state["review_summary"] = text_label(summary, "Selected players", size=13, bold=True)
    state["review_summary"].pack(side="left", padx=16, pady=12)
    state["review_counts"] = muted_label(summary, "", size=11)
    state["review_counts"].pack(side="right", padx=16)
    state["review_requirements"] = muted_label(summary, "", size=11, wraplength=700,
                                              justify="left", anchor="w")

    panes = ctk.CTkFrame(shell, fg_color="transparent")
    panes.pack(fill="both", expand=True)
    state["review_panes"] = panes
    panes.grid_columnconfigure(0, weight=4, minsize=300)
    panes.grid_columnconfigure(1, weight=6, minsize=350)
    panes.grid_rowconfigure(0, weight=1)
    left = ctk.CTkFrame(panes, fg_color=theme.PANEL, corner_radius=theme.R_MD,
                        border_width=1, border_color=theme.BORDER)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    right = ctk.CTkFrame(panes, fg_color=theme.PANEL, corner_radius=theme.R_MD,
                         border_width=1, border_color=theme.BORDER)
    right.grid(row=0, column=1, sticky="nsew")
    tools = ctk.CTkFrame(left, fg_color="transparent")
    tools.pack(fill="x", padx=12, pady=(12, 8))
    text_label(tools, "These players", size=13, bold=True).pack(side="left")
    state["selection_count"] = muted_label(tools, "", size=10)
    state["selection_count"].pack(side="right")
    search = ctk.CTkEntry(left, placeholder_text="Find a selected player…", height=30,
                         fg_color=theme.BG, border_color=theme.BORDER,
                         text_color=theme.TEXT, placeholder_text_color=theme.MUTED)
    search.pack(fill="x", padx=12, pady=(0, 8))
    bulk = ctk.CTkFrame(left, fg_color="transparent")
    bulk.pack(fill="x", padx=12, pady=(0, 8))
    state["review_on_dirty"] = on_dirty
    def select_all(value):
        if state.get("busy") or state.get("interpreting") or state.get("applying"):
            return
        for flag in state.get("include_vars", {}).values():
            flag.set(value)
        dirty(state)
    button(bulk, "All", lambda: select_all(True), kind="ghost", height=24, width=46).pack(side="left")
    button(bulk, "None", lambda: select_all(False), kind="ghost", height=24, width=54).pack(side="left", padx=4)
    muted_label(bulk, "Shift-click selects a range", size=10).pack(side="right")
    list_scroll = themed_scroll(left, fill=theme.PANEL, stable_children=True)
    list_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 6))
    detail_scroll = themed_scroll(right, fill=theme.PANEL, stable_children=True)
    detail_scroll.pack(fill="both", expand=True, padx=12, pady=12)
    state.update(review_rows={}, review_list=list_scroll, review_detail=detail_scroll,
                 review_search=search, review_root=shell)
    search.bind("<KeyRelease>", lambda _e: filter_rows(state))
    return summary, list_scroll.inner, detail_scroll.inner


def dirty(state):
    state["edits_dirty"] = True
    session = state.get("draft_session") or state.get("session")
    eligible = set(session.playerids) if session is not None else set()
    choices = state.setdefault("include_choices", {})
    for pid, flag in state.get("include_vars", {}).items():
        value = bool(flag.get())
        if value != (pid in eligible):
            choices[pid] = value
        else:
            choices.pop(pid, None)
    count = sum(bool(v.get()) for v in state.get("include_vars", {}).values())
    state["selection_count"].configure(text=f"{count} included")
    state["review_summary"].configure(text="Selection changed — rebuild to update the preview")
    callback = state.get("review_on_dirty")
    if callback:
        callback()


def refresh_request_checks(state):
    requirements = state.get("review_requirements")
    if requirements is None:
        return
    ready = state.get("phase") in {"review", "applied"} and not state.get("edits_dirty")
    checks = request_checks(state["plan"], state["session"]) if ready else ()
    requirements.configure(text=" · ".join(checks))
    if checks:
        state["review_summary"].pack_forget()
        state["review_counts"].pack_forget()
        requirements.pack(fill="x", padx=16, pady=12)
    else:
        requirements.pack_forget()
        state["review_summary"].pack(side="left", padx=16, pady=12)
        state["review_counts"].pack(side="right", padx=16)


def filter_rows(state):
    query = state["review_search"].get().strip().casefold()
    order = []
    for pid, row in state["review_rows"].items():
        row["frame"].pack_forget()
        if query in row["search"]:
            row["frame"].pack(fill="x", padx=4, pady=2)
            order.append(pid)
    state["review_visible"] = order
    state["review_list"].sync_scroll()


def render_players(state):
    import customtkinter as ctk
    session = state.get("session") or state.get("draft_session")
    if session is None:
        return
    # Preserve edits across asynchronous ranking and review updates.
    old_flags = dict(state.get("include_choices", {}))
    old_pins = {pid: str(var.get()) for pid, var in state.get("pin_vars", {}).items()
                if pid in state.get("pin_edited", set())}
    excluded = state.get("kept_excluded", set())
    kept = state.get("kept_pins", {})
    host = state["review_list"].inner
    for child in host.winfo_children():
        child.destroy()
    rows, flags, pins = {}, {}, {}
    state.update(include_vars=flags, pin_vars=pins, review_rows=rows)
    original = state.get("original_plan", state["plan"])
    draft = state.get("draft_session") or session
    targets = {item.playerid: item for item in draft.targets}
    targets.update({item.playerid: item for item in session.targets})
    from ...domain.squad_session import SquadTarget
    for member in original.members:
        item = targets.get(member.playerid) or SquadTarget(
            member.playerid, member.name, member.position, member.ovr, member.base.get("potential"),
            member.ovr or 75, "rotation", "Outside the current request scope.",
        )
        pid = item.playerid
        frame = ctk.CTkFrame(host, fg_color=theme.BG, corner_radius=6, height=54,
                             border_width=1, border_color=theme.BORDER)
        frame.pack(fill="x", padx=4, pady=2)
        flag = ctk.BooleanVar(value=old_flags.get(pid, pid in targets and pid not in excluded))
        flags[pid] = flag
        pins[pid] = ctk.StringVar(value=old_pins.get(pid, kept.get(pid, str(item.target_ovr))))
        tick = ctk.CTkCheckBox(frame, text="", width=22, checkbox_width=16, checkbox_height=16,
                              height=18,
                              variable=flag, command=lambda: dirty(state), fg_color=theme.ACCENT,
                              border_color=theme.BORDER)
        tick.pack(side="left", padx=(8, 6), pady=8)
        words = ctk.CTkFrame(frame, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True, pady=6)
        name = text_label(words, item.name, size=12, bold=True, anchor="w", height=18)
        name.pack(fill="x")
        caption = muted_label(words, item.position or "Player", size=10, anchor="w", height=16)
        caption.pack(fill="x")
        score = muted_label(frame, str(item.ovr or "—"), size=12)
        score.pack(side="right", padx=10)
        rows[pid] = {"frame": frame, "caption": caption,
                     "search": f"{item.name} {item.position}".casefold(), "tick": tick}
        def clicked(event, player=pid):
            if state.get("busy") or state.get("interpreting") or state.get("applying"):
                select_player(state, player)
                return
            if int(getattr(event, "state", 0) or 0) & 1:
                span = inclusive_span(state.get("review_visible", []), state.get("include_anchor"), player)
                if span:
                    def select():
                        for key in span:
                            flags[key].set(True)
                        dirty(state)
                    event.widget.after_idle(select)
                else:
                    state["include_anchor"] = player
            else:
                state["include_anchor"] = player
            select_player(state, player)
        for widget in (frame, words, name, caption, score, tick):
            widget.bind("<Button-1>", clicked, add="+")
    selected = state.get("review_selected")
    if selected not in rows:
        selected = next(iter(rows), None)
    state["review_selected"] = selected
    state["review_list"].bind_wheel_children(host)
    filter_rows(state)
    refresh_review(state)


def refresh_review(state):
    plan = state["plan"]
    ready = state.get("phase") in {"review", "applied"}
    changed, shirts, plus = review_totals(plan) if ready else (0, 0, 0)
    state["review_summary"].configure(text=(
        "Previous preview — rebuild to update" if ready and state.get("edits_dirty") else
        "Changes applied" if state.get("phase") == "applied" else
        "Repair affected players before Apply" if ready and plan.blocking_issues else
        "Review your changes" if ready else "Selected players"))
    state["review_counts"].configure(text=(
        f"{changed} players change  ·  {shirts} shirts  ·  {plus} with PlayStyle+" if ready
        else f"{len(state.get('review_rows', {}))} players  ·  Preview appears when ready"))
    refresh_request_checks(state)
    included = sum(bool(v.get()) for v in state.get("include_vars", {}).values())
    state["selection_count"].configure(text=f"{included} included")
    for pid, row in state.get("review_rows", {}).items():
        member = _review_index(plan)["members"].get(pid)
        if member:
            blocked = any(issue.startswith(member.name + ":") for issue in plan.blocking_issues)
            row["caption"].configure(text="Build incomplete — repair this player" if ready and blocked else
                                      player_summary(plan, pid, ready=ready),
                                      text_color=theme.WARNING if ready and blocked else theme.MUTED)
        row["tick"].configure(state="disabled" if state.get("busy") or state.get("interpreting")
                               or state.get("applying") else "normal")
    select_player(state, state.get("review_selected"))


def select_player(state, pid):
    import customtkinter as ctk
    if pid is None:
        return
    state["review_selected"] = pid
    for key, row in state.get("review_rows", {}).items():
        row["frame"].configure(border_color=theme.ACCENT if key == pid else theme.BORDER,
                                fg_color=theme.CARD if key == pid else theme.BG)
    scroll = state["review_detail"]
    host = scroll.inner
    for child in host.winfo_children():
        child.destroy()
    plan = state["plan"]
    original = state.get("original_plan", plan)
    member = _review_index(plan)["members"].get(pid) or _review_index(original)["members"].get(pid)
    target = state["session"].target_for(pid) or (state.get("draft_session").target_for(pid) if state.get("draft_session") else None)
    if not member:
        return
    if target is None:
        from ...domain.squad_session import SquadTarget
        target = SquadTarget(pid, member.name, member.position, member.ovr, member.base.get("potential"),
                             member.ovr or 75, "rotation", "Outside the current request scope.")
    text_label(host, member.name, size=20, bold=True).pack(anchor="w")
    muted_label(host, f"{target.position or 'Player'}  ·  Current overall {member.ovr or '—'}", size=11).pack(anchor="w", pady=(3, 12))
    if state.get("team_context") is not None:
        context = state["team_context"]
        role_row = ctk.CTkFrame(host, fg_color="transparent")
        role_row.pack(fill="x", pady=(0, 8))
        muted_label(role_row, "Team role", size=11).pack(side="left")
        current_role = context.roles.get(pid, "starter" if pid in context.starters else "automatic")
        def role_changed(label):
            from .planner_team import set_player_role
            try:
                set_player_role(state, pid, label)
                dirty(state)
            except ValueError as exc:
                role_menu.set(current_role.title())
                state["progress_lbl"].configure(text=str(exc))
        role_menu = ctk.CTkOptionMenu(role_row, values=["Automatic", "Starter", "Rotation", "Prospect"],
                                      command=role_changed, width=120, height=28)
        role_menu.set(current_role.title())
        role_menu.pack(side="right")
        if state.get("busy") or state.get("interpreting") or state.get("applying"):
            role_menu.configure(state="disabled")
    def overall_control():
        edit = ctk.CTkFrame(host, fg_color=theme.CARD, corner_radius=6)
        edit.pack(fill="x", pady=(0, 12))
        muted_label(edit, "Overall target", size=11).pack(side="left", padx=12, pady=10)
        pin = ctk.CTkEntry(edit, textvariable=state["pin_vars"][pid], width=58, height=28,
                           justify="center", fg_color=theme.BG, border_color=theme.BORDER,
                           text_color=theme.TEXT)
        pin.pack(side="right", padx=10)
        if state.get("busy") or state.get("interpreting") or state.get("applying"):
            pin.configure(state="disabled")
        def pin_changed(_event):
            state.setdefault("pin_edited", set()).add(pid)
            dirty(state)
        pin.bind("<KeyRelease>", pin_changed)
        scroll.bind_wheel_children(edit)
        scroll.sync_scroll()
    show_overall = target.pinned or target.target_ovr != member.ovr or bool(state["session"].recipe.named_overalls)
    if show_overall:
        overall_control()
    ready = state.get("phase") in {"review", "applied"}
    if ready and state.get("edits_dirty"):
        muted_label(host, "Previous preview. Your new request, selection or roles need a rebuild before Apply.",
                    size=11, wraplength=340, justify="left", color=theme.WARNING).pack(anchor="w", pady=(4, 8))
    recipe = state["session"].recipe
    if recipe.requested_age is not None and recipe.wants_age:
        from ...domain.card_import import FC26_SEASON_START
        section(host, "Age target", f"{recipe.requested_age} at FC 26 season start ({FC26_SEASON_START.isoformat()}). Career date is unavailable; review the birthdate below.")
    if not ready:
        text_label(host, "Your changes will appear here", size=15, bold=True).pack(anchor="w", pady=(12, 6))
        muted_label(host, "Build the plan, then compare shirt numbers, PlayStyle+ and ratings for each player.",
                    size=12, wraplength=340, justify="left").pack(anchor="w")
    else:
        player_blocked = False
        for issue in plan.blocking_issues:
            if issue.startswith(member.name + ":"):
                player_blocked = True
                action = "Rebuild your changed request first." if state.get("edits_dirty") else "Use Repair this player below."
                muted_label(host, issue.replace("Rebuild this plan.", action), size=12, color=theme.WARNING, wraplength=340,
                            justify="left").pack(anchor="w", pady=(0, 10))
        if player_blocked and state.get("repair_player") and not state.get("edits_dirty"):
            repair = button(host, "Repair this player", lambda: state["repair_player"](pid),
                            kind="primary", height=32, width=170)
            repair.pack(anchor="w", pady=(0, 10))
            if state.get("busy") or state.get("applying"):
                repair.configure(state="disabled")
        for note in plan.warnings:
            if note.startswith(member.name + ": removed incompatible existing styles:"):
                muted_label(host, note, size=11, wraplength=340, justify="left").pack(anchor="w", pady=(0, 8))
        patch = plan.patch_for(pid)
        shirt = plan.jersey_numbers.get(pid)
        if shirt is not None:
            old = plan.shirt_book.get(pid) or member.base.get("jerseynumber")
            section(host, "Shirt number", f"#{old or '—'}   →   #{shirt}")
            for note in plan.warnings:
                if member.name in note and ("reserved" in note or "moves from #" in note or "stays with" in note):
                    muted_label(host, note, size=11, wraplength=340, justify="left").pack(anchor="w", pady=(4, 0))
        if "icontrait1" in patch or "icontrait2" in patch:
            names = plus_names(patch)
            before_known = all(k in member.base for k in ("icontrait1", "icontrait2"))
            before = set(plus_names(member.base)) if before_known else set()
            section(host, f"PlayStyle+ · {len(names)} proposed", "")
            for name in names:
                muted_label(host, ("Keep · " if name in before else "Add · " if before_known else "Proposed · ") + name,
                            size=12, height=20, color=theme.ACCENT if name not in before else theme.TEXT).pack(anchor="w", pady=2)
            for name in sorted(before - set(names)):
                muted_label(host, "Remove · " + name, size=11, color=theme.WARNING).pack(anchor="w", pady=2)
        if "trait1" in patch or "trait2" in patch:
            names = plus_names({"icontrait1": patch.get("trait1", 0), "icontrait2": patch.get("trait2", 0)})
            section(host, f"PlayStyles · {len(names)} proposed", ", ".join(names) or "None")
        changed = changes_for(plan, pid)
        useful = [r for r in changed if not r["field"].startswith(("trait", "icontrait"))
                  and r["field"] != "jerseynumber"]
        if useful:
            section(host, "Ratings & other changes", "")
            for row in useful:
                spec = FIELD_SPECS.get(row["field"])
                label = "Skill moves" if row["field"] == "skillmoves" else spec.label if spec else row["field"]
                line = ctk.CTkFrame(host, fg_color="transparent")
                line.pack(fill="x", pady=3)
                muted_label(line, label, size=11).pack(side="left")
                text_label(line, f"{field_value_text(row['field'], row['before'])}  →  {field_value_text(row['field'], row['after'])}",
                           size=11, color=theme.ACCENT).pack(side="right")
        reason = plan.reasons.get(pid)
        if reason:
            section(host, "Why these changes", reason)
        hint = plan.library_hints.get(pid)
        if hint:
            section(host, "Library reference", hint)
        if not changed and not shirt:
            section(host, "No changes proposed", "This player will keep their current values.")
        other_shirts = {key: value for key, value in plan.jersey_numbers.items() if key not in plan.playerids}
        if other_shirts:
            section(host, "Other squad shirts that must move", "")
            for key, number in other_shirts.items():
                section_text = f"{plan.shirt_names.get(key, key)} · #{plan.shirt_book.get(key) or '—'} → #{number}"
                muted_label(host, section_text, size=11, wraplength=340, justify="left").pack(anchor="w")
        # All exact values remain available, without taking over the review.
        advanced = ctk.CTkFrame(host, fg_color="transparent")
        def show_exact():
            if advanced.winfo_manager():
                advanced.pack_forget()
                return
            for child in advanced.winfo_children():
                child.destroy()
            for key, value in sorted(patch.items()):
                muted_label(advanced, f"{FIELD_SPECS[key].label}: {value}", size=10).pack(anchor="w", pady=2)
            advanced.pack(fill="x", pady=8)
            scroll.bind_wheel_children(advanced)
            scroll.sync_scroll()
        button(host, "Show exact values", show_exact, kind="ghost", height=28).pack(anchor="w", pady=(16, 0))
        if plan.warnings:
            notes = ctk.CTkFrame(host, fg_color="transparent")
            def show_notes():
                if notes.winfo_manager():
                    notes.pack_forget()
                else:
                    for child in notes.winfo_children():
                        child.destroy()
                    for note in plan.warnings:
                        muted_label(notes, note, size=11, wraplength=340, justify="left",
                                    color=theme.WARNING).pack(anchor="w", pady=3)
                    notes.pack(fill="x", pady=8)
                    scroll.bind_wheel_children(notes)
                    scroll.sync_scroll()
            button(host, f"Plan notes · {len(plan.warnings)}", show_notes, kind="ghost", height=28).pack(anchor="w", pady=4)
    if state.get("team_context") is not None:
        from ...domain.team_plan import team_summary
        section(host, "Team balance", "")
        for line in team_summary(original, plan if ready else original, state["team_context"]):
            muted_label(host, line, size=11, wraplength=340, justify="left").pack(anchor="w", pady=2)
    if not show_overall:
        def open_overall():
            overall_control()
            edit_button.configure(state="disabled")
        edit_button = button(host, "Edit overall target", open_overall, kind="ghost", height=28)
        edit_button.pack(anchor="w", pady=(12, 0))
    scroll.bind_wheel_children(host)
    scroll.sync_scroll()
    scroll.canvas.yview_moveto(0)


def section(host, title, text):
    text_label(host, title, size=13, bold=True).pack(anchor="w", pady=(12, 5))
    if text:
        muted_label(host, text, size=12, wraplength=340, justify="left").pack(anchor="w")
