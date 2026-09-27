"""Reusable Sign row: rebinding data never destroys or recreates widgets."""
from typing import Any

import customtkinter as ctk

from ... import theme
from ...widgets.primitives import button, muted_label, text_label
from .._common import monogram


def mount(owner: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"row": {}, "presentation": None, "state": None, "wide": None}
    frame = ctk.CTkFrame(owner.scroll.inner, fg_color=theme.CARD, border_width=1,
                         border_color=theme.BORDER, corner_radius=theme.R_SM)
    inner = ctk.CTkFrame(frame, fg_color="transparent")
    inner.pack(fill="x", padx=9, pady=9)
    check_var = ctk.BooleanVar(value=False)
    check = ctk.CTkCheckBox(inner, text="", width=18, variable=check_var,
                           checkbox_width=16, checkbox_height=16, border_width=1,
                           fg_color=theme.ACCENT, border_color=theme.MUTED_DIM,
                           command=lambda: owner._check(item["row"]))
    mono = text_label(inner, "", width=32, height=32, size=11, bold=True,
                      fg_color=theme.CARD_HOVER, corner_radius=16, color=theme.ACCENT)
    mono.pack(side="left", padx=(0, 8))
    actions = ctk.CTkFrame(inner, fg_color="transparent")
    actions.pack(side="right", padx=(6, 0))
    fav = button(actions, "☆", lambda: owner._favourite(item["row"]), kind="ghost", width=28, height=28)
    add = button(actions, "+ Add", lambda: owner.on_row_activate(item["row"]), kind="secondary", width=64, height=28)
    club = muted_label(inner, "", size=11, width=100)
    copy = ctk.CTkFrame(inner, fg_color="transparent", width=80)
    copy.pack(side="left", fill="x", expand=True)
    copy.grid_columnconfigure(0, weight=1)
    title = text_label(copy, "", size=13, bold=True, anchor="w", width=1, wraplength=180, justify="left")
    title.grid(row=0, column=0, sticky="ew")
    rating = text_label(copy, "", width=28, height=22, size=11, bold=True,
                        corner_radius=4, anchor="center")
    subtitle = muted_label(copy, "", size=10, anchor="w", width=1)
    subtitle.grid(row=1, column=0, sticky="ew", pady=(4, 0))
    face = muted_label(copy, "", size=10)
    reason = muted_label(copy, "", size=10, wraplength=220, justify="left")

    def resize(event: Any) -> None:
        width = max(60, event.width - 36)
        if item.get("wrap") != width:
            item["wrap"] = width
            title.configure(wraplength=width)
            reason.configure(wraplength=max(90, event.width))

    copy.bind("<Configure>", resize, add="+")
    for widget in (frame, inner, mono, copy, title, subtitle, rating, face, reason):
        widget.bind("<Button-1>", lambda event: owner._select(item["row"], event), add="+")
        widget.bind("<Double-Button-1>", lambda _event: (
            owner.on_row_activate(item["row"]) if not (item["row"].get("_raw") or {}).get("_history") else None
        ), add="+")
    item.update(frame=frame, cells={"name": title}, check_var=check_var, check=check,
                mono=mono, add=add, fav=fav, club=club, club_text="", actions=actions,
                rating=rating, subtitle=subtitle, face=face, reason=reason)
    owner.scroll.bind_wheel_children(frame)
    return item


def bind(owner: Any, item: dict[str, Any], row: Any) -> None:
    card = row.get("_raw") or row
    history = bool(card.get("_history"))
    # Compare only painted fields. Card attributes can contain hundreds of values.
    signature = tuple(row.get(k) for k in ("_key", "name", "year", "ovr", "pos", "face", "reason", "club")) + (history, card.get("_signed_at"))
    item["row"] = row
    if signature == item["presentation"]:
        return
    item["presentation"], item["state"], item["wide"] = signature, None, None
    name = str(row.get("name") or "Player")
    item["cells"]["name"].configure(text=name)
    item["mono"].configure(text=monogram(name)[:2])
    item["club_text"] = str(card.get("club") or card.get("club_name") or "")
    item["club"].configure(text=item["club_text"][:22])
    if history:
        item["check"].pack_forget()
        item["fav"].pack_forget()
        item["add"].pack_forget()
        item["rating"].grid_remove()
        item["face"].grid_remove()
        metadata = "Signed · " + str(card.get("_signed_at") or "")[:10]
    else:
        item["check"].pack(side="left", padx=(0, 6), before=item["mono"])
        item["fav"].pack(side="left", padx=(0, 3))
        item["add"].pack(side="left")
        try:
            saved = owner.svc.catalog.is_favorite(card) if owner.svc.catalog else False
        except Exception:
            saved = False
        item["fav"].configure(text="★" if saved else "☆", text_color=theme.ACCENT if saved else theme.MUTED)
        try:
            rating = int(row.get("ovr"))
        except (ValueError, TypeError):
            rating = None
        if rating is None:
            item["rating"].grid_remove()
        else:
            fill, color = theme.ovr_colors(rating)
            item["rating"].configure(text=str(rating), fg_color=fill, text_color=color)
            item["rating"].grid(row=0, column=1, padx=(6, 0))
        year, pos = str(row.get("year") or ""), str(row.get("pos") or "")
        metadata = " · ".join(p for p in (f"FC {year}" if year not in {"", "-"} else "", pos.replace(", ", " / ")) if p and p != "-")
        item["face"].configure(text="✓" if row.get("face") == "Verified" else "No face",
                               text_color=theme.SUCCESS if row.get("face") == "Verified" else theme.WARNING)
        item["face"].grid(row=1, column=1, sticky="e")
    item["subtitle"].configure(text=metadata)
    reason = str(row.get("reason") or "").strip()
    if reason:
        item["reason"].configure(text=reason)
        item["reason"].grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
    else:
        item["reason"].grid_remove()
