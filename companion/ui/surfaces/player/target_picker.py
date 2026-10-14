"""Live-squad target picker and session target helpers."""

from __future__ import annotations

from typing import Any

from ... import theme
from ...widgets.primitives import button, muted_label, panel, search_box, set_disabled, text_label
from ...widgets.table import Column, DataGrid, TableModel
from .._common import section_header, set_status, squad_board_row

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def _build_target_picker(root: Any, svc: Any, view: dict[str, Any] | None = None) -> None:
    section_header(
        root,
        "Player",
        subtitle="Choose who to edit. Nothing changes in FC 26 until you review and apply.",
    )
    state = svc.store.snapshot()
    squad = state.squad
    live = state.bridge.liveness
    verified = _has_verified_live_squad(squad, live)
    if not squad.players or not verified:
        from ...icons import ctk_icon

        gate = panel(root, level=1)
        gate.pack(fill="x", pady=(0, theme.SP3))
        top = ctk.CTkFrame(gate, fg_color="transparent")
        top.pack(fill="x", padx=theme.SP5, pady=theme.SP5)
        image = ctk_icon("squad", 32, tint=theme.ACCENT)
        if image is not None:
            ctk.CTkLabel(top, text="", image=image).pack(side="left", padx=(0, theme.SP4))
        copy = ctk.CTkFrame(top, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True)
        text_label(copy, "Read your current squad first", size=theme.T_HEADLINE,
                   display=True).pack(anchor="w")
        muted_label(
            copy,
            "Old squad caches are deliberately unavailable here, so a player from another save cannot be edited by mistake.",
            size=theme.T_SMALL, wraplength=520, justify="left",
        ).pack(anchor="w", pady=(theme.SP1, 0))
        button(
            top, "Open Club", lambda: svc.ui.navigate("club"),
            kind="primary", height=theme.BTN_LG, width=130,
        ).pack(side="right", padx=(theme.SP3, 0))
        return

    rows = [squad_board_row(raw) for raw in squad.players]
    all_rows = tuple(rows)
    model = TableModel(
        columns=(
            Column("name", "PLAYER", width=170, stretch=True, max_width=380),
            Column("pos", "POS", width=52, kind="pos"),
            Column("ovr", "OVR", width=50, numeric=True, kind="ovr"),
            Column("pot", "POT", width=50, numeric=True, kind="ovr", priority=1),
            Column("age", "AGE", width=48, numeric=True, priority=2),
        ),
        rows=rows,
        key_field="id",
        multi_select=False,
    )
    selected: dict[str, Any] = {}

    # Bottom bar first, so a long squad can never push "Open player" off screen.
    bar = panel(root, level=1)
    bar.pack(side="bottom", fill="x", pady=(theme.SP3, 0))
    bar_row = ctk.CTkFrame(bar, fg_color="transparent")
    bar_row.pack(fill="x", padx=theme.SP4, pady=theme.SP3)
    picked_label = muted_label(bar_row, "No player chosen yet.", size=theme.T_SMALL)
    picked_label.pack(side="left")
    open_button = button(
        bar_row,
        "Open player",
        lambda: _pick_squad_player(svc, selected) if selected else None,
        kind="primary",
        height=theme.BTN_MD,
        icon="edit",
        disabled_reason="Choose a player first.",
    )
    open_button.pack(side="right")
    if getattr(svc, "ui", None) is not None:
        button(
            bar_row, "Start from a Library card", lambda: svc.ui.navigate("library"),
            kind="ghost", height=theme.BTN_MD,
        ).pack(side="right", padx=(0, theme.SP2))

    card = panel(root, level=1)
    card.pack(fill="both", expand=True)
    search_row = ctk.CTkFrame(card, fg_color="transparent")
    search_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
    query = ctk.StringVar(value=str((view or {}).get("squad_query") or ""))
    search = search_box(
        search_row, variable=query,
        placeholder="Search your current squad by name, position, or ID",
    )
    search.pack(side="left", fill="x", expand=True)
    count_row = ctk.CTkFrame(card, fg_color="transparent")
    count_row.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP2))
    count_label = muted_label(count_row, f"{len(rows)} players", size=theme.T_CAPTION)
    count_label.pack(side="left")
    muted_label(count_row, "Click to choose  ·  Double-click or Enter opens",
                size=theme.T_CAPTION).pack(side="right")

    def focus_player(row: dict[str, Any]) -> None:
        selected.clear()
        selected.update(row)
        set_disabled(open_button, "")
        open_button.configure(text=f"Open {row.get('name') or 'player'}")
        picked_label.configure(
            text=f"{row.get('name') or 'Player'}  ·  {row.get('pos') or '—'}  ·  OVR {row.get('ovr') or '—'}",
            text_color=theme.TEXT,
        )

    grid = DataGrid(
        card,
        model,
        show_checkboxes=False,
        fill_available=True,
        select_on_click=False,
        on_row_click=focus_player,
        on_row_activate=lambda row: _pick_squad_player(svc, row),
    )

    def filter_squad(*_args: Any) -> None:
        if view is not None:
            view["squad_query"] = query.get() or ""
        needle = (query.get() or "").strip().casefold()
        if not needle:
            filtered = all_rows
        else:
            filtered = tuple(
                row for row in all_rows
                if needle in " ".join(
                    str(row.get(field) or "") for field in ("name", "pos", "id")
                ).casefold()
            )
        model.set_rows(filtered)
        grid.refresh()
        count_label.configure(
            text=f"{len(filtered)} of {len(all_rows)} players" if needle else f"{len(all_rows)} players"
        )

    query.trace_add("write", filter_squad)
    if query.get():
        filter_squad()
    grid.pack(fill="both", expand=True, padx=theme.SP2, pady=(0, theme.SP2))


def _has_verified_live_squad(squad: Any, live: Any) -> bool:
    """Only the active Live Editor session may supply an editable player list."""
    return bool(
        live.armed
        and live.session_id
        and squad.session_id == live.session_id
        and not squad.stale
    )


def _pick_squad_player(svc: Any, row: dict[str, Any]) -> None:
    from ....app.commands.player import lock_player
    from ....domain.player import PlayerValidationError

    raw = row.get("_raw") or row
    try:
        selected = lock_player(
            svc,
            raw.get("playerid", raw.get("id")),
            record=raw,
            name=str(raw.get("name") or raw.get("playername") or ""),
            source="squad",
            teamid=svc.store.snapshot().squad.teamid,
        )
    except PlayerValidationError as exc:
        set_status(svc, str(exc))
        return
    set_status(svc, f"Selected {selected.name or 'player'} — no ID entry needed.")
    if getattr(svc, "ui", None) is not None:
        svc.ui.navigate("player")


def _reload(svc: Any) -> None:
    from ....app.commands.player import reload_player
    from ....domain.player import PlayerValidationError

    try:
        job_id = reload_player(svc)
    except PlayerValidationError as exc:
        set_status(svc, str(exc))
        return
    set_status(svc, f"Live player read queued ({job_id[:8]}…).")


def _reset(svc: Any) -> None:
    from ....app import events as E

    svc.store.dispatch(E.EditorReset())
    set_status(svc, "Staged edits cleared.")


def _choose_another_player(svc: Any) -> None:
    """Return to the live-squad picker without letting one player's draft leak.

    This is deliberately an explicit action.  ``TargetCleared`` also clears the
    editor because a draft is bound to the player it was created for; retaining
    it while another player is selected would make Review/Apply unsafe.
    """
    from ....app import events as E

    state = svc.store.snapshot()
    had_draft = state.editor.has_changes
    old_name = state.target.name or "the previous player"
    svc.store.dispatch(E.TargetCleared())
    if had_draft:
        set_status(
            svc,
            f"Draft for {old_name} discarded. Choose another player from your squad.",
        )
    else:
        set_status(svc, "Choose another player from your squad.")
    if getattr(svc, "ui", None) is not None:
        svc.ui.navigate("player")

