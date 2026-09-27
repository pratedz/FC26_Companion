"""Live-squad target picker and session target helpers."""

from __future__ import annotations

from typing import Any

from ... import theme
from ...widgets.primitives import button, eyebrow, muted_label, panel, text_label
from ...widgets.table import Column, DataGrid, TableModel
from .._common import section_header, set_status

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def _build_target_picker(root: Any, svc: Any, view: dict[str, Any] | None = None) -> None:
    title_row = ctk.CTkFrame(root, fg_color="transparent")
    title_row.pack(fill="x", pady=(0, theme.SP1))
    text_label(title_row, "Player", size=22, bold=True).pack(anchor="w")
    section_header(
        root,
        "Choose a player",
        subtitle="Start with a verified player from the Career save that is open now.",
    )
    state = svc.store.snapshot()
    squad = state.squad
    live = state.bridge.liveness
    verified = _has_verified_live_squad(squad, live)
    if not squad.players or not verified:
        gate = panel(root, level=1)
        gate.pack(fill="x", pady=(theme.SP1, theme.SP3))
        top = ctk.CTkFrame(gate, fg_color="transparent")
        top.pack(fill="x", padx=theme.SP4, pady=theme.SP3)
        copy = ctk.CTkFrame(top, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True)
        eyebrow(copy, "Live squad required").pack(anchor="w")
        text_label(copy, "Read your current squad before editing", size=16, bold=True).pack(
            anchor="w", pady=(2, 0)
        )
        muted_label(
            copy,
            "Old squad caches are deliberately unavailable here, so a player from another save cannot be edited by mistake.",
            size=11,
        ).pack(anchor="w", pady=(2, 0))
        button(
            top, "Open Club", lambda: svc.ui.navigate("club"),
            kind="primary", height=theme.BTN_LG, width=130,
        ).pack(side="right", padx=(theme.SP3, 0))
        path = ctk.CTkFrame(gate, fg_color="transparent")
        path.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP3))
        muted_label(path, "Club", size=11).pack(side="left")
        text_label(path, "›", size=15, color=theme.BORDER).pack(side="left", padx=theme.SP2)
        text_label(path, "Read squad", size=11, bold=True, color=theme.ACCENT).pack(side="left")
        text_label(path, "›", size=15, color=theme.BORDER).pack(side="left", padx=theme.SP2)
        muted_label(path, "Choose player", size=11).pack(side="left")
        return

    rows = []
    for raw in squad.players:
        rows.append(
            {
                "name": raw.get("name") or raw.get("playername") or "-",
                "pos": raw.get("position") or raw.get("preferredposition1") or "-",
                "ovr": raw.get("overallrating") or raw.get("ovr") or "-",
                "pot": raw.get("potential") or raw.get("pot") or "-",
                "id": raw.get("playerid") or raw.get("id"),
                "_raw": raw,
            }
        )
    all_rows = tuple(rows)
    guide = panel(root, level=1)
    guide.pack(fill="x", pady=(theme.SP1, theme.SP2))
    guide_row = ctk.CTkFrame(guide, fg_color="transparent")
    guide_row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    eyebrow(guide_row, f"{len(rows)} players").pack(side="left")
    muted_label(
        guide_row, "Select a row to preview it, then open it. Double-click opens immediately.", size=11,
    ).pack(side="left", padx=(theme.SP3, 0))
    search_row = ctk.CTkFrame(root, fg_color="transparent")
    search_row.pack(fill="x", pady=(theme.SP1, 0))
    query = ctk.StringVar(value=str((view or {}).get("squad_query") or ""))
    search_box = ctk.CTkEntry(
        search_row,
        textvariable=query,
        placeholder_text="Search your current squad by name, position, or ID…",
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    )
    search_box.pack(side="left", fill="x", expand=True)
    model = TableModel(
        columns=(
            Column("name", "PLAYER", width=220),
            Column("pos", "POS", width=55),
            Column("ovr", "OVR", width=55, numeric=True, kind="ovr"),
            Column("pot", "POT", width=55, numeric=True, kind="ovr"),
        ),
        rows=rows,
        key_field="id",
        multi_select=False,
    )
    selected: dict[str, Any] = {}
    picker_actions = ctk.CTkFrame(root, fg_color="transparent")
    picker_actions.pack(fill="x", pady=(theme.SP2, 0))
    open_button = button(
        picker_actions,
        "Open player",
        lambda: _pick_squad_player(svc, selected) if selected else None,
        kind="primary",
        height=theme.BTN_MD,
        disabled_reason="Choose a player first.",
    )
    open_button.pack(side="left")
    muted_label(
        picker_actions, "Nothing is changed when you open a player.", size=11,
    ).pack(side="left", padx=(theme.SP2, 0))

    def focus_player(row: dict[str, Any]) -> None:
        selected.clear()
        selected.update(row)
        try:
            open_button.configure(state="normal")
        except Exception:
            pass

    grid = DataGrid(
        root,
        model,
        show_checkboxes=False,
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

    query.trace_add("write", filter_squad)
    if query.get():
        filter_squad()
    button(
        search_row,
        "Clear",
        lambda: query.set(""),
        kind="ghost",
        height=theme.BTN_MD,
        width=62,
    ).pack(side="left", padx=(theme.SP2, 0))
    grid.pack(fill="both", expand=True, pady=(theme.SP2, 0))
    if getattr(svc, "ui", None) is not None:
        button(
            root, "Use a Library card instead", lambda: svc.ui.navigate("library"),
            kind="ghost", height=theme.BTN_MD,
        ).pack(anchor="w", pady=(theme.SP2, 0))


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

