"""'Fix shirts' after the game refused shirt writes.

Refresh the squad, re-plan only the refused shirts against the fresh club
shirt map (no AI, nobody else moves), show the result, then queue one small
shirt-only job. The failed operation is never replayed as it was.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from .. import theme
from ..widgets.primitives import button, ensure_ctk, muted_label, text_label
from ._common import set_status


def shirt_conflict_text(conflicts: Sequence[Mapping[str, Any]]) -> str:
    """One plain sentence for the shirts a plan could not write."""
    items = list(conflicts)
    shown = ", ".join(f"{item.get('name') or item.get('playerid')} #{item.get('number')}" for item in items[:3])
    more = f" and {len(items) - 3} more" if len(items) > 3 else ""
    count = f"{len(items)} shirt number{'s' if len(items) != 1 else ''}"
    return (f"Everything else is applied. {count} could not be written because "
            f"someone already wears them ({shown}{more}). Use Fix shirts.")


def fix_shirts_flow(svc: Any, plan_id: str, win: Any, *, on_result: Callable[[Any], None] | None = None,
                    on_done: Callable[[str], None] | None = None, timeout_s: float = 90.0,
                    confirm: Callable[[Any, Any, Sequence[str]], bool] | None = None) -> None:
    """Refresh the squad, re-plan the refused shirts, confirm, then queue them."""
    from ...app.commands.squad import sync_squad
    from ...app.commands.squad_plan import apply_shirt_fix, plan_shirt_fix

    confirm = confirm or confirm_shirt_fix
    finish = on_done or (lambda message: set_status(svc, message))
    before = svc.store.snapshot().squad.taken
    try:
        sync_squad(svc)
    except Exception as exc:  # noqa: BLE001
        finish(f"Could not read the squad: {exc}")
        return
    set_status(svc, "Reading the squad, then re-planning the refused shirts…")
    deadline = time.monotonic() + timeout_s

    def ready() -> None:
        try:
            plan, notes = plan_shirt_fix(svc, plan_id)
        except Exception as exc:  # noqa: BLE001
            finish(f"Could not fix shirts: {exc}")
            return
        if not plan.jersey_numbers:
            finish(" ".join(notes) or "No shirt number needs to change.")
            return
        if not confirm(win, plan, notes):
            finish("Shirt fix cancelled.")
            return
        try:
            fix_id = apply_shirt_fix(svc, plan_id, plan, on_result=on_result)
        except Exception as exc:  # noqa: BLE001
            finish(f"Could not queue the shirt fix: {exc}")
            return
        finish(f"Shirt fix queued ({len(plan.jersey_numbers)} shirt{'s' if len(plan.jersey_numbers) != 1 else ''}, "
               f"{fix_id[:8]}…).")

    def poll() -> None:
        squad = svc.store.snapshot().squad
        if squad.taken is not None and squad.taken != before and not squad.stale:
            ready()
            return
        if time.monotonic() > deadline:
            finish("The squad read did not finish. Stay in the Career hub, then try Fix shirts again.")
            return
        try:
            win.after(500, poll)
        except Exception:
            finish("The squad read did not finish.")

    try:
        win.after(500, poll)
    except Exception:
        poll()


def confirm_shirt_fix(win: Any, plan: Any, notes: Sequence[str]) -> bool:
    """Show the re-planned shirts before anything is queued."""
    ensure_ctk()
    import customtkinter as ctk

    chosen = {"ok": False}
    dialog = ctk.CTkToplevel(win)
    dialog.title("Fix shirts")
    dialog.configure(fg_color=theme.BG)
    dialog.resizable(False, False)
    try:
        dialog.transient(win)
    except Exception:
        pass
    body = ctk.CTkFrame(dialog, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=(22, 8))
    count = len(plan.jersey_numbers)
    text_label(body, f"Write {count} shirt number{'s' if count != 1 else ''}?", size=18, bold=True).pack(anchor="w")
    names = {member.playerid: member.name for member in plan.members}
    for pid, number in plan.jersey_numbers.items():
        old = plan.shirt_book.get(pid) or "—"
        text_label(body, f"{names.get(pid, plan.shirt_names.get(pid, pid))}: #{old} → #{number}",
                   size=13).pack(anchor="w", pady=(6, 0))
    for note in notes:
        muted_label(body, note, size=11, wraplength=400, justify="left").pack(anchor="w", pady=(4, 0))
    muted_label(body, "Nobody else changes number. Keep Career Mode open while it writes.",
                size=12, wraplength=400, justify="left").pack(anchor="w", pady=(10, 0))
    actions = ctk.CTkFrame(dialog, fg_color="transparent")
    actions.pack(fill="x", padx=24, pady=(14, 22))

    def choose(ok: bool) -> None:
        chosen["ok"] = ok
        try:
            dialog.grab_release()
        except Exception:
            pass
        dialog.destroy()

    confirm = button(actions, "Write shirts", lambda: choose(True), kind="primary", height=38)
    confirm.pack(side="right")
    button(actions, "Cancel", lambda: choose(False), kind="ghost", height=38, width=90).pack(side="right", padx=(0, 8))
    dialog.bind("<Escape>", lambda _event: choose(False))
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    try:
        dialog.after(60, lambda: (dialog.grab_set(), confirm.focus_set()))
    except Exception:
        pass
    win.wait_window(dialog)
    return chosen["ok"]
