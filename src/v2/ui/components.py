"""v2 component set.

Every v2 screen is built from these. Adding a component means adding it here
first — v1 drifted because each tab hand-rolled its own widgets, ending with
nine equally-loud primary buttons on one screen and no shared vocabulary.

Two components carry rules rather than just style:

* :func:`button` refuses more than one ``primary`` per parent screen. The rule
  is enforced by :class:`PrimaryBudget` rather than left to reviewer discipline.
* :class:`DataGrid` is the squad/player grid v1 never had — no sorting, no
  filtering and no multi-select was the third-ranked UX gap in the audit.

Images are deliberately absent from the render path. Every avatar/crest draws a
monogram immediately; a real image may be swapped in later by the caller. The
UI never waits on I/O, so an offline user gets a fully functional app.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import theme

try:  # pragma: no cover - exercised only with a display present
    import customtkinter as ctk
    import tkinter as tk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]
    tk = None  # type: ignore[assignment]


def _require_ctk() -> None:
    if ctk is None:  # pragma: no cover
        raise RuntimeError("customtkinter is required for v2 UI components")


# ── the one-primary rule ─────────────────────────────────────────────


class PrimaryBudget:
    """Enforces "at most one primary action per screen".

    v1's own design doc contained this rule and every tab broke it. Making it a
    runtime object means a screen that tries to spend the budget twice raises
    during development instead of shipping.
    """

    def __init__(self, screen: str, budget: int = 1) -> None:
        self.screen = screen
        self.budget = budget
        self.spent = 0

    def spend(self) -> None:
        self.spent += 1
        if self.spent > self.budget:
            raise ValueError(
                f"{self.screen}: more than {self.budget} primary action(s). "
                "Demote the others to accent/secondary/ghost."
            )

    def reset(self) -> None:
        self.spent = 0


# ── C1 Button ────────────────────────────────────────────────────────


def button(
    parent: Any,
    text: str,
    command: Optional[Callable[[], None]] = None,
    *,
    tone: str = "secondary",
    width: int = 0,
    height: int = 30,
    budget: Optional[PrimaryBudget] = None,
    disabled_reason: str = "",
    **kwargs: Any,
) -> Any:
    """A themed button.

    ``disabled_reason`` disables the button *and* explains why in its tooltip —
    v1 had gated controls that simply did nothing when clicked, with the reason
    buried in a collapsed panel.
    """
    _require_ctk()
    spec = theme.BUTTON_TONES.get(tone, theme.BUTTON_TONES["secondary"])
    if tone == "primary" and budget is not None:
        budget.spend()

    opts: Dict[str, Any] = {
        "text": text,
        "command": command,
        "height": height,
        "corner_radius": theme.R_SM,
        "fg_color": spec["fg"],
        "hover_color": spec["hover"],
        "text_color": spec["text"],
        "border_width": 0 if spec["border"] == "transparent" else 1,
        "border_color": spec["border"],
        "font": (theme.FONT_UI, theme.FS_MD, "bold" if tone == "primary" else "normal"),
    }
    if width:
        opts["width"] = width
    opts.update(kwargs)
    btn = ctk.CTkButton(parent, **opts)
    if disabled_reason:
        btn.configure(state="disabled")
        attach_tooltip(btn, disabled_reason)
    return btn


# ── tooltips ─────────────────────────────────────────────────────────


def attach_tooltip(widget: Any, text: str, delay_ms: int = 450) -> None:
    """Lightweight hover tooltip. Cancels cleanly so it cannot outlive its widget."""
    _require_ctk()
    state: Dict[str, Any] = {"after": None, "win": None}

    def show() -> None:
        if state["win"] is not None or not widget.winfo_exists():
            return
        try:
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(
                win,
                text=text,
                bg=theme.CARD,
                fg=theme.TEXT,
                font=(theme.FONT_UI, theme.FS_SM),
                padx=8,
                pady=4,
                borderwidth=1,
                relief="solid",
                wraplength=320,
                justify="left",
            ).pack()
            state["win"] = win
        except Exception:  # noqa: BLE001
            state["win"] = None

    def enter(_evt: Any = None) -> None:
        cancel()
        state["after"] = widget.after(delay_ms, show)

    def cancel(_evt: Any = None) -> None:
        if state["after"] is not None:
            try:
                widget.after_cancel(state["after"])
            except Exception:  # noqa: BLE001
                pass
            state["after"] = None
        if state["win"] is not None:
            try:
                state["win"].destroy()
            except Exception:  # noqa: BLE001
                pass
            state["win"] = None

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", cancel, add="+")
    widget.bind("<Destroy>", cancel, add="+")


# ── containers ───────────────────────────────────────────────────────


def panel(parent: Any, **kwargs: Any) -> Any:
    _require_ctk()
    opts: Dict[str, Any] = {
        "fg_color": theme.PANEL,
        "corner_radius": theme.R_MD,
        "border_width": 1,
        "border_color": theme.BORDER,
    }
    opts.update(kwargs)
    return ctk.CTkFrame(parent, **opts)


def label(
    parent: Any,
    text: str,
    *,
    size: int = theme.FS_MD,
    bold: bool = False,
    colour: str = theme.TEXT,
    mono: bool = False,
    **kwargs: Any,
) -> Any:
    _require_ctk()
    family = theme.FONT_MONO if mono else theme.FONT_UI
    opts: Dict[str, Any] = {
        "text": text,
        "text_color": colour,
        "font": (family, size, "bold" if bold else "normal"),
        "anchor": "w",
        "justify": "left",
    }
    opts.update(kwargs)
    return ctk.CTkLabel(parent, **opts)


def section_header(parent: Any, title: str, subtitle: str = "") -> Any:
    _require_ctk()
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    label(wrap, title, size=theme.FS_LG, bold=True).pack(anchor="w")
    if subtitle:
        label(wrap, subtitle, size=theme.FS_SM, colour=theme.MUTED).pack(anchor="w")
    return wrap


# ── C3 OvrChip ───────────────────────────────────────────────────────


def ovr_chip(parent: Any, value: Any, *, size: int = theme.FS_MD, width: int = 38) -> Any:
    _require_ctk()
    fill = theme.ovr_fill(value)
    fg = theme.ovr_text(value)
    text = "—" if value in (None, "") else str(value)
    return ctk.CTkLabel(
        parent,
        text=text,
        width=width,
        height=22,
        corner_radius=theme.R_XS,
        fg_color=fill,
        text_color=fg,
        font=(theme.FONT_UI, size, "bold"),
    )


def ovr_delta(parent: Any, before: Any, after: Any) -> Any:
    """`⟨86⟩ → ⟨91⟩` with each chip in its own band, so an upgrade reads as colour."""
    _require_ctk()
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    ovr_chip(wrap, before).pack(side="left")
    label(wrap, " → ", colour=theme.MUTED).pack(side="left")
    ovr_chip(wrap, after).pack(side="left")
    return wrap


# ── C4 PositionChip ──────────────────────────────────────────────────


def position_chip(parent: Any, pos: Any, *, width: int = 40) -> Any:
    _require_ctk()
    fill, fg = theme.position_colours(pos)
    return ctk.CTkLabel(
        parent,
        text=theme.position_name(pos),
        width=width,
        height=20,
        corner_radius=theme.R_XS,
        fg_color=fill,
        text_color=fg,
        font=(theme.FONT_UI, theme.FS_SM, "bold"),
    )


# ── C5 Avatar (monogram fallback, never blocks) ──────────────────────


def initials(name: str) -> str:
    parts = [p for p in str(name or "").replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar(parent: Any, name: str, pos: Any = None, *, size: int = theme.AVATAR_GRID) -> Any:
    """Monogram disc on a position-coloured background.

    Renders instantly with no I/O. A caller may later swap in a real headshot;
    the box is fixed so the layout never shifts when it arrives.
    """
    _require_ctk()
    fill, fg = theme.position_colours(pos) if pos is not None else (theme.CARD, theme.MUTED)
    return ctk.CTkLabel(
        parent,
        text=initials(name),
        width=size,
        height=size,
        corner_radius=size // 2,
        fg_color=fill,
        text_color=fg,
        font=(theme.FONT_UI, max(theme.FS_XS, size // 3), "bold"),
    )


# ── C2 ConnectionChip ────────────────────────────────────────────────


def connection_chip(parent: Any, state: str, *, on_click: Optional[Callable[[], None]] = None) -> Any:
    _require_ctk()
    colour = theme.connection_colour(state)
    chip = ctk.CTkLabel(
        parent,
        text=f"● {theme.connection_label(state)}",
        fg_color=theme.CARD,
        text_color=colour,
        corner_radius=theme.R_PILL,
        height=24,
        font=(theme.FONT_UI, theme.FS_SM, "bold"),
        padx=10,
    )
    attach_tooltip(chip, theme.connection_hint(state))
    if on_click is not None:
        chip.bind("<Button-1>", lambda _e: on_click())
    return chip


# ── C11 StatBar ──────────────────────────────────────────────────────


def stat_bar(parent: Any, value: float, maximum: float, *, tone: str = theme.ACCENT, width: int = 140) -> Any:
    _require_ctk()
    frac = 0.0 if maximum <= 0 else max(0.0, min(1.0, float(value) / float(maximum)))
    bar = ctk.CTkProgressBar(
        parent, width=width, height=6, corner_radius=3, progress_color=tone, fg_color=theme.SKELETON
    )
    bar.set(frac)
    return bar


# ── empty / loading states ───────────────────────────────────────────


def empty_state(
    parent: Any,
    title: str,
    body: str = "",
    *,
    action: Optional[Tuple[str, Callable[[], None]]] = None,
    budget: Optional[PrimaryBudget] = None,
) -> Any:
    """An empty state must always name the next action, never just say 'nothing'."""
    _require_ctk()
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    inner = ctk.CTkFrame(wrap, fg_color="transparent")
    inner.pack(expand=True)
    label(inner, title, size=theme.FS_LG, bold=True, anchor="center", justify="center").pack(pady=(0, theme.SP2))
    if body:
        label(
            inner, body, colour=theme.MUTED, anchor="center", justify="center", wraplength=420
        ).pack(pady=(0, theme.SP4))
    if action:
        text, cmd = action
        button(inner, text, cmd, tone="primary", budget=budget, width=200).pack()
    return wrap


def skeleton_rows(parent: Any, count: int = 6, height: int = theme.ROW_H) -> Any:
    """Static placeholder bars. Deliberately not animated — v1's animated
    scroll/refresh helpers froze CustomTkinter and had to be made no-ops."""
    _require_ctk()
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    for i in range(count):
        row = ctk.CTkFrame(
            wrap, height=height - 6, fg_color=theme.SKELETON, corner_radius=theme.R_XS
        )
        row.pack(fill="x", pady=3, padx=2)
        row.pack_propagate(False)
    return wrap


# ── C8 DataGrid ──────────────────────────────────────────────────────


class Column:
    """One grid column.

    ``render`` returns a display string; ``sort_key`` returns something
    orderable. Keeping them separate is what lets OVR sort numerically while
    displaying as a coloured chip.
    """

    def __init__(
        self,
        key: str,
        title: str,
        *,
        width: int = 90,
        align: str = "w",
        render: Optional[Callable[[Dict[str, Any]], str]] = None,
        sort_key: Optional[Callable[[Dict[str, Any]], Any]] = None,
        kind: str = "text",
    ) -> None:
        self.key = key
        self.title = title
        self.width = width
        self.align = align
        self.kind = kind
        self._render = render
        self._sort_key = sort_key

    def text(self, row: Dict[str, Any]) -> str:
        if self._render is not None:
            return self._render(row)
        value = row.get(self.key)
        return "—" if value in (None, "") else str(value)

    def sort_value(self, row: Dict[str, Any]) -> Any:
        if self._sort_key is not None:
            return self._sort_key(row)
        value = row.get(self.key)
        if value is None or value == "":
            return (1, 0, "")
        if isinstance(value, (int, float)):
            return (0, -float(value), "")
        return (0, 0, str(value).lower())


def sort_rows(
    rows: Sequence[Dict[str, Any]], column: Column, descending: bool = False
) -> List[Dict[str, Any]]:
    """Pure sort helper — testable without a display."""
    ordered = sorted(rows, key=column.sort_value)
    return list(reversed(ordered)) if descending else ordered


def filter_rows(rows: Sequence[Dict[str, Any]], needle: str, keys: Sequence[str]) -> List[Dict[str, Any]]:
    """Case-insensitive substring filter across the given keys."""
    q = str(needle or "").strip().lower()
    if not q:
        return list(rows)
    out: List[Dict[str, Any]] = []
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value is not None and q in str(value).lower():
                out.append(row)
                break
    return out
