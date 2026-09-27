"""Sign discovery workspace, with the existing safe Checkout as its authority."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ....core.log import get_logger
from ....domain.add_player import (
    INDIVIDUAL_PLAYERS, add_to_sign_list, cards_from_display_rows,
    review_lineup, sign_list_change_message, sign_list_items,
)
from ... import theme
from ...widgets.primitives import button, muted_label, panel, set_disabled
from .._common import section_header, set_status, surface_root

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

from .assistant import CodexLibraryDraft, SquadAssistantPanel, grok_library_worker
from .basket import SignListPanel, bag_summary_copy
from .details import PlayerDetailsPanel
from .discovery import DiscoveryPanel
from .readiness import (
    _active_add_count, _copy_sign_lua_drain, _readiness, _recent_signings,
    _recent_signings_copy, _signing_progress_copy, _wait_for_team_add_repair,
)
from .review import _confirm_add_label, _open_review_window, _report_invalid_lineup
from .search import (
    LibrarySearchPanel, _annotate_card_face, _card_constraints, _card_face_status,
    _card_row, _request_card_constraints, _request_player_count,
    _restore_grid_selection, add_to_bag_label, chosen_age_from_view,
)

_LOG = get_logger("ui.sign")
__all__ = [
    "build", "chosen_age_from_view", "grok_library_worker", "CodexLibraryDraft",
    "SignListPanel", "SquadAssistantPanel", "add_to_bag_label", "bag_summary_copy",
    "_annotate_card_face", "_card_constraints", "_card_face_status", "_card_row",
    "_confirm_add_label", "_copy_sign_lua_drain", "_request_card_constraints",
    "_request_player_count", "_restore_grid_selection", "_readiness",
    "_recent_signings", "_recent_signings_copy", "_signing_progress_copy",
    "_wait_for_team_add_repair",
]


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    view = vm if isinstance(vm, dict) else {}
    defaults = {"query": "", "year": "", "ovr_min": "", "ovr_max": "", "pos": "", "source": "",
                "verified_only": True, "best_only": True, "formation": INDIVIDUAL_PLAYERS,
                "force_age": False, "age": "25", "grok_prompt": "", "grok_count": "3",
                "results_generation": 0, "grok_generation": 0, "selected_key": "", "sign_list": (),
                "discovery_mode": "Search", "discovery_cache": {}}
    for key, value in defaults.items():
        view.setdefault(key, value)
    if view.get("rows") and not view["discovery_cache"]:
        view["discovery_cache"][view["discovery_mode"]] = {
            "rows": view["rows"], "selected_key": view.get("selected_key", ""),
            "checked_keys": view.get("checked_keys", ())}
    section_header(root, "Sign Player", subtitle=
                   "Search verified cards, add them to your bag, then Checkout once to review safe free-agent targets.")
    readiness_host = ctk.CTkFrame(root, fg_color="transparent", height=1)
    readiness_host.pack(fill="x")
    _readiness(readiness_host, svc, svc.store.snapshot(), view)
    progress_row = panel(root, level=1)
    progress_inner = ctk.CTkFrame(progress_row, fg_color="transparent", height=1)
    progress_inner.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    progress_label = muted_label(progress_inner, "", size=11, wraplength=520, justify="left")
    progress_label.pack(side="left", fill="x", expand=True)
    activity_btn = button(progress_inner, "Activity", lambda: svc.ui.open_activity(), kind="ghost", height=28, width=70)
    drain_btn = button(progress_inner, "Copy Lua drain", lambda: _copy_sign_lua_drain(svc), kind="ghost", height=28, width=100)
    workspace = ctk.CTkFrame(root, fg_color="transparent", height=1, width=1)
    workspace.pack(fill="both", expand=True)
    workspace.grid_rowconfigure(0, weight=1)
    workspace.grid_columnconfigure(0, weight=1)
    workspace.grid_columnconfigure(1, minsize=284, weight=0)
    left = ctk.CTkFrame(workspace, fg_color="transparent", width=1, height=1)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.SP2))
    rail = ctk.CTkFrame(workspace, fg_color="transparent", width=284, height=1)
    rail.grid(row=0, column=1, sticky="nsew")
    rail.grid_propagate(False)
    rail.grid_columnconfigure(0, weight=1)
    rail.grid_rowconfigure(0, weight=3, minsize=170)
    rail.grid_rowconfigure(1, weight=2, minsize=160)
    catalog = panel(left, level=1)
    catalog.pack(fill="x", pady=(0, theme.SP2))
    grok_host = ctk.CTkFrame(left, fg_color="transparent", width=1, height=1)
    results = panel(left, level=1, height=1)
    refs: dict[str, Any] = {}
    search = LibrarySearchPanel(catalog, results, svc, view,
                               on_selection_change=lambda: update_review_cta(),
                               on_add=lambda: add_display_rows(search.picked_rows()),
                               on_add_row=lambda row: add_display_rows((row,)))

    def save_view() -> None:
        view.update(search.search_fields())
        view.update(assistant.assistant_fields())
        search.cache_current_mode()

    def recommendations_ready(rows: Any, **kwargs: Any) -> None:
        search.replace_rows(rows, mode="Recommendations", **kwargs)

    assistant = SquadAssistantPanel(grok_host, svc, view, card_constraints=search.card_constraints,
                                    replace_rows=recommendations_ready, save_view=save_view,
                                    toggle_parent=grok_host, discovery=True)

    def review_rows(rows: Sequence[Mapping[str, Any]]) -> None:
        cards = list(cards_from_display_rows(rows))
        if not cards:
            set_status(svc, "Add at least one Library card to your bag first.")
            return
        try:
            age = search.chosen_age()
            from ....app.commands.team import preview_cards_for_team
            preview = preview_cards_for_team(svc, cards, forced_age=age)
            lineup = review_lineup(cards, formation=assistant.current_formation())
            if not lineup.valid:
                _report_invalid_lineup(svc, lineup)
                return
            if preview.face_verified_count != preview.selected_count:
                missing = ", ".join(
                    entry.name for entry in preview.entries if not entry.face_verified
                )
                raise RuntimeError(
                    "Verified FC26 real face is unavailable for " + missing + ". "
                    "Replace those card(s); this draft will not silently keep a dummy face."
                )
            save_view()
            _LOG.info(
                "checking out bag (%s): %s",
                preview.selected_count,
                ", ".join(entry.name for entry in preview.entries),
            )
            _open_review_window(
                root,
                svc,
                preview,
                lineup,
                age,
                view=view,
                on_queued=_after_sign_queued,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("bag checkout failed: %s", exc)
            set_status(svc, str(exc))

    def add_display_rows(rows: Sequence[Mapping[str, Any]]) -> None:
        blocked = search._refuse_unverified(rows)
        if blocked:
            set_status(svc, blocked)
            search.set_feedback(blocked)
            return
        change = add_to_sign_list(sign_list_items(view.get("sign_list")), cards_from_display_rows(rows))
        basket.set_items(change.items)
        search.clear_checks()
        message = sign_list_change_message(change)
        set_status(svc, message)
        search.set_feedback(message)
        search.grid.refresh()

    def update_review_cta() -> None:
        message, is_busy = _signing_progress_copy(svc)
        items = sign_list_items(view.get("sign_list"))
        n = len(items)
        bag = refs.get("basket")
        if bag is not None and refs.get("checkout_count") != n:
            refs["checkout_count"] = n
            bag.checkout_button.configure(text=f"Checkout ({n} player{'s' if n != 1 else ''})" if n else "Checkout")
            set_disabled(bag.checkout_button, "" if n else "Add a verified card to your bag first.")
        search.sync_add_button()
        details = refs.get("details")
        if details is not None:
            details.show(search.selected)
        search.grid.refresh()
        if refs.get("progress") == (message, is_busy):
            return
        refs["progress"] = (message, is_busy)
        progress_label.configure(text=message, text_color=theme.WARNING if is_busy else theme.MUTED)
        if is_busy:
            if not progress_row.winfo_manager():
                progress_row.pack(fill="x", pady=(0, theme.SP2), before=workspace)
            activity_btn.pack(side="right")
            drain_btn.pack(side="right", padx=(0, theme.SP2))
        else:
            progress_row.pack_forget()

    def _after_sign_queued() -> None:
        basket.set_items(())
        try:
            from ....app.commands.squad import sync_squad
            sync_squad(svc)
        except Exception:
            pass
        refresh_signing_progress()

    basket = SignListPanel(rail, svc, view, on_change=update_review_cta,
                           on_focus_search=lambda: refs["discovery"].select("Search"),
                           on_checkout=lambda: review_rows(sign_list_items(view.get("sign_list"))),
                           on_inspect=lambda card: refs["details"].show(card), mount="grid")
    refs["basket"] = basket
    def favourites_changed() -> None:
        details._signature = None
        refs["discovery"].refresh_favourites()
        details.show(search.selected)

    details = PlayerDetailsPanel(rail, svc, on_favourite=favourites_changed)
    refs["details"] = details
    results.pack(fill="both", expand=True)
    discovery = DiscoveryPanel(left, svc, view, search, search_host=catalog,
                               assistant_host=grok_host, assistant=assistant)
    refs["discovery"] = discovery
    search.grid.on_favourite = favourites_changed
    root.signing_review_open = False

    def refresh_signing_progress() -> None:
        update_review_cta()
        discovery.refresh_recent()

    readiness_state = [svc.store.snapshot()]

    def refresh_state() -> bool:
        """Squad updates preserve the mounted workspace, focus and scroll."""
        state, before = svc.store.snapshot(), readiness_state[0]
        lv, old_lv = state.bridge.liveness, before.bridge.liveness
        if (state.squad is not before.squad or state.prefs is not before.prefs
                or (state.bridge.armed, lv.core_version, lv.capabilities.get("add_to_team"))
                != (before.bridge.armed, old_lv.core_version, old_lv.capabilities.get("add_to_team"))):
            for child in readiness_host.winfo_children():
                child.destroy()
            _readiness(readiness_host, svc, state, view)
        readiness_state[0] = state
        refresh_signing_progress()
        return True

    root.refresh_state = refresh_state
    root.refresh_signing_progress = refresh_signing_progress
    root.sign_search, root.sign_discovery, root.sign_bag = search, discovery, basket
    root.sign_details = details
    take_pending = getattr(getattr(svc, "ui", None), "take_signing_card", None)
    pending_card = take_pending() if callable(take_pending) else None
    if isinstance(pending_card, Mapping):
        discovery.select("Search")
        search.replace_rows((pending_card,), mode="Search")
        if search.model.rows:
            search.select(search.model.rows[0])
            add_display_rows(search.model.rows)
    refresh_signing_progress()
    return root
