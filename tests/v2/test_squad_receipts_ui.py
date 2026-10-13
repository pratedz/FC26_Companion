from types import SimpleNamespace

import pytest

from companion.app import events as E
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.ui.surfaces.squad_receipts import receipt_scope_reason, receipt_summary


def test_receipt_does_not_claim_verified_for_queued_or_partial_result():
    assert "Read-back pending" in receipt_summary({"status": "queued", "verification": "pending"})
    assert "Read-back failed" in receipt_summary({"status": "verification_failed", "verification": "failed"})
    assert "Read-back verified" in receipt_summary({"status": "applied", "verification": "verified"})


def test_receipt_actions_explain_wrong_save_club_and_stale_roster(tmp_path):
    svc = Services(paths=TempAppPaths(tmp_path), clock=FakeClock(), executor=InlineExecutor(),
                   store=Store(), transport=FakeTransport())
    receipt = {"source": {"save_uid": "A", "teamid": 11}}
    assert "original Career save" in receipt_scope_reason(svc, receipt)
    svc.store.dispatch(E.SquadSynced(save_uid="A", teamid=12, players=(), taken=svc.clock.now()))
    assert "original club" in receipt_scope_reason(svc, receipt)
    svc.store.dispatch(E.SquadSynced(save_uid="A", teamid=11, players=(), taken=svc.clock.now()))
    assert receipt_scope_reason(svc, receipt) == ""


@pytest.mark.gui
def test_real_activity_receipt_cards_and_details_render_without_live_jobs(tmp_path, monkeypatch):
    import customtkinter as ctk
    from companion.app.commands import squad_plan
    from companion.ui.surfaces import squad_receipts
    receipt = {"plan_id": "test-receipt", "label": "Squad improvement", "status": "failed",
               "parts_applied": 1, "parts_total": 2, "players_applied": 1, "players_requested": 2,
               "fields_applied": 1, "verification": "pending", "source": {"save_uid": "A", "teamid": 11},
               "parts": [{"job_id": "first", "status": "applied"}, {"job_id": "second", "status": "failed"}],
               "rows": [{"playerid": 1, "name": "Test player", "field": "skillmoves", "before": 2,
                         "planned": 4, "readback": None, "verified": False}]}
    monkeypatch.setattr(squad_plan, "list_squad_plans", lambda svc: [receipt])
    monkeypatch.setattr(squad_plan, "squad_plan_receipt", lambda svc, pid: receipt)
    svc = Services(paths=TempAppPaths(tmp_path), clock=FakeClock(), executor=InlineExecutor(),
                   store=Store(), transport=FakeTransport())
    svc.store.dispatch(E.SquadSynced(save_uid="A", teamid=11, players=({"playerid": 1},), taken=svc.clock.now()))
    root = ctk.CTk()
    root.withdraw()
    windows = []
    real = ctk.CTkToplevel

    def hidden(*args, **kwargs):
        win = real(*args, **kwargs)
        win.withdraw()
        windows.append(win)
        return win

    monkeypatch.setattr(ctk, "CTkToplevel", hidden)
    try:
        host = ctk.CTkScrollableFrame(root)
        squad_receipts.render_receipts(host, svc)
        widgets = []
        def descend(widget):
            for child in widget.winfo_children():
                widgets.append(child)
                descend(child)
        descend(host)
        buttons = {widget.cget("text") for widget in widgets if isinstance(widget, ctk.CTkButton)}
        assert {"Player results", "Resume remaining", "Undo plan", "Open Club"} <= buttons
        squad_receipts.open_receipt(svc, "test-receipt")
        widgets.clear()
        descend(windows[0])
        texts = [widget.cget("text") for widget in widgets if isinstance(widget, ctk.CTkLabel)]
        assert any("Before 3★ → Planned 5★" in text and "Awaiting verification" in text for text in texts)
        assert svc.transport.submitted == []
    finally:
        for win in windows:
            win.destroy()
        root.destroy()
