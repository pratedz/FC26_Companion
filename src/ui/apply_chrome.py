"""Shared apply-status chrome — dock is the primary status surface (feedback P0)."""

from __future__ import annotations

from typing import Any, List, Optional

from .. import ui_perf
from ..ui_theme import (
    ACCENT,
    BORDER,
    DANGER,
    MUTED,
    SUCCESS,
    TEXT,
    WARNING,
)

_STEP_LABELS = ("① Write", "② Queue", "③ Bridge", "④ Game")


def set_apply_steps(app: Any, step: int) -> None:
    """Color step chips + keep string var for backward compat.

    step 0: all muted (idle)
    i < step: success / check (done)
    i == step: accent (active)
    i > step: muted (pending)
    """
    labels = list(_STEP_LABELS)
    parts: List[str] = []
    for i, lab in enumerate(labels, start=1):
        if step <= 0:
            parts.append(lab)
        elif i < step:
            parts.append(f"✓ {lab}")
        elif i == step:
            parts.append(f"▶ {lab}")
        else:
            parts.append(lab)
    try:
        app._apply_steps_var.set("  ·  ".join(parts))
    except Exception:
        pass

    if getattr(app, "_last_apply_step_chip", None) == step:
        return
    app._last_apply_step_chip = step
    chips = getattr(app, "_apply_step_labels", None) or []
    for i, chip in enumerate(chips):
        idx = i + 1  # 1-based step index
        try:
            if step <= 0:
                chip.configure(text=labels[i], text_color=MUTED)
            elif idx < step:
                chip.configure(text=f"✓ {labels[i]}", text_color=SUCCESS)
            elif idx == step:
                chip.configure(text=f"▶ {labels[i]}", text_color=ACCENT)
            else:
                chip.configure(text=labels[i], text_color=MUTED)
        except Exception:
            pass


def set_apply_status(
    app: Any,
    msg: str,
    *,
    prog: Optional[float] = None,
    state: str = "info",
    step: Optional[int] = None,
    paint: bool = False,
    footer: bool = True,
) -> None:
    """Update APPLY dock (primary). Footer gets a short one-liner only.

    paint defaults False: forced idletasks during scroll/apply freezes CTk.
    StringVars + configure are enough; Tk paints on next idle naturally.
    """
    # Skip no-op updates (tick + apply spam)
    prev = getattr(app, "_last_apply_status_key", None)
    key = (msg, state, step, prog)
    if prev == key and not paint:
        return
    app._last_apply_status_key = key

    app.apply_status_var.set(msg)
    if footer:
        first = (msg or "").split("\n", 1)[0][:100]
        if len(first) > 90:
            first = first[:87] + "…"
        app.status.set(first)
    if step is not None:
        set_apply_steps(app, step)
    bar_widget = getattr(app, "apply_prog", None) or getattr(app, "_dock_prog", None)
    if prog is not None and bar_widget is not None:
        try:
            bar_widget.set(max(0.0, min(1.0, float(prog))))
        except Exception:
            pass
    color = TEXT
    border = BORDER
    bar = SUCCESS
    if state == "ok":
        color, border, bar = SUCCESS, SUCCESS, SUCCESS
    elif state == "warn":
        color, border, bar = WARNING, WARNING, WARNING
    elif state == "err":
        color, border, bar = DANGER, DANGER, DANGER
    elif state == "busy":
        color, border, bar = ACCENT, ACCENT, ACCENT

    # Only reconfigure chrome when visual state changes
    prev_state = getattr(app, "_last_apply_visual_state", None)
    if prev_state != state:
        app._last_apply_visual_state = state
        try:
            if hasattr(app, "apply_status_label") and app.apply_status_label is not None:
                app.apply_status_label.configure(text_color=color)
            if getattr(app, "_apply_box", None) is not None:
                app._apply_box.configure(border_color=border, border_width=2)
            if hasattr(app, "apply_prog") and app.apply_prog is not None:
                app.apply_prog.configure(progress_color=bar)
            if getattr(app, "_dock_status_label", None) is not None:
                app._dock_status_label.configure(text_color=color)
            if getattr(app, "_dock_box", None) is not None:
                app._dock_box.configure(border_color=border, border_width=2)
            if getattr(app, "_dock_prog", None) is not None:
                app._dock_prog.configure(progress_color=bar)
        except Exception:
            pass
    if prog is not None and getattr(app, "_dock_prog", None) is not None:
        try:
            app._dock_prog.set(max(0.0, min(1.0, float(prog))))
        except Exception:
            pass
    if paint:
        try:
            ui_perf.soft_refresh(app, force=False)
        except Exception:
            pass


def set_apply_btn_text(app: Any, text: str) -> None:
    for attr in ("_apply_btn", "_dock_apply_btn", "_editor_apply_btn"):
        btn = getattr(app, attr, None)
        if btn is not None:
            try:
                btn.configure(text=text)
            except Exception:
                pass


def set_apply_enabled(app: Any, enabled: bool, *, tip: str = "") -> None:
    """Enable/disable Apply buttons (WORKER OFF gate). Skip no-op configures."""
    # Only cache state after at least one Apply button exists (dock may build later)
    any_btn = False
    for attr in ("_apply_btn", "_dock_apply_btn", "_editor_apply_btn"):
        if getattr(app, attr, None) is not None:
            any_btn = True
            break
    if not any_btn:
        return
    prev = getattr(app, "_last_apply_enabled", None)
    if prev is enabled and not tip:
        return
    app._last_apply_enabled = enabled
    state = "normal" if enabled else "disabled"
    for attr in ("_apply_btn", "_dock_apply_btn", "_editor_apply_btn"):
        btn = getattr(app, attr, None)
        if btn is None:
            continue
        try:
            btn.configure(state=state)
            if not enabled and tip:
                if "Arm" not in str(btn.cget("text") or ""):
                    btn.configure(text="Arm worker first")
            elif enabled:
                cur = str(btn.cget("text") or "")
                if cur in ("Arm worker first", "Applying…") or cur.startswith("Wait"):
                    if not getattr(app, "_apply_busy", False):
                        btn.configure(text="Apply to game")
        except Exception:
            pass


def build_apply_dock(app: Any, parent: Any) -> None:
    """Compact bottom apply strip — primary status surface."""
    import customtkinter as ctk

    from ..ui_theme import (
        ACCENT,
        BORDER,
        CARD,
        LIST_BG,
        MUTED,
        PANEL,
        RADIUS_SM,
        SP1,
        SP2,
        SP3,
        SUCCESS,
        TEXT,
    )
    from . import widgets as w

    dock = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=0, height=72)
    dock.pack(fill="x", side="bottom")
    dock.pack_propagate(False)
    app._apply_dock = dock

    box = ctk.CTkFrame(
        dock, fg_color=LIST_BG, corner_radius=RADIUS_SM, border_width=2, border_color=BORDER
    )
    box.pack(fill="both", expand=True, padx=SP3 + SP1, pady=SP1 + 2)  # 14, 6-ish
    app._dock_box = box

    head = ctk.CTkFrame(box, fg_color="transparent")
    head.pack(fill="x", padx=SP2 + SP1, pady=(SP1, 0))  # 10, 4
    ctk.CTkLabel(
        head, text="APPLY", text_color=ACCENT, font=ctk.CTkFont(size=11, weight="bold")
    ).pack(side="left")

    # Four step chips (replaces single muted string label)
    steps_row = ctk.CTkFrame(head, fg_color="transparent")
    steps_row.pack(side="left", padx=SP3)
    app._apply_step_labels = []
    for i, lab in enumerate(_STEP_LABELS):
        chip = ctk.CTkLabel(
            steps_row,
            text=lab,
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        )
        chip.pack(side="left", padx=(0 if i == 0 else SP2, 0))
        app._apply_step_labels.append(chip)

    app._dock_apply_btn = w.btn(
        head,
        "Apply to game",
        lambda: _dock_apply_clicked(app),
        kind="primary",
        width=150,
        icon="apply",
    )
    app._dock_apply_btn.pack(side="right", padx=SP1)
    app._dock_arm_btn = w.btn(
        head,
        "How to arm",
        app._show_arm_guide,
        kind="ghost",
        width=100,
        height=30,
        icon="help",
    )
    app._dock_arm_btn.pack(side="right", padx=SP1)

    app._dock_status_label = ctk.CTkLabel(
        box,
        textvariable=app.apply_status_var,
        text_color=TEXT,
        font=ctk.CTkFont(size=12, weight="bold"),
        anchor="w",
        justify="left",
        wraplength=1000,
    )
    app._dock_status_label.pack(fill="x", padx=SP2 + SP1, pady=(SP1 // 2, 0))
    app._dock_prog = ctk.CTkProgressBar(box, height=7, progress_color=SUCCESS, fg_color=CARD)
    app._dock_prog.pack(fill="x", padx=SP2 + SP1, pady=(SP1 // 2, SP1 + 2))
    app._dock_prog.set(0)

    # Sync chip colors with current string var / idle state
    set_apply_steps(app, 0)


def _dock_apply_clicked(app: Any) -> None:
    """Route dock Apply by active tab first (never hijack Editor with leftover hits)."""
    try:
        tab = ""
        try:
            tab = str(app.tabs.get() or "")
        except Exception:
            pass
        if "Add team" in tab:
            from .tabs import add_team as add_team_tab

            add_team_tab.dock_apply_from_add_team(app)
            return
        if "Editor" in tab:
            app._editor_apply()
            return
        if "Boost" in tab:
            # No card apply from Boost — user must use Run on a pack/card
            set_apply_status(
                app,
                "Use Run on a Boost pack/profile (dock Apply is for Cards/Editor)",
                prog=0,
                state="warn",
            )
            return
        if "Squad" in tab or "Catalog" in tab or "About" in tab or "Home" in tab:
            set_apply_status(
                app,
                "Dock Apply is for Cards/Editor · use Add team tab for new roster slots",
                prog=0,
                state="warn",
            )
            return
        # Cards / other → card apply when hits exist
        app._apply_card()
    except Exception as e:
        set_apply_status(app, f"✗ Apply · {e}", prog=0, state="err")


def update_dock_for_active_tab(app: Any) -> None:
    """Tab-aware dock button label + idle hint (Add team vs Apply)."""
    if getattr(app, "_apply_busy", False):
        return
    try:
        tab = str(app.tabs.get() or "")
    except Exception:
        return
    try:
        if "Add team" in tab:
            from .tabs import add_team as add_team_tab

            set_apply_btn_text(app, add_team_tab.dock_idle_label(app))
            add_team_tab.update_dock_copy(app)
        else:
            set_apply_btn_text(app, "Apply to game")
    except Exception:
        pass
