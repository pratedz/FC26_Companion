"""Boost tab — packs, profile cards, batch/match helpers."""

from __future__ import annotations

import threading
from typing import Any, Optional

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    messagebox = None  # type: ignore

from ... import boost_queue as boost_q
from ... import profiles
from ... import target_players
from ...ui_theme import (
    ACCENT,
    ACCENT_DIM,
    BG,
    BORDER,
    FONT_UI,
    LIST_BG,
    MUTED,
    MUTED_DIM,
    PANEL,
    RADIUS,
    RADIUS_SM,
    SP2,
    SP3,
    SP4,
    SUCCESS,
    TEXT,
    WARNING,
)
from .. import widgets as w


def category_icon(category: str) -> str:
    """Map profile category → assets/icons stem for Boost cards."""
    cat = (category or "").strip().lower()
    if cat == "user_team" or "fitness" in cat:
        return "fitness"
    if cat == "user_team_event":
        return "refresh"
    if cat == "contracts":
        return "contract"
    if cat == "mass_edit":
        return "unlock"
    if cat in ("unlocks", "unlock"):
        return "unlock"
    if cat == "squad":
        return "export"
    return "medal"

def _build_profiles_tab(app: Any) -> None:
    """Boost tab — shell + packs immediately; profile cards in idle batches.

    Building 15 nested CTk card trees at once freezes the tab switch.
    Paint chrome first, then add cards in small batches so the UI stays live.
    """
    # Invalidate any in-flight batch paint from a prior build
    app._boost_gen = int(getattr(app, "_boost_gen", 0) or 0) + 1
    gen = app._boost_gen

    # Tear down prior build if any (re-entry / hot reload safety)
    for child in list(app.tab_profiles.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass

    wrap = ctk.CTkScrollableFrame(app.tab_profiles, fg_color=BG)
    wrap.pack(fill="both", expand=True, padx=4, pady=4)
    app._boost_scroll = wrap

    # Shared fonts (CTkFont per-label was a measurable cost on open)
    app._boost_font_title = ctk.CTkFont(family=FONT_UI, size=18, weight="bold")
    app._boost_font_sub = ctk.CTkFont(family=FONT_UI, size=13)
    app._boost_font_badge = ctk.CTkFont(family=FONT_UI, size=10, weight="bold")
    app._boost_font_card = ctk.CTkFont(family=FONT_UI, size=14, weight="bold")
    app._boost_font_desc = ctk.CTkFont(family=FONT_UI, size=12)

    ctk.CTkLabel(
        wrap,
        text="Boost my squad",
        font=app._boost_font_title,
        text_color=TEXT,
        anchor="w",
    ).pack(anchor="w", padx=8, pady=(6, 2))
    ctk.CTkLabel(
        wrap,
        text="Queue mode · mash Run on many boosts — they stack into LE, no wait per click.",
        font=app._boost_font_sub,
        text_color=MUTED,
        anchor="w",
    ).pack(anchor="w", padx=8, pady=(0, SP2))

    _build_queue_panel(app, wrap)

    # Synergy workflows first (multi-feature chains)
    _build_workflow_packs_panel(app, wrap)

    # Product packs row (matchday / squad boost / CE career packs) — keep light
    packs_row = ctk.CTkFrame(wrap, fg_color="transparent")
    packs_row.pack(fill="x", padx=8, pady=(0, 10))
    try:
        from ... import product as _product

        for pack in _product.list_packs():
            if pack.get("category") not in (
                "boost",
                "career",
                "mass",
                "squad",
                "visual",
                "export",
                "unlocks",
            ):
                continue
            w.btn(
                packs_row,
                pack["label"],
                lambda pid=pack["id"]: app._run_pack(pid),
                kind="primary" if pack["id"] in ("matchday", "squad_boost") else "accent",
                width=140,
                height=30,
            ).pack(side="left", padx=3, pady=2)
    except Exception:
        pass

    # Career ops (transfer / loan / release / budget) — LE DOC APIs
    _build_career_ops_panel(app, wrap)

    grid = ctk.CTkFrame(wrap, fg_color="transparent")
    grid.pack(fill="both", expand=True, padx=4)
    app._boost_grid = grid
    for c in range(3):
        grid.columnconfigure(c, weight=1)

    try:
        items = list(profiles.load_profiles())
    except Exception:
        items = []
    app._boost_queue = items
    app._boost_built_i = 0
    # Shell only this frame — first cards on next idle so tab paint is instant
    from ... import ui_perf as _perf

    first = int(getattr(_perf, "BOOST_FIRST_BATCH", 3) or 3)
    gap = int(getattr(_perf, "BOOST_BATCH_GAP_MS", 16) or 16)
    app.after(
        1,
        lambda g=gen, b=first: app._boost_fill_cards(batch=b, gen=g),
    )



def _build_workflow_packs_panel(app: Any, parent: Any) -> None:
    """Multi-feature synergy packs (export + boost, season kickoff, signing settle)."""
    panel = ctk.CTkFrame(
        parent, fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=ACCENT
    )
    panel.pack(fill="x", padx=8, pady=(0, 10))
    ctk.CTkLabel(
        panel,
        text="Workflows · combined packs",
        font=getattr(app, "_boost_font_card", None),
        text_color=TEXT,
        anchor="w",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        panel,
        text="One click stacks several features into the turbo queue (export + boost + contracts…).",
        font=getattr(app, "_boost_font_desc", None),
        text_color=MUTED,
        anchor="w",
    ).pack(anchor="w", padx=10, pady=(0, 6))
    row = ctk.CTkFrame(panel, fg_color="transparent")
    row.pack(fill="x", padx=10, pady=(0, 10))
    try:
        from ... import product as _product

        for pack in _product.list_packs():
            if pack.get("category") != "workflow":
                continue
            w.btn(
                row,
                pack["label"],
                lambda pid=pack["id"]: _run_workflow_pack(app, pid),
                kind="primary",
                width=150,
                height=32,
            ).pack(side="left", padx=3, pady=2)
    except Exception:
        pass
    # Cross-tab shortcuts
    w.btn(
        row,
        "Add team →",
        lambda: app._goto("  Add team  "),
        kind="ghost",
        width=100,
        height=32,
    ).pack(side="left", padx=(SP3, 2))
    w.btn(
        row,
        "Cards →",
        lambda: app._goto("  Cards  "),
        kind="ghost",
        width=90,
        height=32,
    ).pack(side="left", padx=2)


def _run_workflow_pack(app: Any, pack_id: str) -> None:
    """Prefer non-blocking boost queue so multi-step packs stack cleanly."""
    use_queue = True
    try:
        var = getattr(app, "boost_queue_mode", None)
        if var is not None and hasattr(var, "get"):
            use_queue = bool(var.get())
    except Exception:
        use_queue = True
    if use_queue:
        try:
            from ... import boost_queue as boost_q

            jobs = boost_q.get_boost_queue().enqueue_pack(pack_id)
            app.status.set(f"Workflow queued · {pack_id} · {len(jobs)} job(s)")
            try:
                if hasattr(app, "_boost_refresh_queue_ui"):
                    app._boost_refresh_queue_ui()
            except Exception:
                pass
            return
        except Exception:
            pass
    app._run_pack(pack_id)


def _build_career_ops_panel(app: Any, parent: Any) -> None:
    """Minimal CE-parity career ops: transfer / loan / release / budget."""
    panel = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=BORDER)
    panel.pack(fill="x", padx=8, pady=(0, 10))
    ctk.CTkLabel(
        panel,
        text="Career ops (LE API)",
        font=getattr(app, "_boost_font_card", None),
        text_color=TEXT,
        anchor="w",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        panel,
        text="Transfer / loan / release / budget — queues LE Lua (must be LIVE in CM). Synergy: squad row locks Player ID.",
        font=getattr(app, "_boost_font_desc", None),
        text_color=MUTED,
        anchor="w",
    ).pack(anchor="w", padx=10, pady=(0, 6))

    if not hasattr(app, "career_pid_var"):
        app.career_pid_var = ctk.StringVar(value="")
        app.career_team_var = ctk.StringVar(value="")
        app.career_budget_var = ctk.StringVar(value="50000000")
        app.career_fee_var = ctk.StringVar(value="0")
        app.career_months_var = ctk.StringVar(value="12")

    row = ctk.CTkFrame(panel, fg_color="transparent")
    row.pack(fill="x", padx=10, pady=(0, 8))
    ctk.CTkLabel(row, text="Player ID", text_color=MUTED, width=70).pack(side="left")
    ctk.CTkEntry(row, textvariable=app.career_pid_var, width=90, height=28).pack(side="left", padx=4)
    ctk.CTkLabel(row, text="Team ID", text_color=MUTED, width=60).pack(side="left")
    ctk.CTkEntry(row, textvariable=app.career_team_var, width=80, height=28).pack(side="left", padx=4)
    ctk.CTkLabel(row, text="Fee", text_color=MUTED, width=30).pack(side="left")
    ctk.CTkEntry(row, textvariable=app.career_fee_var, width=80, height=28).pack(side="left", padx=4)
    ctk.CTkLabel(row, text="Budget", text_color=MUTED, width=50).pack(side="left")
    ctk.CTkEntry(row, textvariable=app.career_budget_var, width=100, height=28).pack(side="left", padx=4)

    btns = ctk.CTkFrame(panel, fg_color="transparent")
    btns.pack(fill="x", padx=10, pady=(0, 10))

    # Values snapshotted on the UI thread by _run before the worker starts.
    _snapshot: dict[str, str] = {}

    def _snap(key: str, var: Any) -> int:
        raw = _snapshot.get(key)
        if raw is None:
            raw = (var.get() or "0").strip() or "0"
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _pid() -> int:
        return _snap("pid", app.career_pid_var)

    def _tid() -> int:
        return _snap("tid", app.career_team_var)

    def _fee() -> int:
        return _snap("fee", app.career_fee_var)

    def _budget() -> int:
        return _snap("budget", app.career_budget_var)

    def _run(label: str, fn: Any) -> None:
        # The callables passed in read Tk variables (_pid/_tid/career_fee_var).
        # Tcl is not thread-safe, so snapshot every field on the UI thread here
        # and let _pid/_tid/_fee serve from the snapshot inside the worker.
        _snapshot.clear()
        try:
            _snapshot.update(
                pid=(app.career_pid_var.get() or "0").strip() or "0",
                tid=(app.career_team_var.get() or "0").strip() or "0",
                fee=(app.career_fee_var.get() or "0").strip() or "0",
                budget=(app.career_budget_var.get() or "0").strip() or "0",
            )
        except Exception:  # noqa: BLE001
            pass

        def work() -> None:
            try:
                r = fn()
                msg = f"{label}: {getattr(r, 'outcome', r)} {getattr(r, 'reason', '')}"
                app.after(0, lambda: app.status.set(msg[:120]))
            except Exception as e:  # noqa: BLE001
                err = str(e)
                app.after(0, lambda m=err: messagebox.showerror(label, m) if messagebox else None)

        threading.Thread(target=work, daemon=True).start()

    def do_transfer() -> None:
        from ... import product as _p

        _run(
            "Transfer",
            lambda: _p.career_transfer(
                playerid=_pid(),
                to_teamid=_tid(),
                transfersum=_fee(),
                wait=True,
            ),
        )

    def do_loan() -> None:
        from ... import product as _p

        months = int((app.career_months_var.get() or "12").strip() or "12")
        _run(
            "Loan",
            lambda: _p.career_loan(playerid=_pid(), to_teamid=_tid(), length_months=months, wait=True),
        )

    def do_release() -> None:
        from ... import product as _p

        _run("Release", lambda: _p.career_release(playerid=_pid(), wait=True))

    def do_budget() -> None:
        from ... import product as _p

        amt = int((app.career_budget_var.get() or "0").strip() or "0")
        _run("Budget", lambda: _p.career_set_budget(amount=amt, wait=True))

    def do_log_budget() -> None:
        from ... import product as _p

        _run("GetBudget", lambda: _p.career_get_budget(wait=True))

    w.btn(btns, "Transfer", do_transfer, kind="primary", width=100, height=28).pack(side="left", padx=3)
    w.btn(btns, "Loan", do_loan, kind="secondary", width=80, height=28).pack(side="left", padx=3)
    w.btn(btns, "Release", do_release, kind="ghost", width=80, height=28).pack(side="left", padx=3)
    w.btn(btns, "Set budget", do_budget, kind="accent", width=100, height=28).pack(side="left", padx=3)
    w.btn(btns, "Log budget", do_log_budget, kind="ghost", width=90, height=28).pack(side="left", padx=3)


def _boost_fill_cards(app: Any, batch: int = 3, gen: Optional[int] = None) -> None:
    """Append up to `batch` profile cards; schedule remainder on idle."""
    if gen is not None and gen != getattr(app, "_boost_gen", None):
        return  # superseded by a newer Boost rebuild
    grid = getattr(app, "_boost_grid", None)
    queue = getattr(app, "_boost_queue", None)
    if grid is None or queue is None:
        return
    try:
        if not grid.winfo_exists():
            return
    except Exception:
        return
    from ... import ui_perf as _perf

    batch = max(1, int(batch or getattr(_perf, "BOOST_BATCH", 3) or 3))
    start = int(getattr(app, "_boost_built_i", 0) or 0)
    end = min(start + batch, len(queue))
    cols = 3
    card_min_h = 118  # shorter → less CTkScrollableFrame canvas work on wheel
    f_badge = getattr(app, "_boost_font_badge", None)
    f_card = getattr(app, "_boost_font_card", None)
    f_desc = getattr(app, "_boost_font_desc", None)

    try:
        for i in range(start, end):
            p = queue[i]
            r, c = divmod(i, cols)
            card = ctk.CTkFrame(
                grid,
                fg_color=PANEL,
                corner_radius=RADIUS,
                border_width=1,
                border_color=ACCENT_DIM,
                height=card_min_h,
            )
            card.grid(row=r, column=c, sticky="nsew", padx=5, pady=5)
            grid.rowconfigure(r, weight=1, minsize=card_min_h)

            # Flat card (no head subframe) — fewer CTk frames per profile
            badge = (p.category or "boost").replace("_", " ").upper()
            ctk.CTkLabel(
                card,
                text=badge,
                font=f_badge,
                text_color=ACCENT,
                anchor="w",
            ).pack(anchor="w", padx=10, pady=(8, 0))
            ctk.CTkLabel(
                card,
                text=p.label,
                font=f_card,
                text_color=TEXT,
                anchor="w",
                wraplength=220,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(2, 0))

            desc = (p.description or "").strip().replace("\n", " ")
            if len(desc) > 52:
                desc = desc[:49].rstrip() + "…"
            ctk.CTkLabel(
                card,
                text=desc or "Ready to queue.",
                font=f_desc,
                text_color=MUTED,
                anchor="w",
                wraplength=220,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(2, 0))

            # Text-only Run — icons on every card were a main open cost
            w.btn(
                card,
                "Queue",
                lambda pid=p.id: app._run_profile(pid),
                kind="accent",
                width=88,
                height=26,
            ).pack(anchor="w", padx=10, pady=(6, 8))
    except Exception:
        # Widget destroyed mid-batch (tab rebuild / app close)
        return

    app._boost_built_i = end
    if end < len(queue):
        gap = int(getattr(_perf, "BOOST_BATCH_GAP_MS", 16) or 16)
        bnext = int(getattr(_perf, "BOOST_BATCH", 3) or 3)
        # Yield so wheel / tab paint can run between batches
        app.after(gap, lambda g=gen, b=bnext: app._boost_fill_cards(batch=b, gen=g))


# ── Boost multi-run queue UI ─────────────────────────────────────────


def _build_queue_panel(app: Any, parent: Any) -> None:
    """Live job strip: stack many Runs without blocking the UI."""
    if not hasattr(app, "boost_queue_mode"):
        app.boost_queue_mode = ctk.BooleanVar(value=True)

    panel = w.panel(parent)
    panel.pack(fill="x", padx=8, pady=(0, SP3))
    app._boost_q_panel = panel

    head = ctk.CTkFrame(panel, fg_color="transparent")
    head.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    w.label(head, "Apply queue", bold=True, size=13).pack(side="left")

    app.boost_queue_summary = ctk.StringVar(value=boost_q.get_boost_queue().summary_line())
    ctk.CTkLabel(
        head,
        textvariable=app.boost_queue_summary,
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        anchor="e",
    ).pack(side="right")

    opts = ctk.CTkFrame(panel, fg_color="transparent")
    opts.pack(fill="x", padx=SP4, pady=(0, SP2))
    ctk.CTkCheckBox(
        opts,
        text="Queue mode (recommended) — stack Runs, don’t wait for each",
        variable=app.boost_queue_mode,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
        fg_color=SUCCESS,
        hover_color=SUCCESS,
        border_color=BORDER,
    ).pack(side="left")

    btns = ctk.CTkFrame(opts, fg_color="transparent")
    btns.pack(side="right")
    w.btn(
        btns,
        "Clear done",
        lambda: _queue_clear_done(app),
        kind="ghost",
        width=90,
        height=28,
    ).pack(side="left", padx=2)
    w.btn(
        btns,
        "Cancel pending",
        lambda: _queue_cancel_pending(app),
        kind="ghost",
        width=110,
        height=28,
    ).pack(side="left", padx=2)

    list_wrap = ctk.CTkFrame(panel, fg_color=LIST_BG, corner_radius=RADIUS_SM)
    list_wrap.pack(fill="x", padx=SP4, pady=(0, SP2))
    app.boost_queue_list = w.listbox(list_wrap, height=5)
    scroll = ctk.CTkScrollbar(list_wrap, command=app.boost_queue_list.yview)
    app.boost_queue_list.configure(yscrollcommand=scroll.set)
    app.boost_queue_list.pack(side="left", fill="both", expand=True, padx=(SP2, 0), pady=SP2)
    scroll.pack(side="right", fill="y", padx=(0, SP2), pady=SP2)
    app.boost_queue_list.insert("end", "  Empty · press Queue / pack buttons — they stack here")

    ctk.CTkLabel(
        panel,
        text=(
            "LE still runs scripts one-by-one in-game; you don’t wait between clicks. "
            "Keep worker LIVE ✓ so the pile drains automatically."
        ),
        text_color=MUTED_DIM,
        font=ctk.CTkFont(size=11),
        wraplength=720,
        justify="left",
        anchor="w",
    ).pack(fill="x", padx=SP4, pady=(0, SP3))

    # Subscribe once per app
    mq = boost_q.get_boost_queue()

    def _on_q() -> None:
        try:
            app.after(0, lambda: _refresh_queue_ui(app))
        except Exception:
            pass

    # Avoid double-subscribe on tab rebuild
    old = getattr(app, "_boost_q_listener", None)
    if old is not None:
        try:
            mq.unsubscribe(old)
        except Exception:
            pass
    app._boost_q_listener = _on_q
    mq.subscribe(_on_q)
    _refresh_queue_ui(app)


def _refresh_queue_ui(app: Any) -> None:
    if not hasattr(app, "boost_queue_list"):
        return
    mq = boost_q.get_boost_queue()
    try:
        app.boost_queue_summary.set(mq.summary_line())
    except Exception:
        pass
    jobs = mq.jobs_snapshot()
    lb = app.boost_queue_list
    try:
        lb.delete(0, "end")
        if not jobs:
            lb.insert("end", "  Empty · press Queue / pack buttons — they stack here")
        else:
            # newest last, show last 12
            for j in jobs[-12:]:
                lb.insert("end", f"  {j.line()}")
            lb.see("end")
    except Exception:
        pass
    # Dock / footer status without locking Apply
    c = mq.counts()
    try:
        if c["active"] > 0:
            app._set_apply_status(
                f"Boost queue · {c['active']} in flight · {c['applied']} done · {c['error']} err\n"
                f"{mq.summary_line()}",
                prog=min(0.95, 0.2 + 0.7 * (c["applied"] / max(1, c["total"]))),
                state="busy" if c["active"] else "ok",
                step=3 if c["active"] else 4,
                paint=False,
            )
        elif c["applied"] > 0 and c["error"] == 0:
            # Soft success when queue drains
            if not getattr(app, "_apply_busy", False):
                app._set_apply_status(
                    f"✓ Boost queue drained · {c['applied']} applied",
                    prog=1.0,
                    state="ok",
                    step=4,
                    paint=False,
                )
    except Exception:
        pass
    try:
        app.status.set(f"Boost · {mq.summary_line()}")
    except Exception:
        pass


def _queue_clear_done(app: Any) -> None:
    n = boost_q.get_boost_queue().clear_finished()
    app.status.set(f"Boost queue · cleared {n} finished")
    _refresh_queue_ui(app)


def _queue_cancel_pending(app: Any) -> None:
    n = boost_q.get_boost_queue().cancel_pending()
    app.status.set(f"Boost queue · cancelled {n} not-yet-written")
    _refresh_queue_ui(app)


def _queue_mode_on(app: Any) -> bool:
    try:
        return bool(app.boost_queue_mode.get())
    except Exception:
        return True


def _run_profile(app: Any, profile_id: str) -> None:
    """Queue (default) or legacy wait-for-each boost."""
    if not app._require_live_worker(action="Run boost"):
        return
    try:
        from ... import profiles as profiles_mod

        prof = profiles_mod.get_profile(profile_id)
        label = prof.label
    except Exception as e:  # noqa: BLE001
        app.status.set(f"Error · {e}")
        messagebox.showerror("Profile failed", str(e))
        return

    if _queue_mode_on(app):
        job = boost_q.get_boost_queue().enqueue_profile(profile_id)
        app.status.set(f"Queued · {job.label} · stack more anytime")
        try:
            app._set_apply_status(
                f"Queued · {job.label}\nBoost queue · no wait — LE drains in order",
                prog=0.25,
                state="busy",
                step=2,
                paint=False,
            )
        except Exception:
            pass
        return

    # Legacy: wait for single apply (slow path)
    try:
        from ... import product as product_mod
        from .. import apply_flow

        detail = f"Boost · {label}"

        def job(_on_tick: Any) -> Any:
            return product_mod.run_profile_turbo(profile_id, wait=True)

        apply_flow.run_apply_job(
            app,
            job,
            detail=detail,
            busy_msg=f"Boost starting · {label}",
            detailed=False,
            copy_bridge_on_wait=False,
        )
    except Exception as e:  # noqa: BLE001
        app.status.set(f"Error · {e}")
        messagebox.showerror("Profile failed", str(e))


def _run_pack(app: Any, pack_id: str) -> None:
    if not app._require_live_worker(action="Run pack"):
        return
    from ... import product as product_mod

    pack = product_mod.PACKS.get(pack_id) or {}
    label = pack.get("label", pack_id)

    if _queue_mode_on(app):
        try:
            jobs = boost_q.get_boost_queue().enqueue_pack(pack_id)
        except Exception as e:  # noqa: BLE001
            app.status.set(f"Pack error · {e}")
            messagebox.showerror("Pack failed", str(e))
            return
        n = len(jobs)
        app.status.set(f"Queued pack · {label} · {n} job(s) — keep clicking others")
        try:
            app._set_apply_status(
                f"Queued pack · {label} · {n} jobs\nNo wait — stack more boosts anytime",
                prog=0.25,
                state="busy",
                step=2,
                paint=False,
            )
        except Exception:
            pass
        return

    # Legacy wait path
    app._set_apply_status(
        f"Pack · {label}…",
        prog=0.1,
        state="busy",
        step=1,
    )
    app._set_apply_btn_text("Pack…")

    def work() -> None:
        try:
            out = product_mod.run_pack(pack_id, wait=True)
        except Exception as e:  # noqa: BLE001
            err = str(e)

            def fail(msg: str = err) -> None:
                app._set_apply_btn_text("Apply to game")
                app._set_apply_status(
                    f"✗ Pack error · {msg}", prog=0, state="err", step=0
                )

            app.after(0, fail)
            return

        def done() -> None:
            app._set_apply_btn_text("Apply to game")
            if out.get("ok"):
                app._set_apply_status(
                    f"✓ Pack {out.get('label')} · {len(out.get('results') or [])} jobs",
                    prog=1.0,
                    state="ok",
                    step=4,
                )
            else:
                app._set_apply_status(
                    f"✗ Pack failed · {out}", prog=0, state="err", step=0
                )

        app.after(0, done)

    threading.Thread(target=work, daemon=True).start()



def _best_match_for_target(app: Any) -> None:
    """Card intelligence: rank FUT cards for locked target id/name."""
    from ... import card_match
    from ... import product as product_mod

    raw = (app.target_var.get() or "").strip()
    name = (app.target_name_var.get() or "").strip()
    year = (app.year_var.get() or "").strip() or None
    try:
        tid = int(raw) if raw else None
    except ValueError:
        tid = None
    if tid:
        matches = product_mod.best_card_for_playerid(
            tid, year=year or "26", limit=12
        )
    elif name:
        ranked = card_match.best_matches(name, year=year, limit=12)
        matches = []
        for sc, c in ranked:
            row = dict(c)
            row["_match_score"] = round(sc, 2)
            matches.append(row)
    else:
        messagebox.showinfo("Best match", "Set Target ID or name first.")
        return
    if not matches:
        messagebox.showinfo("Best match", "No cards ranked. Search/export squad first.")
        return
    app._all_hits = list(matches)
    app._hits = list(matches)
    app._selected_idx = 0
    try:
        app.filter_fav_only.set(False)
    except Exception:
        pass
    app._ensure_tab("  Cards  ")
    app._apply_card_filters()
    try:
        app.variant_list.selection_clear(0, "end")
        app.variant_list.selection_set(0)
    except Exception:
        pass
    app.status.set(
        f"Best match · {matches[0].get('name')} score={matches[0].get('_match_score')}"
    )



def _batch_apply_squad(app: Any) -> None:
    """Apply selected card to all exported squad playerids."""
    if not app._require_live_worker(action="Batch apply"):
        return
    if not app._hits:
        messagebox.showinfo("Batch", "Search/select a card first.")
        return
    sq = target_players.load_squad()
    players = sq.get("players") or []
    ids = []
    for p in players:
        try:
            ids.append(int(p.get("playerid")))
        except (TypeError, ValueError):
            continue
    if not ids:
        messagebox.showinfo("Batch", "Export squad first (pack: Export Squad).")
        return
    sel = app.variant_list.curselection() if hasattr(app, "variant_list") else ()
    idx = int(sel[0]) if sel else app._selected_idx
    card = app._hits[idx] if 0 <= idx < len(app._hits) else app._hits[0]
    cats = app._card_enabled_categories() if hasattr(app, "_card_enabled_categories") else None
    app._set_apply_status(
        f"Batch · {card.get('name')} × {len(ids)} players…",
        prog=0.1,
        state="busy",
        step=1,
    )

    def work() -> None:
        from ... import product as product_mod

        out = product_mod.batch_apply_card(
            card, ids, enabled_categories=cats, wait_each=False, wait_last=True
        )

        def done() -> None:
            app._set_apply_btn_text("Apply to game")
            ok_n = sum(1 for r in out.get("results") or [] if r.get("applied") or r.get("outcome") == "queued_live")
            app._set_apply_status(
                f"{'✓' if out.get('ok') else '·'} Batch {ok_n}/{len(ids)} · {card.get('name')}",
                prog=1.0 if out.get("ok") else 0.5,
                state="ok" if out.get("ok") else "busy",
                step=4,
            )

        app.after(0, done)

    threading.Thread(target=work, daemon=True).start()



def _restore_last_snapshot(app: Any) -> None:
    if not app._require_live_worker(action="Undo/restore"):
        return
    from ... import product as product_mod

    def work() -> None:
        try:
            r = product_mod.restore_snapshot(None, wait=True)
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            app.after(
                0,
                lambda: messagebox.showerror("Restore", err_msg),
            )
            return

        def done() -> None:
            app._set_apply_status(
                f"{'✓' if r.applied else '·'} Restore · {r.outcome} · {r.reason}",
                prog=1.0 if r.applied else 0.5,
                state="ok" if r.applied else "busy",
                step=4,
            )

        app.after(0, done)

    threading.Thread(target=work, daemon=True).start()

# ── cards ────────────────────────────────────────────────────────

