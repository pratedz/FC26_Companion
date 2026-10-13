"""Matchday: one preparation action, a quiet routine, and its real receipt."""
from __future__ import annotations

from typing import Any

from ...app.commands import matchday as commands
from ...domain.automation_guide import trusted_cached_teamid
from ...domain.matchday import Routine, TARGETS
from .. import theme
from ..widgets.primitives import button, muted_label, panel, set_disabled, text_label
from ._common import section_header, set_status, surface_root

import customtkinter as ctk


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    view = vm if isinstance(vm, dict) else {}
    routine = commands.load_routine(svc)
    variables = {key: ctk.BooleanVar(master=root, value=value) for key, value in routine.to_pref().items()}

    section_header(root, "Matchday")
    hero = panel(root, border=True, corner_radius=18)
    hero.pack(fill="x", pady=(4, 16))
    body = ctk.CTkFrame(hero, fg_color="transparent")
    body.pack(fill="x", padx=24, pady=(20, 16))
    scope_label = muted_label(body, "CURRENT CAREER SQUAD", height=0, size=11, color=theme.ACCENT)
    scope_label.pack(anchor="w")
    text_label(body, "Ready for kick-off?", height=0, size=28, bold=True).pack(anchor="w", pady=(6, 4))
    muted_label(body, "Give your squad a boost before the next match.", height=0, size=13).pack(anchor="w")

    targets = ctk.CTkFrame(body, fg_color="transparent")
    targets.pack(fill="x", pady=(16, 14))
    target_labels = {}
    for index, (key, label, value) in enumerate(TARGETS):
        targets.grid_columnconfigure(index, weight=1, uniform="matchday-targets")
        tile = ctk.CTkFrame(targets, fg_color=theme.CARD, corner_radius=10)
        tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
        muted_label(tile, label, height=0, size=11).pack(anchor="w", padx=14, pady=(12, 2))
        target_labels[key] = text_label(tile, str(value), height=0, size=23, bold=True, color=theme.TEXT)
        target_labels[key].pack(anchor="w", padx=14, pady=(0, 10))
    muted_label(body, "Your chosen targets · applied when you prepare", height=0, size=10).pack(anchor="w", pady=(0, 16))
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

    prepare_btn = button(action, "Prepare squad", prepare, kind="primary", width=200, height=44)
    prepare_btn.pack(side="left")
    injury_note = muted_label(action, "", height=0, size=11)
    injury_note.pack(side="left", padx=16)

    options = ctk.CTkFrame(hero, fg_color=theme.ROW_ALT, corner_radius=12)
    option_header = ctk.CTkFrame(hero, fg_color="transparent")
    option_header.pack(fill="x", padx=24, pady=(0, 14))
    summary = muted_label(option_header, "", height=0, size=11)
    summary.pack(side="left", fill="x", expand=True)

    def toggle_routine() -> None:
        view["routine_open"] = not view.get("routine_open", False)
        if view["routine_open"]:
            options.pack(fill="x", padx=24, pady=(0, 20))
            receipt.pack_forget()
        else:
            options.pack_forget()
            receipt.pack(fill="x", before=footer)
        adjust.configure(text="Done" if view["routine_open"] else "Adjust routine")

    adjust = button(option_header, "Adjust routine", toggle_routine, kind="ghost", width=118, height=28)
    adjust.pack(side="right")

    def changed() -> None:
        commands.save_routine(svc, selected())
        refresh()

    checks = ctk.CTkFrame(options, fg_color="transparent")
    checks.pack(fill="x", padx=14, pady=(14, 8))
    for index, (key, label, _value) in enumerate(TARGETS):
        checks.grid_columnconfigure(index, weight=1, uniform="matchday-options")
        ctk.CTkCheckBox(checks, text=label, variable=variables[key], command=changed,
                        width=100, checkbox_width=18, checkbox_height=18,
                        fg_color=theme.ACCENT, hover_color=theme.ACCENT, text_color=theme.TEXT,
                        border_color=theme.MUTED_DIM).grid(row=0, column=index, sticky="w")
    ctk.CTkCheckBox(options, text="Recover injured squad players", variable=variables["injuries"],
                    command=changed, checkbox_width=18, checkbox_height=18, fg_color=theme.ACCENT,
                    hover_color=theme.ACCENT, text_color=theme.TEXT,
                    border_color=theme.MUTED_DIM).pack(anchor="w", padx=14, pady=(4, 8))
    muted_label(options, "Optional recovery clears current squad injuries. Your routine is remembered.",
                height=0, size=10).pack(anchor="w", padx=14, pady=(0, 14))
    if view.get("routine_open"):
        options.pack(fill="x", padx=24, pady=(0, 20))
        adjust.configure(text="Done")

    reason_label = muted_label(root, "", height=0, size=11, color=theme.WARNING, justify="left", wraplength=680)
    reason_label.pack(anchor="w", pady=(0, 12))
    receipt = panel(root, border=True, corner_radius=12)
    receipt.pack(fill="x")
    receipt_body = ctk.CTkFrame(receipt, fg_color="transparent")
    receipt_body.pack(fill="x", padx=20, pady=16)
    top = ctk.CTkFrame(receipt_body, fg_color="transparent")
    top.pack(fill="x")
    muted_label(top, "LAST PREPARATION", height=0, size=10).pack(side="left")
    timestamp = muted_label(top, "", height=0, size=10)
    timestamp.pack(side="right")
    result_title = text_label(receipt_body, "", height=0, size=16, bold=True)
    result_title.pack(anchor="w", pady=(6, 3))
    result_detail = muted_label(receipt_body, "", height=0, size=11, wraplength=700, justify="left")
    result_detail.pack(anchor="w")
    result_steps = muted_label(receipt_body, "", height=0, size=10, wraplength=700, justify="left")
    result_steps.pack(anchor="w", pady=(8, 0))
    history_btn = button(receipt_body, "View result in Activity", lambda: svc.ui.open_activity() if svc.ui else None,
                         kind="ghost", width=160, height=28)

    if view.get("routine_open"):
        receipt.pack_forget()

    footer = ctk.CTkFrame(root, fg_color="transparent")
    footer.pack(fill="x", pady=(16, 0))
    muted_label(footer, "Before you play", height=0, size=11).pack(side="left", padx=(0, 12))
    button(footer, "Open Club", lambda: svc.ui.navigate("club") if svc.ui else None,
           kind="ghost", width=96, height=28).pack(side="left", padx=(0, 8))
    button(footer, "Review injuries", lambda: svc.ui.navigate("injury") if svc.ui else None,
           kind="ghost", width=112, height=28).pack(side="left")

    def refresh() -> None:
        routine = selected()
        for key, _label, value in TARGETS:
            enabled = getattr(routine, key)
            target_labels[key].configure(text=str(value) if enabled else "Off",
                                         text_color=theme.TEXT if enabled else theme.MUTED_DIM)
        enabled_count = sum(getattr(routine, key) for key, _label, _value in TARGETS) + int(routine.injuries)
        summary.configure(text=f"{enabled_count} step{'s' if enabled_count != 1 else ''} in your routine · one click")
        injury_note.configure(text="Includes injury recovery" if routine.injuries else "")
        latest = commands.last_preparation(svc)
        reason = commands.disabled_reason(svc, routine)
        prepare_btn.configure(text="Preparation queued…" if latest["busy"] else "Prepare squad")
        set_disabled(prepare_btn, reason)
        reason_label.configure(text=reason or "Open Team Management or return to the Career hub after preparing.",
                               text_color=theme.WARNING if reason else theme.MUTED)
        result_title.configure(text=latest["title"], text_color=theme.tone_fg(latest["tone"]))
        result_detail.configure(text=latest["detail"])
        result_steps.configure(text=" · ".join(latest["steps"]))
        stamp = str(latest.get("submitted") or "")
        timestamp.configure(text=stamp[:16].replace("T", " ") + " UTC" if stamp else "")
        if latest["job_id"]:
            if not history_btn.winfo_manager():
                history_btn.pack(anchor="w", pady=(10, 0))
        else:
            history_btn.pack_forget()
        state = svc.store.snapshot()
        hint = trusted_cached_teamid(state, now=svc.clock.now())
        scope_label.configure(text=(f"CURRENT CAREER SQUAD · {len(state.squad.players)} PLAYERS" if hint
                                   else "CURRENT CAREER SQUAD"))

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
