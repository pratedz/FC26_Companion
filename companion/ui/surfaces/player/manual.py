"""Manual quick editor and detailed field editor."""

from __future__ import annotations

from typing import Any

from ....app.presenters import editor_view
from ....domain.player import ATTRIBUTE_GROUPS, CATEGORIES, FIELD_SPECS
from ... import theme
from ...widgets.primitives import button, eyebrow, muted_label, panel, text_label, themed_scroll
from .._common import set_status

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def _build_quick_editor(
    root: Any, svc: Any, ed: dict[str, Any], view: dict[str, Any] | None = None
) -> None:
    """Put common ratings first, with one focused details area beneath them."""
    host = panel(root, level=1)
    host.pack(fill="both", expand=True)
    header = ctk.CTkFrame(host, fg_color="transparent")
    header.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    title = ctk.CTkFrame(header, fg_color="transparent")
    title.pack(side="left", fill="x", expand=True)
    text_label(title, "Manual edit", size=15, bold=True).pack(anchor="w")
    muted_label(title, "Change a value to stage it. Review before applying.", size=10).pack(anchor="w")
    button(
        header,
        "All player fields",
        lambda: _open_detailed_editor(svc),
        kind="ghost",
        height=theme.BTN_MD,
    ).pack(side="right")

    ratings = panel(host, level=2)
    ratings.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    eyebrow(ratings, "Core ratings").pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
    quick = ctk.CTkFrame(ratings, fg_color="transparent")
    quick.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP2))
    left_quick = ctk.CTkFrame(quick, fg_color="transparent")
    left_quick.pack(side="left", fill="x", expand=True, padx=(0, theme.SP2))
    right_quick = ctk.CTkFrame(quick, fg_color="transparent")
    right_quick.pack(side="left", fill="x", expand=True)
    for index, field in enumerate(("overallrating", "potential", "internationalrep", "modifier")):
        spec = FIELD_SPECS[field]
        _stepper_field_row(
            left_quick if index < 2 else right_quick,
            svc,
            spec.name,
            spec.label,
            ed["merged"].get(spec.name),
            spec.name in ed["dirty"],
            spec.minimum,
            spec.maximum,
            compact=True,
        )

    section_bar = ctk.CTkFrame(host, fg_color="transparent")
    section_bar.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    eyebrow(section_bar, "Fine tune").pack(side="left", padx=(0, theme.SP3))
    section_buttons: dict[str, Any] = {}
    detail = themed_scroll(host, fill=theme.CARD, height=220)
    detail.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))

    def render_section(section: str) -> None:
        chosen = "body" if section == "body" else "attributes"
        if view is not None:
            view["manual_section"] = chosen
        for child in list(detail.inner.winfo_children()):
            child.destroy()
        columns = ctk.CTkFrame(detail.inner, fg_color="transparent")
        columns.pack(fill="x", padx=theme.SP1, pady=theme.SP1)
        left_col = ctk.CTkFrame(columns, fg_color="transparent")
        left_col.pack(side="left", fill="x", expand=True, padx=(0, theme.SP1))
        right_col = ctk.CTkFrame(columns, fg_color="transparent")
        right_col.pack(side="left", fill="x", expand=True, padx=(theme.SP1, 0))
        if chosen == "attributes":
            muted_label(
                detail.inner,
                "Edit individual attributes below. Overall rating remains independent.",
                size=10,
            ).pack(anchor="w", padx=theme.SP2, pady=(0, theme.SP1), before=columns)
            outfield = [item for item in ATTRIBUTE_GROUPS if item[0] != "Goalkeeping"]
            for index, (group_name, items) in enumerate(outfield):
                parent = left_col if index % 2 == 0 else right_col
                card = panel(parent, level=1)
                card.pack(fill="x", pady=(0, theme.SP2))
                text_label(card, group_name, size=12, bold=True).pack(
                    anchor="w", padx=theme.SP2, pady=(theme.SP2, theme.SP1)
                )
                for field_name, label in items:
                    spec = FIELD_SPECS[field_name]
                    _stepper_field_row(
                        card, svc, spec.name, label, ed["merged"].get(spec.name),
                        spec.name in ed["dirty"], spec.minimum, spec.maximum, compact=True,
                    )
        else:
            unread = []
            fields = (
                "height", "weight", "skillmoves", "weakfootabilitytypecode",
                "preferredfoot", "bodytypecode", "runstylecode",
            )
            for index, field in enumerate(fields):
                spec = FIELD_SPECS[field]
                value = ed["merged"].get(spec.name)
                if value is None and spec.name not in ed["dirty"]:
                    unread.append(spec.label)
                    continue
                _stepper_field_row(
                    left_col if index % 2 == 0 else right_col, svc, spec.name,
                    spec.label, value, spec.name in ed["dirty"],
                    spec.minimum, spec.maximum, compact=True,
                )
            if unread:
                muted_label(
                    detail.inner,
                    "Reload live to read: " + ", ".join(unread) + ". Unknown values stay untouched.",
                    size=11,
                    wraplength=640,
                ).pack(anchor="w", padx=theme.SP2, pady=theme.SP2)
        for name, control in section_buttons.items():
            on = name == chosen
            control.configure(
                fg_color=theme.CARD_HOVER if on else "transparent",
                border_color=theme.ACCENT if on else theme.BORDER,
                text_color=theme.TEXT if on else theme.MUTED,
            )
        detail.sync_scroll()
        detail.canvas.yview_moveto(0)

    for key, label in (("attributes", "Attributes"), ("body", "Body & skills")):
        control = button(
            section_bar, label, lambda selected=key: render_section(selected),
            kind="ghost", height=theme.BTN_SM,
        )
        control.pack(side="left", padx=(0, theme.SP1))
        section_buttons[key] = control
    render_section((view or {}).get("manual_section", "attributes"))


def _open_detailed_editor(svc: Any) -> None:
    """Open one category at a time instead of a permanently huge page scroll."""
    ensure_ctk = getattr(ctk, "CTkToplevel", None)
    if ensure_ctk is None:
        set_status(svc, "Detailed editor is unavailable in this UI session.")
        return
    win = ctk.CTkToplevel()
    win.title("Player details")
    win.geometry("760x620")
    win.configure(fg_color=theme.BG)
    try:
        win.transient(win.master)
    except Exception:
        pass
    title = text_label(win, "Player details", size=18, bold=True)
    title.pack(anchor="w", padx=theme.SP4, pady=(theme.SP4, 2))
    muted_label(
        win, "Choose one area. Only this area scrolls, so your card workflow stays visible behind it.", size=11
    ).pack(anchor="w", padx=theme.SP4)
    controls = ctk.CTkFrame(win, fg_color="transparent")
    controls.pack(fill="x", padx=theme.SP4, pady=theme.SP2)
    selected_category = ctk.StringVar(value="Ratings")
    picker = ctk.CTkComboBox(
        controls,
        values=list(CATEGORIES),
        variable=selected_category,
        width=250,
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        button_color=theme.CARD_HOVER,
        text_color=theme.TEXT,
    )
    picker.pack(side="left")
    scroll = themed_scroll(win, fill=theme.BG, height=480)
    scroll.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP4))
    body = scroll.inner
    pool: list[dict[str, Any]] = []

    def render(category: str | None = None) -> None:
        wanted = category or selected_category.get()
        latest = editor_view(svc.store.snapshot())
        specs = [item for item in FIELD_SPECS.values() if item.category == wanted]
        while len(pool) < len(specs):
            pool.append(_pooled_field_row(body, svc))
        for index, spec in enumerate(specs):
            _bind_pooled_field_row(
                pool[index],
                svc,
                spec.name,
                spec.label,
                latest["merged"].get(spec.name),
                spec.name in latest["dirty"],
                spec.minimum,
                spec.maximum,
            )
            try:
                if not pool[index]["row"].winfo_manager():
                    pool[index]["row"].pack(fill="x", pady=(0, theme.SP1))
            except Exception:
                pool[index]["row"].pack(fill="x", pady=(0, theme.SP1))
        for index in range(len(specs), len(pool)):
            try:
                pool[index]["row"].pack_forget()
            except Exception:
                pass

    picker.configure(command=render)
    render("Ratings")


def _pooled_field_row(parent: Any, svc: Any) -> dict[str, Any]:
    """Create one reusable detailed-editor field row (label + entry)."""
    del svc
    row = panel(parent, level=1)
    inner = ctk.CTkFrame(row, fg_color="transparent")
    inner.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    label_widget = text_label(inner, "", size=12, width=230)
    label_widget.pack(side="left")
    entry = ctk.CTkEntry(
        inner,
        width=92,
        height=theme.BTN_SM,
        placeholder_text="—",
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
        font=ctk.CTkFont(family=theme.MONO, size=12),
    )
    entry.pack(side="right")
    error = muted_label(row, "", size=10, color=theme.DANGER)
    error.pack(anchor="e", padx=theme.SP3, pady=(0, theme.SP1))
    return {
        "row": row,
        "label": label_widget,
        "entry": entry,
        "error": error,
        "field": "",
        "caption": "",
        "lo": None,
        "hi": None,
    }


def _bind_pooled_field_row(
    slot: dict[str, Any],
    svc: Any,
    field: str,
    label: str,
    value: Any,
    is_dirty: bool,
    lo: int | None,
    hi: int | None,
) -> None:
    slot["field"] = field
    slot["caption"] = label
    slot["lo"] = lo
    slot["hi"] = hi
    try:
        slot["label"].configure(
            text=f"{label} ·" if is_dirty else label,
            text_color=theme.ACCENT if is_dirty else theme.TEXT,
        )
    except Exception:
        pass
    entry = slot["entry"]
    error = slot.get("error")

    def show_error(message: str) -> None:
        if error is not None:
            try:
                error.configure(text=message, text_color=theme.DANGER)
            except Exception:
                pass
        if message:
            set_status(svc, f"{slot.get('caption') or field}: {message}", tone="warn")

    def clear_error() -> None:
        if error is not None:
            try:
                error.configure(text="")
            except Exception:
                pass

    def on_change(raw_value: str) -> None:
        from ....app import events as E

        raw = (raw_value or "").strip()
        if not raw:
            clear_error()
            return
        name = str(slot.get("field") or "")
        try:
            number = int(raw)
        except ValueError:
            show_error("enter a whole number")
            return
        minimum = slot.get("lo")
        maximum = slot.get("hi")
        if minimum is not None and number < minimum:
            show_error(f"minimum is {minimum}")
            return
        if maximum is not None and number > maximum:
            show_error(f"maximum is {maximum}")
            return
        clear_error()
        svc.store.dispatch(E.FieldEdited(field_name=name, value=number))

    try:
        entry.configure(border_color=theme.ACCENT if is_dirty else theme.BORDER)
        entry.delete(0, "end")
        if value is not None:
            entry.insert(0, str(value))
        clear_error()
    except Exception:
        pass
    entry.bind("<FocusOut>", lambda _e, widget=entry: on_change(widget.get()))
    entry.bind("<Return>", lambda _e, widget=entry: on_change(widget.get()))


def _field_row(
    parent: Any,
    svc: Any,
    field: str,
    label: str,
    value: Any,
    is_dirty: bool,
    lo: int | None,
    hi: int | None,
) -> None:
    """One-shot field row (tests / call sites that do not pool)."""
    slot = _pooled_field_row(parent, svc)
    _bind_pooled_field_row(slot, svc, field, label, value, is_dirty, lo, hi)
    slot["row"].pack(fill="x", pady=(0, theme.SP1))


def _stepper_field_row(
    parent: Any,
    svc: Any,
    field: str,
    label: str,
    value: Any,
    is_dirty: bool,
    lo: int | None,
    hi: int | None,
    *,
    compact: bool = False,
) -> None:
    """Label + optional −/+ + entry; same FieldEdited validation as detailed rows."""
    row = panel(parent, level=1) if not compact else ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(0, theme.SP1 if not compact else 2))
    pad = theme.SP2 if compact else theme.SP3
    inner = ctk.CTkFrame(row, fg_color="transparent")
    inner.pack(fill="x", padx=pad, pady=theme.SP1 if compact else theme.SP2)

    label_widget = text_label(
        inner,
        f"{label} ·" if is_dirty else label,
        size=11 if compact else 12,
        color=theme.ACCENT if is_dirty else theme.TEXT,
    )
    label_widget.pack(side="left")

    controls = ctk.CTkFrame(inner, fg_color="transparent")
    controls.pack(side="right")

    entry = ctk.CTkEntry(
        controls,
        width=64 if compact else 92,
        height=theme.BTN_SM,
        placeholder_text="—",
        fg_color=theme.CARD,
        border_color=theme.ACCENT if is_dirty else theme.BORDER,
        text_color=theme.TEXT,
        font=ctk.CTkFont(family=theme.MONO, size=12),
    )
    if value is not None:
        entry.insert(0, str(value))

    error = muted_label(row, "", size=9, color=theme.DANGER)

    def show_error(message: str) -> None:
        try:
            error.configure(text=message, text_color=theme.DANGER)
        except Exception:
            pass
        if message:
            set_status(svc, f"{label}: {message}", tone="warn")

    def clear_error() -> None:
        try:
            error.configure(text="")
        except Exception:
            pass

    def commit(raw_value: str) -> None:
        from ....app import events as E

        raw = (raw_value or "").strip()
        if not raw:
            clear_error()
            return
        try:
            number = int(raw)
        except ValueError:
            show_error("enter a whole number")
            return
        if lo is not None and number < lo:
            show_error(f"minimum is {lo}")
            return
        if hi is not None and number > hi:
            show_error(f"maximum is {hi}")
            return
        clear_error()
        svc.store.dispatch(E.FieldEdited(field_name=field, value=number))

    def nudge(delta: int) -> None:
        raw = (entry.get() or "").strip()
        try:
            current = int(raw) if raw else (int(value) if value is not None else (lo or 0))
        except (TypeError, ValueError):
            current = lo or 0
        nxt = current + delta
        if lo is not None:
            nxt = max(lo, nxt)
        if hi is not None:
            nxt = min(hi, nxt)
        entry.delete(0, "end")
        entry.insert(0, str(nxt))
        commit(str(nxt))

    minus = button(controls, "−", lambda: nudge(-1), kind="ghost", height=theme.BTN_SM, width=28)
    minus.pack(side="left", padx=(0, 2))
    entry.pack(side="left")
    plus = button(controls, "+", lambda: nudge(1), kind="ghost", height=theme.BTN_SM, width=28)
    plus.pack(side="left", padx=(2, 0))
    entry.bind("<FocusOut>", lambda _e: commit(entry.get()))
    entry.bind("<Return>", lambda _e: commit(entry.get()))
    if not compact:
        error.pack(anchor="e", padx=pad, pady=(0, 2))
