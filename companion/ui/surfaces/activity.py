"""Activity drawer — job history and undo surface.

Refresh rebuilds from the current store. Undo requires typing UNDO.
"""

from __future__ import annotations

from typing import Any

from ...app import events as E
from ...app.commands import doctor as doctor_cmd
from ...app.presenters import jobs_view, liveness_view
from .. import theme
from ..nav import segmented_rail
from ..widgets import states
from ..widgets.primitives import button, eyebrow, ensure_ctk, muted_label, panel, pill, text_label
from ..widgets.table import Column, DataGrid, TableModel
from ._common import section_header, set_status, surface_root

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def confirm_undo(typed: str) -> bool:
    """Pure gate — UI and tests share this."""
    return (typed or "").strip() == "UNDO"


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    # Clear residual widgets if shell reuses the host (rebuild path).
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass

    view = jobs_view(svc.store.snapshot())
    view_model = vm if isinstance(vm, dict) else {}
    view_model.setdefault("activity_segment", "queue")

    section_header(
        root,
        "Activity",
        subtitle="In-flight jobs and recent history.",
    )

    history_model = TableModel(
        columns=(
            Column("status", "STATUS", width=72),
            Column("label", "JOB", width=160, stretch=True),
            Column("detail", "DETAIL", width=220),
            Column("age", "WHEN", width=88),
        ),
        rows=(),
        key_field="_key",
    )
    queue_page = ctk.CTkFrame(root, fg_color="transparent")
    history_page = ctk.CTkFrame(root, fg_color="transparent")
    health_page = ctk.CTkFrame(root, fg_color="transparent")
    inflight_host = ctk.CTkFrame(queue_page, fg_color="transparent")
    history_host = ctk.CTkFrame(history_page, fg_color="transparent")
    action_row = ctk.CTkFrame(root, fg_color="transparent")
    slots: dict[str, Any] = {
        "empty": None,
        "inflight_host": inflight_host,
        "history_host": history_host,
        "history_label": None,
        "history_grid": None,
        "packed_inflight": False,
        "packed_history": False,
        "segment": str(view_model.get("activity_segment") or "queue"),
        "pages": {"queue": queue_page, "history": history_page, "health": health_page},
        "action_row": action_row,
        "health_host": health_page,
    }
    view_model["history_model"] = history_model

    def refresh_jobs() -> None:
        _render_body(svc, jobs_view(svc.store.snapshot()), history_model, slots)

    def rebuild() -> None:
        refresh_jobs()
        set_status(svc, "Activity refreshed.")

    def show_segment(key: str) -> None:
        view_model["activity_segment"] = key
        slots["segment"] = key
        for name, page in slots["pages"].items():
            try:
                page.pack_forget()
            except Exception:
                pass
        slots["pages"][key].pack(fill="both", expand=True)
        rail.set_active(key)
        _sync_action(svc, slots, jobs_view(svc.store.snapshot()))
        if key == "health":
            _paint_health(svc, health_page)

    rail = segmented_rail(
        root,
        (("queue", "Queue"), ("history", "History"), ("health", "Health")),
        active=str(view_model.get("activity_segment") or "queue"),
        on_select=show_segment,
    )
    rail.pack(fill="x", pady=(0, theme.SP2))
    action_row.pack(fill="x", pady=(0, theme.SP2))
    show_segment(str(view_model.get("activity_segment") or "queue"))
    _render_body(svc, view, history_model, slots)
    root.refresh_jobs = refresh_jobs  # type: ignore[attr-defined]
    return root


def _sync_action(svc: Any, slots: dict[str, Any], view: dict[str, Any]) -> None:
    row = slots.get("action_row")
    if row is None:
        return
    _clear_children(row)
    key = str(slots.get("segment") or "queue")
    if key == "queue":
        button(
            row, "Clear waiting queue", lambda: _clear_all_queue(svc),
            kind="danger", height=theme.BTN_SM,
            disabled_reason="" if view.get("active") else "Nothing waiting in the queue.",
        ).pack(side="left")
        live = liveness_view(svc.store.snapshot().bridge.liveness)
        if live.get("show_force_drain"):
            button(
                row, "Force Drain", lambda: _force_drain(svc),
                kind="secondary", height=theme.BTN_SM,
            ).pack(side="left", padx=(theme.SP2, 0))
    elif key == "history":
        snapshot = svc.db.last_apply_snapshot() if svc.db is not None else None
        button(
            row, "Undo last change", lambda: _undo_last(svc, snapshot),
            kind="danger", height=theme.BTN_SM,
            disabled_reason="" if snapshot is not None else "No snapshot to undo.",
        ).pack(side="left")
    else:
        button(
            row, "Run doctor", lambda: _paint_health(svc, slots["health_host"]),
            kind="secondary", height=theme.BTN_SM,
        ).pack(side="left")


def _history_rows(history: list[dict[str, Any]] | tuple) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for index, job in enumerate(history[:40]):
        outcome = job.get("outcome") or {}
        rows.append({
            "status": outcome.get("label") or "—",
            "label": job.get("label") or job.get("job_id") or "job",
            "detail": job.get("detail") or "",
            "age": _job_age(job),
            "_key": str(job.get("job_id") or index),
            "_tone": outcome.get("tone") or "muted",
        })
    return tuple(rows)


def _job_age(job: dict[str, Any]) -> str:
    submitted = str(job.get("submitted") or "").strip()
    if not submitted:
        return "—"
    return submitted.replace("T", " ")[:16]


def _forget(widget: Any) -> None:
    if widget is None:
        return
    try:
        widget.pack_forget()
    except Exception:
        pass


def _clear_children(host: Any) -> None:
    if host is None:
        return
    for child in list(host.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


def _render_body(
    svc: Any,
    view: dict[str, Any],
    history_model: TableModel,
    slots: dict[str, Any],
) -> None:
    active = list(view.get("active") or ())
    history = list(view.get("history") or ())
    inflight_host = slots["inflight_host"]
    history_host = slots["history_host"]
    queue_page = slots["pages"]["queue"]
    _sync_action(svc, slots, view)

    if not active and not history:
        _forget(inflight_host)
        slots["packed_inflight"] = False
        if slots.get("empty") is None:
            empty = states.empty_state(
                queue_page,
                headline="Nothing yet",
                body="Apply a change or run an automation — jobs show up here.",
                action_label="Go to Club",
                action=lambda: svc.ui.navigate("club"),
                glyph="⧉",
            )
            empty.pack(fill="both", expand=True)
            slots["empty"] = empty
    else:
        empty = slots.get("empty")
        if empty is not None:
            try:
                empty.destroy()
            except Exception:
                pass
            slots["empty"] = None

        if not slots.get("packed_inflight"):
            inflight_host.pack(fill="x", anchor="n")
            slots["packed_inflight"] = True
        _clear_children(inflight_host)
        if active:
            eyebrow(inflight_host, "In flight").pack(
                anchor="w", pady=(theme.SP2, theme.SP1)
            )
            for job in active:
                _job_card(inflight_host, svc, job, live=True)
        else:
            muted_label(inflight_host, "Nothing in flight.", size=12).pack(
                anchor="w", pady=theme.SP2
            )

    if not slots.get("packed_history"):
        history_host.pack(fill="both", expand=True)
        slots["packed_history"] = True
    if history:
        if slots.get("history_label") is None or slots.get("history_grid") is None:
            _clear_children(history_host)
            slots["history_label"] = eyebrow(history_host, "Recent")
            slots["history_label"].pack(anchor="w", pady=(theme.SP1, theme.SP1))
            slots["history_grid"] = DataGrid(
                history_host,
                history_model,
                show_checkboxes=False,
                visible_rows=10,
            )
            slots["history_grid"].pack(fill="both", expand=True)
        rows = _history_rows(history)
        try:
            slots["history_grid"].set_rows(rows)
        except Exception:
            history_model.set_rows(rows)
            try:
                slots["history_grid"].refresh()
            except Exception:
                pass
    else:
        slots["history_label"] = None
        slots["history_grid"] = None
        _clear_children(history_host)
        states.empty_state(
            history_host,
            headline="No finished jobs yet",
            body="Completed jobs land here after they run in Career Mode.",
            action_label="Go to Club",
            action=lambda: svc.ui.navigate("club"),
            glyph="⧉",
        ).pack(fill="both", expand=True)


def _job_card(parent: Any, svc: Any, job: dict, *, live: bool) -> None:
    ensure_ctk()
    card = panel(parent, level=1)
    card.pack(fill="x", pady=(0, theme.SP1))
    outcome = job.get("outcome") or {}
    tone = outcome.get("tone") or "muted"
    color = {
        "ok": theme.SUCCESS,
        "warn": theme.WARNING,
        "error": theme.DANGER,
        "info": theme.ACCENT,
        "muted": theme.MUTED,
    }.get(tone, theme.MUTED)

    row = ctk.CTkFrame(card, fg_color="transparent")
    row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    text_label(row, outcome.get("label") or "—", size=11, bold=True, color=color, width=80).pack(
        side="left"
    )
    mid = ctk.CTkFrame(row, fg_color="transparent")
    mid.pack(side="left", fill="x", expand=True)
    text_label(mid, job.get("label") or job.get("job_id") or "job", size=12).pack(anchor="w")
    muted_label(mid, job.get("job_id") or "", size=10).pack(anchor="w")
    muted_label(mid, job.get("detail") or "", size=10, wraplength=280, justify="left").pack(anchor="w")
    if live:
        chip = str((job.get("stage") or {}).get("chip") or "Queued")
        try:
            claimed = str(job.get("job_id") or "") in set(svc.transport.claimed_ids())
        except Exception:
            claimed = False
        if claimed:
            chip = "In Live Editor"
        muted_label(row, chip, size=10, color=theme.ACCENT).pack(side="right")
    elif job.get("retry_suggested"):
        try:
            from .automations import automation_retryable, retry_recorded_automation

            if automation_retryable(svc, str(job.get("job_id") or "")):
                button(
                    row,
                    "Retry",
                    lambda jid=str(job.get("job_id") or ""): retry_recorded_automation(svc, jid),
                    kind="secondary",
                    height=theme.BTN_SM,
                    width=62,
                ).pack(side="right")
        except Exception:
            pass


def _clear_finished(svc: Any) -> None:
    svc.store.dispatch(E.JobHistoryCleared())
    set_status(svc, "Finished jobs cleared from this Activity view. The durable audit remains saved.")


def _clear_all_queue(svc: Any) -> None:
    from ...app.commands.queue import clear_all

    clear_all(svc)


def _force_drain(svc: Any) -> None:
    try:
        snippet = svc.transport.force_drain_snippet()
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Force drain unavailable: {exc}")
        return
    try:
        path = svc.paths.root / "force_drain_snippet.lua"
        path.write_text(snippet if isinstance(snippet, str) else str(snippet), encoding="utf-8")
        set_status(svc, f"Wrote {path.name} — paste into LE Lua Engine if stuck.")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not write snippet: {exc}")


def _undo_last(svc: Any, snapshot: Any = None) -> None:
    label = "last change"
    if snapshot is not None:
        tid = getattr(snapshot, "target_id", None) or getattr(snapshot, "label", None)
        if tid:
            label = str(tid)
    dialog = ctk.CTkInputDialog(
        text=f"Undo {label}? This re-applies the pre-write snapshot. Type UNDO to confirm.",
        title="Confirm undo",
    )
    if not confirm_undo(dialog.get_input() or ""):
        set_status(svc, "Undo cancelled. Nothing was queued.")
        return
    try:
        from ...app.commands.apply import restore_snapshot

        job_id = restore_snapshot(svc)
        set_status(svc, f"Undo queued ({job_id}). It will run on the next safe game tick.")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Undo unavailable: {exc}")


def _paint_health(svc: Any, host: Any) -> None:
    ensure_ctk()
    _clear_children(host)
    try:
        report = doctor_cmd.run(svc)
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Health check failed: {exc}")
        return
    card = panel(host, level=1)
    card.pack(fill="x")
    title = "Healthy" if report.healthy else "Needs attention"
    head = ctk.CTkFrame(card, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    pill(head, title, tone="ok" if report.healthy else "warn").pack(side="left")
    for check in report.checks:
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=theme.SP3, pady=1)
        pill(
            line,
            "OK" if check.ok else "Needs attention",
            tone="ok" if check.ok else "warn",
        ).pack(side="left")
        muted_label(
            line,
            f"{check.name}: {check.detail}"
            + (f" → {check.action}" if check.action and not check.ok else ""),
            size=10,
        ).pack(side="left", padx=(theme.SP2, 0))
    button(
        card, "Force drain snippet", lambda: _force_drain(svc),
        kind="ghost", height=theme.BTN_SM,
    ).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, theme.SP2))
    set_status(svc, f"Health: {'ok' if report.healthy else 'issues found'}.")
