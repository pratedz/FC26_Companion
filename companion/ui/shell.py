"""The window: chrome, surface host, Activity drawer, and the ONE Apply button.

WHY this file is thin
---------------------
It replaces ``src/gui.py``'s ``PremiumApp(ctk.CTk)`` — 182 instance attributes,
262 distinct ``app.*`` names across ``src/``, 112 methods of which ~100 were
one-line forwarders. Everything that made that class large lives somewhere with
a name now: domain state in ``AppState``, mutation in ``Store``, ports in
``Services``, widget-local state in per-view ViewModels, and destinations in
``nav.SurfaceRegistry``. What is left is genuinely just the window.

Three behaviours here are load-bearing and easy to get wrong:

1. **Thread discipline (P-2).** Liveness polling and result collection touch the
   disk, so they run on ``svc.executor``. They dispatch Events from a worker
   thread, which means the store notifies subscribers off the UI thread — so
   this shell's subscriber does nothing but stash the state and marshal a
   repaint through ``executor.on_ui_thread``. Tk is only ever touched from the
   ``after()`` pump that drains that queue.
2. **One Apply (P2).** The Changes bar is the app's only path to the queue, and
   both its buttons open the same diff sheet (§5.2) — Apply is never a shortcut
   past the preview, because P4 makes the preview mandatory.
3. **Honest chrome (P5).** The pill renders whatever ``presenters.liveness_view``
   says, including ARMED-with-an-empty-queue as a *success* tone. v1 showed OFF
   there and it was the single most-reported confusion in the product.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..app.commands.apply import ApplyError, apply_editor, collect_finished, refresh_liveness
from ..app.presenters import editor_view, header_view
from ..app.services import Services
from ..app.state import AppState
from ..domain.player import FIELD_SPECS
from . import nav, theme, wallpaper
from .placement import MIN_SIZE
from .widgets import diff as diffkit
from .widgets.primitives import (
    button,
    debounce,
    ensure_ctk,
    hairline,
    icon_button,
    muted_label,
    panel,
    pill,
    set_disabled,
    text_label,
    tooltip,
    truncate,
    update_pill,
)
from .widgets.toast import ToastHost

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

PUMP_MS = 50        # UI-callback drain cadence
POLL_MS = 1000      # liveness + finished-job sweep cadence

_SURFACE_DEPS: dict[str, tuple[str, ...]] = {
    "club": ("squad", "target", "bridge_view", "squad_sync"),
    # Do not rebuild Player merely because a live read was submitted: its card
    # picker owns a visible "Reading from FC 26…" state until the result
    # changes target/editor data and naturally refreshes the page.
    "player": ("target", "editor", "squad"),
    # Add Player owns a persisted draft VM, so it can safely rebuild as each
    # individual member of a reviewed batch moves through the queue.
    "add_player": ("squad", "bridge_view", "prefs", "jobs"),
    # Automations owns a visible queue backed by JobsState.  Without this
    # dependency a clicked boost was submitted but the on-page queue remained
    # frozen until the user changed tabs.
    "automations": ("target", "squad", "bridge_view", "jobs"),
    "library": ("search", "target"),
}


def _bridge_view_fingerprint(state: AppState) -> tuple[Any, ...]:
    """Only values rendered inside surfaces; drain age changes every poll."""
    lv = state.bridge.liveness
    return (lv.pill, lv.armed, lv.message)


def _jobs_only_changed(before: AppState, state: AppState) -> bool:
    """True when Sign can refresh its job strip without destroying search/selection."""
    if before.jobs is state.jobs:
        return False
    if before.squad is not state.squad or before.prefs is not state.prefs:
        return False
    if before.target is not state.target:
        return False
    return _bridge_view_fingerprint(before) == _bridge_view_fingerprint(state)


def _sync_squad_active(state: AppState) -> bool:
    """True only while a Sync squad job is still non-terminal."""
    return any(
        (not job.done) and job.label == "Sync squad"
        for job in state.jobs.active.values()
    )


def _surface_needs_refresh(key: str, before: AppState, state: AppState) -> bool:
    for field in _SURFACE_DEPS.get(key, ()):
        if field == "bridge_view":
            if _bridge_view_fingerprint(before) != _bridge_view_fingerprint(state):
                return True
        elif field == "squad_sync":
            if _sync_squad_active(before) != _sync_squad_active(state):
                return True
        elif getattr(before, field) is not getattr(state, field):
            return True
    return False


class PollGate:
    """At most one shell poll body runs at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def enter(self) -> bool:
        return self._lock.acquire(blocking=False)

    def leave(self) -> None:
        self._lock.release()


class Shell:
    """The application window. Owns no domain logic — only wiring and paint."""

    def __init__(self, svc: Services, registry: nav.SurfaceRegistry | None = None) -> None:
        ensure_ctk()
        self.svc = svc
        self.registry = registry or nav.default_registry()
        self.active = self.registry.keys()[0] if self.registry.keys() else "club"
        self._surface_vms: dict[str, dict[str, Any]] = {}
        self.drawer_open = False
        self.on_palette: Any = None  # Ctrl+K hook; a surface may install one

        self._lock = threading.Lock()
        self._pending: AppState | None = svc.store.snapshot()
        self._rendered_state: AppState | None = None
        self._closing = False
        self._poll_gate = PollGate()
        self._surface_epoch: dict[str, AppState] = {}
        self._last_poll_error = ""
        self._last_status_id = 0
        # Every scheduled callback is tracked so close() can cancel it. An
        # `after` left queued past destroy() fires into a dead interpreter and
        # prints `invalid command name ..._pump` to stderr.
        self._after_ids: list[str] = []

        self.root = ctk.CTk()
        try:
            ctk.set_appearance_mode("Dark")
        except Exception:
            pass
        self.root.title("LE Companion — FC 26 Career")
        self.root.configure(fg_color=theme.BG)
        self.root.geometry("1280x820")
        self.root.minsize(*MIN_SIZE)
        self._apply_ui_scale(self._pref_ui_scale())

        self._wallpaper = wallpaper.WallpaperLayer(self.root)
        self._wallpaper.mount()
        self._build_chrome()
        self._build_body()
        self._build_changes_bar()
        self._apply_wallpaper(self._pref_wallpaper())
        self._toasts = ToastHost(self.root)
        self._bind_keys()
        self.svc.ui.bind_navigation(self._show)
        self.svc.ui.bind_review(self.review)
        self.svc.ui.bind_activity(self.open_drawer)
        self.svc.ui.bind_signing(self._open_signing)
        self.svc.ui.bind_player_card(self._open_player_card)
        self.svc.ui.bind_settings(self._open_settings)
        self.svc.store.subscribe(self._sync_ai_prefs)
        self._sync_ai_prefs(self.svc.store.snapshot())

        self._unsubscribe = svc.store.subscribe(self._on_state)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._schedule(PUMP_MS, self._pump)
        # Read liveness on this thread before the first Club build so the
        # header and the first surface share one snapshot. Later polls stay
        # off the UI thread.
        try:
            refresh_liveness(self.svc)
        except Exception as exc:  # noqa: BLE001
            self._report_poll_error(exc)
        self._show(self.active)
        self._poll(auto_sync=False)

    # ---- chrome -------------------------------------------------------

    def _build_chrome(self) -> None:
        bar = ctk.CTkFrame(self.root, fg_color=theme.CHROME, corner_radius=0, height=56)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SP4, pady=theme.SP2)

        self.status_holder = ctk.CTkFrame(inner, fg_color="transparent")
        self.status_holder.pack(side="left")
        self._pill = None
        self.bridge_caption = muted_label(inner, "Checking Live Editor…", size=12)
        self.bridge_caption.pack(side="left", padx=(theme.SP3, 0))
        self.squad_caption = muted_label(inner, "", size=12)
        self.squad_caption.pack(side="left", padx=(theme.SP3, 0))

        self.settings_btn = icon_button(inner, "☰", self._open_settings,
                                        tooltip_text="Settings")
        self.settings_btn.pack(side="right")
        self.drawer_btn = icon_button(inner, "⧉", self.toggle_drawer,
                                      tooltip_text="Activity  (Ctrl+J)")
        self.drawer_btn.pack(side="right", padx=(0, theme.SP2))
        self.palette_btn = icon_button(inner, "⌘K", self.open_palette,
                                       tooltip_text="Command palette  (Ctrl+K)",
                                       size=30, width=46)
        self.palette_btn.pack(side="right", padx=(0, theme.SP3))

        self.target_chip = button(
            inner, "No player selected", self._open_target_chip,
            kind="ghost", height=theme.BTN_SM, width=220,
        )
        self.target_chip.pack(side="right", padx=(0, theme.SP3))
        tooltip(self.target_chip, "Open the selected player, or choose one on Player.")
        self.target_label = self.target_chip  # test seam: header target control
        ctk.CTkFrame(self.root, height=1, fg_color=theme.BORDER, corner_radius=0).pack(fill="x")

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.tabs = nav.side_nav(body, self.registry, active=self.active, on_select=self._show)
        self.tabs.pack(side="left", fill="y")
        ctk.CTkFrame(body, width=1, fg_color=theme.BORDER, corner_radius=0).pack(side="left", fill="y")
        self.host = ctk.CTkFrame(body, fg_color=theme.BG)
        self.host.pack(side="left", fill="both", expand=True)
        # The drawer is a sibling, not an overlay: CTk cannot composite, and an
        # overlay that steals clicks is worse than one that takes width.
        self.drawer = ctk.CTkFrame(body, fg_color=theme.CHROME, width=360,
                                   corner_radius=0)
        self.drawer.pack_propagate(False)
        ctk.CTkFrame(
            self.drawer, width=1, fg_color=theme.CHROME_LINE, corner_radius=0,
        ).pack(side="left", fill="y")
        self._surfaces: dict[str, Any] = {}

    def _build_changes_bar(self) -> None:
        self.changes_divider = hairline(self.root)
        self.changes_divider.pack(fill="x")
        bar = ctk.CTkFrame(self.root, fg_color=theme.CHROME, corner_radius=0, height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.changes_bar = bar
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SP4, pady=theme.SP2)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self.changes_title = text_label(left, "Changes", size=12, bold=True,
                                        color=theme.MUTED)
        self.changes_title.pack(anchor="w")
        self.changes_summary = muted_label(left, "No changes staged.", size=12)
        self.changes_summary.pack(anchor="w")

        self.apply_btn = button(inner, "Review changes", self.apply, kind="primary",
                                width=140, height=theme.BTN_MD)
        self.apply_btn.pack(side="right")
        self.review_btn = button(inner, "How it runs", self._automation_help, kind="ghost",
                                 width=100, height=theme.BTN_MD)
        # Packed only on Automations as contextual help — player review uses apply_btn.

    def _set_changes_bar_visible(self, visible: bool) -> None:
        """Show the global review dock only when it offers a real next action."""
        if visible:
            if not self.changes_divider.winfo_manager():
                self.changes_divider.pack(fill="x")
            if not self.changes_bar.winfo_manager():
                self.changes_bar.pack(fill="x")
        else:
            self.changes_divider.pack_forget()
            self.changes_bar.pack_forget()
        try:
            self._toasts.set_bottom_offset(128 if visible else 70)
        except Exception:
            pass

    def _bind_keys(self) -> None:
        """Global shortcuts. Number keys do not switch tabs."""
        binds = {
            "<Control-k>": lambda _e: self.open_palette(),
            "<Control-K>": lambda _e: self.open_palette(),
            "<Control-j>": lambda _e: self.toggle_drawer(),
            "<Control-J>": lambda _e: self.toggle_drawer(),
            "<Control-Return>": lambda _e: self.apply(),
            "<Escape>": lambda _e: self.close_drawer(),
        }
        for sequence, fn in binds.items():
            try:
                self.root.bind_all(sequence, fn)
            except Exception:
                pass

    # ---- surfaces -----------------------------------------------------

    def _show(self, key: str) -> None:
        """Switch surface, building it lazily and never fatally (see nav.py)."""
        if key not in self.registry:
            return
        for widget in self._surfaces.values():
            try:
                widget.pack_forget()
            except Exception:
                pass
        widget = self._surfaces.get(key)
        if widget is None:
            widget = self.registry.build(
                key,
                self.host,
                self.svc,
                vm=self._surface_vms.setdefault(key, {}),
                retry=lambda k=key: self._rebuild(k),
            )
            self._surfaces[key] = widget
        try:
            widget.pack(fill="both", expand=True, padx=theme.SP4, pady=theme.SP4)
        except Exception:
            pass
        self.active = key
        self.tabs.set_active(key)
        self._surface_epoch[key] = self.svc.store.snapshot()

    def _open_target_chip(self) -> None:
        """Header player chip: jump to Player (picker if none locked)."""
        self._show("player")

    def _rebuild(self, key: str) -> None:
        widget = self._surfaces.pop(key, None)
        if widget is not None:
            try:
                widget.destroy()
            except Exception:
                pass
        self.registry.forget(key)
        self._show(key)

    def _refresh_surface(self, key: str) -> None:
        """Rebuild one visible surface on the Tk thread from current state."""
        widget = self._surfaces.pop(key, None)
        if widget is not None:
            try:
                widget.destroy()
            except Exception:
                pass
        self.registry.forget(key)
        parent = self.drawer if key == nav.DRAWER.key else self.host
        widget = self.registry.build(
            key,
            parent,
            self.svc,
            vm=self._surface_vms.setdefault(key, {}),
            retry=lambda k=key: self._rebuild(k),
        )
        self._surfaces[key] = widget
        try:
            widget.pack(
                side="left" if key == nav.DRAWER.key else "top",
                fill="both",
                expand=True,
                padx=theme.SP3 if key == nav.DRAWER.key else theme.SP4,
                pady=theme.SP3 if key == nav.DRAWER.key else theme.SP4,
            )
        except Exception:
            pass
        self._surface_epoch[key] = self.svc.store.snapshot()

    def toggle_drawer(self) -> None:
        self.close_drawer() if self.drawer_open else self.open_drawer()

    def open_drawer(self) -> None:
        self.drawer.pack(side="right", fill="y")
        if self._surfaces.get(nav.DRAWER.key) is None:
            self._surfaces[nav.DRAWER.key] = self.registry.build(
                nav.DRAWER.key, self.drawer, self.svc,
                vm=self._surface_vms.setdefault(nav.DRAWER.key, {}),
                retry=lambda: self._rebuild(nav.DRAWER.key),
            )
            try:
                self._surfaces[nav.DRAWER.key].pack(
                    side="left", fill="both", expand=True,
                    padx=theme.SP3, pady=theme.SP3,
                )
            except Exception:
                pass
        self.drawer_open = True

    def close_drawer(self) -> None:
        try:
            self.drawer.pack_forget()
        except Exception:
            pass
        self.drawer_open = False

    def open_palette(self) -> None:
        """Ctrl+K — real command palette (navigate, activity, settings, doctor, apply)."""
        if callable(self.on_palette):
            self.on_palette()
            return
        ensure_ctk()
        from .palette import dispatch_palette, filter_commands, palette_commands

        self._status("Command palette open — type to filter.")
        win = ctk.CTkToplevel(self.root)
        win.title("Command palette")
        win.geometry("420x360")
        win.configure(fg_color=theme.BG)
        try:
            win.transient(self.root)
            win.grab_set()
        except Exception:
            pass

        muted_label(win, "Type to filter · Enter runs the first match", size=11).pack(
            anchor="w", padx=theme.SP3, pady=(theme.SP2, 0)
        )
        query = ctk.StringVar()
        entry = ctk.CTkEntry(win, textvariable=query, placeholder_text="Jump to…")
        entry.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
        list_host = ctk.CTkScrollableFrame(win, fg_color="transparent")
        list_host.pack(fill="both", expand=True, padx=theme.SP3, pady=(0, theme.SP3))

        def run(cmd: Any) -> None:
            def _doctor() -> None:
                try:
                    from ..app.commands import doctor as doctor_cmd

                    report = doctor_cmd.run(self.svc)
                    self._status(
                        "Doctor: healthy" if report.healthy else "Doctor: needs attention"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._status(f"Doctor failed: {exc}")

            msg = dispatch_palette(
                cmd,
                navigate=self._show,
                open_activity=self.open_drawer,
                open_settings=self._open_settings,
                run_doctor=_doctor,
                open_apply=self.review,
            )
            self._status(msg)
            try:
                win.destroy()
            except Exception:
                pass

        buttons: list[Any] = []

        def repaint(*_a: Any) -> None:
            hits = filter_commands(query.get())
            while len(buttons) < len(hits):
                btn = button(
                    list_host, "", lambda: None, kind="ghost", height=28,
                )
                buttons.append(btn)
            for index, cmd in enumerate(hits):
                btn = buttons[index]
                try:
                    btn.configure(text=cmd.label, command=lambda c=cmd: run(c))
                except Exception:
                    pass
                try:
                    if not btn.winfo_manager():
                        btn.pack(fill="x", pady=1)
                except Exception:
                    btn.pack(fill="x", pady=1)
            for index in range(len(hits), len(buttons)):
                try:
                    buttons[index].pack_forget()
                except Exception:
                    pass

        query.trace_add("write", debounce(entry, 120, repaint))
        entry.bind("<Return>", lambda _e: (
            run(filter_commands(query.get())[0])
            if filter_commands(query.get()) else None
        ))
        repaint()
        try:
            entry.focus_set()
        except Exception:
            pass
        # Silence unused import if linted
        _ = palette_commands

    def _open_signing(self, card: dict[str, Any]) -> None:
        """Navigate to Sign and rebuild so the pending Library card is consumed."""
        _ = card  # UiActions already stashed a defensive copy for take_signing_card.
        key = "add_player"
        if self.active == key:
            self._rebuild(key)
        else:
            widget = self._surfaces.pop(key, None)
            if widget is not None:
                try:
                    widget.destroy()
                except Exception:
                    pass
            self.registry.forget(key)
            self._show(key)

    def _open_player_card(self, card: dict[str, Any]) -> None:
        """Navigate to Player Card Library and rebuild so the pending card is consumed."""
        _ = card
        key = "player"
        self._surface_vms.setdefault(key, {})["player_source"] = "cards"
        if self.active == key:
            self._rebuild(key)
        else:
            widget = self._surfaces.pop(key, None)
            if widget is not None:
                try:
                    widget.destroy()
                except Exception:
                    pass
            self.registry.forget(key)
            self._show(key)

    def _pref_wallpaper(self) -> bool:
        return wallpaper.pref_enabled(self.svc.store.snapshot().prefs.values)

    def _apply_wallpaper(self, on: bool) -> None:
        try:
            self._wallpaper.set_enabled(on)
        except Exception:
            on = False
        try:
            self.host.configure(fg_color="transparent" if on else theme.BG)
        except Exception:
            pass

    def _pref_ui_scale(self) -> float:
        raw = self.svc.store.snapshot().prefs.values.get("ui_scale", 1.0)
        try:
            scale = float(raw)
        except (TypeError, ValueError):
            scale = 1.0
        return max(0.85, min(1.5, scale))

    def _apply_ui_scale(self, scale: float) -> None:
        try:
            ctk.set_widget_scaling(float(scale))
        except Exception:
            pass
        try:
            ctk.set_window_scaling(float(scale))
        except Exception:
            pass

    def _sync_ai_prefs(self, state: Any) -> None:
        from ..integrations.ai_provider import load_from_prefs

        try:
            load_from_prefs(state.prefs.values)
        except Exception:
            pass

    def _open_settings(self) -> None:
        """About + paths + worker install + doctor (not a full tab)."""
        ensure_ctk()
        try:
            from .. import CORE_VERSION, PROTOCOL_V, __version__
            from ..app import events as E
            from ..app.commands import doctor as doctor_cmd
            from ..platform import le_install
        except Exception as exc:  # noqa: BLE001
            self._status(f"Settings unavailable: {exc}", tone="error")
            return

        win = ctk.CTkToplevel(self.root)
        win.title("Settings — LE Companion")
        win.geometry("560x720")
        win.configure(fg_color=theme.BG)
        try:
            win.transient(self.root)
            win.grab_set()
        except Exception:
            pass

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=theme.SP4, pady=theme.SP4)

        def group(title: str, *, subtitle: str = "") -> Any:
            box = panel(scroll, level=1)
            box.pack(fill="x", pady=(0, theme.SP3))
            text_label(box, title, size=14, bold=True).pack(
                anchor="w", padx=theme.SP3, pady=(theme.SP3, 2)
            )
            if subtitle:
                muted_label(box, subtitle, size=11).pack(
                    anchor="w", padx=theme.SP3, pady=(0, theme.SP2)
                )
            return box

        about = group(
            "About",
            subtitle=(
                f"Companion {__version__}  ·  core {CORE_VERSION}  ·  protocol v{PROTOCOL_V}"
            ),
        )
        muted_label(
            about,
            "Club has the first-run checklist. Activity (Ctrl+J) has undo and queue history.",
            size=11,
        ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP3))

        display = group("Display", subtitle="UI scale for this window (saved with prefs).")
        scale_row = ctk.CTkFrame(display, fg_color="transparent")
        scale_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
        scale_choices = ("0.90", "1.00", "1.10", "1.25")
        current_scale = f"{self._pref_ui_scale():.2f}"
        if current_scale not in scale_choices:
            scale_choices = (*scale_choices, current_scale)
        scale_var = ctk.StringVar(value=current_scale)
        scale_menu = ctk.CTkOptionMenu(
            scale_row,
            values=list(scale_choices),
            variable=scale_var,
            width=100,
            height=theme.BTN_MD,
            fg_color=theme.CARD,
            button_color=theme.BORDER,
            button_hover_color=theme.CARD_HOVER,
            dropdown_fg_color=theme.CARD,
            text_color=theme.TEXT,
        )
        scale_menu.pack(side="left")

        def apply_scale() -> None:
            try:
                scale = float(scale_var.get())
            except (TypeError, ValueError):
                scale = 1.0
            scale = max(0.85, min(1.5, scale))
            self.svc.store.dispatch(E.PrefChanged("ui_scale", scale))
            self._apply_ui_scale(scale)
            self._status(f"UI scale set to {scale:.2f}.", tone="ok")

        button(
            scale_row, "Apply scale", apply_scale, kind="secondary", height=theme.BTN_MD,
        ).pack(side="left", padx=(theme.SP2, 0))

        muted_label(
            display,
            "Stadium wallpaper behind the HUD. Cards and tables stay solid.",
            size=11,
        ).pack(anchor="w", padx=theme.SP3)
        wall_row = ctk.CTkFrame(display, fg_color="transparent")
        wall_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP3))
        wall_var = ctk.BooleanVar(value=self._pref_wallpaper())

        def apply_wallpaper() -> None:
            enabled = bool(wall_var.get())
            self.svc.store.dispatch(E.PrefChanged("wallpaper", enabled))
            self._apply_wallpaper(enabled)
            self._status(
                "Stadium wallpaper on." if enabled else "Stadium wallpaper off.",
                tone="ok",
            )

        ctk.CTkSwitch(
            wall_row,
            text="Stadium wallpaper",
            variable=wall_var,
            command=apply_wallpaper,
            progress_color=theme.ACCENT,
            button_color=theme.TEXT,
            button_hover_color=theme.MUTED,
            text_color=theme.TEXT,
        ).pack(side="left")

        ai = group(
            "AI & Codex",
            subtitle="One provider and one model for Club, Player, and Sign.",
        )
        from ..widgets.ai_bar import mount_ai_settings

        mount_ai_settings(ai, self.svc)

        paths = group("Paths")
        for label, path in (
            ("App root", self.svc.paths.root),
            ("Queue", self.svc.paths.queue),
            ("State DB", self.svc.paths.state_db),
            ("Profiles", self.svc.paths.profiles_file),
            ("Catalog", self.svc.paths.universe_db),
        ):
            muted_label(
                paths, f"{label}: {truncate(str(path), 56)}", size=11,
            ).pack(anchor="w", padx=theme.SP3)
        ctk.CTkFrame(paths, fg_color="transparent", height=theme.SP2).pack()

        worker = group("Worker")
        status_lbl = muted_label(worker, "Checking…", size=11)
        status_lbl.pack(anchor="w", padx=theme.SP3)

        def refresh_worker() -> None:
            try:
                st = le_install.status()
                status_lbl.configure(
                    text=(
                        f"Installed: {st.get('installed')}  ·  "
                        f"core {st.get('core_version') or '—'}  ·  "
                        f"{st.get('detail') or st.get('message') or ''}"
                    )[:200]
                )
            except Exception as exc:  # noqa: BLE001
                status_lbl.configure(text=f"Worker status error: {exc}")

        def do_install() -> None:
            try:
                report = le_install.install()
                ok = bool(report.get("ok") if isinstance(report, dict) else report)
                self._status(
                    "Worker installed." if ok else f"Install: {report}",
                    tone="ok" if ok else "error",
                )
            except Exception as exc:  # noqa: BLE001
                self._status(f"Install failed: {exc}", tone="error")
            refresh_worker()

        row = ctk.CTkFrame(worker, fg_color="transparent")
        row.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP3))
        button(row, "Refresh status", refresh_worker, kind="ghost", height=theme.BTN_MD).pack(
            side="left"
        )
        button(
            row, "Install / repair worker", do_install, kind="primary", height=theme.BTN_MD,
        ).pack(side="left", padx=(theme.SP2, 0))
        refresh_worker()

        health = group("Health")
        health_box = ctk.CTkFrame(health, fg_color="transparent")
        health_box.pack(fill="x", padx=theme.SP3)

        def run_doctor() -> None:
            for child in list(health_box.winfo_children()):
                child.destroy()
            try:
                report = doctor_cmd.run(self.svc)
            except Exception as exc:  # noqa: BLE001
                muted_label(health_box, f"Doctor failed: {exc}", size=11).pack(anchor="w")
                return
            overall = ctk.CTkFrame(health_box, fg_color="transparent")
            overall.pack(fill="x", pady=(0, theme.SP1))
            pill(
                overall,
                "Healthy" if report.healthy else "Needs attention",
                tone="ok" if report.healthy else "warn",
            ).pack(side="left")
            for check in report.checks:
                line = ctk.CTkFrame(health_box, fg_color="transparent")
                line.pack(fill="x", pady=1)
                pill(
                    line,
                    "OK" if check.ok else "Needs attention",
                    tone="ok" if check.ok else "warn",
                ).pack(side="left")
                muted_label(
                    line,
                    f"{check.name}: {check.detail}",
                    size=10,
                ).pack(side="left", padx=(theme.SP2, 0))

        button(health, "Run doctor", run_doctor, kind="secondary", height=theme.BTN_MD).pack(
            anchor="w", padx=theme.SP3, pady=(theme.SP2, theme.SP3)
        )

    def _status(self, text: str, *, tone: str = "info") -> None:
        from ..app import events as E

        self.svc.store.dispatch(E.StatusSet(text, tone=tone))

    def _report_poll_error(self, exc: Exception) -> None:
        message = f"Connection refresh failed: {exc}"
        if message == self._last_poll_error:
            return
        self._last_poll_error = message
        self._status(message)
        if self.svc.log is not None:
            self.svc.log.exception("shell poll failed")

    # ---- apply (P2/P4: one verb, always previewed) ---------------------

    def _diff_model(self) -> diffkit.DiffModel:
        state = self.svc.store.snapshot()
        view = editor_view(state)
        head = header_view(state)
        provenance = state.editor.source_label or state.editor.source
        review_note = f"Draft source: {provenance}" if provenance else ""
        if state.editor.warnings:
            review_note = " · ".join(
                part for part in (review_note, *state.editor.warnings) if part
            )
        return diffkit.build_diff(
            diffkit.pairs_from(state.editor.base, view["dirty"]),
            labels={name: spec.label for name, spec in FIELD_SPECS.items()},
            groups={name: spec.category for name, spec in FIELD_SPECS.items()},
            subject=head["target"]["name"],
            subject_id=head["target"]["playerid"],
            # The current in-game runner does not yet guarantee creation of
            # snapshot_to. Never promise an undo artifact that may not exist.
            undo_available=False,
            blast_radius=review_note,
        )

    def review(self) -> None:
        state = self.svc.store.snapshot()
        diffkit.diff_sheet(
            self.root,
            self._diff_model(),
            on_apply=self._commit,
            blocked_reason=editor_view(state)["blocked_reason"],
        )

    def apply(self) -> None:
        """The single Apply affordance — it opens the mandatory preview (P4)."""
        if self.active == "automations":
            self.open_drawer()
            self._automation_help()
            return
        self.review()

    def _automation_help(self) -> None:
        self._status(
            "Automations are already queued. FC runs writes only after a settled Career screen event; open Team Management or return to the Career hub."
        )

    def _commit(self) -> None:
        try:
            apply_editor(self.svc)
        except ApplyError as exc:
            # Bind the message before any callback can close over it: v1's
            # deferred `except ... as e` reference ate every failed apply.
            self._status(str(exc))

    # ---- state -> paint ------------------------------------------------

    def _on_state(self, state: AppState) -> None:
        """Store subscriber. May run on a worker thread — so it must not touch Tk."""
        with self._lock:
            self._pending = state
        self.svc.executor.on_ui_thread(self._render)

    def _render(self) -> None:
        with self._lock:
            state, self._pending = self._pending, None
        if state is None or self._closing:
            return
        before = self._rendered_state
        self._rendered_state = state
        head = header_view(state)
        bridge = head["bridge"]

        tip = f"{bridge['message']}  {bridge['age_text']}".strip()
        if bridge["pill"] in ("WAITING", "STALLED"):
            tip = f"{bridge['message']} Open Activity. {bridge['age_text']}".strip()
        if self._pill is None:
            self._pill = pill(
                self.status_holder, bridge["pill"], tone=bridge["tone"], glyph="●",
            )
            self._pill.pack()
            tooltip(self._pill, tip)
        else:
            update_pill(
                self._pill, bridge["pill"], tone=bridge["tone"], glyph="●",
            )
            tooltip(self._pill, tip)
        try:
            self.bridge_caption.configure(text=truncate(str(bridge.get("message") or ""), 42))
        except Exception:
            pass
        squad = state.squad
        team = "Squad"
        if squad.players:
            sample = squad.players[0]
            team = str(sample.get("teamname") or sample.get("club") or "Squad")
        count = len(squad.players or ())
        squad_text = f"{team}  ·  {count} players" if count else "Squad not read"
        try:
            self.squad_caption.configure(text=truncate(squad_text, 36))
        except Exception:
            pass
        self.target_chip.configure(text=head["target"]["text"])

        status_id = int(head.get("status_id") or 0)
        if status_id and status_id != self._last_status_id:
            self._last_status_id = status_id
            status_text = str(head.get("status") or "").strip()
            if status_text:
                try:
                    self._toasts.show(status_text, tone=str(head.get("status_tone") or "info"))
                except Exception:
                    pass
        if before is not None:
            prev_pill = before.bridge.liveness.pill.value.upper()
            if prev_pill != bridge["pill"] and bridge["pill"] in ("WAITING", "STALLED"):
                try:
                    self._toasts.show(
                        "Jobs are waiting on Career. Open Activity.",
                        tone="warn",
                    )
                except Exception:
                    pass

        n = head["in_flight"]
        self.drawer_btn.configure(
            text=f"⧉ {n}" if n else "⧉",
            border_color=theme.ACCENT if n else theme.BORDER,
            text_color=theme.ACCENT if n else theme.MUTED,
        )

        view = editor_view(state)
        count = view["dirty_count"]
        if self.active == "automations":
            # Automation controls submit durable jobs immediately. A player
            # Review/Apply dock on this tab implies a second click is needed.
            self.changes_title.configure(text="Automation queue", text_color=theme.ACCENT)
            self.changes_summary.configure(
                text="Choose a boost above; queued jobs run at the next safe Career screen event."
            )
            self.review_btn.configure(text="How it runs", command=self._automation_help)
            self.apply_btn.configure(text="Open activity", command=self.open_drawer)
            if not self.review_btn.winfo_manager():
                self.review_btn.pack(side="right", padx=(0, theme.SP2))
            set_disabled(self.review_btn, "")
            set_disabled(self.apply_btn, "")
            self._set_changes_bar_visible(False)
        else:
            # Persistent actions only open the mandatory review sheet. The
            # actual commit retains its Apply label inside that sheet.
            # Status feedback is toasted separately — this dock is review-only.
            self.apply_btn.configure(text="Review changes", command=self.apply)
            self.review_btn.pack_forget()
            self.changes_title.configure(
                text=f"Changes ({count})" if count else "Changes",
                text_color=theme.ACCENT if count else theme.MUTED,
            )
            self.changes_summary.configure(
                text=self._diff_model().summary() if count else
                "No changes staged. Pick a player or run an automation."
            )
            set_disabled(self.review_btn, "" if count else "Nothing staged to review.")
            # P5: with the worker merely idle, Apply still works — it queues.
            set_disabled(self.apply_btn, view["blocked_reason"])
            self._set_changes_bar_visible(bool(count))

        # Surface builders are pure state -> widget views. Refresh only when
        # their consumed slice changed; age-only liveness polling therefore
        # does not destroy a user's search entry. Inactive cached surfaces are
        # invalidated too, otherwise switching back resurrects stale content.
        if before is None:
            built = self._surface_epoch.get(self.active)
            if (
                built is not None
                and built is not state
                and _surface_needs_refresh(self.active, built, state)
            ):
                self._refresh_surface(self.active)
        elif before is not None:
            for key in tuple(self._surfaces):
                if key == nav.DRAWER.key or not _surface_needs_refresh(
                    key, before, state
                ):
                    continue
                if key == "add_player":
                    widget = self._surfaces.get(key)
                    refresh_state = getattr(widget, "refresh_state", None)
                    if callable(refresh_state):
                        try:
                            if refresh_state():
                                self._surface_epoch[key] = state
                                continue
                        except Exception:
                            from ..core.log import get_logger
                            get_logger("ui.sign").exception("Sign refresh_state failed")
                if key in ("add_player", "automations"):
                    widget = self._surfaces.get(key)
                    review_open = key == "add_player" and bool(
                        widget is not None and getattr(widget, "signing_review_open", False)
                    )
                    jobs_only = _jobs_only_changed(before, state)
                    if review_open or jobs_only:
                        refresh = None
                        if widget is not None:
                            refresh = (
                                getattr(widget, "refresh_signing_progress", None)
                                or getattr(widget, "refresh_automation_queue", None)
                            )
                        if callable(refresh):
                            try:
                                refresh()
                                continue
                            except Exception:
                                if review_open:
                                    continue
                if key == "club":
                    widget = self._surfaces.get(key)
                    refresh_board = getattr(widget, "refresh_board", None) if widget else None
                    if callable(refresh_board):
                        try:
                            if refresh_board():
                                continue
                        except Exception:
                            from ..core.log import get_logger

                            get_logger("ui.club").exception("club refresh_board failed")
                if key == self.active:
                    self._refresh_surface(key)
                else:
                    widget = self._surfaces.pop(key, None)
                    if widget is not None:
                        try:
                            widget.destroy()
                        except Exception:
                            pass
                    self.registry.forget(key)
            if self.drawer_open and before.jobs is not state.jobs:
                drawer = self._surfaces.get(nav.DRAWER.key)
                refresh_jobs = getattr(drawer, "refresh_jobs", None) if drawer else None
                if callable(refresh_jobs):
                    try:
                        refresh_jobs()
                    except Exception:
                        self._refresh_surface(nav.DRAWER.key)
                else:
                    self._refresh_surface(nav.DRAWER.key)

    # ---- pumps ---------------------------------------------------------

    def _schedule(self, delay_ms: int, fn: Any) -> None:
        """``after`` with a cancellable handle. See ``_after_ids``."""
        if self._closing:
            return
        try:
            self._after_ids.append(self.root.after(delay_ms, fn))
        except Exception:
            pass

    def _pump(self) -> None:
        """Drain UI callbacks on the Tk thread. The only place Tk work happens."""
        if self._closing:
            return
        # InlineExecutor has nothing to drain (it ran the callback inline), so
        # the pump is a no-op under test rather than an exception to swallow.
        drain = getattr(self.svc.executor, "drain_ui", None)
        if drain is not None:
            try:
                drain(200)
            except Exception:
                pass
        self._schedule(PUMP_MS, self._pump)

    def _poll(self, *, auto_sync: bool = True) -> None:
        """Liveness + missed-follow sweep, off the UI thread (P-2)."""
        if self._closing:
            return
        if not self._poll_gate.enter():
            self._schedule(POLL_MS, self._poll)
            return

        def work(_token: Any) -> None:
            try:
                self._poll_once(auto_sync=auto_sync)
            finally:
                self._poll_gate.leave()

        try:
            self.svc.executor.submit("shell.poll", work)
        except Exception as exc:  # noqa: BLE001
            self._poll_gate.leave()
            self._report_poll_error(exc)
        self._schedule(POLL_MS, self._poll)

    def _poll_once(self, *, auto_sync: bool) -> None:
        """Each step is isolated so one failure does not skip the others."""
        from ..core.log import get_logger

        log = get_logger("ui.shell")
        steps: list[tuple[str, Any]] = [
            ("liveness", lambda: refresh_liveness(self.svc)),
            ("results", lambda: collect_finished(self.svc)),
        ]
        if auto_sync:
            from ..app.commands.squad import ensure_squad_fresh

            steps.append(("squad", lambda: ensure_squad_fresh(self.svc)))
        for name, step in steps:
            try:
                step()
            except Exception as exc:  # noqa: BLE001
                log.exception("shell poll step %s failed", name)
                self._report_poll_error(exc)
        from ..app.commands.sync_trace import sync_debug_enabled, sync_log

        if sync_debug_enabled():
            state = self.svc.store.snapshot()
            live = state.bridge.liveness
            memory = self.svc.squad_sync
            retry = "-"
            if memory.retry_after_ts is not None:
                retry = f"{max(0.0, memory.retry_after_ts - self.svc.clock.now_ts()):.0f}"
            sync_log(
                "POLL",
                f"page={self.active} session_id={live.session_id or '-'} "
                f"save_uid={live.save_uid or '-'} hub_ready={bool((live.capabilities or {}).get('hub_ready'))} "
                f"armed={live.armed} teamid={state.squad.teamid if state.squad.teamid is not None else '-'} "
                f"players={len(state.squad.players)} stale={state.squad.stale} "
                f"sync_active={_sync_squad_active(state)} failures={memory.failures} retry_after={retry}",
            )

    # ---- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Idempotent — ``WM_DELETE_WINDOW`` can fire more than once."""
        if self._closing:
            return
        self._closing = True
        try:
            self._unsubscribe()
        except Exception:
            pass
        for handle in self._after_ids:
            try:
                self.root.after_cancel(handle)
            except Exception:
                pass
        self._after_ids.clear()
        try:
            self.svc.executor.shutdown()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> int:
        self._render()
        self.root.mainloop()
        return 0


def run_gui(root: Path | None = None) -> int:
    """Entry point ``companion/cli.py::cmd_gui`` calls. Builds Services and runs.

    Returns a process exit code; import/display failures come back as a non-zero
    code with a printed reason rather than a traceback, because this is what a
    double-clicked exe shows the user.
    """
    from ..bootstrap import build_services

    svc = build_services(root)
    try:
        shell = Shell(svc)
    except Exception as exc:  # noqa: BLE001 — the window is the product
        message = str(exc)
        try:
            svc.executor.shutdown()
        except Exception:
            pass
        print(f"could not open the window: {message}")
        return 70
    return shell.run()
