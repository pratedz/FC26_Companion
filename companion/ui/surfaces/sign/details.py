"""Catalog-backed player details. Missing values are omitted, never invented."""
from __future__ import annotations

from typing import Any, Mapping
import customtkinter as ctk

from ... import theme
from ...widgets.primitives import button, muted_label, panel, text_label, themed_scroll
from ...widgets.ovr import ovr_chip
from .._common import monogram, set_status


def real_text(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return "" if text.casefold() in {"", "none", "null", "-", "—", "unknown"} else text


def detail_fields(card: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    fields = (
        ("Year", card.get("year") or card.get("game_year")),
        ("Potential", card.get("potential")),
        ("Positions", card.get("positions_text")),
        ("Club", card.get("club") or card.get("club_name")),
        ("Nationality", card.get("nation") or card.get("nationality_name")),
        ("Card", card.get("variant") or card.get("revision")),
        ("Face", card.get("_face_status")),
        ("Source", card.get("source_kind") or card.get("source")),
        ("Signed", card.get("_signed_at")),
    )
    return tuple((label, real_text(value)) for label, value in fields
                 if real_text(value) and not (label == "Potential" and str(value) == "0"))


class PlayerDetailsPanel:
    def __init__(self, parent: Any, svc: Any, *, on_favourite: Any = None) -> None:
        self.svc = svc
        self.on_favourite = on_favourite
        self.host = panel(parent, level=1)
        self.host.grid(row=1, column=0, sticky="nsew", pady=(theme.SP2, 0))
        self.host.grid_rowconfigure(1, weight=1)
        self.host.grid_columnconfigure(0, weight=1)
        text_label(self.host, "Player details", size=14, bold=True).grid(
            row=0, column=0, sticky="w", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
        self.scroll = themed_scroll(self.host, fill=theme.PANEL, height=150)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=theme.SP3, pady=(0, theme.SP3))
        self._signature = None
        self.show({})

    def show(self, row: Mapping[str, Any]) -> None:
        card = row.get("_raw") or row
        signature = (detail_fields(card), card.get("name"), card.get("playername"),
                     card.get("overallrating"), card.get("overall"), card.get("ovr"),
                     card.get("_history"), card.get("_key"), card.get("_card_key"),
                     card.get("playerid"), card.get("variant_id"), card.get("person_id"))
        if signature == self._signature:
            return
        self._signature = signature
        for child in self.scroll.inner.winfo_children():
            child.destroy()
        if not card:
            muted_label(self.scroll.inner, "Select a card to inspect its local Library information.",
                        size=11, wraplength=235, justify="left").pack(anchor="w")
            return
        head = ctk.CTkFrame(self.scroll.inner, fg_color="transparent")
        head.pack(fill="x", pady=(0, theme.SP2))
        name = real_text(card.get("name") or card.get("playername")) or "Player"
        text_label(head, monogram(name)[:2], size=14, bold=True, color=theme.ACCENT,
                   width=36, height=36).pack(side="left", padx=(0, theme.SP2))
        text_label(head, name, size=14, bold=True, wraplength=175, justify="left").pack(
            side="left", fill="x", expand=True)
        raw_ovr = card.get("overallrating") or card.get("overall") or card.get("ovr")
        try:
            value = int(raw_ovr)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            rating = ctk.CTkFrame(self.scroll.inner, fg_color="transparent")
            rating.pack(fill="x", pady=(0, theme.SP2))
            muted_label(rating, "Overall", size=11).pack(side="left")
            ovr_chip(rating, value, size="sm").pack(side="right")
        for label, value in detail_fields(card):
            line = ctk.CTkFrame(self.scroll.inner, fg_color="transparent")
            line.pack(fill="x", pady=2)
            line.grid_columnconfigure(1, weight=1)
            muted_label(line, label, size=11, width=72, anchor="w").grid(row=0, column=0, sticky="nw")
            text_label(line, value, size=11, wraplength=165, justify="right",
                       color=theme.SUCCESS if label == "Face" and value == "Verified" else theme.TEXT
                       ).grid(row=0, column=1, sticky="e")
        if not card.get("_history") and self.svc.catalog is not None:
            try:
                saved = self.svc.catalog.is_favorite(card)
                button(self.scroll.inner, "★ Saved to favourites" if saved else "☆ Save to favourites",
                       lambda: self._toggle(card), kind="ghost", height=theme.BTN_SM).pack(
                           fill="x", pady=(theme.SP2, 0))
            except Exception:
                pass
        self.scroll.fit_inner()

    def _toggle(self, card: Mapping[str, Any]) -> None:
        try:
            self.svc.catalog.toggle_favorite(card)
            self._signature = None
            self.show(card)
            if self.on_favourite:
                self.on_favourite()
        except Exception as exc:
            set_status(self.svc, str(exc))
