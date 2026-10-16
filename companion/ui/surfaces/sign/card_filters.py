"""Card version (Best/Base/All) and collection controls for Search and Suggestions.

Search shows them as a full row under the search box. Suggestions has no
spare height at 1040x720, so a compact copy sits in the results toolbar.
Both copies edit the same view state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ....domain import card_collections as cc
from ... import theme
from ...nav import segmented_rail
from ...widgets.primitives import tooltip

try:  # pragma: no cover - desktop only
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

ANY_COLLECTION = "All collections"
#: Card-type entries at the top of the Collection menu (they clear the collection).
KIND_ENTRIES = {"All players": cc.KIND_PLAYER, "All Icons": cc.KIND_ICON, "All Heroes": cc.KIND_HERO}
PICK_LABELS = ((cc.PICK_BEST, "Best"), (cc.PICK_BASE, "Base"), (cc.PICK_ALL, "All"))
FULL_PICK_LABELS = ((cc.PICK_BEST, "Best card"), (cc.PICK_BASE, "Base card"), (cc.PICK_ALL, "All versions"))
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
    # All versions is the neutral/default browse mode.  Older saved views that
    # explicitly carry best_only=True continue to honour that choice.
    return cc.PICK_BEST if bool(view.get("best_only", False)) else cc.PICK_ALL


def set_view_pick(view: dict[str, Any], pick: str) -> None:
    view["card_pick"] = pick if pick in cc.PICKS else cc.PICK_ALL
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


def apply_menu_value(view: dict[str, Any], value: str, *, year_hint: int | None = None) -> None:
    """One menu holds both: a type entry clears the collection and vice versa.

    A promo picked from an FC year's group also sets the Year menu to that year.
    """
    if value in KIND_ENTRIES:
        view["kind"], view["collection"] = KIND_ENTRIES[value], ""
    else:
        view["kind"], view["collection"] = "", "" if value == ANY_COLLECTION else value
        if year_hint is not None and view["collection"]:
            view["year"] = f"{year_hint:02d}"


def filter_caption(view: Mapping[str, Any]) -> str:
    """Plain words for the active collection or type, for status lines."""
    value = menu_value(view)
    return "" if value == ANY_COLLECTION else value


YEAR_ANY = "Any year"
BASE_COLLECTIONS = (cc.BASE_GOLD, cc.BASE_SILVER, cc.BASE_BRONZE, cc.BASE_ICONS, cc.BASE_HEROES)
WEEKLY_COLLECTIONS = tuple(name for name in cc.CORE_COLLECTIONS if name not in BASE_COLLECTIONS)
#: A promo group with more entries than this splits its smaller campaigns into a second column.
COLUMN_LIMIT = 24
_YEAR_RE = re.compile(r"\d{2,4}")
_KIND_WORDS = {cc.KIND_ICON: "Icons", cc.KIND_HERO: "Heroes"}


@dataclass(frozen=True)
class MenuGroup:
    """One cascade in the Collection menu."""

    title: str
    names: tuple[str, ...]
    year: int | None = None  # an FC year's promo group: picking one also sets the Year
    split: int = 0           # index where a second column starts; 0 keeps one column


def _year_int(text: Any) -> int | None:
    match = _YEAR_RE.search(str(text or ""))
    if not match:
        return None
    value = int(match.group(0))
    return value % 100 if value >= 2000 else value


def year_label(text: Any) -> str:
    """Year menu text for a view year such as "26" (or legacy "2026")."""
    number = _year_int(text)
    return f"FC {number:02d}" if number is not None else YEAR_ANY


def year_value(label: str) -> str:
    """View year for a Year menu label: "FC 26" -> "26", "Any year" -> ""."""
    number = _year_int(label)
    return f"{number:02d}" if number is not None else ""


def _scope_names(by_year: Mapping[int, Sequence[str]], year: int | None) -> set[str]:
    if year is None:
        return {name for names in by_year.values() for name in names}
    return set(by_year.get(year, ()))


def collection_menu(
    by_year: Mapping[int, Sequence[str]], year: int | None = None,
) -> tuple[tuple[str, ...], tuple[MenuGroup, ...]]:
    """Top-level type entries and cascades the Collection menu shows for one year, or all years."""
    present = _scope_names(by_year, year)
    kinds = tuple(
        label for label, kind in KIND_ENTRIES.items()
        if kind == cc.KIND_PLAYER
        or (kind == cc.KIND_ICON and cc.BASE_ICONS in present)
        or (kind == cc.KIND_HERO and cc.BASE_HEROES in present)
    )
    groups: list[MenuGroup] = []
    base = tuple(name for name in BASE_COLLECTIONS if name in present)
    if base:
        groups.append(MenuGroup("Base cards", base))
    weekly = tuple(name for name in WEEKLY_COLLECTIONS if name in present)
    if weekly:
        groups.append(MenuGroup("Every week", weekly))
    fixed = set(BASE_COLLECTIONS) | set(WEEKLY_COLLECTIONS)
    for number in sorted(by_year) if year is None else [year]:
        promos = sorted(set(by_year.get(number, ())) - fixed, key=cc.collection_rank)
        if not promos:
            continue
        main = sum(1 for name in promos if cc.collection_rank(name)[0] == 1)
        split = main if len(promos) > COLUMN_LIMIT else 0
        groups.append(MenuGroup(f"FC{number:02d} promos", tuple(promos), year=number, split=split))
    return kinds, tuple(groups)


def reconcile_year(view: dict[str, Any], by_year: Mapping[int, Sequence[str]]) -> str:
    """Clear a collection or type the chosen year does not have; return the note for the user."""
    number = _year_int(view.get("year"))
    if number is None or not by_year:  # no Library listing yet: keep the choice
        return ""
    present = _scope_names(by_year, number)
    label = year_label(view.get("year"))
    collection = str(view.get("collection") or "")
    if collection and collection not in present:
        view["collection"] = ""
        return f"{label} has no {collection} cards — showing all collections."
    kind = str(view.get("kind") or "")
    needed = {cc.KIND_ICON: cc.BASE_ICONS, cc.KIND_HERO: cc.BASE_HEROES}.get(kind, "")
    if needed and needed not in present:
        view["kind"] = ""
        return f"{label} has no {_KIND_WORDS[kind]} — showing all players."
    return ""


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


if ctk is not None:  # the widget needs customtkinter; the pure helpers above do not
    from customtkinter.windows.widgets.core_widget_classes.dropdown_menu import DropdownMenu

    MenuProvider = Callable[[], tuple[tuple[str, ...], tuple[MenuGroup, ...]]]

    class GroupedCollectionMenu(ctk.CTkOptionMenu):
        """Collection button whose popup nests the groups as cascades, rebuilt on every open.

        ``provider`` gives the type entries and groups for the current year; ``on_pick``
        receives the chosen value and, for a promo, its FC year.
        """

        def __init__(self, master: Any, *, provider: MenuProvider,
                     on_pick: Callable[[str, int | None], None], **kwargs: Any) -> None:
            self._provider, self._on_pick = provider, on_pick
            self._built: list[Any] = []
            super().__init__(master, values=[ANY_COLLECTION], **kwargs)

        def build_menu(self) -> Any:
            """Rebuild the popup for the current year and return its top menu."""
            kinds, groups = self._provider()
            top = DropdownMenu(master=self, values=[ANY_COLLECTION, *kinds],
                               command=self._picked, font=self._font)
            built: list[Any] = [top]
            if groups:
                top.add_separator()
            for group in groups:
                sub = DropdownMenu(master=top, values=list(group.names), font=self._font,
                                   command=lambda value, year=group.year: self._picked(value, year))
                if group.split:
                    # Tk starts a new column at this entry; smaller campaigns sit beside the main ones.
                    sub.entryconfigure(group.split, columnbreak=1)
                top.add_cascade(label=group.title, menu=sub)
                built.append(sub)
            old, self._built = self._built, built
            self._dropdown_menu = top
            for menu in old:
                menu.destroy()
            return top

        def _open_dropdown_menu(self) -> None:
            self.build_menu()
            super()._open_dropdown_menu()

        def _picked(self, value: str, year: int | None = None) -> None:
            self.set(value)
            self._on_pick(value, year)


class CardFilterBar:
    """Collection menu (grouped, with Players/Icons/Heroes) plus the Best/Base/All switch."""

    def __init__(self, parent: Any, svc: Any, view: dict[str, Any], *,
                 on_change: Callable[[str], None], compact: bool = False) -> None:
        """``compact`` is the toolbar copy for Suggestions; Search gets the full row."""
        self.svc, self.view, self.on_change = svc, view, on_change
        view.setdefault("collection", "")
        view.setdefault("kind", "")
        set_view_pick(view, view_pick(view))
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        # The version switch comes first: it is the choice every search needs.
        labels = PICK_LABELS if compact else FULL_PICK_LABELS
        self.pick = segmented_rail(
            self.frame, list(labels), active=view_pick(view),
            on_select=lambda value: self._pick_changed(value), compact=True,
        )
        self.pick.configure(width=150 if compact else 252)
        self.pick.pack(side="left")
        for value, widget in self.pick.segments.items():
            tooltip(widget, PICK_HELP[value])
        self.by_year: dict[int, tuple[str, ...]] = {}
        current = menu_value(view)
        self.collection = GroupedCollectionMenu(
            self.frame, provider=self._menu_entries, on_pick=self._menu_changed,
            width=164 if compact else 200, height=theme.BTN_SM if compact else theme.BTN_MD,
            dynamic_resizing=False,
            font=ctk.CTkFont(family=theme.FONT, size=theme.T_SMALL if compact else theme.T_BODY),
        )
        self.collection.set(current)
        self.collection.pack(side="left", padx=(theme.SP2, 0))
        tooltip(self.collection, "Pick a collection, grouped as Base cards, Every week and each FC year's "
                                 "promos. The Year box narrows the list to that year. Or only Icons, "
                                 "Heroes or normal players.")
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

    def _menu_entries(self) -> tuple[tuple[str, ...], tuple[MenuGroup, ...]]:
        return collection_menu(self.by_year, _year_int(self.view.get("year")))

    def _menu_state(self) -> tuple[str, str, str]:
        return (str(self.view.get("collection") or ""), str(self.view.get("kind") or ""),
                str(self.view.get("year") or ""))

    def _menu_changed(self, value: str, year: int | None = None) -> None:
        before = self._menu_state()
        apply_menu_value(self.view, value, year_hint=year)
        if self._menu_state() != before:
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
        if catalog is None or not hasattr(catalog, "collections_by_year"):
            return

        def work(token: Any) -> None:
            try:
                by_year = dict(catalog.collections_by_year())
            except Exception:  # noqa: BLE001 - the menu keeps the groups it has
                return
            if token.cancelled or not by_year:
                return

            def done() -> None:
                try:
                    if self.frame.winfo_exists():
                        self.by_year = by_year
                except Exception:
                    pass

            self.svc.executor.on_ui_thread(done)

        try:
            self.svc.executor.submit("sign.collections", work)
        except Exception:
            pass
