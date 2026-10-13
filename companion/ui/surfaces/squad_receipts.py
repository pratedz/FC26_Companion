"""Grouped plan receipts, durable resume and complete undo in Activity."""
from __future__ import annotations

import json
from typing import Any

from .. import theme
from ..widgets.primitives import button, muted_label, panel, text_label
from ._common import set_status


def receipt_summary(receipt: dict[str, Any]) -> str:
    status = str(receipt.get("status") or "pending").replace("_", " ")
    parts = f"{receipt.get('parts_applied', 0)}/{receipt.get('parts_total', 0)} parts"
    players = f"{receipt.get('players_applied', 0)}/{receipt.get('players_requested', 0)} players"
    verification = str(receipt.get("verification") or "pending")
    checks = "Read-back verified" if verification == "verified" else f"Read-back {verification}"
    return f"{status.capitalize()} · {parts} · {players} · {checks}"


def receipt_scope_reason(svc: Any, receipt: dict[str, Any]) -> str:
    squad = svc.store.snapshot().squad
    source = receipt.get("source") or {}
    save = source.get("save_uid") or source.get("source_save_uid") or receipt.get("source_save_uid")
    team = source.get("teamid") or source.get("source_teamid") or receipt.get("source_teamid")
    if save and str(save) != str(squad.save_uid):
        return "Open this plan's original Career save."
    if team is not None and str(team) != str(squad.teamid):
        return "Open this plan's original club."
    if squad.stale:
        return "Refresh Club to revalidate the affected players."
    return ""


def render_receipts(host: Any, svc: Any) -> None:
    from ...app.commands.squad_plan import list_squad_plans
    try:
        receipts = list_squad_plans(svc)[:20]
    except Exception as exc:
        receipts = [{"status": "unavailable", "error": str(exc)}]
    squad = svc.store.snapshot().squad
    key = json.dumps(receipts, default=str, sort_keys=True) + str((squad.save_uid, squad.teamid, squad.taken, squad.stale))
    if getattr(host, "_plan_receipt_key", None) == key:
        return
    host._plan_receipt_key = key
    for child in host.winfo_children():
        child.destroy()
    if not receipts:
        muted_label(host, "No squad plans yet. Completed and interrupted plans appear here, including after a restart.",
                    size=12, wraplength=520).pack(anchor="w", pady=theme.SP3)
        return
    for receipt in receipts:
        card = panel(host, level=1)
        card.pack(fill="x", pady=(0, theme.SP2))
        text_label(card, receipt.get("label") or "Squad plan", size=13, bold=True).pack(
            anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
        muted_label(card, receipt_summary(receipt), size=11, wraplength=540).pack(anchor="w", padx=theme.SP3)
        if receipt.get("error"):
            muted_label(card, str(receipt["error"]), size=11, color=theme.WARNING, wraplength=540).pack(
                anchor="w", padx=theme.SP3)
        plan_id = str(receipt.get("plan_id") or "")
        if not plan_id:
            continue
        row = _frame(card)
        row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
        button(row, "Player results", lambda rid=plan_id: open_receipt(svc, rid),
               kind="secondary", height=theme.BTN_SM).pack(side="left")
        reason = receipt_scope_reason(svc, receipt)
        remaining = int(receipt.get("parts_applied") or 0) < int(receipt.get("parts_total") or 0)
        if remaining and not receipt.get("undo_plan_id"):
            button(row, "Resume remaining", lambda rid=plan_id: _resume(svc, rid),
                   disabled_reason=reason, kind="primary", height=theme.BTN_SM).pack(side="left", padx=theme.SP1)
        elif receipt.get("verification") == "failed" and not receipt.get("undo_plan_id"):
            button(row, "Verify again", lambda rid=plan_id: _resume(svc, rid),
                   disabled_reason=reason, kind="secondary", height=theme.BTN_SM).pack(side="left", padx=theme.SP1)
        if int(receipt.get("fields_applied") or 0) > 0 and not receipt.get("undo_plan_id"):
            button(row, "Undo plan", lambda rid=plan_id: _undo(svc, rid),
                   disabled_reason=reason, kind="danger", height=theme.BTN_SM).pack(side="left", padx=theme.SP1)
        button(row, "Open Club", lambda rec=receipt: _open_club(svc, rec),
               kind="ghost", height=theme.BTN_SM).pack(side="right")
        if reason:
            muted_label(card, reason, size=10, wraplength=540).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))


def _frame(parent: Any) -> Any:
    import customtkinter as ctk
    return ctk.CTkFrame(parent, fg_color="transparent")


def _resume(svc: Any, plan_id: str) -> None:
    from ...app.commands.squad_plan import resume_squad_plan
    try:
        resume_squad_plan(svc, plan_id)
        set_status(svc, "Following the plan's remaining work and verification. Successful writes will be kept.")
    except Exception as exc:
        set_status(svc, f"Could not resume plan: {exc}", tone="error")


def _undo(svc: Any, plan_id: str) -> None:
    import customtkinter as ctk
    from ...app.commands.squad_plan import undo_squad_plan
    dialog = ctk.CTkInputDialog(text="Restore the verified pre-plan values for all applied parts? Type UNDO.",
                              title="Undo squad plan")
    if (dialog.get_input() or "").strip() != "UNDO":
        return
    try:
        undo_squad_plan(svc, plan_id, confirm_token="UNDO")
        set_status(svc, "Complete plan undo queued. Follow its progress in Squad plans.")
    except Exception as exc:
        set_status(svc, f"Could not undo plan: {exc}", tone="error")


def _open_club(svc: Any, receipt: dict[str, Any]) -> None:
    source = receipt.get("source") or {}
    ids = tuple(dict.fromkeys(int(row["playerid"]) for row in receipt.get("rows") or () if row.get("playerid")))
    opener = getattr(svc.ui, "open_club", None)
    if callable(opener):
        opener(ids, source.get("save_uid") or source.get("source_save_uid") or "",
               source.get("teamid") or source.get("source_teamid"))
    else:
        svc.ui.navigate("club")


def open_receipt(svc: Any, plan_id: str) -> None:
    import customtkinter as ctk
    from ...app.commands.squad_plan import squad_plan_receipt
    try:
        receipt = squad_plan_receipt(svc, plan_id)
    except Exception as exc:
        set_status(svc, f"Could not open receipt: {exc}", tone="error")
        return
    win = ctk.CTkToplevel()
    win.title("Squad plan results")
    win.geometry("740x600")
    win.minsize(620, 420)
    win.configure(fg_color=theme.BG)
    text_label(win, receipt_summary(receipt), size=13, bold=True, wraplength=690).pack(
        anchor="w", padx=theme.SP3, pady=theme.SP3)
    scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
    scroll.pack(fill="both", expand=True, padx=theme.SP3)
    live_changes = receipt.get("live_changes") or ()
    if live_changes:
        text_label(scroll, "Changed in Career since you built this plan", size=12, bold=True).pack(
            anchor="w", pady=(0, theme.SP1))
        for note in live_changes:
            muted_label(scroll, str(note), size=11, wraplength=660).pack(anchor="w")
        text_label(scroll, "Planned values", size=12, bold=True).pack(anchor="w", pady=(theme.SP3, 0))
    rows = receipt.get("rows") or ()
    for row in rows:
        from ...domain.player import FIELD_SPECS
        spec = FIELD_SPECS.get(str(row.get("field")))
        field_label = spec.label if spec else "Shirt number" if row.get("field") == "jerseynumber" else str(row.get("field"))
        label = f"{row.get('name') or row.get('playerid')} · {field_label}"
        text_label(scroll, label, size=11, bold=True).pack(anchor="w", pady=(theme.SP2, 0))
        from .planner_review import field_value_text
        before = field_value_text(str(row.get("field")), row.get("before"))
        planned = field_value_text(str(row.get("field")), row.get("planned"))
        actual = field_value_text(str(row.get("field")), row.get("readback"))
        status = "Verified" if row.get("verified") else "Awaiting verification" if row.get("readback") is None else "Mismatch"
        muted_label(scroll, f"Before {before} → Planned {planned} · Read-back {actual} · {status}",
                    size=11, wraplength=660).pack(anchor="w")
    for error in receipt.get("verification_errors") or ():
        muted_label(scroll, str(error), size=11, color=theme.WARNING, wraplength=660).pack(anchor="w", pady=theme.SP1)
    footer = _frame(win)
    footer.pack(fill="x", padx=theme.SP3, pady=theme.SP3)
    button(footer, "Open Club", lambda: _open_club(svc, receipt), kind="secondary").pack(side="left")
    button(footer, "Close", win.destroy, kind="ghost").pack(side="right")
