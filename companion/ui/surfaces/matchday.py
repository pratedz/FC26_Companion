"""Matchday: one preparation action, a quiet routine, and its real receipt."""
from __future__ import annotations

from typing import Any

from ...app.commands import matchday as commands
from ...domain.automation_guide import trusted_cached_teamid
from ...domain.matchday import Routine, TARGETS
from .. import theme
from ..widgets.primitives import button, muted_label, panel, section_title, set_disabled, text_label
from ._common import section_header, set_status, surface_root

import customtkinter as ctk


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    view = vm if isinstance(vm, dict) else {}
    routine = commands.load_routine(svc)
    variables = {key: ctk.BooleanVar(master=root, value=value) for key, value in routine.to_pref().items()}

    header = section_header(root, "Matchday", subtitle="Your current Career squad")
    scope_label = header.subtitle_label
    hero = panel(root, border=True)
    hero.pack(fill="x", pady=(0, theme.SP4))
    body = ctk.CTkFrame(hero, fg_color="transparent")
    body.pack(fill="x", padx=theme.SP5, pady=(theme.SP5, theme.SP4))
    text_label(body, "Ready for kick-off?", height=0, size=theme.T_TITLE, display=True).pack(anchor="w")
    muted_label(body, "Give your squad a boost before the next match.", height=0,
                size=theme.T_BODY).pack(anchor="w", pady=(theme.SP1, 0))

    targets = ctk.CTkFrame(body, fg_color="transparent")
    targets.pack(fill="x", pady=(theme.SP4, theme.SP2))
    target_labels = {}
    for index, (key, label, value) in enumerate(TARGETS):
        targets.grid_columnconfigure(index, weight=1, uniform="matchday-targets")
        tile = ctk.CTkFrame(targets, fg_color=theme.CARD, corner_radius=theme.R_MD,
                            border_width=1, border_color=theme.BORDER)
        tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else theme.SP2, 0))
        muted_label(tile, f"{label} target", height=0, size=theme.T_SMALL).pack(
            anchor="w", padx=theme.SP4, pady=(theme.SP3, 0))
        target_labels[key] = text_label(tile, str(value), height=0, size=theme.T_SCORE,
                                        display=True, color=theme.TEXT)
        target_labels[key].pack(anchor="w", padx=theme.SP4, pady=(0, theme.SP3))
    muted_label(body, "Your chosen targets, applied when you prepare.", height=0,
                size=theme.T_CAPTION).pack(anchor="w", pady=(0, theme.SP4))
    action = ctk.CTkFrame(body, fg_color="transparent")
    action.pack(fill="x")

    def selected() -> Routine:
        return Routine(**{key: bool(var.get()) for key, var in variables.items()})

    def prepare() -> None:
        try:
            job_id = commands.prepare_squad(svc, selected())
            set_status(svc, f"Prepare squad queued ({job_id[:8]}…). Open Team Management or return to the Career hub.")
        except Exception as exc:
            set_status(svc, str(exc))
        refresh()

    prepare_btn = button(action, "Prepare squad", prepare, kind="primary", icon="whistle",
                         width=200, height=theme.BTN_LG + 6)
    prepare_btn.pack(side="left")
    injury_note = muted_label(action, "", height=0, size=theme.T_SMALL)
    injury_note.pack(side="left", padx=theme.SP4)

    options = ctk.CTkFrame(hero, fg_color=theme.ROW_ALT, corner_radius=theme.R_MD)
    option_header = ctk.CTkFrame(hero, fg_color="transparent")
    option_header.pack(fill="x", padx=theme.SP5, pady=(0, theme.SP4))
    summary = muted_label(option_header, "", height=0, size=theme.T_SMALL)
    summary.pack(side="left", fill="x", expand=True)

    def toggle_routine() -> None:
        view["routine_open"] = not view.get("routine_open", False)
        if view["routine_open"]:
            options.pack(fill="x", padx=theme.SP5, pady=(0, theme.SP5))
            receipt.pack_forget()
        else:
            options.pack_forget()
            receipt.pack(fill="x", before=footer)
        adjust.configure(text="Done" if view["routine_open"] else "Adjust routine")

    adjust = button(option_header, "Adjust routine", toggle_routine, kind="ghost", width=124,
                    height=theme.BTN_SM)
    adjust.pack(side="right")

    def changed() -> None:
        commands.save_routine(svc, selected())
        refresh()

    checks = ctk.CTkFrame(options, fg_color="transparent")
    checks.pack(fill="x", padx=theme.SP4, pady=(theme.SP4, theme.SP2))
    for index, (key, label, _value) in enumerate(TARGETS):
        checks.grid_columnconfigure(index, weight=1, uniform="matchday-options")
        ctk.CTkCheckBox(checks, text=label, variable=variables[key], command=changed,
                        width=100, checkbox_width=18, checkbox_height=18).grid(row=0, column=index, sticky="w")
    ctk.CTkCheckBox(options, text="Recover injured squad players", variable=variables["injuries"],
                    command=changed, checkbox_width=18, checkbox_height=18).pack(
        anchor="w", padx=theme.SP4, pady=(theme.SP1, theme.SP2))
    muted_label(options, "Optional recovery clears current squad injuries. Your routine is remembered.",
                height=0, size=theme.T_CAPTION).pack(anchor="w", padx=theme.SP4, pady=(0, theme.SP4))
    if view.get("routine_open"):
        options.pack(fill="x", padx=theme.SP5, pady=(0, theme.SP5))
        adjust.configure(text="Done")

    reason_label = muted_label(root, "", height=0, size=theme.T_SMALL, color=theme.WARNING,
                               justify="left", wraplength=680)
    reason_label.pack(anchor="w", pady=(0, theme.SP3))
    receipt = panel(root, border=True)
    receipt.pack(fill="x")
    receipt_body = ctk.CTkFrame(receipt, fg_color="transparent")
    receipt_body.pack(fill="x", padx=theme.SP5, pady=theme.SP4)
    top = ctk.CTkFrame(receipt_body, fg_color="transparent")
    top.pack(fill="x")
    section_title(top, "Last preparation").pack(side="left")
    timestamp = muted_label(top, "", height=0, size=theme.T_CAPTION)
    timestamp.pack(side="right")
    result_title = text_label(receipt_body, "", height=0, size=theme.T_HEADING, bold=True)
    result_title.pack(anchor="w", pady=(theme.SP2, 2))
    result_detail = muted_label(receipt_body, "", height=0, size=theme.T_SMALL, wraplength=700,
                                justify="left")
    result_detail.pack(anchor="w")
    result_steps = muted_label(receipt_body, "", height=0, size=theme.T_CAPTION, wraplength=700,
                               justify="left")
    result_steps.pack(anchor="w", pady=(theme.SP2, 0))
    history_btn = button(receipt_body, "View result in Activity",
                         lambda: svc.ui.open_activity() if svc.ui else None,
                         kind="ghost", icon="queue", height=theme.BTN_SM)

    if view.get("routine_open"):
        receipt.pack_forget()

    footer = ctk.CTkFrame(root, fg_color="transparent")
    footer.pack(fill="x", pady=(theme.SP4, 0))
    muted_label(footer, "Before you play", height=0, size=theme.T_SMALL).pack(
        side="left", padx=(0, theme.SP3))
    button(footer, "Open Club", lambda: svc.ui.navigate("club") if svc.ui else None,
           kind="ghost", icon="squad", height=theme.BTN_SM).pack(side="left", padx=(0, theme.SP2))
    button(footer, "Review injuries", lambda: svc.ui.navigate("injury") if svc.ui else None,
           kind="ghost", icon="medical", height=theme.BTN_SM).pack(side="left")

    def refresh() -> None:
        routine = selected()
        for key, _label, value in TARGETS:
            enabled = getattr(routine, key)
            target_labels[key].configure(text=str(value) if enabled else "Off",
                                         text_color=theme.TEXT if enabled else theme.MUTED_DIM)
        enabled_count = sum(getattr(routine, key) for key, _label, _value in TARGETS) + int(routine.injuries)
        summary.configure(text=f"{enabled_count} step{'s' if enabled_count != 1 else ''} in your routine  ·  one click")
        injury_note.configure(text="Includes injury recovery" if routine.injuries else "")
        latest = commands.last_preparation(svc)
        reason = commands.disabled_reason(svc, routine)
        prepare_btn.configure(text="Preparation queued…" if latest["busy"] else "Prepare squad")
        set_disabled(prepare_btn, reason)
        reason_label.configure(text=reason or "After preparing, open Team Management or return to the Career hub so FC 26 applies it.",
                               text_color=theme.WARNING if reason else theme.MUTED)
        result_title.configure(text=latest["title"], text_color=theme.tone_fg(latest["tone"]))
        result_detail.configure(text=latest["detail"])
        result_steps.configure(text="  ·  ".join(latest["steps"]))
        stamp = str(latest.get("submitted") or "")
        timestamp.configure(text=stamp[:16].replace("T", " ") + " UTC" if stamp else "")
        if latest["job_id"]:
            if not history_btn.winfo_manager():
                history_btn.pack(anchor="w", pady=(theme.SP3, 0))
        else:
            history_btn.pack_forget()
        state = svc.store.snapshot()
        hint = trusted_cached_teamid(state, now=svc.clock.now())
        if scope_label is not None:
            team = ""
            if state.squad.players:
                team = str(state.squad.players[0].get("teamname") or "")
            scope_label.configure(text=(
                f"{team + '  ·  ' if team else ''}{len(state.squad.players)} players in your current Career squad"
                if hint else "Your current Career squad"
            ))

    def responsive(event: Any) -> None:
        if event.widget is root:
            wrap = max(280, event.width - 80)
            result_detail.configure(wraplength=wrap)
            result_steps.configure(wraplength=wrap)
            reason_label.configure(wraplength=wrap)

    root.bind("<Configure>", responsive, add="+")
    root.refresh_automation_queue = refresh  # stable shell soft-refresh hook
    root.matchday_prepare_button = prepare_btn
    root.matchday_variables = variables
    root.matchday_adjust_button = adjust
    refresh()
    return root
