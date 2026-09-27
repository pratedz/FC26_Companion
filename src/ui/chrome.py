"""App chrome — header, ops bar, footer, sync/arm, Codex connect."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    messagebox = None  # type: ignore

from .. import actions
from .. import apply_service
from .. import card_to_lua
from .. import grok_client
from .. import health_check
from .. import job_history
from .. import le_apply
from .. import paths
from .. import undo_apply
from .. import __version__
from ..ui_theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    CARD,
    DANGER,
    FONT_MONO,
    FONT_UI,
    LIST_BG,
    MUTED,
    MUTED_DIM,
    PANEL,
    RADIUS,
    RADIUS_SM,
    SUCCESS,
    TEXT,
    WARNING,
)
from . import apply_chrome
from . import widgets as w

# Pill tokens with fallbacks (match gui.py)
from .. import ui_theme as _ui_theme

PILL_LIVE_BG = getattr(_ui_theme, "PILL_LIVE_BG", "#052e1c")
PILL_OFF_BG = getattr(_ui_theme, "PILL_OFF_BG", "#422006")
PILL_BUSY_BG = getattr(_ui_theme, "PILL_BUSY_BG", "#083344")

# Secondary actions under Tools (calm top chrome)
_TOOLS_MENU_LABEL = "Tools…"
_TOOLS_ACTIONS = (
    "Best match",
    "Batch squad",
    "Force drain",
    "Queue",
    "History",
    "Health",
    "Copy bridge again",
    "Copy last Lua",
    "How to go LIVE",
    "Install worker only",
    "Remove auto-arm",
)


def _on_tools_menu(app: Any, choice: str) -> None:
    """Dispatch Tools menu; reset label so the same action can re-fire."""
    try:
        menu = getattr(app, "_tools_menu", None)
        if menu is not None:
            menu.set(_TOOLS_MENU_LABEL)
    except Exception:
        pass
    if not choice or choice == _TOOLS_MENU_LABEL:
        return
    if choice == "Remove auto-arm":
        try:
            from .. import product as product_mod

            out = product_mod.remove_inject_autoarm()
            messagebox.showinfo("Remove auto-arm", out.get("next") or str(out))
            app._last_chrome_key = None
            app._refresh_sync_chrome()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Remove auto-arm", str(e))
        return
    handlers = {
        "Best match": getattr(app, "_best_match_for_target", None),
        "Batch squad": getattr(app, "_batch_apply_squad", None),
        "Force drain": getattr(app, "_force_drain", None),
        "Queue": getattr(app, "_show_queue_inspector", None),
        "History": getattr(app, "_show_job_history", None),
        "Health": getattr(app, "_show_health", None),
        "Copy bridge again": getattr(app, "_copy_bridge_script", None),
        "Copy last Lua": getattr(app, "_copy_last_apply_lua", None),
        "How to go LIVE": getattr(app, "_show_arm_guide", None),
        "Install worker only": getattr(app, "_enable_auto_apply", None),
    }
    fn = handlers.get(choice)
    if callable(fn):
        fn()


def _build_header(app: Any) -> None:
    """Minimal header: brand · status pill · one Go LIVE CTA."""
    header = ctk.CTkFrame(app, fg_color=PANEL, corner_radius=0, height=56)
    header.pack(fill="x", side="top")
    header.pack_propagate(False)

    left = ctk.CTkFrame(header, fg_color="transparent")
    left.pack(side="left", padx=16, pady=8)

    title_row = ctk.CTkFrame(left, fg_color="transparent")
    title_row.pack(anchor="w")
    mark = ctk.CTkFrame(
        title_row,
        fg_color=CARD,
        width=30,
        height=30,
        corner_radius=8,
        border_width=1,
        border_color=ACCENT,
    )
    mark.pack(side="left", padx=(0, 8))
    mark.pack_propagate(False)
    ctk.CTkLabel(
        mark,
        text="LE",
        font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
        text_color=ACCENT,
    ).place(relx=0.5, rely=0.5, anchor="center")
    ctk.CTkLabel(
        title_row,
        text="Companion",
        font=ctk.CTkFont(family=FONT_UI, size=18, weight="bold"),
        text_color=TEXT,
    ).pack(side="left")
    ctk.CTkLabel(
        title_row,
        text=f"v{__version__}",
        font=ctk.CTkFont(family=FONT_UI, size=11),
        text_color=MUTED,
    ).pack(side="left", padx=(8, 0))

    right = ctk.CTkFrame(header, fg_color="transparent")
    right.pack(side="right", padx=16)
    brow = ctk.CTkFrame(right, fg_color="transparent")
    brow.pack(anchor="e")

    app._sync_pill = ctk.CTkFrame(
        brow,
        fg_color=PILL_OFF_BG,
        corner_radius=16,
        border_width=2,
        border_color=WARNING,
    )
    app._sync_pill.pack(side="left", padx=(0, 10))
    app._sync_pill_label = ctk.CTkLabel(
        app._sync_pill,
        text="  OFF  ",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=WARNING,
    )
    app._sync_pill_label.pack(padx=10, pady=6)

    # Single CTA: installs worker, auto-copies bridge, optional auto-arm
    app._inject_btn = w.btn(
        brow,
        "Go LIVE",
        app._one_click_inject_arm,
        kind="primary",
        width=100,
        height=32,
        icon="bridge",
    )
    app._inject_btn.pack(side="left", padx=2)
    app._sync_btn = app._inject_btn  # _refresh_sync_chrome reuses this
    app._copy_bridge_btn = None  # advanced: Tools → Copy bridge again

    app._bridge_status_label = ctk.CTkLabel(
        right,
        textvariable=app.bridge_status_var,
        font=ctk.CTkFont(size=10),
        text_color=MUTED,
        anchor="e",
    )
    app._bridge_status_label.pack(anchor="e", pady=(2, 0))
    app._refresh_sync_chrome()


def _build_ops_bar(app: Any) -> None:
    """Slim ops: target/queue line + Undo + Tools menu."""
    bar = ctk.CTkFrame(app, fg_color=PANEL, corner_radius=0, height=36)
    bar.pack(fill="x", side="top")
    bar.pack_propagate(False)
    row = ctk.CTkFrame(bar, fg_color="transparent")
    row.pack(fill="x", padx=16, pady=4)
    app._ops_line = ctk.CTkLabel(
        row,
        text="Target · —  ·  Queue · 0 pending",
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        anchor="w",
    )
    app._ops_line.pack(side="left", fill="x", expand=True)

    app._tools_menu = ctk.CTkOptionMenu(
        row,
        values=[_TOOLS_MENU_LABEL, *_TOOLS_ACTIONS],
        command=lambda c: _on_tools_menu(app, c),
        width=108,
        height=28,
        fg_color=CARD,
        button_color=CARD,
        button_hover_color=ACCENT_HOVER,
        dropdown_fg_color=PANEL,
        dropdown_hover_color=CARD,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
    )
    app._tools_menu.set(_TOOLS_MENU_LABEL)
    app._tools_menu.pack(side="right", padx=(4, 0))
    w.btn(
        row, "Undo", app._undo_last_apply, kind="ghost", width=72, height=28, icon="back"
    ).pack(side="right", padx=2)


def _build_footer(app: Any) -> None:
    foot = ctk.CTkFrame(app, fg_color=PANEL, corner_radius=0, height=38)
    foot.pack(fill="x", side="bottom")
    foot.pack_propagate(False)
    ctk.CTkLabel(
        foot,
        textvariable=app.status,
        font=ctk.CTkFont(size=12),
        text_color=MUTED,
        anchor="w",
    ).pack(side="left", padx=18)
    # Short path tail only (less industrial noise in footer)
    _ap = str(paths.app_root())
    _ap_short = ("…" + _ap[-42:]) if len(_ap) > 44 else _ap
    ctk.CTkLabel(
        foot,
        text=_ap_short,
        font=ctk.CTkFont(size=10),
        text_color=MUTED_DIM,
        anchor="e",
    ).pack(side="right", padx=18)

# ── shared target picker ─────────────────────────────────────────



def _one_click_inject_arm(app: Any) -> None:
    """Go LIVE: install worker, auto-copy bridge, optional LE Load auto-arm."""
    from .. import product as product_mod

    app.status.set("Go LIVE · preparing worker…")
    try:
        app._set_apply_status(
            "Go LIVE · install worker · copy bridge…",
            prog=0.15,
            state="busy",
            step=1,
        )
    except Exception:
        pass
    try:
        if hasattr(app, "_sync_btn") and app._sync_btn is not None:
            app._sync_btn.configure(text="…")
    except Exception:
        pass

    def work() -> None:
        try:
            # Never patch live_editor.lua — that broke LE launch for users
            out = product_mod.go_live(prefer_autoarm=False)
        except Exception as e:  # noqa: BLE001
            err = str(e)

            def fail(msg: str = err) -> None:
                app.status.set(f"Go LIVE failed · {msg}")
                try:
                    app._set_apply_status(f"✗ Go LIVE · {msg}", prog=0, state="err")
                except Exception:
                    pass
                try:
                    if hasattr(app, "_sync_btn") and app._sync_btn is not None:
                        app._sync_btn.configure(text="Go LIVE")
                except Exception:
                    pass
                messagebox.showerror("Go LIVE failed", msg)

            app.after(0, fail)
            return

        def done() -> None:
            app._last_bridge_line = None
            app._last_chrome_key = None
            try:
                app._refresh_sync_chrome()
            except Exception:
                pass
            state = str(out.get("state") or "")
            title = str(out.get("title") or "Go LIVE")
            body = str(out.get("body") or out.get("next") or "")
            # Always re-copy on UI thread so clipboard is guaranteed after Go LIVE
            clip = bool(out.get("clipboard_ok") or out.get("clipboard_bridge"))
            live = state == "already_live" or bool((out.get("arm") or {}).get("live"))
            need_paste = state == "need_paste"
            if need_paste or not live:
                try:
                    clip = bool(apply_service.copy_bridge_to_clipboard()) or clip
                except Exception:
                    pass

            if live:
                status_line = "LIVE ✓ · apply anytime"
                btn = "LIVE ✓"
                ui_state = "ok"
                prog = 1.0
            elif need_paste:
                status_line = (
                    "Bridge AUTO-COPIED · LE → Ctrl+V → Execute"
                    if clip
                    else "Go LIVE OK · clipboard failed — try Tools → Copy bridge again"
                )
                btn = "Go LIVE"
                ui_state = "warn"
                prog = 0.55
            else:
                status_line = f"Go LIVE · {body.split(chr(10), 1)[0][:80]}"
                btn = "Go LIVE"
                ui_state = "err" if state == "failed" else "warn"
                prog = 0.0 if state == "failed" else 0.5

            app.status.set(status_line[:100])
            try:
                app._set_apply_status(
                    f"{'✓ ' if live else ('→ ' if need_paste else '✗ ')}{status_line}\n{body}",
                    prog=prog,
                    state=ui_state,
                    step=4 if live else (2 if need_paste else 0),
                )
            except Exception:
                pass
            try:
                if hasattr(app, "_sync_btn") and app._sync_btn is not None:
                    app._sync_btn.configure(text=btn)
            except Exception:
                pass
            # Only interrupt with a dialog when the user still must paste
            if need_paste or state == "failed":
                try:
                    if state == "failed":
                        messagebox.showerror(title, body)
                    else:
                        messagebox.showinfo(title, body)
                except Exception:
                    pass

        app.after(0, done)

    threading.Thread(target=work, daemon=True).start()


def _turbo_install_arm(app: Any) -> None:
    """Alias: turbo arm = Go LIVE."""
    _one_click_inject_arm(app)



def _focus_card_search(app: Any) -> None:
    try:
        app._ensure_tab("  Cards  ")
        app.tabs.set("  Cards  ")
        ent = getattr(app, "_card_search_entry", None)
        if ent is not None:
            ent.focus_set()
    except Exception:
        pass



def _maybe_first_run(app: Any) -> None:
    flag = paths.app_root() / ".first_run_done"
    if flag.is_file():
        return
    try:
        messagebox.showinfo(
            "Welcome · LE Companion",
            "Get LIVE in three steps:\n\n"
            "1. Start FC 26 with Live Editor Launcher → load Career\n"
            "2. Click Go LIVE (top of this app) — bridge copies itself\n"
            "3. If asked: LE → Lua Engine → Ctrl+V → Execute once\n\n"
            "When the header shows LIVE ✓, Export squad and Apply work.\n"
            "You should not need a separate Copy bridge click.",
        )
        flag.write_text("1", encoding="utf-8")
    except Exception:
        pass



def _prebuild_card_index_bg(app: Any) -> None:
    """Warm SQLite catalog index so first Search is not a cold multi-second stall."""
    def work() -> None:
        try:
            from .. import card_index

            def prog(frac: float, msg: str) -> None:
                # Keep footer short; do not flood APPLY dock
                if frac >= 1.0 or frac < 0.08:
                    app.after(
                        0,
                        lambda m=msg: app.status.set(f"Index · {m}"[:100]),
                    )

            scope = card_index._default_years_scope(paths.card_db_dir())
            card_index.ensure_index(years=scope, progress=prog)
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()



def _force_drain(app: Any) -> None:
    clip = False
    live = False
    try:
        from .. import product as product_mod

        out = product_mod.force_turbo_drain_signal()
        clip = bool(out.get("clipboard_force_drain"))
        live = bool(out.get("live"))
        if not clip:
            apply_service.copy_bridge_to_clipboard()
            clip = True
    except Exception:
        try:
            apply_service.copy_bridge_to_clipboard()
            clip = True
        except Exception:
            pass
    if live:
        app._set_apply_status(
            "Drain script COPIED · LE Lua Engine → Ctrl+V → Execute",
            prog=0.6,
            state="warn",
        )
        messagebox.showinfo(
            "Drain queue",
            "A short drain script is on your clipboard.\n\n"
            "LE → Features → Lua Engine → Ctrl+V → Execute\n\n"
            "(Only needed if jobs sit in queue while LIVE.)",
        )
    else:
        app._set_apply_status(
            "Not LIVE · full bridge COPIED · click Go LIVE first, then paste in LE",
            prog=0.3,
            state="err",
        )
        messagebox.showwarning(
            "Not LIVE yet",
            "Worker is OFF. Full bridge was copied.\n\n"
            "Easier path: click Go LIVE in the header, then follow the dialog.\n"
            "Or: LE → Lua Engine → Ctrl+V → Execute once.",
        )



def _show_health(app: Any) -> None:
    rep = health_check.format_report()
    app._set_apply_status("Health check · see dialog", prog=0.3, state="info")
    messagebox.showinfo("Health check", rep)



def _show_job_history(app: Any) -> None:
    rows = job_history.load()
    if not rows:
        messagebox.showinfo("Job history", "No jobs yet. Apply a card or export squad.")
        return
    text = "\n".join(job_history.format_line(r) for r in rows[:25])
    messagebox.showinfo("Job history (latest)", text)



def _show_queue_inspector(app: Any) -> None:
    pending = le_apply.list_pending_lua()
    lr = le_apply.last_result_text() or "(none)"
    js = le_apply.job_status_text() or "(none)"
    lines = [f"Pending: {len(pending)}", f"Last result: {lr}", f"Job status: {js}", ""]
    for p in pending[:20]:
        lines.append(f"  · {p.name}")
    if not pending:
        lines.append("  (empty)")
    win = ctk.CTkToplevel(app)
    win.title("Queue inspector")
    win.geometry("520x360")
    win.configure(fg_color=BG)
    box = ctk.CTkTextbox(win, font=ctk.CTkFont(family=FONT_MONO, size=12))
    box.pack(fill="both", expand=True, padx=12, pady=12)
    box.insert("1.0", "\n".join(lines))
    box.configure(state="disabled")
    w.btn(win, "Clear all pending", app._clear_queue_all, kind="ghost", width=140).pack(
        pady=(0, 12)
    )



def _clear_queue_all(app: Any) -> None:
    n = le_apply.clear_stale_jobs()
    app._set_apply_status(f"Cleared {n} pending job(s)", prog=0, state="ok")



def _undo_last_apply(app: Any) -> None:
    try:
        lua = undo_apply.generate_undo_lua()
    except ValueError as e:
        messagebox.showwarning("Undo", str(e))
        return
    snap = undo_apply.load_snapshot() or {}
    # Prefer multi-snapshot turbo restore
    try:
        app._restore_last_snapshot()
        return
    except Exception:
        pass
    detail = f"Undo/reapply · id {snap.get('target_id')} · {snap.get('label')}"
    tid = snap.get("target_id")
    try:
        tid_i = int(tid) if tid is not None else None
    except (TypeError, ValueError):
        tid_i = None
    app._queue_and_wait_bridge(
        lua,
        stem=f"undo_{snap.get('target_id') or 'x'}",
        success_title="Undo",
        detail=detail,
        kind="undo",
        target_id=tid_i,
    )



def _refresh_sync_chrome(app: Any) -> None:
    """Header pill + short status only. Skip widget.configure when state unchanged."""
    try:
        state = le_apply.sync_state()
        busy = bool(getattr(app, "_apply_busy", False))
        chrome_key = (state, busy)
        # Always keep header status string fresh (StringVar is cheap)
        line = le_apply.apply_status_line(short=True)
        if getattr(app, "_last_bridge_line", None) != line:
            app._last_bridge_line = line
            app.bridge_status_var.set(line)

        if getattr(app, "_last_chrome_key", None) == chrome_key:
            # State unchanged — still gate apply once if never set
            if not hasattr(app, "_last_apply_enabled"):
                apply_chrome.set_apply_enabled(app, state == "live" and not busy)
            return
        app._last_chrome_key = chrome_key

        if busy:
            pill_txt, pill_fg, pill_bd, pill_bg, lab = (
                "  APPLYING  ",
                ACCENT,
                ACCENT,
                PILL_BUSY_BG,
                ACCENT,
            )
            btn_txt = "Go LIVE"
            apply_ok = state == "live"
        elif state == "live":
            pill_txt, pill_fg, pill_bd, pill_bg, lab = (
                "  LIVE ✓  ",
                SUCCESS,
                SUCCESS,
                PILL_LIVE_BG,
                SUCCESS,
            )
            btn_txt = "LIVE ✓"
            apply_ok = True
        elif state == "installed":
            pill_txt, pill_fg, pill_bd, pill_bg, lab = (
                "  OFF  ",
                WARNING,
                WARNING,
                PILL_OFF_BG,
                WARNING,
            )
            btn_txt = "Go LIVE"
            apply_ok = False
        else:
            pill_txt, pill_fg, pill_bd, pill_bg, lab = (
                "  OFF  ",
                DANGER,
                DANGER,
                PILL_OFF_BG,
                DANGER,
            )
            btn_txt = "Go LIVE"
            apply_ok = False
        if hasattr(app, "_sync_pill") and app._sync_pill is not None:
            app._sync_pill.configure(border_color=pill_bd, fg_color=pill_bg)
        if hasattr(app, "_sync_pill_label") and app._sync_pill_label is not None:
            app._sync_pill_label.configure(text=pill_txt, text_color=pill_fg)
        if hasattr(app, "_bridge_status_label") and app._bridge_status_label is not None:
            app._bridge_status_label.configure(text_color=lab)
        if hasattr(app, "_sync_btn") and app._sync_btn is not None and not busy:
            cur = ""
            try:
                cur = str(app._sync_btn.cget("text") or "")
            except Exception:
                pass
            # Don't stomp mid-action label
            if cur not in ("…", "Installing…"):
                app._sync_btn.configure(text=btn_txt)
        if not busy:
            apply_chrome.set_apply_enabled(app, apply_ok)
    except Exception:
        pass



def _require_live_worker(app: Any, *, action: str = "Apply") -> bool:
    """Block apply until LIVE; auto-copy bridge and point at one Go LIVE path."""
    if le_apply.bridge_alive(90):
        return True
    apply_chrome.set_apply_enabled(app, False)
    clip = False
    try:
        clip = bool(apply_service.copy_bridge_to_clipboard())
    except Exception:
        pass
    app._set_apply_status(
        f"Not LIVE · cannot {action}\n"
        + (
            "Bridge COPIED · click Go LIVE (or LE → Ctrl+V → Execute)"
            if clip
            else "Click Go LIVE in the header"
        ),
        prog=0,
        state="err",
        step=2,
    )
    try:
        messagebox.showwarning(
            f"Not LIVE — cannot {action}",
            f"The game worker is OFF.\n\n"
            f"{'Bridge is already on your clipboard.' if clip else 'Click Go LIVE to copy the bridge.'}\n\n"
            "1. Click Go LIVE (header)\n"
            "2. LE → Features → Lua Engine → Ctrl+V → Execute once\n"
            "3. Wait for LIVE ✓, then try again",
        )
    except Exception:
        pass
    return False



def _tick_bridge_status(app: Any) -> None:
    """Poll worker/queue slowly — never every frame; skip redundant configures."""
    try:
        app._refresh_sync_chrome()
        pending = le_apply.list_pending_lua()
        n = len(pending)
        if not app._apply_busy and hasattr(app, "apply_status_var"):
            alive = le_apply.bridge_alive(90)
            age_txt = le_apply.format_age(le_apply.bridge_heartbeat_age_sec())
            # Keep bridge on clipboard while OFF so paste is always ready
            if not alive:
                import time as _time

                last = float(getattr(app, "_last_auto_clip_ts", 0) or 0)
                now = _time.time()
                if now - last >= 25.0:
                    app._last_auto_clip_ts = now
                    try:
                        apply_service.auto_clipboard_bridge_if_off()
                    except Exception:
                        pass
            cur = (app.apply_status_var.get() or "")
            can_overwrite = cur.startswith(
                ("IDLE", "WAITING", "WORKER OFF", "NOT LIVE", "✓ Worker", "Installed")
            )
            tip = None
            if n > 0 and can_overwrite:
                tip = (
                    f"WAITING · {n} job(s) · {'LIVE' if alive else 'NOT LIVE'} "
                    f"(pulse {age_txt})"
                )
            elif n == 0 and can_overwrite and cur.startswith(
                ("WAITING", "IDLE", "NOT LIVE", "WORKER OFF")
            ):
                tip = (
                    f"IDLE · queue empty · {'LIVE' if alive else 'WORKER OFF'} "
                    f"(pulse {age_txt})"
                    + ("" if alive else " · bridge auto-copied")
                )
            if tip is not None and tip != cur:
                app.apply_status_var.set(tip)
                app.status.set(tip[:100])
        if hasattr(app, "_ops_line") and app._ops_line is not None:
            name = ""
            if hasattr(app, "target_name_var"):
                name = (app.target_name_var.get() or "").strip()
            ops = f"Target · {name or '—'}  ·  Queue · {n} pending"
            if getattr(app, "_last_ops_line", None) != ops:
                app._last_ops_line = ops
                app._ops_line.configure(text=ops)
    except Exception:
        pass
    try:
        from .. import ui_perf as _perf

        _ms = int(getattr(_perf, "BRIDGE_TICK_MS", 5000) or 5000)
    except Exception:
        _ms = 5000
    app.after(_ms, app._tick_bridge_status)



def _copy_bridge_script(app: Any) -> None:
    """Advanced: re-copy worker Lua (Go LIVE already does this automatically)."""
    try:
        le_apply.install_bridge()
        src = le_apply.bridge_script_text_for_clipboard()
        ok = actions.copy_to_clipboard(src)
        nlines = src.count("\n") + (0 if src.endswith("\n") else 1)
        if ok:
            app._set_apply_status(
                f"✓ Bridge COPIED again ({nlines} lines)\n"
                f"LE → Lua Engine → Ctrl+A → Delete → Ctrl+V → Execute",
                prog=1.0,
                state="ok",
                step=0,
            )
            try:
                messagebox.showinfo(
                    "Bridge copied",
                    "Bridge is on the clipboard.\n\n"
                    "LE → Features → Lua Engine → Ctrl+V → Execute once.\n\n"
                    "Tip: next time use Go LIVE — it copies for you.",
                )
            except Exception:
                pass
        else:
            app._set_apply_status(
                "✗ Clipboard copy failed — open lua/scripts/00_le_companion_bridge.lua and copy all",
                prog=0,
                state="err",
            )
    except Exception as e:  # noqa: BLE001
        app._set_apply_status(f"✗ Copy bridge failed · {e}", prog=0, state="err")



def _copy_last_apply_lua(app: Any) -> None:
    """Copy the latest generated apply job (or rebuild from selection) to clipboard."""
    try:
        q = le_apply.queue_dir()
        # Prefer newest apply_*.lua in queue or done
        candidates = sorted(
            list(q.glob("apply_*.lua")) + list((q / "done").glob("apply_*.lua")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        text = None
        label = ""
        if candidates:
            text = candidates[0].read_text(encoding="utf-8")
            label = candidates[0].name
        elif app._hits:
            # Rebuild from current card selection
            sel = app.variant_list.curselection()
            idx = int(sel[0]) if sel else app._selected_idx
            raw = (app.target_var.get() or "").strip()
            target = int(raw) if raw.isdigit() else None
            if target is not None and 0 <= idx < len(app._hits):
                text = card_to_lua.generate_apply_card_lua(app._hits[idx], target)
                label = f"apply_{target}_live.lua"
        if not text:
            app._set_apply_status(
                "✗ No apply Lua yet · Search a card, set Target, then Apply or Copy apply Lua",
                prog=0,
                state="err",
            )
            return
        ok = actions.copy_to_clipboard(text)
        nlines = text.count("\n") + (0 if text.endswith("\n") else 1)
        if ok:
            app._set_apply_status(
                f"✓ Apply Lua COPIED · {label} ({nlines} lines, CRLF)\n"
                f"LE → Lua Engine → paste → Execute (manual one-shot apply)",
                prog=1.0,
                state="ok",
                step=0,
            )
        else:
            app._set_apply_status("✗ Clipboard copy failed", prog=0, state="err")
    except Exception as e:  # noqa: BLE001
        app._set_apply_status(f"✗ Copy apply Lua failed · {e}", prog=0, state="err")



def _enable_auto_apply(app: Any) -> None:
    """Home / Tools entry → same as Go LIVE (install + auto-copy)."""
    _one_click_inject_arm(app)



def _set_apply_steps(app: Any, step: int) -> None:
    apply_chrome.set_apply_steps(app, step)



def _set_apply_status(app: Any, msg: str, *, prog: Optional[float] = None, state: str = "info", step: Optional[int] = None, paint: bool = True, ) -> None:
    apply_chrome.set_apply_status(
        app, msg, prog=prog, state=state, step=step, paint=paint
    )



def _set_apply_btn_text(app: Any, text: str) -> None:
    apply_chrome.set_apply_btn_text(app, text)



def _show_arm_guide(app: Any) -> None:
    """Short how-to: one button, auto clipboard."""
    clip = False
    try:
        clip = bool(apply_service.copy_bridge_to_clipboard())
    except Exception:
        pass
    live = le_apply.bridge_alive(90)
    auto = le_apply.autoarm_installed()
    if live:
        msg = "You're already LIVE ✓.\n\nApply, Boost, and Add team work now."
        status = "✓ LIVE — apply anytime"
        st = "ok"
    else:
        msg = (
            "How to go LIVE\n\n"
            "1. Start FC 26 with Live Editor Launcher → Career Mode\n"
            "2. Click Go LIVE (top of Companion) — bridge copies automatically\n"
            "3. If still OFF: LE → Features → Lua Engine → Ctrl+V → Execute once\n"
            "4. Header shows LIVE ✓\n\n"
            f"{'Bridge is on your clipboard right now.' if clip else 'Click Go LIVE to copy the bridge.'}\n"
            f"Auto-arm for next launches: {'ON' if auto else 'OFF'}"
        )
        status = "→ Click Go LIVE" if not auto else "→ Go LIVE or restart LE after arm"
        st = "warn"
    app._set_apply_status(status, prog=1.0 if live else 0.5, state=st)
    try:
        messagebox.showinfo("How to go LIVE", msg)
    except Exception:
        pass



def _queue_and_wait_bridge(app: Any, lua: str, *, stem: str, success_title: str, detail: str, kind: str = "apply", target_id: Optional[int] = None, ) -> None:
    """Queue via product turbo path; shared status presentation."""
    del success_title
    from .. import product as product_mod
    from . import apply_flow

    live0 = le_apply.bridge_alive(90)
    age_txt = le_apply.format_age(le_apply.bridge_heartbeat_age_sec())
    if live0:
        busy = f"2/4 Queued · worker LIVE (pulse {age_txt})"
    else:
        busy = (
            f"2/4 Queued · OFF (pulse {age_txt})\n"
            "→ Click Go LIVE · LE Lua Engine → Ctrl+V → Execute"
        )

    def job(on_tick: Any) -> Any:
        return product_mod.turbo_apply_lua(
            lua,
            stem=stem,
            detail=detail,
            kind=kind,
            target_id=target_id,
            wait=True,
            on_tick=on_tick,
            to_generated=True,
        )

    apply_flow.run_apply_job(
        app,
        job,
        detail=detail,
        busy_msg=busy,
        with_ticks=True,
        detailed=True,
        copy_bridge_on_wait=True,
    )



def _grok_connect_popup(app: Any) -> None:
    win = ctk.CTkToplevel(app)
    win.title("Connect Codex")
    win.geometry("520x320")
    win.configure(fg_color=BG)
    win.transient(app)
    win.grab_set()

    detail = grok_client.auth_detail()

    w.label(win, "Codex CLI login", bold=True, size=18).pack(
        anchor="w", padx=22, pady=(20, 4)
    )
    w.label(
        win,
        "Uses the ChatGPT session from `codex login`. No API key.",
        muted=True,
        size=12,
    ).pack(anchor="w", padx=22)

    status_l = ctk.CTkLabel(
        win,
        text=detail.get("status") or grok_client.auth_status(),
        text_color=SUCCESS if detail.get("connected") else MUTED,
        font=ctk.CTkFont(size=12, weight="bold"),
        wraplength=460,
        justify="left",
    )
    status_l.pack(anchor="w", padx=22, pady=(10, 8))

    panel = w.panel(win)
    panel.pack(fill="x", padx=18, pady=6)
    w.label(panel, "Reuse the login already on this PC", bold=True, size=13).pack(
        anchor="w", padx=14, pady=(12, 2)
    )
    w.label(
        panel,
        "Found Codex session" if detail.get("has_grok_build") else "No Codex session yet — run `codex login`",
        muted=True,
        size=11,
    ).pack(anchor="w", padx=14)

    def use_codex() -> None:
        status_l.configure(text="Checking Codex CLI login…", text_color=MUTED)

        def work() -> None:
            try:
                info = grok_client.import_grok_build_session()
                ok, msg = grok_client.test_connection()
                st = info.get("status") or msg

                def done() -> None:
                    app.grok_status_var.set(grok_client.auth_status())
                    if ok:
                        status_l.configure(text=st, text_color=SUCCESS)
                        messagebox.showinfo("Connected", f"Using Codex CLI login.\n{msg}")
                        win.destroy()
                    else:
                        status_l.configure(text=msg, text_color=DANGER)

                app.after(0, done)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                app.after(0, lambda m=err: status_l.configure(text=m, text_color=DANGER))

        threading.Thread(target=work, daemon=True).start()

    btn = w.btn(panel, "Use Codex CLI login", use_codex, kind="accent", width=220)
    btn.pack(anchor="w", padx=14, pady=(8, 14))
    if not detail.get("has_grok_build"):
        btn.configure(state="disabled")

    bot = ctk.CTkFrame(win, fg_color="transparent")
    bot.pack(fill="x", padx=18, pady=12)
    w.btn(bot, "Close", win.destroy, kind="secondary", width=90).pack(side="right", padx=4)


