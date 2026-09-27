"""Home tab — LIVE-first setup and goals (visual polish)."""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import target_players
from ...icons import ctk_icon
from ...ui_theme import (
    ACCENT,
    BG,
    BORDER,
    CARD,
    MUTED,
    MUTED_DIM,
    PANEL,
    RADIUS,
    RADIUS_SM,
    SP2,
    SP3,
    SP4,
    SP5,
    SUCCESS,
    TEXT,
)
from .. import widgets as w


def _setup_completion_flags() -> Dict[str, bool]:
    """Lightweight one-shot flags for setup step checkmarks (soft fail)."""
    flags = {"worker": False, "live": False, "squad": False, "grok": False}
    try:
        from ... import le_apply

        flags["worker"] = bool(le_apply.bridge_installed())
        try:
            flags["live"] = bool(le_apply.bridge_alive(90)) or le_apply.sync_state() == "live"
        except Exception:
            flags["live"] = bool(le_apply.bridge_alive(90))
    except Exception:
        pass
    try:
        sq = target_players.load_squad()
        n = int(sq.get("count") or 0)
        players = sq.get("players") or []
        flags["squad"] = n > 0 or bool(players)
    except Exception:
        pass
    try:
        from ... import grok_client

        flags["grok"] = bool(grok_client.is_connected())
    except Exception:
        pass
    return flags


def _number_badge(parent: Any, num: str, *, done: bool) -> Any:
    """Circle-ish badge: ACCENT number on CARD, or SUCCESS check when done."""
    badge = ctk.CTkFrame(
        parent,
        fg_color=CARD,
        width=30,
        height=30,
        corner_radius=15,
        border_width=1,
        border_color=SUCCESS if done else ACCENT,
    )
    badge.pack_propagate(False)
    ctk.CTkLabel(
        badge,
        text="✓" if done else num,
        font=ctk.CTkFont(size=13, weight="bold"),
        text_color=SUCCESS if done else ACCENT,
    ).place(relx=0.5, rely=0.5, anchor="center")
    return badge


def _pipeline_chip(parent: Any, label: str) -> Any:
    chip = ctk.CTkFrame(
        parent,
        fg_color=CARD,
        corner_radius=RADIUS_SM,
        border_width=1,
        border_color=BORDER,
    )
    ctk.CTkLabel(
        chip,
        text=label,
        text_color=TEXT,
        font=ctk.CTkFont(size=12, weight="bold"),
    ).pack(padx=SP3, pady=SP2)
    return chip


def build_home_tab(app: Any) -> None:
    wrap = ctk.CTkScrollableFrame(app.tab_home, fg_color=BG)
    wrap.pack(fill="both", expand=True, padx=SP2, pady=SP2)

    # ── Title block ──────────────────────────────────────────────────
    title_row = ctk.CTkFrame(wrap, fg_color="transparent")
    title_row.pack(anchor="w", fill="x", padx=SP2, pady=(SP2, SP2))

    home_img = None
    try:
        home_img = ctk_icon("home", size=26)
    except Exception:
        home_img = None
    if home_img is not None:
        ctk.CTkLabel(title_row, text="", image=home_img, width=28).pack(
            side="left", padx=(0, SP2)
        )

    title_col = ctk.CTkFrame(title_row, fg_color="transparent")
    title_col.pack(side="left", fill="x", expand=True)
    w.label(title_col, "What do you want to do?", bold=True, size=20).pack(anchor="w")
    w.label(
        title_col,
        "Go LIVE once → Export squad → chain Cards · Add team · Boost workflows.",
        muted=True,
        size=13,
    ).pack(anchor="w", pady=(SP2 // 2, 0))

    flags = _setup_completion_flags()

    # ── LIVE-first setup (one primary action) ─────────────────────────
    setup = w.panel(wrap)
    setup.pack(fill="x", padx=SP2, pady=(SP3, SP3))
    w.label(setup, "Connect to the game", bold=True, size=15).pack(
        anchor="w", padx=SP4, pady=(SP4, SP2)
    )
    w.label(
        setup,
        "One button. Bridge copies itself. You only paste in LE if the header still says OFF.",
        muted=True,
        size=12,
    ).pack(anchor="w", padx=SP4, pady=(0, SP3))

    # Primary CTA row
    cta = ctk.CTkFrame(setup, fg_color=CARD, corner_radius=RADIUS_SM)
    cta.pack(fill="x", padx=SP3, pady=(0, SP3))
    _number_badge(cta, "1", done=flags["live"]).pack(
        side="left", padx=(SP2, 0), pady=SP3
    )
    cta_txt = (
        "LIVE ✓ — game worker is connected"
        if flags["live"]
        else "Click Go LIVE (copies bridge automatically)"
    )
    ctk.CTkLabel(
        cta,
        text=cta_txt,
        text_color=SUCCESS if flags["live"] else TEXT,
        font=ctk.CTkFont(size=13, weight="bold"),
        anchor="w",
    ).pack(side="left", padx=SP2, fill="x", expand=True)
    w.btn(
        cta,
        "LIVE ✓" if flags["live"] else "Go LIVE",
        app._one_click_inject_arm,
        kind="primary" if not flags["live"] else "ghost",
        width=100,
        height=34,
        icon="bridge",
    ).pack(side="right", padx=SP2, pady=SP3)

    # Secondary steps (only after LIVE)
    steps = [
        (
            "2",
            "Export your Career squad (needed for target players)",
            app._export_squad,
            flags["squad"],
            flags["live"],
        ),
        (
            "3",
            "Optional · Connect Codex for AI builds",
            app._grok_connect_popup,
            flags["grok"],
            True,
        ),
    ]
    for num, text, cmd, done, enabled in steps:
        row = ctk.CTkFrame(setup, fg_color=CARD, corner_radius=RADIUS_SM)
        row.pack(fill="x", padx=SP3, pady=SP2 // 2)
        _number_badge(row, num, done=done).pack(side="left", padx=(SP2, 0), pady=SP2)
        label_txt = f"{text}  · done" if done else text
        ctk.CTkLabel(
            row,
            text=label_txt,
            text_color=MUTED if done else TEXT,
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).pack(side="left", padx=SP2, fill="x", expand=True)
        if cmd and enabled:
            w.btn(row, "Do it", cmd, kind="ghost", width=80, height=30).pack(
                side="right", padx=SP2, pady=SP2
            )
        elif cmd and not enabled:
            ctk.CTkLabel(
                row,
                text="needs LIVE",
                text_color=MUTED_DIM,
                font=ctk.CTkFont(size=11),
            ).pack(side="right", padx=SP3)

    squad_line = target_players.squad_status_line()
    if flags["squad"]:
        squad_line = f"✓ {squad_line}"
    w.label(setup, squad_line, muted=True, size=11).pack(
        anchor="w", padx=SP4, pady=(SP2, SP4)
    )

    # ── Main goals ───────────────────────────────────────────────────
    w.label(wrap, "Main goals", bold=True, size=15).pack(
        anchor="w", padx=SP3, pady=(SP2, SP2)
    )
    grid = ctk.CTkFrame(wrap, fg_color="transparent")
    grid.pack(fill="x", padx=SP2)

    journeys = [
        {
            "title": "Match Ready (combo)",
            "blurb": "Export squad + Match Day fitness/sharpness in one workflow pack.",
            "steps": "LIVE ✓ → Boost → Match Ready · or Club Refresh for full boost",
            "cta": "Open Boost workflows",
            "tab": "  Boost  ",
            "kind": "primary",
            "icon": "fitness",
            "cta_icon": "run",
        },
        {
            "title": "Upgrade a squad player",
            "blurb": "Squad locks Target → Cards search → Import overwrite (or AI tweak).",
            "steps": "Export squad → Squad click → Cards → Import · Apply",
            "cta": "Open Cards",
            "tab": "  Cards  ",
            "kind": "primary",
            "icon": "card",
            "cta_icon": "card",
        },
        {
            "title": "Sign a special card",
            "blurb": "Safe free-agent overwrite + transfer (no CreatePlayer freeze). Optional Signing Settle after.",
            "steps": "Export squad (FA pool) → Cards pick → Add team → Add · settle pack",
            "cta": "Open Add team",
            "tab": "  Add team  ",
            "kind": "primary",
            "icon": "target",
            "cta_icon": "run",
        },
        {
            "title": "Boost my squad",
            "blurb": "Stack fitness, form, morale, contracts — turbo queue, no wait per click.",
            "steps": "LIVE ✓ → Boost → mash Run / workflow packs",
            "cta": "Open Boost",
            "tab": "  Boost  ",
            "kind": "accent",
            "icon": "fitness",
            "cta_icon": "run",
        },
        {
            "title": "Build a player (manual or AI)",
            "blurb": "Editor form → Apply to target, or hand off to Add team as a new signing.",
            "steps": "Editor fill / AI → Apply · or Add team ← From Editor",
            "cta": "Open Editor",
            "tab": "  Editor  ",
            "kind": "accent",
            "icon": "edit",
            "cta_icon": "edit",
        },
        {
            "title": "Download FUT catalog",
            "blurb": "Pull specials/Icons from FUT.GG so Cards + Add team share the same pool.",
            "steps": "Catalog → Download → Cards / Add team search",
            "cta": "Open Catalog",
            "tab": "  Catalog  ",
            "kind": "secondary",
            "icon": "download",
            "cta_icon": "download",
        },
    ]

    for i, j in enumerate(journeys):
        r, c = divmod(i, 2)
        card = ctk.CTkFrame(
            grid,
            fg_color=PANEL,
            corner_radius=RADIUS,
            border_width=1,
            border_color=BORDER,
        )
        card.grid(row=r, column=c, sticky="nsew", padx=SP2, pady=SP2)
        grid.columnconfigure(c, weight=1)

        # Subtle top accent bar
        ctk.CTkFrame(card, fg_color=ACCENT, height=3, corner_radius=0).pack(
            fill="x", padx=1, pady=(1, 0)
        )

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(anchor="w", fill="x", padx=SP4, pady=(SP3, SP2))
        j_img: Optional[Any] = None
        try:
            j_img = ctk_icon(j["icon"], size=22)
        except Exception:
            j_img = None
        if j_img is not None:
            ctk.CTkLabel(head, text="", image=j_img, width=24).pack(
                side="left", padx=(0, SP2)
            )
        ctk.CTkLabel(
            head,
            text=j["title"],
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            card,
            text=j["blurb"],
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", padx=SP4, pady=(0, SP2))
        ctk.CTkLabel(
            card,
            text=j["steps"],
            text_color=MUTED_DIM,
            font=ctk.CTkFont(size=11),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", padx=SP4, pady=(0, SP3))
        w.btn(
            card,
            j["cta"],
            lambda t=j["tab"]: app._goto(t),
            kind=j["kind"],
            width=160,
            icon=j["cta_icon"],
            icon_size=16,
        ).pack(anchor="w", padx=SP4, pady=(0, SP4))

    # ── How Apply works ──────────────────────────────────────────────
    how = w.panel(wrap)
    how.pack(fill="x", padx=SP2, pady=(SP3, SP5))
    w.label(how, "How “Apply” works (every path)", bold=True, size=14).pack(
        anchor="w", padx=SP4, pady=(SP4, SP2)
    )
    ctk.CTkLabel(
        how,
        text="This app never injects into the game. It writes Lua for Live Editor.",
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        anchor="w",
    ).pack(anchor="w", padx=SP4, pady=(0, SP3))

    pipe = ctk.CTkFrame(how, fg_color="transparent")
    pipe.pack(fill="x", padx=SP4, pady=(0, SP2))
    stages = ("Write", "Queue", "Bridge", "Game")
    for idx, stage in enumerate(stages):
        _pipeline_chip(pipe, stage).pack(side="left")
        if idx < len(stages) - 1:
            ctk.CTkLabel(
                pipe,
                text="→",
                text_color=ACCENT,
                font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(side="left", padx=SP2)

    ctk.CTkLabel(
        how,
        text="Generate → job in queue/ → LIVE worker drains into Career · re-arm once per session",
        text_color=MUTED_DIM,
        font=ctk.CTkFont(size=11),
        anchor="w",
    ).pack(anchor="w", padx=SP4, pady=(SP2, SP4))
