"""Shared GUI apply orchestration — one path for status + background jobs.

PremiumApp must expose:
  _apply_busy, _set_apply_status, _set_apply_btn_text, after,
  optional _refresh_sync_chrome
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .. import apply_service

OnTick = Optional[Callable[[float, str], None]]
ApplyJob = Callable[[OnTick], apply_service.ApplyResult]


def present_apply_result(
    app: Any,
    result: apply_service.ApplyResult,
    detail: str = "",
    *,
    copy_bridge_on_wait: bool = True,
    detailed: bool = True,
) -> None:
    """Map ApplyResult → dock status (single presentation policy)."""
    app._apply_busy = False
    try:
        from . import apply_chrome as _ac

        _ac.update_dock_for_active_tab(app)
    except Exception:
        app._set_apply_btn_text("Apply to game")
    try:
        refresh = getattr(app, "_refresh_sync_chrome", None)
        if callable(refresh):
            refresh()
    except Exception:
        pass

    qname = Path(result.queue_file).name if result.queue_file else ""
    lr = result.last_result or ""
    job_line = ""
    try:
        job_line = apply_service.format_result_with_job_status(result)
    except Exception:
        job_line = ""
    if not job_line and result.reason and (
        "path=" in result.reason or "id=" in result.reason or "add_to_team" in result.reason
    ):
        job_line = result.reason
    extra = f"\n{job_line}" if job_line else ""

    if not detailed:
        if result.applied:
            app._set_apply_status(
                f"✓ Applied · {detail}{extra}", prog=1.0, state="ok", step=4
            )
        elif result.outcome == "queued_live":
            app._set_apply_status(
                f"Queued LIVE · {result.reason}\n{detail}",
                prog=0.7,
                state="busy",
                step=3,
            )
        else:
            app._set_apply_status(
                f"✗ {result.reason or result.outcome}\n{detail}{extra}",
                prog=0,
                state="err",
                step=0,
            )
        return

    if result.outcome == "applied":
        path_id = f"\n{job_line}" if job_line else ""
        app._set_apply_status(
            f"4/4 ✓ APPLIED in game\n{detail}{path_id}\n"
            f"Result: {lr or 'OK'}\n→ Re-open the player card / squad in FC26.",
            prog=1.0,
            state="ok",
            step=4,
        )
        return
    if result.outcome == "queued_live":
        if copy_bridge_on_wait:
            try:
                # Prefer short ForceDrain when worker already LIVE
                from .. import product as _product

                _product.force_turbo_drain_signal()
            except Exception:
                try:
                    apply_service.copy_bridge_to_clipboard()
                except Exception:
                    pass
        app._set_apply_status(
            f"4/4 ⏳ STILL QUEUED (pulse LIVE)\n"
            f"{result.reason}\n"
            f"Drain script COPIED · LE → Lua Engine → Ctrl+V → Execute\n"
            f"(or open Career hub / advance a day so events drain)\n"
            f"Last LE: {lr or '(none)'}\nFile: {qname}",
            prog=0.5,
            state="warn",
            step=3,
        )
        return
    if result.outcome == "error":
        path_id = f"\n{job_line}" if job_line else ""
        app._set_apply_status(
            f"4/4 ✗ ERROR\n{result.reason}{path_id}\n{detail}",
            prog=0,
            state="err",
            step=0,
        )
        return
    if copy_bridge_on_wait:
        try:
            apply_service.copy_bridge_to_clipboard()
        except Exception:
            pass
    app._set_apply_status(
        f"4/4 ✗ WORKER OFF — job not drained\n"
        f"{result.reason}\n"
        f"Bridge COPIED · How to arm / Force drain\n"
        f"Then Apply again. File: {qname}",
        prog=0,
        state="err",
        step=2,
    )


def _make_ui_tick(app: Any, detail: str) -> OnTick:
    """Throttle status updates so CTk is not flooded on ~0.06s poll."""
    app._apply_tick_last_ui = 0.0
    app._apply_tick_btn_sec = -1

    def on_tick(elapsed: float, msg: str) -> None:
        now = time.monotonic()
        if now - float(getattr(app, "_apply_tick_last_ui", 0.0) or 0.0) < 0.45:
            return
        app._apply_tick_last_ui = now

        def ui() -> None:
            app._set_apply_status(
                f"3/4 {msg}\n{detail}",
                prog=0.35 + min(0.5, elapsed / 45.0 * 0.5),
                state="busy",
                step=3,
                paint=False,
            )
            sec = int(elapsed)
            if getattr(app, "_apply_tick_btn_sec", -1) != sec:
                app._apply_tick_btn_sec = sec
                app._set_apply_btn_text(f"Wait {sec}s…")

        app.after(0, ui)

    return on_tick


def run_apply_job(
    app: Any,
    job: ApplyJob,
    *,
    detail: str,
    busy_msg: str = "Applying…",
    with_ticks: bool = False,
    detailed: bool = True,
    copy_bridge_on_wait: bool = True,
    on_result: Optional[Callable[[apply_service.ApplyResult], None]] = None,
) -> bool:
    """Start one background apply. Returns False if already busy.

    on_result: optional UI callback after present_apply_result (e.g. Add team banner).
    """
    if getattr(app, "_apply_busy", False):
        app._set_apply_status(
            "BUSY · already applying — wait a few seconds",
            prog=0,
            state="warn",
            step=3,
        )
        return False

    app._apply_busy = True
    app._set_apply_btn_text("Applying…")
    app._set_apply_status(
        f"{busy_msg}\n{detail}",
        prog=0.12,
        state="busy",
        step=1,
    )
    on_tick = _make_ui_tick(app, detail) if with_ticks else None

    def work() -> None:
        try:
            result = job(on_tick)

            def done() -> None:
                present_apply_result(
                    app,
                    result,
                    detail,
                    copy_bridge_on_wait=copy_bridge_on_wait,
                    detailed=detailed,
                )
                if on_result is not None:
                    try:
                        on_result(result)
                    except Exception:
                        pass

            app.after(0, done)
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)

            def err() -> None:
                app._apply_busy = False
                app._set_apply_btn_text("Apply to game")
                app._set_apply_status(
                    f"✗ Apply error · {err_msg}", prog=0, state="err", step=0
                )

            app.after(0, err)

    threading.Thread(target=work, daemon=True).start()
    return True


def run_background(app: Any, work: Callable[[], None]) -> None:
    """Fire-and-forget daemon thread (packs, batch, etc.)."""
    threading.Thread(target=work, daemon=True).start()
