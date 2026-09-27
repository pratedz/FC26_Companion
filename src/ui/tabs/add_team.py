"""Add team tab — create/reuse a player and transfer into Career club.

Separate from Cards (overwrite Target) and Editor (apply form to Target).
Sources: Catalog search · Editor/AI form · Manual name+OVR.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import card_catalog
from ... import le_apply
from ... import player_schema
from ... import target_players
from ...ui_theme import (
    ACCENT,
    BG,
    BORDER,
    CARD,
    DANGER,
    ENTRY_H,
    LIST_BG,
    MUTED,
    MUTED_DIM,
    PANEL,
    RADIUS_SM,
    SP2,
    SP3,
    SP4,
    SUCCESS,
    TEXT,
    WARNING,
)
from .. import widgets as w

TAB_NAME = "  Add team  "

# Human mode labels → internal value
_MODE_LABELS = (
    ("Recommended — free-agent overwrite + transfer (safe)", "auto"),
    ("Free-agent dummy list + transfer", "dummy"),
    ("CreatePlayer + transfer (FREEZE RISK — advanced)", "create"),
)
_MODE_BY_LABEL = {lab: val for lab, val in _MODE_LABELS}
_LABEL_BY_MODE = {val: lab for lab, val in _MODE_LABELS}

_SOURCE_CATALOG = "catalog"
_SOURCE_EDITOR = "editor"
_SOURCE_MANUAL = "manual"


def build_add_team_tab(app: Any) -> None:
    """Dedicated tab: readiness → pick source → preview → Add to team."""
    root = ctk.CTkScrollableFrame(app.tab_add_team, fg_color=BG)
    root.pack(fill="both", expand=True, padx=SP2, pady=SP2)
    app._add_team_root = root

    _init_state(app)

    # ── Title (short) ────────────────────────────────────────────────
    title = ctk.CTkFrame(root, fg_color="transparent")
    title.pack(fill="x", padx=SP2, pady=(SP2, SP2))
    w.label(title, "Add a new player to your club", bold=True, size=16).pack(anchor="w")
    ctk.CTkLabel(
        title,
        text="Does not replace someone already on the squad (use Cards → Apply for that).",
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        anchor="w",
    ).pack(anchor="w", pady=(SP2 // 2, 0))

    # How it works (collapsed detail)
    how = ctk.CTkFrame(root, fg_color="transparent")
    how.pack(fill="x", padx=SP2, pady=(0, SP2))
    app._add_team_how_open = False
    app._add_team_how_btn = w.btn(
        how,
        "How it works ▾",
        lambda: _toggle_how(app),
        kind="ghost",
        width=130,
        height=28,
    )
    app._add_team_how_btn.pack(anchor="w")
    app._add_team_how_body = ctk.CTkLabel(
        how,
        text=(
            "SAFE default: overwrite a worst free-agent dummy → import-style stats/name → TransferPlayer.\n"
            "1) LIVE + Career  ·  2) Export squad (harvests free agents)  ·  3) Pick card/name  ·  4) Add\n"
            "CreatePlayer is Advanced-only (freeze risk). Does NOT overwrite squad stars — use Cards for that.\n"
            "Synergy: after success → Export squad + optional New Signing Settle (Boost pack)."
        ),
        text_color=MUTED_DIM,
        font=ctk.CTkFont(size=11),
        wraplength=740,
        justify="left",
        anchor="w",
    )
    # hidden until expanded

    # ── Readiness strip ──────────────────────────────────────────────
    ready = w.panel(root)
    ready.pack(fill="x", padx=SP2, pady=(0, SP3))
    rhead = ctk.CTkFrame(ready, fg_color="transparent")
    rhead.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    w.label(rhead, "Ready?", bold=True, size=13).pack(side="left")
    w.btn(
        rhead,
        "Export squad",
        lambda: _export_and_refresh(app),
        kind="accent",
        width=120,
        height=30,
    ).pack(side="right")
    w.btn(
        rhead,
        "Refresh",
        lambda: refresh_readiness(app),
        kind="ghost",
        width=80,
        height=30,
    ).pack(side="right", padx=(0, SP2))

    chips = ctk.CTkFrame(ready, fg_color="transparent")
    chips.pack(fill="x", padx=SP4, pady=(0, SP2))
    app._add_team_chip_live = _chip(chips, "LIVE · …")
    app._add_team_chip_live.pack(side="left", padx=(0, SP2))
    app._add_team_chip_team = _chip(chips, "Team · …")
    app._add_team_chip_team.pack(side="left", padx=(0, SP2))
    app._add_team_chip_fa = _chip(chips, "FA · …")
    app._add_team_chip_fa.pack(side="left", padx=(0, SP2))
    app._add_team_chip_cat = _chip(chips, "Catalog · …")
    app._add_team_chip_cat.pack(side="left", padx=(0, SP2))

    app.add_team_team_var = ctk.StringVar(value="")
    app.add_team_squad_label = ctk.CTkLabel(
        ready,
        textvariable=app.add_team_team_var,
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        anchor="w",
    )
    app.add_team_squad_label.pack(fill="x", padx=SP4, pady=(0, SP3))
    try:
        app._squad_labels.append(app.add_team_squad_label)
    except Exception:
        pass

    # ── Source chips ─────────────────────────────────────────────────
    src_panel = w.panel(root)
    src_panel.pack(fill="x", padx=SP2, pady=(0, SP3))
    w.label(src_panel, "1 · Player source", bold=True, size=13).pack(
        anchor="w", padx=SP4, pady=(SP3, SP2)
    )
    srow = ctk.CTkFrame(src_panel, fg_color="transparent")
    srow.pack(fill="x", padx=SP4, pady=(0, SP3))
    app._add_team_src_btns: Dict[str, Any] = {}
    for key, lab in (
        (_SOURCE_CATALOG, "From catalog"),
        (_SOURCE_EDITOR, "From Editor / AI"),
        (_SOURCE_MANUAL, "Manual name"),
    ):
        b = w.btn(
            srow,
            lab,
            lambda k=key: set_source(app, k),
            kind="secondary",
            width=140,
            height=32,
        )
        b.pack(side="left", padx=(0, SP2))
        app._add_team_src_btns[key] = b
    w.btn(
        srow,
        "← Cards pick",
        lambda: _pull_from_cards(app),
        kind="accent",
        width=120,
        height=32,
    ).pack(side="left", padx=(SP2, 0))

    # ── Source bodies ────────────────────────────────────────────────
    app._add_team_body_host = ctk.CTkFrame(root, fg_color="transparent")
    app._add_team_body_host.pack(fill="both", expand=True, padx=SP2, pady=(0, SP3))

    app._add_team_frames: Dict[str, Any] = {}
    app._add_team_frames[_SOURCE_CATALOG] = _build_catalog_source(app, app._add_team_body_host)
    app._add_team_frames[_SOURCE_EDITOR] = _build_editor_source(app, app._add_team_body_host)
    app._add_team_frames[_SOURCE_MANUAL] = _build_manual_source(app, app._add_team_body_host)

    # ── Preview + action ─────────────────────────────────────────────
    prev = w.panel(root)
    prev.pack(fill="x", padx=SP2, pady=(0, SP3))
    w.label(prev, "2 · Preview & add", bold=True, size=13).pack(
        anchor="w", padx=SP4, pady=(SP3, SP2)
    )
    app.add_team_preview_var = ctk.StringVar(value="Select a player to preview the plan.")
    app._add_team_preview_label = ctk.CTkLabel(
        prev,
        textvariable=app.add_team_preview_var,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
        wraplength=720,
        justify="left",
        anchor="w",
    )
    app._add_team_preview_label.pack(fill="x", padx=SP4, pady=(0, SP2))

    # Real face (FUT base player id → headassetid) + post-add synergy
    if not hasattr(app, "add_team_real_face"):
        app.add_team_real_face = ctk.BooleanVar(value=True)
    if not hasattr(app, "add_team_settle_after"):
        app.add_team_settle_after = ctk.BooleanVar(value=True)
    face_row = ctk.CTkFrame(prev, fg_color="transparent")
    face_row.pack(fill="x", padx=SP4, pady=(0, SP2))
    ctk.CTkCheckBox(
        face_row,
        text="Use FUT base face id (Icons/heroes real face when available)",
        variable=app.add_team_real_face,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
        fg_color=SUCCESS,
        hover_color=SUCCESS,
        border_color=BORDER,
        command=lambda: update_preview(app),
    ).pack(side="left")
    settle_row = ctk.CTkFrame(prev, fg_color="transparent")
    settle_row.pack(fill="x", padx=SP4, pady=(0, SP2))
    ctk.CTkCheckBox(
        settle_row,
        text="After success · queue New Signing Settle (fitness + form/morale/sharpness)",
        variable=app.add_team_settle_after,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
        fg_color=SUCCESS,
        hover_color=SUCCESS,
        border_color=BORDER,
    ).pack(side="left")

    # Advanced mode (collapsed)
    adv_row = ctk.CTkFrame(prev, fg_color="transparent")
    adv_row.pack(fill="x", padx=SP4, pady=(0, SP2))
    app._add_team_adv_open = False
    app._add_team_adv_btn = w.btn(
        adv_row,
        "Advanced path ▸",
        lambda: _toggle_advanced(app),
        kind="ghost",
        width=140,
        height=28,
    )
    app._add_team_adv_btn.pack(side="left")
    app._add_team_adv_frame = ctk.CTkFrame(prev, fg_color="transparent")
    # not packed until open
    w.label(app._add_team_adv_frame, "Path", muted=True, size=11).pack(side="left")
    ctk.CTkComboBox(
        app._add_team_adv_frame,
        variable=app.add_team_mode_label_var,
        width=360,
        height=ENTRY_H,
        values=[lab for lab, _ in _MODE_LABELS],
        corner_radius=RADIUS_SM,
        fg_color=LIST_BG,
        border_color=BORDER,
        button_color=CARD,
        text_color=TEXT,
        command=lambda _v: update_preview(app),
    ).pack(side="left", padx=(SP2, 0))

    brow = ctk.CTkFrame(prev, fg_color="transparent")
    app._add_team_brow = brow
    brow.pack(fill="x", padx=SP4, pady=(SP2, SP2))
    app._add_team_btn = w.btn(
        brow,
        "Add to team",
        lambda: run_add_to_team(app),
        kind="primary",
        width=180,
        height=38,
    )
    app._add_team_btn.pack(side="left")
    app.add_team_gate_var = ctk.StringVar(value="")
    app._add_team_gate_label = ctk.CTkLabel(
        brow,
        textvariable=app.add_team_gate_var,
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        anchor="w",
        wraplength=480,
    )
    app._add_team_gate_label.pack(side="left", padx=(SP3, 0), fill="x", expand=True)

    # ── Last result banner ───────────────────────────────────────────
    res = w.panel(root)
    res.pack(fill="x", padx=SP2, pady=(0, SP4))
    w.label(res, "Last result", bold=True, size=13).pack(
        anchor="w", padx=SP4, pady=(SP3, SP2)
    )
    app.add_team_result_var = ctk.StringVar(value="No add yet this session.")
    app._add_team_result_label = ctk.CTkLabel(
        res,
        textvariable=app.add_team_result_var,
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        wraplength=720,
        justify="left",
        anchor="w",
    )
    app._add_team_result_label.pack(fill="x", padx=SP4, pady=(0, SP2))
    rbtns = ctk.CTkFrame(res, fg_color="transparent")
    rbtns.pack(fill="x", padx=SP4, pady=(0, SP3))
    w.btn(
        rbtns,
        "Export squad again",
        lambda: _export_and_refresh(app),
        kind="secondary",
        width=150,
        height=30,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        rbtns,
        "Signing Settle",
        lambda: _queue_settle(app),
        kind="accent",
        width=130,
        height=30,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        rbtns,
        "Match Ready",
        lambda: _queue_match_ready(app),
        kind="accent",
        width=120,
        height=30,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        rbtns,
        "Open Squad",
        lambda: app._goto("  Squad  "),
        kind="ghost",
        width=110,
        height=30,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        rbtns,
        "Cards overwrite",
        lambda: _open_cards_for_last(app),
        kind="ghost",
        width=130,
        height=30,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        rbtns,
        "Copy player id",
        lambda: _copy_last_playerid(app),
        kind="ghost",
        width=120,
        height=30,
    ).pack(side="left")

    set_source(app, _SOURCE_CATALOG)
    refresh_readiness(app)
    update_preview(app)
    update_gate(app)


def _init_state(app: Any) -> None:
    if not hasattr(app, "add_team_q_var"):
        app.add_team_q_var = ctk.StringVar(value="")
    if not hasattr(app, "add_team_year_var"):
        try:
            y = str(app.year_var.get() or "26")
        except Exception:
            y = "26"
        app.add_team_year_var = ctk.StringVar(value=y)
    if not hasattr(app, "add_team_filter_ovr_min"):
        app.add_team_filter_ovr_min = ctk.StringVar(value="")
    if not hasattr(app, "add_team_filter_ovr_max"):
        app.add_team_filter_ovr_max = ctk.StringVar(value="")
    if not hasattr(app, "add_team_filter_pos"):
        app.add_team_filter_pos = ctk.StringVar(value="")
    if not hasattr(app, "add_team_mode_label_var"):
        app.add_team_mode_label_var = ctk.StringVar(value=_MODE_LABELS[0][0])
    if not hasattr(app, "add_team_manual_name"):
        app.add_team_manual_name = ctk.StringVar(value="")
    if not hasattr(app, "add_team_manual_ovr"):
        app.add_team_manual_ovr = ctk.StringVar(value="85")
    if not hasattr(app, "add_team_manual_pos"):
        app.add_team_manual_pos = ctk.StringVar(value="ST")
    app._add_team_hits: List[Dict[str, Any]] = []
    app._add_team_selected_idx = 0
    app._add_team_source = _SOURCE_CATALOG
    app._add_team_last_playerid: Optional[int] = None
    app._add_team_search_busy = False


def _chip(parent: Any, text: str) -> Any:
    fr = ctk.CTkFrame(
        parent,
        fg_color=CARD,
        corner_radius=RADIUS_SM,
        border_width=1,
        border_color=BORDER,
    )
    lab = ctk.CTkLabel(
        fr,
        text=text,
        text_color=TEXT,
        font=ctk.CTkFont(size=11, weight="bold"),
    )
    lab.pack(padx=SP3, pady=SP2)
    fr._label = lab  # type: ignore[attr-defined]
    return fr


def _set_chip(chip: Any, text: str, *, ok: bool, warn: bool = False) -> None:
    color = SUCCESS if ok else (WARNING if warn else MUTED)
    border = SUCCESS if ok else (WARNING if warn else BORDER)
    try:
        chip.configure(border_color=border)
        chip._label.configure(text=text, text_color=color)
    except Exception:
        pass


def _toggle_how(app: Any) -> None:
    open_ = not bool(getattr(app, "_add_team_how_open", False))
    app._add_team_how_open = open_
    try:
        app._add_team_how_btn.configure(text="How it works ▴" if open_ else "How it works ▾")
    except Exception:
        pass
    try:
        if open_:
            app._add_team_how_body.pack(anchor="w", fill="x", pady=(SP2, 0))
        else:
            app._add_team_how_body.pack_forget()
    except Exception:
        pass


def _toggle_advanced(app: Any) -> None:
    open_ = not bool(getattr(app, "_add_team_adv_open", False))
    app._add_team_adv_open = open_
    try:
        app._add_team_adv_btn.configure(text="Advanced path ▾" if open_ else "Advanced path ▸")
    except Exception:
        pass
    try:
        if open_:
            kw: Dict[str, Any] = {"fill": "x", "padx": SP4, "pady": (0, SP2)}
            brow = getattr(app, "_add_team_brow", None)
            if brow is not None:
                kw["before"] = brow
            app._add_team_adv_frame.pack(**kw)
        else:
            app._add_team_adv_frame.pack_forget()
    except Exception:
        pass
    update_preview(app)


def set_source(app: Any, key: str) -> None:
    app._add_team_source = key
    for k, fr in (getattr(app, "_add_team_frames", None) or {}).items():
        try:
            if k == key:
                fr.pack(fill="both", expand=True)
            else:
                fr.pack_forget()
        except Exception:
            pass
    for k, b in (getattr(app, "_add_team_src_btns", None) or {}).items():
        try:
            if k == key:
                b.configure(fg_color=ACCENT, text_color="#042f2e", hover_color=ACCENT)
            else:
                b.configure(fg_color="#243044", text_color=TEXT)
        except Exception:
            pass
    if key == _SOURCE_EDITOR:
        _pull_editor_into_label(app)
    update_preview(app)
    update_gate(app)


def _build_catalog_source(app: Any, parent: Any) -> Any:
    fr = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS_SM, border_width=1, border_color=BORDER)
    w.label(fr, "Catalog search", bold=True, size=13).pack(
        anchor="w", padx=SP4, pady=(SP3, SP2)
    )
    ctk.CTkLabel(
        fr,
        text="Year 26 specials need Catalog download first.",
        text_color=MUTED_DIM,
        font=ctk.CTkFont(size=11),
        anchor="w",
    ).pack(anchor="w", padx=SP4, pady=(0, SP2))

    srow = ctk.CTkFrame(fr, fg_color="transparent")
    srow.pack(fill="x", padx=SP4, pady=(0, SP2))
    w.label(srow, "Name", muted=True, size=11).grid(row=0, column=0, sticky="w")
    se = w.entry(srow, app.add_team_q_var, width=200)
    se.grid(row=1, column=0, padx=(0, SP2), sticky="w")
    se.bind("<Return>", lambda _e: search_add_team(app))
    app._add_team_search_entry = se
    w.label(srow, "Year", muted=True, size=11).grid(row=0, column=1, sticky="w")
    ctk.CTkComboBox(
        srow,
        variable=app.add_team_year_var,
        width=90,
        height=ENTRY_H,
        values=["", "local", "18", "19", "20", "21", "22", "23", "24", "25", "26", "futbin"],
        corner_radius=RADIUS_SM,
        fg_color=LIST_BG,
        border_color=BORDER,
        button_color=CARD,
        text_color=TEXT,
    ).grid(row=1, column=1, padx=(0, SP2), sticky="w")
    w.btn(srow, "Search", lambda: search_add_team(app), kind="primary", width=100, height=ENTRY_H).grid(
        row=1, column=2, padx=SP2
    )
    w.btn(
        srow,
        "Open Catalog",
        lambda: app._goto("  Catalog  "),
        kind="ghost",
        width=110,
        height=ENTRY_H,
    ).grid(row=1, column=3, padx=2)

    frow = ctk.CTkFrame(fr, fg_color="transparent")
    frow.pack(fill="x", padx=SP4, pady=(0, SP2))
    w.label(frow, "OVR min", muted=True, size=10).pack(side="left")
    w.entry(frow, app.add_team_filter_ovr_min, width=48).pack(side="left", padx=(SP2, SP3))
    w.label(frow, "max", muted=True, size=10).pack(side="left")
    w.entry(frow, app.add_team_filter_ovr_max, width=48).pack(side="left", padx=(SP2, SP3))
    w.label(frow, "Pos", muted=True, size=10).pack(side="left")
    w.entry(frow, app.add_team_filter_pos, width=48).pack(side="left", padx=(SP2, SP3))
    w.btn(frow, "Filter", lambda: _apply_filters(app), kind="ghost", width=70, height=28).pack(
        side="left"
    )

    prog = ctk.CTkFrame(fr, fg_color="transparent")
    prog.pack(fill="x", padx=SP4, pady=(0, SP2))
    app.add_team_search_prog = ctk.CTkProgressBar(
        prog, height=6, progress_color=ACCENT, fg_color=LIST_BG
    )
    app.add_team_search_prog.pack(fill="x", side="left", expand=True)
    app.add_team_search_prog.set(0)
    app.add_team_search_prog_label = ctk.CTkLabel(
        prog, text="", text_color=MUTED, font=ctk.CTkFont(size=11), width=120
    )
    app.add_team_search_prog_label.pack(side="right", padx=(SP3, 0))

    list_wrap = ctk.CTkFrame(fr, fg_color=LIST_BG, corner_radius=RADIUS_SM)
    list_wrap.pack(fill="both", expand=True, padx=SP4, pady=(0, SP2))
    app.add_team_list = w.listbox(list_wrap, height=9)
    scroll = ctk.CTkScrollbar(list_wrap, command=app.add_team_list.yview)
    app.add_team_list.configure(yscrollcommand=scroll.set)
    app.add_team_list.pack(side="left", fill="both", expand=True, padx=(SP3, 0), pady=SP3)
    scroll.pack(side="right", fill="y", padx=(0, SP3), pady=SP3)
    app.add_team_list.bind("<<ListboxSelect>>", lambda _e: _on_select(app))
    app.add_team_list.bind("<Double-Button-1>", lambda _e: run_add_to_team(app, confirm=True))
    app.add_team_list.insert("end", "  Search a player name — variants appear here")

    app.add_team_selected_label = ctk.CTkLabel(
        fr,
        text="Selected · none  ·  double-click to Add",
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        anchor="w",
    )
    app.add_team_selected_label.pack(fill="x", padx=SP4, pady=(0, SP3))
    return fr


def _build_editor_source(app: Any, parent: Any) -> Any:
    fr = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS_SM, border_width=1, border_color=BORDER)
    w.label(fr, "From Editor / AI", bold=True, size=13).pack(
        anchor="w", padx=SP4, pady=(SP3, SP2)
    )
    ctk.CTkLabel(
        fr,
        text="Uses the current Editor form (AI fill or manual build). Open Editor to edit, then return here.",
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        wraplength=700,
        justify="left",
        anchor="w",
    ).pack(anchor="w", padx=SP4, pady=(0, SP2))
    app.add_team_editor_summary = ctk.StringVar(value="Editor form · not loaded yet")
    ctk.CTkLabel(
        fr,
        textvariable=app.add_team_editor_summary,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
        anchor="w",
        wraplength=700,
        justify="left",
    ).pack(fill="x", padx=SP4, pady=(0, SP3))
    erow = ctk.CTkFrame(fr, fg_color="transparent")
    erow.pack(fill="x", padx=SP4, pady=(0, SP3))
    w.btn(
        erow,
        "Refresh from Editor",
        lambda: _pull_editor_into_label(app),
        kind="secondary",
        width=160,
        height=32,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        erow,
        "Open Editor",
        lambda: app._goto("  Editor  "),
        kind="ghost",
        width=120,
        height=32,
    ).pack(side="left")
    return fr


def _build_manual_source(app: Any, parent: Any) -> Any:
    fr = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS_SM, border_width=1, border_color=BORDER)
    w.label(fr, "Manual player", bold=True, size=13).pack(
        anchor="w", padx=SP4, pady=(SP3, SP2)
    )
    ctk.CTkLabel(
        fr,
        text="Minimal create: name + overall + position. Stats default in-game; refine later via Cards/Editor.",
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        wraplength=700,
        justify="left",
        anchor="w",
    ).pack(anchor="w", padx=SP4, pady=(0, SP2))
    row = ctk.CTkFrame(fr, fg_color="transparent")
    row.pack(fill="x", padx=SP4, pady=(0, SP3))
    w.label(row, "Name", muted=True, size=11).grid(row=0, column=0, sticky="w")
    ne = w.entry(row, app.add_team_manual_name, width=200)
    ne.grid(row=1, column=0, padx=(0, SP3), sticky="w")
    ne.bind("<KeyRelease>", lambda _e: (update_preview(app), update_gate(app)))
    w.label(row, "OVR", muted=True, size=11).grid(row=0, column=1, sticky="w")
    oe = w.entry(row, app.add_team_manual_ovr, width=60)
    oe.grid(row=1, column=1, padx=(0, SP3), sticky="w")
    oe.bind("<KeyRelease>", lambda _e: (update_preview(app), update_gate(app)))
    w.label(row, "Pos", muted=True, size=11).grid(row=0, column=2, sticky="w")
    pe = w.entry(row, app.add_team_manual_pos, width=60)
    pe.grid(row=1, column=2, sticky="w")
    pe.bind("<KeyRelease>", lambda _e: (update_preview(app), update_gate(app)))
    return fr


def _pull_editor_into_label(app: Any) -> None:
    card = _card_from_editor(app)
    if not card:
        app.add_team_editor_summary.set(
            "Editor form empty · open Editor, set Build label / AI fill, then Refresh"
        )
    else:
        name = card.get("name") or "?"
        ovr = card.get("overallrating") or "—"
        app.add_team_editor_summary.set(f"Ready · {name}  ·  OVR {ovr}  ·  from Editor form")
    update_preview(app)
    update_gate(app)


def _card_from_editor(app: Any) -> Optional[Dict[str, Any]]:
    try:
        app._ensure_tab("  Editor  ")
    except Exception:
        pass
    try:
        if hasattr(app, "_editor_collect_card"):
            card = app._editor_collect_card()
        else:
            card = None
    except Exception:
        card = None
    if not card:
        try:
            name = (app.editor_name_var.get() or "").strip()
        except Exception:
            name = ""
        if not name:
            return None
        card = player_schema.empty_player_card()
        card["name"] = name
    if not (str(card.get("name") or "")).strip():
        return None
    return dict(card)


def _card_from_manual(app: Any) -> Optional[Dict[str, Any]]:
    name = (app.add_team_manual_name.get() or "").strip()
    if not name:
        return None
    card = player_schema.empty_player_card()
    card["name"] = name
    try:
        ovr = int(str(app.add_team_manual_ovr.get() or "85").strip() or "85")
    except ValueError:
        ovr = 85
    ovr = max(1, min(99, ovr))
    card["overallrating"] = ovr
    card["potential"] = min(99, ovr + 2)
    pos = (app.add_team_manual_pos.get() or "ST").strip().upper() or "ST"
    # preferredposition1 is often numeric in DB; store string pos for display; Lua maps if needed
    card["position"] = pos
    card["preferredposition1"] = pos
    # light attr fill so create is playable
    for f in (
        "acceleration",
        "sprintspeed",
        "finishing",
        "shortpassing",
        "dribbling",
        "defensiveawareness",
        "stamina",
        "strength",
        "reactions",
        "ballcontrol",
        "composure",
        "shotpower",
        "positioning",
    ):
        if not card.get(f):
            card[f] = ovr
    return card


def _export_and_refresh(app: Any) -> None:
    try:
        app._export_squad()
    except Exception as e:
        app.status.set(f"Export failed · {e}")
    # Poll already refreshes squad; also schedule readiness
    try:
        app.after(600, lambda: refresh_readiness(app))
        app.after(2000, lambda: refresh_readiness(app))
        app.after(5000, lambda: refresh_readiness(app))
    except Exception:
        refresh_readiness(app)


def refresh_readiness(app: Any) -> None:
    """Update LIVE / team / catalog chips and gate button."""
    if not hasattr(app, "_add_team_chip_live"):
        return
    # LIVE
    live = False
    live_txt = "LIVE · OFF"
    try:
        live = bool(le_apply.bridge_alive(90))
        if not live:
            try:
                live = le_apply.sync_state() == "live"
            except Exception:
                pass
        line = le_apply.apply_status_line(short=True)
        if live:
            live_txt = "LIVE ✓"
        else:
            live_txt = "LIVE · OFF"
        if line and not live:
            live_txt = f"LIVE · OFF"
    except Exception:
        pass
    _set_chip(app._add_team_chip_live, live_txt, ok=live, warn=not live)

    # Team + free-agent pool (SAFE v15 needs FA dummies)
    tid, tname, n = _team_meta()
    squad = target_players.load_squad()
    if tid > 0:
        label = tname or f"team {tid}"
        _set_chip(
            app._add_team_chip_team,
            f"Team · {label}",
            ok=True,
        )
        try:
            app.add_team_team_var.set(f"{label}  ·  id {tid}  ·  {n} players exported")
        except Exception:
            pass
    else:
        _set_chip(app._add_team_chip_team, "Team · export needed", ok=False, warn=True)
        try:
            app.add_team_team_var.set("No teamid yet · Export squad in Career Mode")
        except Exception:
            pass
    try:
        from .. import handoffs

        fa_n = handoffs.free_agent_pool_count(squad)
        if hasattr(app, "_add_team_chip_fa"):
            _set_chip(
                app._add_team_chip_fa,
                f"FA · {fa_n}" if fa_n else "FA · 0",
                ok=fa_n > 0,
                warn=fa_n <= 0,
            )
        if tid > 0:
            try:
                app.add_team_team_var.set(
                    f"{tname or f'team {tid}'}  ·  id {tid}  ·  {n} squad  ·  {fa_n} free agents"
                )
            except Exception:
                pass
    except Exception:
        pass

    # Catalog
    cat_ok, cat_msg = _catalog_status()
    _set_chip(app._add_team_chip_cat, cat_msg, ok=cat_ok, warn=not cat_ok)

    update_preview(app)
    update_gate(app)


def _team_meta() -> Tuple[int, str, int]:
    try:
        sq = target_players.load_squad()
        tid = int(sq.get("teamid") or 0)
        name = str(sq.get("teamname") or "").strip()
        n = int(sq.get("count") or 0) or len(sq.get("players") or [])
        return tid if tid > 0 else 0, name, n
    except Exception:
        return 0, "", 0


def _catalog_status() -> Tuple[bool, str]:
    try:
        # Cheap probe: any year-26 or local hit for a common letter
        hits = card_catalog.search_cards("a", year="26", limit=3) or []
        if hits:
            return True, "Catalog · year 26 OK"
        hits2 = card_catalog.search_cards("a", year="local", limit=3) or []
        if hits2:
            return True, "Catalog · local OK"
        return False, "Catalog · empty · open Catalog"
    except Exception:
        return False, "Catalog · unknown"


def current_mode(app: Any) -> str:
    lab = (app.add_team_mode_label_var.get() or "").strip()
    return _MODE_BY_LABEL.get(lab, "auto")


def resolve_card(app: Any) -> Optional[Dict[str, Any]]:
    src = getattr(app, "_add_team_source", _SOURCE_CATALOG)
    if src == _SOURCE_EDITOR:
        return _card_from_editor(app)
    if src == _SOURCE_MANUAL:
        return _card_from_manual(app)
    return _selected_catalog_card(app)


def update_preview(app: Any) -> None:
    if not hasattr(app, "add_team_preview_var"):
        return
    card = resolve_card(app)
    tid, tname, _n = _team_meta()
    club = tname or (f"team {tid}" if tid else "your club")
    mode = current_mode(app)
    mode_human = {
        "auto": "Create new player + transfer into club",
        "create": "Create new player only",
        "dummy": "Advanced free-agent overwrite",
    }.get(mode, mode)

    if not card:
        src = getattr(app, "_add_team_source", _SOURCE_CATALOG)
        hint = {
            _SOURCE_CATALOG: "Search and select a catalog variant.",
            _SOURCE_EDITOR: "Load Editor form (Build label / AI), then Refresh.",
            _SOURCE_MANUAL: "Enter a name (and OVR/pos).",
        }.get(src, "Select a player.")
        app.add_team_preview_var.set(hint)
        return

    name = card.get("name") or "?"
    ovr = card.get("overallrating") or "—"
    pos = card.get("preferredposition1") or card.get("position") or ""
    ver = card.get("version") or card.get("cardtype") or card.get("rarity") or ""
    bits = [f"{name}", f"OVR {ovr}"]
    if pos:
        bits.append(str(pos))
    if ver:
        bits.append(str(ver))
    line1 = " · ".join(bits)
    join = f"Will join: {club}" + (f" (id {tid})" if tid else " — export squad first")
    try:
        from ... import add_player as _ap

        use_face = True
        try:
            use_face = bool(app.add_team_real_face.get())
        except Exception:
            pass
        plan_row = _ap.card_to_players_row_data(card, use_real_face=use_face)
        first, sur, _j = _ap.resolve_player_names(card)
        h = plan_row.get("height", "180")
        wgt = plan_row.get("weight", "75")
        nat = plan_row.get("nationality") or "unset"
        face = plan_row.get("headassetid") or "generic(new id)"
        body = (
            f"Name “{first} {sur}” · nation id {nat} · face head={face}\n"
            f"~age 28 · {h}cm / {wgt}kg"
        )
    except Exception:
        body = f"Name “{name}” · body/face defaults · ~age 28"
    plan = f"Path: {mode_human}\n{body}"
    app.add_team_preview_var.set(f"{line1}\n{join}\n{plan}")

    # Dynamic CTA with team name when known
    try:
        if tid > 0 and tname:
            app._add_team_btn.configure(text=f"Add to {tname[:18]}")
        else:
            app._add_team_btn.configure(text="Add to team")
    except Exception:
        pass


def update_gate(app: Any) -> None:
    """Enable/disable primary button + explain why."""
    if not hasattr(app, "_add_team_btn"):
        return
    reasons: List[str] = []
    live = False
    try:
        live = bool(le_apply.bridge_alive(90))
        if not live:
            try:
                live = le_apply.sync_state() == "live"
            except Exception:
                pass
    except Exception:
        pass
    if not live:
        reasons.append("Arm LIVE worker first")
    tid, _tname, _n = _team_meta()
    if tid <= 0:
        reasons.append("Export squad first")
    card = resolve_card(app)
    if not card:
        reasons.append("Pick a player")
    elif not (str(card.get("name") or "")).strip():
        reasons.append("Player needs a name")

    ok = not reasons
    try:
        app._add_team_btn.configure(state="normal" if ok else "disabled")
    except Exception:
        pass
    try:
        if ok:
            app.add_team_gate_var.set("Ready · dock status will show path + player id")
            app._add_team_gate_label.configure(text_color=SUCCESS)
        else:
            app.add_team_gate_var.set(" · ".join(reasons))
            app._add_team_gate_label.configure(text_color=WARNING)
    except Exception:
        pass


def search_add_team(app: Any) -> None:
    q = (app.add_team_q_var.get() or "").strip()
    year = (app.add_team_year_var.get() or "").strip()
    if not q:
        app.status.set("Add team · type a player name first")
        return
    if getattr(app, "_add_team_search_busy", False):
        return
    app._add_team_search_busy = True
    app.status.set(f"Add team · searching “{q}”…")
    try:
        app.add_team_search_prog.set(0.15)
        app.add_team_search_prog_label.configure(text="Searching…")
    except Exception:
        pass

    def work() -> None:
        try:
            hits = card_catalog.search_cards(q, year=year or None, limit=120) or []
        except Exception as exc:
            failure = exc
            app.after(0, lambda: _search_fail(app, failure))
            return
        app.after(0, lambda: _show_hits(app, hits, q))

    threading.Thread(target=work, daemon=True).start()


def _search_fail(app: Any, e: Exception) -> None:
    app._add_team_search_busy = False
    app.status.set(f"Add team search failed · {e}")
    try:
        app.add_team_search_prog.set(0)
        app.add_team_search_prog_label.configure(text="Error")
        app.add_team_list.delete(0, "end")
        app.add_team_list.insert("end", f"  Search error · {e}")
    except Exception:
        pass


def _filter_hits(hits: List[Dict[str, Any]], app: Any) -> List[Dict[str, Any]]:
    out = list(hits)
    try:
        omin = (app.add_team_filter_ovr_min.get() or "").strip()
        omax = (app.add_team_filter_ovr_max.get() or "").strip()
        pos = (app.add_team_filter_pos.get() or "").strip().upper()
    except Exception:
        return out
    if omin:
        try:
            lo = int(omin)
            out = [c for c in out if int(c.get("overallrating") or 0) >= lo]
        except ValueError:
            pass
    if omax:
        try:
            hi = int(omax)
            out = [c for c in out if int(c.get("overallrating") or 99) <= hi]
        except ValueError:
            pass
    if pos:
        def _pos_ok(c: Dict[str, Any]) -> bool:
            p = str(c.get("preferredposition1") or c.get("position") or "").upper()
            return pos in p or p == pos

        out = [c for c in out if _pos_ok(c)]
    return out


def _apply_filters(app: Any) -> None:
    raw = getattr(app, "_add_team_raw_hits", None)
    if raw is None:
        raw = getattr(app, "_add_team_hits", []) or []
    q = (app.add_team_q_var.get() or "").strip() or "filter"
    _show_hits(app, list(raw), q, from_filter=True)


def _show_hits(
    app: Any,
    hits: List[Dict[str, Any]],
    q: str,
    *,
    from_filter: bool = False,
) -> None:
    app._add_team_search_busy = False
    if not from_filter:
        app._add_team_raw_hits = list(hits)
    filtered = _filter_hits(getattr(app, "_add_team_raw_hits", hits) or hits, app)
    app._add_team_hits = filtered
    app._add_team_selected_idx = 0
    lb = app.add_team_list
    try:
        lb.delete(0, "end")
    except Exception:
        return
    try:
        app.add_team_search_prog.set(1.0 if filtered else 0)
        app.add_team_search_prog_label.configure(
            text=f"{len(filtered)} hits" if filtered else "0 hits"
        )
    except Exception:
        pass

    if not filtered:
        if not (getattr(app, "_add_team_raw_hits", None) or []):
            lb.insert(
                "end",
                f"  No hits for “{q}” · try year 26 after Catalog download",
            )
            lb.insert("end", "  → double-click empty? Open Catalog tab first")
        else:
            lb.insert("end", "  No hits match OVR/Pos filters · clear filters")
        try:
            app.add_team_selected_label.configure(text="Selected · none")
        except Exception:
            pass
        app.status.set(f"Add team · 0 hits for “{q}”")
        update_preview(app)
        update_gate(app)
        return

    for c in filtered:
        name = str(c.get("name") or "?")
        ovr = c.get("overallrating")
        pos = c.get("preferredposition1") or c.get("position") or ""
        ver = c.get("version") or c.get("cardtype") or c.get("rarity") or ""
        yr = c.get("year") or ""
        line = f"  {name}  ·  OVR {ovr}  ·  {pos}  ·  {ver}  ·  y{yr}".strip()
        lb.insert("end", line)
    lb.selection_clear(0, "end")
    lb.selection_set(0)
    lb.see(0)
    _on_select(app)
    app.status.set(f"Add team · {len(filtered)} hits · select or double-click to Add")
    update_preview(app)
    update_gate(app)


def _on_select(app: Any) -> None:
    hits = getattr(app, "_add_team_hits", None) or []
    if not hits:
        try:
            app.add_team_selected_label.configure(text="Selected · none")
        except Exception:
            pass
        update_preview(app)
        update_gate(app)
        return
    sel = ()
    try:
        sel = app.add_team_list.curselection()
    except Exception:
        pass
    idx = int(sel[0]) if sel else int(getattr(app, "_add_team_selected_idx", 0) or 0)
    if idx < 0 or idx >= len(hits):
        try:
            app.add_team_selected_label.configure(text="Selected · none")
        except Exception:
            pass
        update_preview(app)
        update_gate(app)
        return
    app._add_team_selected_idx = idx
    c = hits[idx]
    name = c.get("name") or "?"
    ovr = c.get("overallrating")
    pid = c.get("playerid") or c.get("base_id") or ""
    try:
        app.add_team_selected_label.configure(
            text=f"Selected · {name}  ·  OVR {ovr}  ·  catalog id {pid}  ·  double-click to Add"
        )
    except Exception:
        pass
    update_preview(app)
    update_gate(app)


def _selected_catalog_card(app: Any) -> Optional[Dict[str, Any]]:
    hits = getattr(app, "_add_team_hits", None) or []
    if not hits:
        return None
    idx = int(getattr(app, "_add_team_selected_idx", 0) or 0)
    try:
        sel = app.add_team_list.curselection()
        if sel:
            idx = int(sel[0])
    except Exception:
        pass
    if idx < 0 or idx >= len(hits):
        return None
    return dict(hits[idx])


def _copy_last_playerid(app: Any) -> None:
    pid = getattr(app, "_add_team_last_playerid", None)
    if not pid:
        app.status.set("No player id yet · run Add to team first")
        return
    try:
        app.clipboard_clear()
        app.clipboard_append(str(pid))
        app.status.set(f"Copied player id {pid}")
    except Exception as e:
        app.status.set(f"Copy failed · {e}")


def _pull_from_cards(app: Any) -> None:
    from .. import handoffs

    if not handoffs.send_card_to_add_team(app):
        app.status.set("No Cards selection · search a card on Cards tab first")


def _queue_settle(app: Any) -> None:
    from .. import handoffs

    n = handoffs.queue_signing_settle(app)
    if n:
        app.status.set(f"Queued New Signing Settle · {n} job(s) — open Boost to watch")
        try:
            app._goto("  Boost  ")
        except Exception:
            pass


def _queue_match_ready(app: Any) -> None:
    from .. import handoffs

    n = handoffs.queue_workflow_pack(app, "match_ready")
    if n:
        app.status.set(f"Queued Match Ready · {n} job(s)")
        try:
            app._goto("  Boost  ")
        except Exception:
            pass


def _open_cards_for_last(app: Any) -> None:
    """Lock last-added (or career) id as Cards overwrite target."""
    from .. import handoffs

    pid = getattr(app, "_add_team_last_playerid", None) or getattr(
        app, "_last_add_playerid", None
    )
    name = getattr(app, "_last_add_name", "") or ""
    if not pid:
        try:
            app._goto("  Cards  ")
        except Exception:
            pass
        app.status.set("No last player id · use Squad to lock a target, then Cards")
        return
    handoffs.lock_squad_target(
        app, {"playerid": int(pid), "name": name or str(pid)}
    )
    try:
        if hasattr(app, "import_mode_var"):
            app.import_mode_var.set("apply")
    except Exception:
        pass
    try:
        app._goto("  Cards  ")
    except Exception:
        pass
    try:
        from . import cards as cards_tab

        cards_tab.update_cards_pipeline(app)
    except Exception:
        pass
    app.status.set(f"Cards target · id {pid} — pick a card to overwrite stats")


def present_add_team_result(app: Any, result: Any) -> None:
    """In-tab success/fail banner after queue job."""
    from ... import apply_service

    if not hasattr(app, "add_team_result_var"):
        return
    parsed = (getattr(result, "meta", None) or {}).get("job_status_parsed") or {}
    if not parsed and (getattr(result, "meta", None) or {}).get("job_status"):
        parsed = apply_service.parse_job_status(str(result.meta.get("job_status") or ""))
    path = parsed.get("path") or ""
    pid = parsed.get("player_id")
    err = parsed.get("err") or ""
    team = parsed.get("team_id")
    tid, tname, _ = _team_meta()
    club = tname or (f"team {team or tid}" if (team or tid) else "club")

    outcome = getattr(result, "outcome", "") or ""
    applied = bool(getattr(result, "applied", False)) or outcome == "applied"

    if applied and parsed.get("ok") is not False and not parsed.get("failed"):
        app._add_team_last_playerid = int(pid) if pid not in (None, "", 0, "0") else None
        path_h = {
            "create": "CreatePlayer (advanced)",
            "dummy": "free-agent overwrite (safe)",
        }.get(str(path), str(path or "auto"))
        name = ""
        try:
            card = resolve_card(app)
            name = (card or {}).get("name") or ""
        except Exception:
            pass
        try:
            from .. import handoffs

            handoffs.remember_last_add(
                app,
                int(pid) if pid not in (None, "", 0, "0") else None,
                name,
            )
        except Exception:
            pass
        settle_note = ""
        try:
            if bool(app.add_team_settle_after.get()):
                from .. import handoffs

                n = handoffs.queue_signing_settle(app)
                settle_note = (
                    f"\n→ Queued New Signing Settle ({n} job) — Boost tab / queue drains LIVE."
                    if n
                    else ""
                )
        except Exception:
            pass
        msg = (
            f"✓ Added{(' ' + name) if name else ''} via {path_h}"
            f"{f' · player id {pid}' if pid else ''}"
            f" · {club}\n"
            f"Next: Export squad · or Signing Settle / Match Ready below · "
            f"leave Squad Hub → re-open if name is blank.\n"
            f"Log: queue/_add_team_crash.log"
            f"{settle_note}"
        )
        app.add_team_result_var.set(msg)
        try:
            app._add_team_result_label.configure(text_color=SUCCESS)
        except Exception:
            pass
    elif outcome == "queued_live":
        app.add_team_result_var.set(
            f"⏳ Queued while LIVE · {getattr(result, 'reason', '') or 'waiting'}\n"
            "Open Career menus or Force drain if it stalls."
        )
        try:
            app._add_team_result_label.configure(text_color=WARNING)
        except Exception:
            pass
    else:
        human_err = _humanize_err(str(err or getattr(result, "reason", "") or "failed"))
        app.add_team_result_var.set(f"✗ {human_err}\nTry another path under Advanced, or re-export squad.")
        try:
            app._add_team_result_label.configure(text_color=DANGER)
        except Exception:
            pass
    refresh_readiness(app)


def _humanize_err(err: str) -> str:
    e = (err or "").lower()
    if "create_failed" in e or "create" in e and "fail" in e:
        return "Game refused CreatePlayer — try free-agent path (Advanced → overwrite free agent)"
    if "no_playerid" in e:
        return "No player id produced — create and dummy both failed"
    if "no teamid" in e or "export" in e:
        return "No club id — Export squad in Career Mode first"
    if "transfer" in e:
        return f"Transfer failed · {err}"
    if "worker" in e or "live" in e or "bridge" in e:
        return f"Worker not LIVE · {err}"
    return err or "Add to team failed"


def run_add_to_team(app: Any, *, confirm: bool = False) -> None:
    """Queue add_to_team for resolved card (catalog / editor / manual)."""
    refresh_readiness(app)
    if not app._require_live_worker(action="Add to team"):
        return

    def _fail(msg: str) -> None:
        app._apply_busy = False
        try:
            app._set_apply_btn_text(dock_idle_label(app))
        except Exception:
            pass
        app._set_apply_status(msg, prog=0, state="err", step=0)
        try:
            app.add_team_result_var.set(msg)
            app._add_team_result_label.configure(text_color=DANGER)
        except Exception:
            pass

    card = resolve_card(app)
    if not card:
        _fail("✗ No player · pick catalog / Editor / manual name")
        return
    if not (str(card.get("name") or "")).strip():
        _fail("✗ Player needs a name")
        return

    from ... import product as product_mod
    from .. import apply_flow

    squad = target_players.load_squad()
    tid = int(squad.get("teamid") or 0)
    if tid <= 0:
        _fail("✗ Export squad first (Career Mode) so teamid is known")
        return

    mode = current_mode(app)
    tname = str(squad.get("teamname") or tid)

    if confirm:
        try:
            from tkinter import messagebox

            if not messagebox.askyesno(
                "Add to team",
                f"Add {card.get('name')} (OVR {card.get('overallrating')}) to {tname}?\n"
                f"Path: {mode}",
            ):
                return
        except Exception:
            pass

    detail = (
        f"Add to {tname} ({tid}) · {card.get('name')} OVR {card.get('overallrating')}\n"
        f"Mode: {mode} · source={getattr(app, '_add_team_source', 'catalog')}"
    )

    def job(_on_tick: Any) -> Any:
        use_face = True
        try:
            use_face = bool(app.add_team_real_face.get())
        except Exception:
            pass
        return product_mod.add_card_to_user_team(
            card, teamid=tid, mode=mode, wait=True, use_real_face=use_face
        )

    def on_result(result: Any) -> None:
        present_add_team_result(app, result)

    apply_flow.run_apply_job(
        app,
        job,
        detail=detail,
        busy_msg="Add to team…",
        detailed=True,
        copy_bridge_on_wait=False,
        on_result=on_result,
    )
    try:
        app.after(800, lambda: refresh_readiness(app))
    except Exception:
        pass


def dock_idle_label(app: Any) -> str:
    """Dock button text when Add team tab is active."""
    try:
        tab = str(app.tabs.get() or "")
    except Exception:
        tab = ""
    if "Add team" in tab:
        tid, tname, _ = _team_meta()
        if tid > 0 and tname:
            return f"Add to {tname[:14]}"
        return "Add to team"
    return "Apply to game"


def on_tab_activated(app: Any) -> None:
    """Call when user switches to Add team — refresh gates + dock."""
    refresh_readiness(app)
    update_dock_copy(app)


def update_dock_copy(app: Any) -> None:
    """Tab-aware dock helper: Add team vs Cards/Editor Apply."""
    try:
        tab = str(app.tabs.get() or "")
    except Exception:
        return
    if "Add team" not in tab:
        return
    if getattr(app, "_apply_busy", False):
        return
    try:
        app._set_apply_btn_text(dock_idle_label(app))
    except Exception:
        pass
    try:
        # Soft idle status so user doesn't use wrong Apply mental model
        cur = (app.apply_status_var.get() or "")
        if cur.startswith("IDLE") or "dock Apply" in cur or not cur.strip():
            app.apply_status_var.set(
                "ADD TEAM · use the green button above (or this dock) · not Cards overwrite"
            )
    except Exception:
        pass


def dock_apply_from_add_team(app: Any) -> None:
    """Route dock primary button when Add team tab is active."""
    run_add_to_team(app)
