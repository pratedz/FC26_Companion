"""Embedded Library card picker for the Player surface."""

from __future__ import annotations

from typing import Any

from ....domain.card_import import changed_fields_by_category
from ... import theme
from ...widgets.ovr import ovr_delta_chips
from ...widgets.primitives import EM_DASH, button, eyebrow, muted_label, panel, text_label, themed_scroll
from ...widgets.table import HEADER_HEIGHT, ROW_HEIGHT, Column, DataGrid, TableModel
from .._common import set_status

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

from .presets import _confirm_draft_replacement
from .workstation import _open_review


def _build_card_picker(
    root: Any, svc: Any, view: dict[str, Any] | None = None
) -> None:
    """Embedded Library workflow: search, choose topics, then stage one card."""
    host = panel(root, level=1)
    host.pack(fill="x", pady=(0, theme.SP2))

    heading = ctk.CTkFrame(host, fg_color="transparent")
    heading.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    eyebrow(heading, "Card library").pack(side="left")
    target = svc.store.snapshot().target
    muted_label(
        heading,
        f"Copy onto {target.name or target.playerid}; Review -> Apply remains required.",
        size=11,
    ).pack(side="left", padx=(theme.SP3, 0))
    options = ctk.CTkFrame(host, fg_color="transparent")
    options.pack(fill="x", padx=theme.SP3, pady=theme.SP1)
    choices = {
        "stats": ctk.BooleanVar(value=True),
        "kit": ctk.BooleanVar(value=False),
        "name": ctk.BooleanVar(value=True),
        "age": ctk.BooleanVar(value=True),
        "force_age": ctk.BooleanVar(value=True),
        "face": ctk.BooleanVar(value=True),
    }
    for key, label in (
        ("stats", "Card build"),
        ("kit", "Kit"),
        ("name", "Name"),
        ("age", "Age"),
        ("face", "Face"),
    ):
        ctk.CTkCheckBox(
            options,
            text=label,
            variable=choices[key],
            onvalue=True,
            offvalue=False,
            width=74,
            height=24,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=theme.ACCENT,
            hover_color=theme.CARD_HOVER,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            command=lambda: render_card_preview(),
        ).pack(side="left", padx=(0, theme.SP2))
    force_age = ctk.CTkEntry(
        options,
        width=46,
        height=28,
        justify="center",
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    )
    force_age.insert(0, "25")
    ctk.CTkCheckBox(
        options,
        text="Force age",
        variable=choices["force_age"],
        onvalue=True,
        offvalue=False,
        width=88,
        height=24,
        checkbox_width=18,
        checkbox_height=18,
        fg_color=theme.ACCENT,
        hover_color=theme.CARD_HOVER,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
        command=lambda: render_card_preview(),
    ).pack(side="left", padx=(theme.SP1, 2))
    force_age.pack(side="left", padx=(0, theme.SP2))
    force_age.bind("<KeyRelease>", lambda _event: render_card_preview())
    muted_label(
        host,
        "Card build includes body and run style; Kit is boots, socks, and jersey fit. "
        "Force age overrides the card birthdate (use 25 for Legends). Name, exact birthdate and face use verified FC26 base-player data only.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))

    search_row = ctk.CTkFrame(host, fg_color="transparent")
    # Finding a card is the first action. Keep search above import options so
    # it is visible immediately when this mode opens in a windowed Companion.
    search_row.pack(fill="x", padx=theme.SP3, pady=theme.SP1, before=options)
    query = ctk.CTkEntry(
        search_row,
        height=theme.BTN_MD,
        placeholder_text="Search a player or card variant...",
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    )
    query.pack(side="left", fill="x", expand=True)
    if view is not None and view.get("card_query"):
        query.insert(0, str(view["card_query"]))

    def persist_card_query(_event: Any = None) -> None:
        if view is not None:
            try:
                view["card_query"] = query.get() or ""
            except Exception:
                pass

    query.bind("<KeyRelease>", persist_card_query)

    model = TableModel(
        columns=(
            Column("name", "CARD", width=190, stretch=True),
            Column("year", "GAME", width=52),
            Column("ovr", "OVR", width=48, numeric=True, kind="ovr"),
            Column("pos", "POS", width=48),
            Column("variant", "VARIANT", width=170),
        ),
        rows=(),
        key_field="_key",
        multi_select=False,
    )
    selected: dict[str, Any] = {}

    preview_host = panel(host, level=2)
    # Packed only while a card is selected so Stage stays on a short window.
    # Diff rows go in themed_scroll (token-coloured canvas, not Tk's default).

    def render_card_preview() -> None:
        """Show the selected card against the current target without staging it."""
        for child in list(preview_host.winfo_children()):
            child.destroy()
        if not selected:
            muted_label(
                preview_host,
                "Select a card variant to preview its build against this player.",
                size=11,
            ).pack(anchor="w", padx=theme.SP3, pady=theme.SP2)
            return
        try:
            from ....app.commands.card_import import prepare_card_proposal

            _target_id, proposed = prepare_card_proposal(
                svc, selected.get("_raw") or selected, topics()
            )
            current = svc.store.snapshot().editor.merged()
        except Exception as exc:  # noqa: BLE001
            muted_label(
                preview_host, f"Preview unavailable: {exc}", size=11, color=theme.WARNING
            ).pack(anchor="w", padx=theme.SP3, pady=theme.SP2)
            return

        card_name = str(selected.get("name") or "Card")
        preview_header = ctk.CTkFrame(preview_host, fg_color="transparent")
        preview_header.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, 2))
        text_label(
            preview_header, f"Preview · {card_name}", size=12, bold=True,
            color=theme.ACCENT,
        ).pack(side="left")
        from ....app.commands.player import editor_has_live_values, player_read_pending

        state = svc.store.snapshot()
        reading = player_read_pending(state, state.target.playerid)
        live = editor_has_live_values(state)
        changes = [
            (field, current.get(field), value)
            for field, value in proposed.fields.items()
            if current.get(field) != value
        ]
        overall = next(
            (item for item in changes if item[0] == "overallrating"), None
        )
        if overall is not None:
            _field, before, after = overall
            ovr_delta_chips(preview_host, before, after, size="md").pack(
                anchor="w", padx=theme.SP3, pady=(0, 4),
            )
        text_label(
            preview_host,
            f"{len(changes)} value(s) will change · {len(proposed.fields)} selected card field(s)",
            size=11,
            color=theme.MUTED,
        ).pack(anchor="w", padx=theme.SP3, pady=(2, theme.SP1))
        if reading and not live:
            muted_label(
                preview_host,
                "Reading live attributes from FC 26… current stats fill in when the read finishes.",
                size=10,
                color=theme.ACCENT,
            ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))
        elif not live:
            muted_label(
                preview_host,
                "Squad cache has overall only. Live attributes are being requested from FC 26.",
                size=10,
                color=theme.MUTED,
            ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))
        groups = [
            (category, rows)
            for category, rows in changed_fields_by_category(proposed.fields, current)
            if category != "Ratings" or any(field != "overallrating" for field, *_rest in rows)
        ]
        if groups:
            scroll = themed_scroll(preview_host, fill=theme.CARD, height=160)
            scroll.pack(fill="x", padx=theme.SP2, pady=(0, theme.SP2))
            body = scroll.inner
            for category, rows in groups:
                visible = [
                    item for item in rows if item[0] != "overallrating"
                ]
                if not visible:
                    continue
                text_label(
                    body, category, size=11, bold=True, color=theme.MUTED,
                ).pack(anchor="w", padx=theme.SP1, pady=(theme.SP1, 0))
                for _field, label, before, after in visible:
                    if before is None:
                        before_text = "…" if reading else EM_DASH
                    else:
                        before_text = before
                    muted_label(
                        body,
                        f"{label}  {before_text}  →  {after}",
                        size=10,
                    ).pack(anchor="w", padx=theme.SP2)
        if proposed.warnings:
            muted_label(
                preview_host, f"Note: {proposed.warnings[0]}", size=10, color=theme.WARNING,
            ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    def select_card(row: dict[str, Any]) -> None:
        selected.clear()
        selected.update(row)
        if view is not None:
            view["selected_card"] = dict(row)
        card_name = str(row.get("name") or "card")
        game = str(row.get("year") or "?")
        ovr = str(row.get("ovr") or "?")
        variant = str(row.get("variant") or "Base")
        try:
            selected_caption.configure(
                text=f"Selected: {card_name} · FC {game} · {ovr} OVR · {variant}",
            )
        except Exception:
            pass
        set_status(
            svc,
            f"Selected {card_name} · FC {game} · {ovr} OVR. Stage and review when ready.",
        )
        show_card_preview()
        render_card_preview()
        _prefetch_live_values()

    def clear_card_selection() -> None:
        """Drop the chosen card so the results list can be used again."""
        if not selected:
            set_status(svc, "No card selected — search and pick a variant.")
            return
        selected.clear()
        if view is not None:
            view.pop("selected_card", None)
        model.clear_selection()
        try:
            selected_caption.configure(
                text="Select a card variant to preview and stage it.",
            )
            grid.refresh()
        except Exception:
            pass
        set_status(svc, "Selection cleared — pick another card from the results.")
        show_results_list()
        render_card_preview()

    def topics() -> Any:
        from ....app.commands.card_import import CardImportTopics

        force_age_value: int | None = None
        if choices["force_age"].get():
            raw_age = (force_age.get() or "").strip()
            try:
                force_age_value = int(raw_age)
            except ValueError as exc:
                raise ValueError("Forced age must be a whole number from 16 to 40.") from exc
        return CardImportTopics(
            stats=bool(choices["stats"].get()),
            kit=bool(choices["kit"].get()),
            name=bool(choices["name"].get()),
            age=bool(choices["age"].get()),
            face=bool(choices["face"].get()),
            forced_age=force_age_value,
        )

    def stage_selected() -> None:
        if svc.store.snapshot().editor.has_changes:
            _confirm_draft_replacement(
                svc,
                "this card",
                stage_selected,
            )
            return
        if not selected:
            selected_caption.configure(text="Choose a card variant above before staging.")
            set_status(svc, "Select a Library card first.")
            return
        try:
            from ....app.commands.card_import import stage_card

            def show_review(result: Any) -> None:
                if getattr(result, "field_count", 0):
                    _open_review(svc)

            result = stage_card(
                svc,
                selected.get("_raw") or selected,
                topics(),
                read_live_first=False,
                on_staged=show_review,
            )
            if result.reload_job_id:
                target_name = svc.store.snapshot().target.name or "this player"
                selected_caption.configure(
                    text=(
                        f"Reading {target_name} from FC 26 now… "
                        "this card will stage and open Review automatically."
                    )
                )
            elif getattr(result, "field_count", 0):
                selected_caption.configure(
                    text="Review is open — Apply is the green button in that sheet."
                )
        except Exception as exc:  # noqa: BLE001
            selected_caption.configure(text=f"Could not stage this card: {exc}")
            set_status(svc, str(exc))

    # Stage stays above the preview so a windowed Companion still shows the
    # continue action. Staging is immediate; Review (with Apply) opens now.
    # A live read may still fill current stats in the background.
    actions = ctk.CTkFrame(host, fg_color="transparent")
    actions.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    back_button = button(
        actions,
        "← Back",
        clear_card_selection,
        kind="ghost",
        height=36,
        width=88,
    )
    back_button.pack(side="left")
    stage_button = button(
        actions,
        "Stage card & review now",
        stage_selected,
        # Draft Review remains the screen primary when changes exist.
        kind="accent",
        height=theme.BTN_LG,
    )
    stage_button.pack(side="left", padx=(theme.SP2, 0))
    selected_caption = muted_label(
        actions,
        "Live attributes load in the background. Stage opens Review — Apply is in that sheet.",
        size=12,
    )
    selected_caption.pack(side="left", padx=(theme.SP3, 0), fill="x", expand=True)
    muted_label(
        host,
        "Copy selected card values to staged changes. Apply stays inside Review — never a page shortcut.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))

    grid = DataGrid(
        host,
        model,
        show_checkboxes=False,
        visible_rows=6,
        on_row_click=select_card,
        on_row_activate=lambda row: (
            select_card(dict(row)), stage_selected()
        ),
    )
    try:
        grid.widget.configure(height=HEADER_HEIGHT + ROW_HEIGHT * 6 + 8)
        grid.widget.pack_propagate(False)
    except Exception:
        pass

    def show_results_list() -> None:
        try:
            preview_host.pack_forget()
        except Exception:
            pass
        grid.pack(fill="x", padx=theme.SP3, pady=theme.SP1)

    def show_card_preview() -> None:
        try:
            grid.pack_forget()
        except Exception:
            pass
        preview_host.pack(
            fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP2),
        )

    def _prefetch_live_values() -> None:
        try:
            from ....app.commands.player import editor_has_live_values, ensure_live_player

            state = svc.store.snapshot()
            if editor_has_live_values(state):
                return
            job_id = ensure_live_player(svc)
            if job_id:
                set_status(
                    svc,
                    "Reading live attributes from FC 26… current stats will fill in automatically.",
                )
                render_card_preview()
        except Exception:
            pass

    search_state = {"generation": 0, "busy": False}

    def search_cards() -> None:
        needle = (query.get() or "").strip()
        if not needle:
            set_status(svc, "Enter a player or card name to search the Library.")
            return
        if svc.catalog is None:
            set_status(svc, "The local card Library is unavailable.")
            return
        if search_state.get("busy"):
            return
        search_state["generation"] = int(search_state.get("generation", 0)) + 1
        generation = int(search_state["generation"])
        search_state["busy"] = True
        set_status(svc, "Searching Library…")

        def work(_token: Any) -> None:
            try:
                hits = svc.catalog.search(needle, limit=40)
                rows = tuple(_player_card_row(dict(hit)) for hit in hits)

                def done() -> None:
                    search_state["busy"] = False
                    if int(search_state.get("generation", 0)) != generation:
                        return
                    selected.clear()
                    if view is not None:
                        view.pop("selected_card", None)
                    model.clear_selection()
                    selected_caption.configure(
                        text="Select a card variant to preview and stage it."
                    )
                    model.set_rows(rows)
                    grid.refresh()
                    show_results_list()
                    render_card_preview()
                    set_status(svc, f"{len(rows)} card variant(s) found for {needle}.")

                svc.executor.on_ui_thread(done)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)

                def failed() -> None:
                    search_state["busy"] = False
                    if int(search_state.get("generation", 0)) != generation:
                        return
                    set_status(svc, message)

                svc.executor.on_ui_thread(failed)

        try:
            svc.executor.submit(f"player.search_cards.{generation}", work)
        except Exception as exc:  # noqa: BLE001
            search_state["busy"] = False
            set_status(svc, str(exc))

    query.bind("<Return>", lambda _event: search_cards())
    button(
        search_row, "Search cards", search_cards, kind="secondary", height=32, width=104,
    ).pack(side="left", padx=(theme.SP2, 0))

    pending = (view or {}).pop("pending_library_card", None)
    saved = (view or {}).get("selected_card")
    if pending:
        select_card(_player_card_row(dict(pending)))
    elif saved:
        select_card(dict(saved))
    else:
        show_results_list()
        render_card_preview()
        _prefetch_live_values()


def _player_card_row(card: dict[str, Any]) -> dict[str, Any]:
    """Small display projection which retains the immutable raw catalog row."""
    return {
        "name": card.get("name") or card.get("playername") or "-",
        "year": card.get("year") or card.get("game_year") or "-",
        "ovr": card.get("overallrating") or card.get("ovr") or "-",
        "pos": card.get("positions_text") or card.get("preferredposition1") or "-",
        "variant": card.get("variant") or card.get("revision") or "Base",
        "_key": card.get("_card_key") or card.get("obs_id") or id(card),
        "_raw": card,
    }

