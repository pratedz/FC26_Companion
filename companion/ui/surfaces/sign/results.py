"""Compact, paged catalog rows with stable selection and shared favourites."""
from __future__ import annotations

from typing import Any, Mapping
import customtkinter as ctk

from ....domain.add_player import sign_list_items, sign_list_key
from ... import theme
from ...widgets.primitives import button, muted_label, themed_scroll, set_disabled
from . import result_row
from .._common import set_status


def can_add(row: Mapping[str, Any]) -> bool:
    return row.get("face") == "Verified" and not (row.get("_raw") or row).get("_history")


class CardResults:
    """The result-list interface used by LibrarySearchPanel, scoped to Sign.

    At most 20 row widgets are mounted. Selection/check changes update existing
    widgets. New searches and pages reuse the same bounded widget pool.
    """
    PAGE_SIZE = 20

    def __init__(self, parent: Any, model: Any, svc: Any, view: dict[str, Any], *,
                 on_row_click: Any, on_row_activate: Any, on_selection_change: Any,
                 on_favourite: Any = None, **_kwargs: Any) -> None:
        self.model, self.svc, self.view = model, svc, view
        self.on_row_click, self.on_row_activate = on_row_click, on_row_activate
        self.on_selection_change, self.on_favourite = on_selection_change, on_favourite
        self.widget = ctk.CTkFrame(parent, fg_color="transparent")
        self.toolbar = ctk.CTkFrame(self.widget, fg_color="transparent")
        self.toolbar.pack(fill="x", pady=(0, theme.SP2))
        self.all_var = ctk.BooleanVar(value=False)
        self.all_check = ctk.CTkCheckBox(self.toolbar, text="Select cards", variable=self.all_var,
                                        command=self._all, width=100, font=(theme.FONT, 11),
                                        checkbox_width=16, checkbox_height=16, fg_color=theme.ACCENT,
                                        hover_color=theme.CARD_HOVER, border_color=theme.BORDER)
        self.all_check.pack(side="left", padx=theme.SP1)
        self.sort = ctk.CTkOptionMenu(self.toolbar, values=["Overall ↓", "Year ↓", "Name A–Z"],
                                     command=self._sort, width=126, height=28,
                                     fg_color=theme.CARD, button_color=theme.BORDER,
                                     dropdown_fg_color=theme.CARD, dropdown_text_color=theme.TEXT,
                                     text_color=theme.TEXT)
        self.sort.pack(side="right")
        self.scroll = themed_scroll(self.widget, fill=theme.PANEL, height=220, stable_children=True)
        self.scroll.pack(fill="both", expand=True)
        self.pager = ctk.CTkFrame(self.widget, fg_color="transparent")
        self.pager.pack(fill="x", pady=(theme.SP2, 0))
        self.prev = button(self.pager, "‹", lambda: self._page(-1), kind="ghost", width=32, height=28)
        self.prev.pack(side="left")
        self.page_label = muted_label(self.pager, "", size=10)
        self.page_label.pack(side="left", padx=theme.SP2)
        self.next = button(self.pager, "›", lambda: self._page(1), kind="ghost", width=32, height=28)
        self.next.pack(side="left")
        muted_label(self.pager, "Double-click to add", size=10).pack(side="right")
        self._rows: list[dict[str, Any]] = []
        self._signature = None
        self._pool: list[dict[str, Any]] = []
        self._chrome = None
        self._header_labels = {}
        self.widget.bind("<Configure>", self._resize, add="+")

    def pack(self, **kwargs: Any) -> None:
        self.widget.pack(**kwargs)

    def pack_forget(self) -> None:
        self.widget.pack_forget()

    def _page(self, delta: int) -> None:
        self.view["result_page"] = max(0, int(self.view.get("result_page", 0)) + delta)
        self.view.setdefault("discovery_cache", {}).setdefault(self.view.get("discovery_mode", "Search"), {})["page"] = self.view["result_page"]
        self._signature = None
        self.refresh()
        self.scroll.canvas.yview_moveto(0)

    def _sort(self, label: str) -> None:
        key, descending = {"Overall ↓": ("ovr", True), "Year ↓": ("year", True),
                           "Name A–Z": ("name", False)}[label]
        self.model.sort_key, self.model.sort_desc = key, descending
        self.model.set_rows(self.model.rows)
        self.view["result_sort"] = label
        self.view["result_page"] = 0
        self.view.setdefault("discovery_cache", {}).setdefault(self.view.get("discovery_mode", "Search"), {}).update(sort=label, page=0)
        self._signature = None
        self.refresh()
        self.scroll.canvas.yview_moveto(0)

    def _all(self) -> None:
        self.model.checked = ({self.model.row_key(r) for r in self.model.visible_rows()
                               if not r.get("_raw", {}).get("_history")} if self.all_var.get() else set())
        self.on_selection_change(self.model.selected_rows())
        self.refresh()

    def _check(self, row: Mapping[str, Any]) -> None:
        key = self.model.row_key(row)
        if key in self.model.checked:
            self.model.checked.discard(key)
        else:
            self.model.checked.add(key)
        self.on_row_click(row)
        self.on_selection_change(self.model.selected_rows())
        self.refresh()

    def _select(self, row: Mapping[str, Any], event: Any = None) -> None:
        if event is not None and not (row.get("_raw") or row).get("_history") and event.state & 5:
            index = next((i for i,r in enumerate(self.model.visible_rows()) if self.model.row_key(r) == self.model.row_key(row)), -1)
            self.model.click(index, shift=bool(event.state & 1), ctrl=bool(event.state & 4))
            self.on_selection_change(self.model.selected_rows())
        self.on_row_click(row)
        self.refresh()

    def _favourite(self, row: Mapping[str, Any]) -> None:
        try:
            active = self.svc.catalog.toggle_favorite(row.get("_raw") or row)
            set_status(self.svc, "Saved to favourites." if active else "Removed from favourites.")
            for item in self._pool:
                item["presentation"] = None
            self._signature = None
            if self.on_favourite:
                self.on_favourite()
            else:
                self.refresh()
        except Exception as exc:
            set_status(self.svc, str(exc))

    def _resize(self, _event: Any = None) -> None:
        wide = self.widget.winfo_width() >= 710
        for item in self._rows:
            club = item.get("club")
            if club is not None and item.get("wide") != wide:
                item["wide"] = wide
                if wide and item.get("club_text"):
                    club.pack(side="right", padx=theme.SP2, before=item["actions"])
                else:
                    club.pack_forget()

    def refresh(self) -> None:
        rows = self.model.visible_rows()
        page = min(max(0, int(self.view.get("result_page", 0))), max(0, (len(rows) - 1) // self.PAGE_SIZE))
        self.view["result_page"] = page
        batch = rows[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]
        signature = (tuple(id(row) for row in batch), page, self.view.get("discovery_mode"))
        if signature != self._signature:
            self._signature = signature
            while len(self._pool) < len(batch):
                self._pool.append(result_row.mount(self))
            self._rows = self._pool[:len(batch)]
            for item, row in zip(self._rows, batch):
                result_row.bind(self, item, row)
                if not item["frame"].winfo_manager():
                    item["frame"].pack(fill="x", pady=(0, 5))
            for item in self._pool[len(batch):]:
                item["frame"].pack_forget()
            self.scroll.fit_inner()
        bag_keys = {sign_list_key(card) for card in sign_list_items(self.view.get("sign_list"))}
        for item in self._rows:
            row = item["row"]
            picked = str(self.view.get("selected_key") or "") == self.model.row_key(row)
            checked = self.model.is_checked(row)
            in_bag = sign_list_key(row.get("_raw") or row) in bag_keys
            state = (picked, checked, in_bag, can_add(row))
            if item["state"] == state:
                continue
            previous = item["state"]
            item["state"] = state
            if previous is None or previous[0] != picked:
                item["frame"].configure(border_color=theme.ACCENT if picked else theme.BORDER,
                                        fg_color=theme.CARD_HOVER if picked else theme.CARD)
            if item["check_var"].get() != checked:
                item["check_var"].set(checked)
            if previous is None or previous[2:] != state[2:]:
                item["add"].configure(text="In bag" if in_bag else "+ Add")
                set_disabled(item["add"], "Already in your bag" if in_bag else
                             "" if can_add(row) else "A verified local card is required")
        chrome = (len(rows), page, self.view.get("discovery_mode"), self.view.get("result_sort"),
                  bool(rows) and all(self.model.is_checked(r) for r in rows))
        self._resize()
        if chrome == self._chrome:
            return
        self._chrome = chrome
        self.all_var.set(bool(rows) and all(self.model.is_checked(r) for r in rows))
        self.all_check.configure(state="disabled" if self.view.get("discovery_mode") == "Recently Added" else "normal")
        if self.view.get("discovery_mode") == "Recently Added":
            self.sort.pack_forget()
        elif not self.sort.winfo_manager():
            self.sort.pack(side="right")
        self.sort.set(str(self.view.get("result_sort") or "Overall ↓"))
        start = page * self.PAGE_SIZE + 1 if rows else 0
        self.page_label.configure(text=f"{start}–{min((page + 1) * self.PAGE_SIZE, len(rows))} of {len(rows)}")
        set_disabled(self.prev, "First page" if page == 0 else "")
        set_disabled(self.next, "Last page" if (page + 1) * self.PAGE_SIZE >= len(rows) else "")
        self._resize()

