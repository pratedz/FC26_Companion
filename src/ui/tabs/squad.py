"""Squad board tab."""

from __future__ import annotations

from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import target_players
from ...ui_theme import (
    ACCENT,
    BG,
    CARD,
    FONT_MONO,
    LIST_BG,
    MUTED,
    TEXT,
)
from .. import widgets as w

def _build_squad_tab(app: Any) -> None:
    wrap = ctk.CTkFrame(app.tab_squad, fg_color=BG)
    wrap.pack(fill="both", expand=True)
    w.label(wrap, "Squad board", bold=True, size=18).pack(anchor="w", padx=16, pady=(12, 4))
    w.label(
        wrap,
        "Click = lock Target + Career ops ID · double-click = Cards overwrite. Hub for Cards / Add team / Boost.",
        muted=True,
        size=12,
    ).pack(anchor="w", padx=16, pady=(0, 8))
    bar = ctk.CTkFrame(wrap, fg_color="transparent")
    bar.pack(fill="x", padx=16, pady=4)
    # Export = primary CTA; Refresh = accent secondary
    w.btn(
        bar,
        "Export squad",
        app._export_squad,
        kind="primary",
        width=140,
        icon="export",
    ).pack(side="left", padx=4)
    w.btn(
        bar,
        "Refresh",
        app._refresh_squad_board,
        kind="accent",
        width=110,
        icon="refresh",
    ).pack(side="left", padx=4)
    w.btn(
        bar,
        "Match Ready",
        lambda: _squad_workflow(app, "match_ready"),
        kind="accent",
        width=120,
        height=30,
    ).pack(side="left", padx=4)
    w.btn(
        bar,
        "Cards →",
        lambda: app._goto("  Cards  "),
        kind="ghost",
        width=90,
        height=30,
    ).pack(side="left", padx=2)
    w.btn(
        bar,
        "Add team →",
        lambda: app._goto("  Add team  "),
        kind="ghost",
        width=100,
        height=30,
    ).pack(side="left", padx=2)
    app.squad_filter_var = ctk.StringVar(value="")
    filt = w.entry(bar, app.squad_filter_var, width=200)
    try:
        filt.configure(placeholder_text="Filter name / pos / id")
    except Exception:
        pass
    filt.pack(side="left", padx=8)
    w.btn(bar, "Filter", app._refresh_squad_board, kind="ghost", width=70, height=30).pack(
        side="left"
    )
    app.squad_board_status = ctk.CTkLabel(
        bar, text=target_players.squad_status_line(), text_color=MUTED, font=ctk.CTkFont(size=12)
    )
    app.squad_board_status.pack(side="left", padx=12)

    list_frame = w.panel(wrap)
    list_frame.pack(fill="both", expand=True, padx=16, pady=8)
    # Column header (table-like) — ACCENT header text
    hdr = ctk.CTkFrame(list_frame, fg_color=CARD, height=32, corner_radius=0)
    hdr.pack(fill="x", padx=1, pady=(1, 0))
    hdr.pack_propagate(False)
    ctk.CTkLabel(
        hdr,
        text=f"{'#':>3}  {'NAME':<28}  {'OVR':>3}  {'POS':<4}  {'#':>3}  {'ID':>8}",
        font=ctk.CTkFont(family=FONT_MONO, size=11, weight="bold"),
        text_color=ACCENT,
        anchor="w",
    ).pack(side="left", padx=12, pady=4)

    body = ctk.CTkFrame(list_frame, fg_color=LIST_BG)
    body.pack(fill="both", expand=True, padx=1, pady=1)
    app.squad_board = w.listbox(body, height=18)
    scroll = ctk.CTkScrollbar(body, command=app.squad_board.yview)
    app.squad_board.configure(yscrollcommand=scroll.set)
    app.squad_board.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
    scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)
    app.squad_board.bind("<<ListboxSelect>>", app._on_squad_board_select)
    app.squad_board.bind("<Double-Button-1>", lambda _e: app._squad_go_cards())
    app._refresh_squad_board()



def _squad_workflow(app: Any, pack_id: str) -> None:
    from .. import handoffs

    n = handoffs.queue_workflow_pack(app, pack_id)
    if n:
        try:
            app._goto("  Boost  ")
        except Exception:
            pass


def _refresh_squad_board(app: Any) -> None:
    squad = target_players.load_squad()
    rows = list(squad.get("players") or [])
    q = (getattr(app, "squad_filter_var", ctk.StringVar(value="")).get() or "").strip().lower()
    if q:
        rows = [
            p
            for p in rows
            if q in str(p.get("name") or "").lower()
            or q in str(p.get("playerid") or "")
            or q in str(p.get("position") or "").lower()
        ]
    app._squad_board_rows = rows
    if hasattr(app, "squad_board_status"):
        try:
            from .. import handoffs

            fa = handoffs.free_agent_pool_count(squad)
            base = target_players.squad_status_line()
            app.squad_board_status.configure(
                text=f"{base}  ·  FA pool {fa}" if fa else base
            )
        except Exception:
            app.squad_board_status.configure(text=target_players.squad_status_line())
    if not hasattr(app, "squad_board"):
        return
    app.squad_board.delete(0, "end")
    if not app._squad_board_rows:
        # Keep listbox; clear empty message (select handlers ignore empty _squad_board_rows)
        empty_msg = (
            "  No squad yet — Export squad with LIVE worker, or clear the filter."
            if not q
            else "  No players match this filter — clear filter or Export squad."
        )
        app.squad_board.insert("end", empty_msg)
        return
    row_alt = None
    try:
        from ...ui_theme import ROW_ALT as row_alt  # type: ignore
    except Exception:
        row_alt = None
    for i, p in enumerate(app._squad_board_rows):
        name = str(p.get("name") or "?")[:28]
        ovr = str(p.get("overallrating") or "?")[:3]
        pos = str(p.get("position") or "")[:4]
        jn = str(p.get("jerseynumber") or "")[:3]
        pid = str(p.get("playerid") or "")[:8]
        line = f"{i:>3}  {name:<28}  {ovr:>3}  {pos:<4}  {jn:>3}  {pid:>8}"
        app.squad_board.insert("end", line)
        if row_alt and i % 2 == 1:
            try:
                app.squad_board.itemconfig(i, bg=row_alt)
            except Exception:
                pass



def _on_squad_board_select(app: Any, _e: Any = None) -> None:
    sel = app.squad_board.curselection()
    if not sel or not getattr(app, "_squad_board_rows", None):
        return
    idx = int(sel[0])
    if 0 <= idx < len(app._squad_board_rows):
        p = app._squad_board_rows[idx]
        from .. import handoffs

        handoffs.lock_squad_target(app, p)
        app.status.set(
            f"Target · {p.get('name')} · id {p.get('playerid')}  ·  Cards/Boost career ops linked"
        )
        try:
            from . import cards as cards_tab

            cards_tab.update_cards_pipeline(app)
        except Exception:
            pass


def _squad_go_cards(app: Any) -> None:
    sel = ()
    try:
        sel = app.squad_board.curselection()
    except Exception:
        pass
    rows = getattr(app, "_squad_board_rows", None) or []
    if sel and rows:
        idx = int(sel[0])
        if 0 <= idx < len(rows):
            from .. import handoffs

            handoffs.send_squad_player_to_cards(app, rows[idx])
            return
    app._on_squad_board_select()
    try:
        app._goto("  Cards  ")
    except Exception:
        try:
            app.tabs.set("  Cards  ")
        except Exception:
            pass

