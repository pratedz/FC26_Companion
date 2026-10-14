"""Collection and Best/Base/All controls shared by Search and Suggestions.

They live in the results toolbar, so they cost no extra row at the 1040x720
minimum window and stay visible in both modes.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ....domain import card_collections as cc
from ... import theme
from ...nav import segmented_rail
from ...widgets.primitives import tooltip

try:  # pragma: no cover - desktop only
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

ANY_COLLECTION = "All cards"
#: Card-type entries at the top of the Collection menu (they clear the collection).
KIND_ENTRIES = {"All players": cc.KIND_PLAYER, "All Icons": cc.KIND_ICON, "All Heroes": cc.KIND_HERO}
PICK_LABELS = ((cc.PICK_BEST, "Best"), (cc.PICK_BASE, "Base"), (cc.PICK_ALL, "All"))
PICK_HELP = {
    cc.PICK_BEST: "Best card: one card per player, the highest-rated version.",
    cc.PICK_BASE: "Base card: one card per player, the plain card. Base Icon or Hero for "
                  "legends, gold/silver/bronze for everyone else, newest year first.",
    cc.PICK_ALL: "All versions: every year and promo.",
}


def view_pick(view: Mapping[str, Any]) -> str:
    """The Best/Base/All mode, honouring the older ``best_only`` flag."""
    pick = str(view.get("card_pick") or "")
    if pick in cc.PICKS:
        return pick
    return cc.PICK_BEST if bool(view.get("best_only", True)) else cc.PICK_ALL


def set_view_pick(view: dict[str, Any], pick: str) -> None:
    view["card_pick"] = pick if pick in cc.PICKS else cc.PICK_BEST
    view["best_only"] = view["card_pick"] == cc.PICK_BEST


def card_filters(view: Mapping[str, Any]) -> dict[str, Any]:
    """Library search arguments for the current collection/type/pick."""
    return {
        "collection": str(view.get("collection") or ""),
        "kind": str(view.get("kind") or ""),
        "base_only": view_pick(view) == cc.PICK_BASE,
    }


def menu_value(view: Mapping[str, Any]) -> str:
    """What the Collection menu shows for the current view."""
    kind = str(view.get("kind") or "")
    label = next((label for label, value in KIND_ENTRIES.items() if value == kind), "")
    return label or str(view.get("collection") or "") or ANY_COLLECTION


def apply_menu_value(view: dict[str, Any], value: str) -> None:
    """One menu holds both: a type entry clears the collection and vice versa."""
    if value in KIND_ENTRIES:
        view["kind"], view["collection"] = KIND_ENTRIES[value], ""
    else:
        view["kind"], view["collection"] = "", "" if value == ANY_COLLECTION else value


def filter_caption(view: Mapping[str, Any]) -> str:
    """Plain words for the active collection or type, for status lines."""
    value = menu_value(view)
    return "" if value == ANY_COLLECTION else value


def repick_cards(
    catalog: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    pick: str,
    collection: str = "",
    kind: str = "",
    year: int | None = None,
    ovr_min: int | None = None,
    ovr_max: int | None = None,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Swap each shown card for the same player's card that matches the filters.

    Used for Suggestions: the AI chose the people; the filters choose the card.
    Returns the new rows and the names that have no matching card.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for row in rows:
        card = dict(row.get("_raw") or row)
        versions = [dict(v) for v in (catalog.variants(card) if catalog is not None else ())] or [card]
        eligible = [
            v for v in versions
            if cc.matches(v, collection=collection, kind=kind)
            and (year is None or cc.card_year(v) == int(year))
            and (ovr_min is None or cc.card_overall(v) >= int(ovr_min))
            and (ovr_max is None or cc.card_overall(v) <= int(ovr_max))
        ]
        # A suggestion is one card per player, so All behaves like Best here.
        chosen = cc.choose_cards(eligible, cc.PICK_BEST if pick == cc.PICK_ALL else pick)
        if not chosen:
            dropped.append(str(card.get("name") or "player"))
            continue
        replacement = dict(chosen[0])
        for key in ("_ai_reason", "_match_score"):
            if card.get(key) is not None:
                replacement[key] = card[key]
        kept.append(replacement)
    return tuple(kept), tuple(dropped)


class CardFilterBar:
    """Collection menu (with Players/Icons/Heroes) plus the Best/Base/All switch."""

    def __init__(self, parent: Any, svc: Any, view: dict[str, Any], *,
                 on_change: Callable[[str], None]) -> None:
        self.svc, self.view, self.on_change = svc, view, on_change
        view.setdefault("collection", "")
        view.setdefault("kind", "")
        set_view_pick(view, view_pick(view))
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        current = menu_value(view)
        self.collection = ctk.CTkOptionMenu(
            self.frame, values=[ANY_COLLECTION, *KIND_ENTRIES] + ([current] if current not in KIND_ENTRIES and current != ANY_COLLECTION else []),
            width=164, height=theme.BTN_SM, dynamic_resizing=False,
            font=ctk.CTkFont(family=theme.FONT, size=theme.T_SMALL),
            command=self._menu_changed,
        )
        self.collection.set(current)
        self.collection.pack(side="left")
        tooltip(self.collection, "Card collection: a promo campaign, TOTW, Base Icons/Heroes, "
                                 "base gold/silver/bronze, or all Icons/Heroes/players.")
        self.pick = segmented_rail(
            self.frame, list(PICK_LABELS), active=view_pick(view),
            on_select=lambda value: self._pick_changed(value), compact=True,
        )
        self.pick.configure(width=150)
        self.pick.pack(side="left", padx=(theme.SP2, 0))
        for value, widget in self.pick.segments.items():
            tooltip(widget, PICK_HELP[value])
        self._load_collections()

    def pack(self, **kwargs: Any) -> None:
        self.frame.pack(**kwargs)

    def pack_forget(self) -> None:
        self.frame.pack_forget()

    def visible(self) -> bool:
        try:
            return bool(self.frame.winfo_manager())
        except Exception:
            return False

    def _menu_changed(self, value: str) -> None:
        before = (self.view.get("collection", ""), self.view.get("kind", ""))
        apply_menu_value(self.view, value)
        if (self.view["collection"], self.view["kind"]) != before:
            self.on_change("collection")

    def _pick_changed(self, value: str) -> None:
        if value == view_pick(self.view):
            return
        set_view_pick(self.view, value)
        self.pick.set_active(value)
        self.on_change("card_pick")

    def sync(self) -> None:
        """Repaint from the view (another control changed the shared state)."""
        self.pick.set_active(view_pick(self.view))
        self.collection.set(menu_value(self.view))

    def _load_collections(self) -> None:
        catalog = getattr(self.svc, "catalog", None)
        if catalog is None or not hasattr(catalog, "collections"):
            return

        def work(token: Any) -> None:
            try:
                names = tuple(catalog.collections())
            except Exception:  # noqa: BLE001 - the menu keeps its current value
                return
            if token.cancelled or not names:
                return

            def done() -> None:
                try:
                    if self.frame.winfo_exists():
                        self.collection.configure(values=[ANY_COLLECTION, *KIND_ENTRIES, *names])
                except Exception:
                    pass

            self.svc.executor.on_ui_thread(done)

        try:
            self.svc.executor.submit("sign.collections", work)
        except Exception:
            pass
