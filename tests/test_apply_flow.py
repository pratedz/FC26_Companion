"""Unit tests for shared GUI apply presentation (no CTk required)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Tuple

from src.apply_service import ApplyResult
from src.ui import apply_flow


class _FakeApp:
    def __init__(self) -> None:
        self._apply_busy = False
        self.btn_text = "Apply to game"
        self.statuses: List[Tuple[str, dict]] = []
        self._refreshed = 0

    def _set_apply_btn_text(self, text: str) -> None:
        self.btn_text = text

    def _set_apply_status(self, msg: str, **kwargs: Any) -> None:
        self.statuses.append((msg, kwargs))

    def _refresh_sync_chrome(self) -> None:
        self._refreshed += 1

    def after(self, _ms: int, fn: Any) -> None:
        fn()


def test_present_applied_detailed() -> None:
    app = _FakeApp()
    app._apply_busy = True
    r = ApplyResult(
        applied=True,
        queued=True,
        live=True,
        reason="ok",
        queue_file=r"C:\tmp\job.lua",
        last_result="OK",
        outcome="applied",
        detail="Messi",
    )
    apply_flow.present_apply_result(app, r, "Messi → 158023", detailed=True)
    assert app._apply_busy is False
    assert app.btn_text == "Apply to game"
    assert app._refreshed == 1
    assert "APPLIED" in app.statuses[-1][0]
    assert app.statuses[-1][1].get("state") == "ok"


def test_present_simple_queued_live() -> None:
    app = _FakeApp()
    r = ApplyResult(
        applied=False,
        queued=True,
        live=True,
        reason="timeout",
        outcome="queued_live",
    )
    apply_flow.present_apply_result(app, r, "boost", detailed=False)
    assert "Queued LIVE" in app.statuses[-1][0]
    assert app.statuses[-1][1].get("state") == "busy"


def test_run_apply_job_busy_short_circuits() -> None:
    app = _FakeApp()
    app._apply_busy = True
    called = {"n": 0}

    def job(_tick: Any) -> ApplyResult:
        called["n"] += 1
        return ApplyResult(True, True, True, "ok", outcome="applied")

    ok = apply_flow.run_apply_job(app, job, detail="x")
    assert ok is False
    assert called["n"] == 0
    assert "BUSY" in app.statuses[-1][0]
