"""Persistent Sign bag. Add cards here; Checkout reviews once, then Apply queues."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ....domain.add_player import (
    drop_from_sign_list,
    sign_list_items,
    sign_list_key,
)
from ... import theme
from ...widgets.primitives import (
    button,
    muted_label,
    panel,
    pill,
    text_label,
    themed_scroll,
    update_pill,
    set_disabled,
)
from .._common import monogram, set_status
from .search import _annotate_card_face

try:  # pragma: no cover - desktop only
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def bag_summary_copy(items: Sequence[Mapping[str, Any]]) -> str:
    """Footer preview for the bag: count plus the first names."""
    names = [
        str(item.get("name") or item.get("playername") or "Player")
        for item in items
        if isinstance(item, Mapping)
    ]
    n = len(names)
    if n == 0:
        return "Bag is empty"
    if n == 1:
        return f"Bag · 1 · {names[0]}"
    if n == 2:
        return f"Bag · 2 · {names[0]}, {names[1]}"
    return f"Bag · {n} · {names[0]}, {names[1]} + {n - 2} more"


def _card_ovr(card: Mapping[str, Any]) -> int | None:
    raw = card.get("overallrating") if card.get("overallrating") not in (None, "") else card.get("overall")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class SignListPanel:
    """Right-column Signing Bag. Catalog on the left keeps leftover height for players."""

    def __init__(
        self,
        parent: Any,
        svc: Any,
        view: dict[str, Any],
        *,
        on_change: Callable[[], None] | None = None,
        on_focus_search: Callable[[], None] | None = None,
        on_checkout: Callable[[], None] | None = None,
        on_inspect: Callable[[Mapping[str, Any]], None] | None = None,
        mount: str = "pack",
    ) -> None:
        self.svc = svc
        self.view = view
        self.on_change = on_change
        self.on_focus_search = on_focus_search
        self.on_inspect = on_inspect
        self.host = panel(parent, level=1, width=260)
        if mount == "grid":
            self.host.grid(row=0, column=0, sticky="nsew")
        else:
            self.host.pack(side="right", fill="y", padx=(theme.SP2, 0))
        self.host.pack_propagate(False)
        try:
            self.host.configure(border_width=1, border_color=theme.BORDER)
        except Exception:
            pass
        self.header = ctk.CTkFrame(self.host, fg_color="transparent")
        self.header.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, 0))
        title = ctk.CTkFrame(self.header, fg_color="transparent")
        title.pack(side="left", fill="x", expand=True)
        text_label(title, "Signing Bag", size=16, bold=True, color=theme.ACCENT).pack(anchor="w")

        self.count_pill = pill(self.header, "0", tone="muted")
        self.count_pill.pack(side="right", padx=(theme.SP2, 0))
        self.clear_button = button(
            self.header,
            "Clear",
            self.clear,
            kind="ghost",
            height=theme.BTN_MD,
            width=48,
        )
        self.checkout_host = ctk.CTkFrame(self.host, fg_color="transparent")
        self.checkout_host.pack(side="bottom", fill="x", padx=theme.SP3, pady=theme.SP3)
        self.checkout_button = button(self.checkout_host, "Checkout", on_checkout, kind="accent", height=40, width=1,
                                      disabled_reason="Add a verified card to your bag first.")
        self.checkout_button.pack(fill="x")
        muted_label(self.checkout_host, "Review safe targets before signing.", size=10).pack(pady=(4, 0))
        self.empty_row = ctk.CTkFrame(self.host, fg_color="transparent")
        muted_label(
            self.empty_row,
            "Your bag is empty — search a verified card, then double-click it.",
            size=11,
            wraplength=220,
            justify="left",
        ).pack(anchor="w", padx=theme.SP3)
        button(
            self.empty_row,
            "Focus search",
            self._focus_search,
            kind="ghost",
            height=theme.BTN_MD,
        ).pack(anchor="w", padx=theme.SP3, pady=(theme.SP1, 0))
        self.scroll = themed_scroll(self.host, fill=theme.CARD, height=160)
        self.body = self.scroll.inner
        self.host.bind("<Configure>", self._on_host_configure, add="+")
        self.paint()

    def _on_host_configure(self, event: Any) -> None:
        if event.widget not in (self.host, getattr(self.host, "_canvas", None)):
            return
        self._fit_scroll()

    def _fit_scroll(self) -> None:
        """Size the bag list to leftover host height; never expand the canvas."""
        try:
            if not self.scroll.winfo_manager():
                return
            leftover = max(
                80,
                int(self.host.winfo_height() or 0)
                - int(self.header.winfo_height() or 36)
                - int(self.checkout_host.winfo_height() or 64)
                - theme.SP3 * 3,
            )
            inner_w = max(80, int(self.host.winfo_width() or 0) - theme.SP3 * 2)
            if getattr(self, "_scroll_size", None) == (inner_w, leftover):
                return
            self._scroll_size = (inner_w, leftover)
            self.scroll.configure(width=inner_w, height=leftover)
            canvas = getattr(self.scroll, "canvas", None)
            if canvas is not None:
                canvas.configure(height=leftover)
            fit = getattr(self.scroll, "fit_inner", None) or getattr(
                self.scroll, "sync_scroll", None
            )
            if callable(fit):
                fit()
        except Exception:
            pass

    def _focus_search(self) -> None:
        if self.on_focus_search is not None:
            self.on_focus_search()

    def items(self) -> tuple[dict[str, Any], ...]:
        return sign_list_items(self.view.get("sign_list"))

    def set_items(self, items: tuple[Mapping[str, Any], ...]) -> None:
        self.view["sign_list"] = tuple(dict(item) for item in items)
        self.paint()
        if self.on_change is not None:
            self.on_change()

    def clear(self) -> None:
        if not self.items():
            return
        self.set_items(())
        set_status(self.svc, "Bag cleared. Nothing was queued.")

    def remove(self, key: str) -> None:
        current = self.items()
        remaining = drop_from_sign_list(current, key)
        removed = next(
            (
                str(item.get("name") or item.get("playername") or "Player")
                for item in current
                if sign_list_key(item) == str(key or "")
            ),
            "Player",
        )
        self.set_items(remaining)
        set_status(self.svc, f"Removed {removed} from the bag.")

    def paint(self) -> None:
        items = self.items()
        n = len(items)
        update_pill(self.count_pill, str(n), tone="info" if n else "muted")
        self.checkout_button.configure(text=f"Checkout ({n} player{'s' if n != 1 else ''})" if n else "Checkout")
        set_disabled(self.checkout_button, "" if n else "Add a verified card to your bag first.")
        for child in list(self.body.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        if n == 0:
            try:
                self.scroll.pack_forget()
                self.clear_button.pack_forget()
                if not self.empty_row.winfo_manager():
                    self.empty_row.pack(
                        fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP3)
                    )
            except Exception:
                pass
            return
        try:
            self.empty_row.pack_forget()
            if not self.clear_button.winfo_manager():
                self.clear_button.pack(side="right", before=self.count_pill)
            if not self.scroll.winfo_manager():
                self.scroll.pack(
                    fill="x",
                    padx=theme.SP3,
                    pady=(theme.SP2, theme.SP3),
                )
        except Exception:
            pass
        for card in items:
            self._paint_row(card)
        try:
            self.body.update_idletasks()
        except Exception:
            pass
        self._fit_scroll()
        try:
            self.host.after_idle(self._fit_scroll)
        except Exception:
            pass

    def _paint_row(self, card: Mapping[str, Any]) -> None:
        annotated = _annotate_card_face(card)
        row = panel(self.body, level=1)
        row.pack(fill="x", pady=(0, theme.SP1))
        try:
            row.configure(border_width=1, border_color=theme.BORDER)
        except Exception:
            pass
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SP2, pady=theme.SP2)

        name = str(annotated.get("name") or annotated.get("playername") or "Player")
        letters = monogram(name, fallback="PL")
        mono = ctk.CTkFrame(
            inner,
            width=36,
            height=36,
            fg_color=theme.CARD_HOVER,
            corner_radius=theme.R_SM,
        )
        mono.pack(side="left", padx=(0, theme.SP2))
        mono.pack_propagate(False)
        text_label(mono, letters[:2], size=11, bold=True, color=theme.ACCENT).pack(
            expand=True
        )

        copy = ctk.CTkFrame(inner, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True, padx=(0, theme.SP1))
        label = text_label(copy, name, size=12, bold=True, wraplength=150, justify="left")
        label.pack(anchor="w")
        if self.on_inspect:
            label.bind("<Button-1>", lambda _event: self.on_inspect(annotated), add="+")
        year = annotated.get("year") or annotated.get("game_year") or ""
        meta = " · ".join(
            part
            for part in (
                f"FC {year}" if year else "",
                str(annotated.get("positions_text") or "").strip(),
            )
            if part
        )
        if meta:
            muted_label(copy, meta, size=10, wraplength=150, justify="left").pack(anchor="w")

        key = sign_list_key(annotated)
        button(
            inner,
            "×",
            lambda k=key: self.remove(k),
            kind="ghost",
            height=theme.BTN_SM,
            width=28,
        ).pack(side="right", padx=(theme.SP1, 0))
