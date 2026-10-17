"""Activity > Game log — Live Editor's own log, newest first.

Built lazily the first time the tab is opened (like Assets). Polls only while
the page is mapped: a cheap ``after`` tick every ``POLL_MS`` checks visibility
and, when visible, reads the next capped slice on ``svc.executor`` and repaints
on the Tk thread. One read-only text widget holds the rows; error rows use the
theme's danger colours. Log text is shown as data, never acted on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...app.commands import game_log as game_log_cmd
from ...domain.game_log import FILTER_ALL, FILTER_ERRORS, FILTER_WORKER, LogEntry, format_row
from .. import theme
from ..nav import segmented_rail
from ..widgets.primitives import ensure_ctk, mono_font, muted_label, text_label

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

#: Rows painted at once. The buffer keeps more; the view stays light.
VISIBLE_LIMIT = 200
FILTER_ITEMS = ((FILTER_WORKER, "Worker"), (FILTER_ERRORS, "Errors"), (FILTER_ALL, "All"))
_EMPTY_TEXT = {
    FILTER_WORKER: "No worker lines yet in this log.",
    FILTER_ERRORS: "No errors in the lines read so far.",
    FILTER_ALL: "The log is empty so far.",
}


def status_line(view: game_log_cmd.GameLogView, *, shown: int, now: datetime | None = None) -> str:
    """The caption above the rows. Pure, so tests can check it."""
    if view.message:
        return view.message
    stamp = (now or datetime.now()).strftime("%H:%M:%S")
    return f"{view.file_name} · {shown} shown · updated {stamp}"


def row_tags(entry: LogEntry) -> tuple[str, ...]:
    return (entry.tone,)


def configure_tags(box: Any) -> None:
    fill, _text = theme.tone_colors("error")
    box.tag_config("error", foreground=theme.DANGER_TEXT, background=_text)
    box.tag_config("warn", foreground=theme.WARNING)
    box.tag_config("muted", foreground=theme.TEXT)
    box.tag_config("empty", foreground=theme.MUTED)


def paint_rows(box: Any, entries: tuple[LogEntry, ...] | list[LogEntry], mode: str) -> None:
    """Replace the text widget's contents, keeping the user's scroll position."""
    try:
        top = float(box.yview()[0])
    except Exception:  # noqa: BLE001
        top = 0.0
    box.configure(state="normal")
    box.delete("1.0", "end")
    if not entries:
        box.insert("end", _EMPTY_TEXT.get(mode, ""), ("empty",))
    for entry in entries:
        box.insert("end", format_row(entry) + "\n", row_tags(entry))
    box.configure(state="disabled")
    try:
        box.yview_moveto(top)
    except Exception:  # noqa: BLE001
        pass


def build(host: Any, svc: Any, vm: dict[str, Any] | None = None) -> Any:
    ensure_ctk()
    view_model = vm if isinstance(vm, dict) else {}
    mode = str(view_model.get("game_log_filter") or FILTER_WORKER)
    if mode not in {key for key, _ in FILTER_ITEMS}:
        mode = FILTER_WORKER
    state: dict[str, Any] = {"mode": mode, "painted": None, "busy": False}

    top = ctk.CTkFrame(host, fg_color="transparent")
    top.pack(fill="x", pady=(0, theme.SP1))

    def set_mode(key: str) -> None:
        state["mode"] = key
        view_model["game_log_filter"] = key
        rail.set_active(key)
        paint(force=True)

    rail = segmented_rail(top, FILTER_ITEMS, active=mode, on_select=set_mode, compact=True)
    rail.pack(fill="x")
    status = muted_label(host, "Reading the Live Editor log…", size=theme.T_MICRO, anchor="w",
                         justify="left", wraplength=320)
    status.pack(fill="x", pady=(0, theme.SP1))
    box = ctk.CTkTextbox(
        host, wrap="none", font=mono_font(theme.T_MICRO), fg_color=theme.PANEL,
        text_color=theme.TEXT, border_width=1, border_color=theme.BORDER,
        corner_radius=theme.R_SM, activate_scrollbars=True,
    )
    box.pack(fill="both", expand=True)
    configure_tags(box)
    box.configure(state="disabled")

    def paint(*, force: bool = False) -> None:
        try:
            if not host.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        current = state["mode"]
        view = game_log_cmd.view(svc, current, limit=VISIBLE_LIMIT)
        key = (view.version, current, view.file_name)
        if force or key != state["painted"]:
            paint_rows(box, view.entries, current)
            state["painted"] = key
        status.configure(text=status_line(view, shown=len(view.entries)))

    def finished() -> None:
        state["busy"] = False
        paint()

    def visible() -> bool:
        try:
            return bool(host.winfo_exists() and host.winfo_ismapped() and host.winfo_viewable())
        except Exception:  # noqa: BLE001
            return False

    def refresh_now() -> None:
        if state["busy"]:
            return
        state["busy"] = True

        def work(_token: Any) -> None:
            try:
                game_log_cmd.poll(svc)
            finally:
                svc.executor.on_ui_thread(finished)

        try:
            svc.executor.submit("ui.game_log.poll", work)
        except Exception:  # noqa: BLE001
            state["busy"] = False

    def tick() -> None:
        try:
            if not host.winfo_exists():
                return  # page destroyed: the loop ends with it
        except Exception:  # noqa: BLE001
            return
        if visible():
            refresh_now()
        try:
            host.after(game_log_cmd.POLL_MS, tick)
        except Exception:  # noqa: BLE001
            pass

    host.refresh_game_log = refresh_now  # type: ignore[attr-defined]
    refresh_now()
    host.after(game_log_cmd.POLL_MS, tick)
    return host


def render_job_log(host: Any, svc: Any, job_id: str) -> None:
    """History detail: the log lines that name ``job_id`` (read off the UI thread)."""
    ensure_ctk()
    for child in list(host.winfo_children()):
        try:
            child.destroy()
        except Exception:  # noqa: BLE001
            pass
    if not job_id:
        return
    holder = ctk.CTkFrame(host, fg_color="transparent")
    holder.pack(fill="x", pady=(theme.SP2, 0))
    text_label(holder, "Game log for this job", size=theme.T_SMALL, bold=True).pack(anchor="w")
    caption = muted_label(holder, "Reading the Live Editor log…", size=theme.T_MICRO)
    caption.pack(anchor="w")

    def show() -> None:
        try:
            if not holder.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        entries = game_log_cmd.entries_for_job(svc, job_id)
        view = game_log_cmd.view(svc, FILTER_ALL, limit=1)
        if not entries:
            caption.configure(
                text=view.message
                or f"No lines in {view.file_name or 'the log'} mention {job_id} "
                "(only the most recent lines are kept)."
            )
            return
        caption.configure(text=f"{len(entries)} line(s) in {view.file_name}")
        for entry in entries[-12:]:
            color = theme.DANGER_TEXT if entry.error else theme.WARNING if entry.warn else theme.TEXT
            text_label(holder, format_row(entry), size=theme.T_MICRO, mono=True, color=color,
                       wraplength=320, justify="left").pack(anchor="w", fill="x")

    def work(_token: Any) -> None:
        try:
            game_log_cmd.poll(svc)
        finally:
            svc.executor.on_ui_thread(show)

    try:
        svc.executor.submit("ui.game_log.job", work)
    except Exception:  # noqa: BLE001
        show()
