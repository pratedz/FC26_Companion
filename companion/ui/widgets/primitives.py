"""The base of the v2 widget kit: containers, chrome bits, chips, buttons.

WHY this module exists
----------------------
v1 had three parallel "small capsule" implementations (``src/ui/widgets.py``
``badge()``, ``src/ui/badge.py`` ``badge()``, and inline ``CTkFrame`` +
``CTkLabel`` pairs scattered through the tab modules), each with its own
hard-coded soft-wash hex (``#0c2a32``, ``#052e1c``, ``#422006`` …). Those hexes
were invented at the call site, so the tone vocabulary drifted: the same
"warning" was amber in one tab and coral in another.

Here there is exactly one implementation of each primitive and **zero hex
literals** — every colour is either a ``theme`` token or a runtime transform of
one (:func:`shade`). A presenter emits a *tone name* (``ok``/``warn``/
``error``/``info``/``muted``); :func:`pill` turns it into pixels via
``theme.tone_colors``. That indirection is the whole reason the app layer can
say "this is a warning" without knowing what amber is.

Two structural choices worth stating:

1. **Factories, not subclasses.** Nothing here subclasses ``CTkFrame`` at
   import time, so the module imports cleanly on a machine with no
   customtkinter and no display. That is what lets the pure helpers
   (:func:`shade`, :func:`elevation`, :func:`provenance_color`) be unit-tested
   headlessly, and it is why ``tests/v2`` can run in CI.
2. **The elevation ladder is a function, not a convention.** CTk has no
   shadows, so depth is expressed as a background step plus a 1 px ``BORDER``.
   :func:`elevation` names the four rungs so a nested panel cannot accidentally
   land on the same fill as its parent.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from .. import theme

try:  # pragma: no cover - exercised implicitly by the real-Tk smoke tests
    import customtkinter as ctk
    import tkinter as tk
except ImportError as exc:  # pragma: no cover
    ctk = None  # type: ignore[assignment]
    tk = None  # type: ignore[assignment]
    _CTK_IMPORT_ERROR = str(exc)
else:  # pragma: no cover
    _CTK_IMPORT_ERROR = ""

CTK_AVAILABLE = ctk is not None

#: The character the app uses for "we never read this". P3 forbids showing a
#: value we did not read, and this is what stands in its place everywhere.
EM_DASH = "—"
ARROW = "→"

#: The elevation ladder, darkest (app background) to lightest (hovered row).
ELEVATION: tuple[str, ...] = (theme.BG, theme.PANEL, theme.CARD, theme.CARD_HOVER)


class CtkUnavailable(RuntimeError):
    """Raised when a widget factory is called without customtkinter present."""


def ensure_ctk() -> None:
    """Fail loudly and usefully rather than with ``NoneType has no attribute``."""
    if ctk is None:
        detail = f" ({_CTK_IMPORT_ERROR})" if _CTK_IMPORT_ERROR else ""
        raise CtkUnavailable(
            "customtkinter is required for the desktop UI.\n"
            "Install: python -m pip install customtkinter pillow" + detail
        )


# ---------------------------------------------------------------------------
# Pure helpers — no Tk, unit-testable headlessly
# ---------------------------------------------------------------------------


def elevation(level: int) -> str:
    """Background token for a rung of the elevation ladder (0 = app bg).

    Clamped rather than raising: a deeply nested panel asking for rung 9 should
    render flat, not crash the surface it lives on.
    """
    if level < 0:
        level = 0
    if level >= len(ELEVATION):
        level = len(ELEVATION) - 1
    return ELEVATION[level]


def shade(color: str, factor: float) -> str:
    """Lighten (``factor > 1``) or darken (``factor < 1``) a token colour.

    This is how hover and soft-wash colours are produced without inventing new
    hex literals at the call site — the rule in ``theme.py`` is that no hex code
    appears outside it, and a runtime transform of a token honours that rule
    where a hand-picked ``#0c2a32`` does not.
    """
    s = str(color or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return color
    try:
        r, g, b = (int(s[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return color
    f = max(0.0, float(factor))
    r, g, b = (max(0, min(255, round(v * f))) for v in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


def hover_for(color: str) -> str:
    """The hover fill for a solid button of ``color``."""
    return shade(color, 1.14)


def soft_fill(tone: str) -> str:
    """A dark, tinted capsule fill for a tone — readable without shouting."""
    fill, _text = theme.tone_colors(tone)
    return shade(fill, 0.24)


def provenance_color(provenance: str) -> str:
    """P3: ``read`` / ``edited`` / ``unknown`` are three visually distinct states.

    Anything unrecognised falls to ``unknown`` on purpose — an unlabelled value
    is exactly the case P3 exists to catch, so it must not render as trustworthy.
    """
    return {
        "read": theme.PROV_READ,
        "edited": theme.PROV_EDITED,
        "unknown": theme.PROV_UNKNOWN,
    }.get(str(provenance or "").lower(), theme.PROV_UNKNOWN)


def display_value(value: Any, *, unknown: str = EM_DASH) -> str:
    """Render a value for display, honouring P3 for ``None``.

    ``None`` means "never read". It is *not* rendered as ``0``, ``""`` or
    ``None`` — every one of those reads as a real value the user might believe.
    """
    if value is None:
        return unknown
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(v) for v in sorted(map(str, value))) or unknown
    return str(value)


def truncate(text: str, limit: int) -> str:
    """Ellipsise to ``limit`` characters. Used for grid cells and chip labels."""
    s = str(text or "")
    if limit <= 1 or len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Fonts — created lazily because CTkFont needs a live Tk root
# ---------------------------------------------------------------------------


def ui_font(size: int = theme.T_BODY, *, bold: bool = False) -> Any:
    ensure_ctk()
    return ctk.CTkFont(family=theme.FONT, size=size, weight="bold" if bold else "normal")


_DISPLAY_FAMILY: list[str] = []


def display_family() -> str:
    """Bahnschrift when Windows has it (10 1709+), else Segoe UI Semibold."""
    if not _DISPLAY_FAMILY:
        family = theme.DISPLAY_FALLBACK
        try:
            import tkinter.font as tkfont

            if theme.DISPLAY in tkfont.families():
                family = theme.DISPLAY
        except Exception:
            pass
        _DISPLAY_FAMILY.append(family)
    return _DISPLAY_FAMILY[0]


def display_font(size: int = theme.T_TITLE) -> Any:
    """The broadcast voice: page titles and rating numerals (DIN, Bahnschrift)."""
    ensure_ctk()
    return ctk.CTkFont(family=display_family(), size=size, weight="normal")


def num_font(size: int = theme.T_SMALL, *, bold: bool = False) -> Any:
    """Table numbers. Segoe UI figures are tabular, so columns align."""
    ensure_ctk()
    return ctk.CTkFont(family=theme.FONT, size=size, weight="bold" if bold else "normal")


def mono_font(size: int = theme.T_SMALL, *, bold: bool = False) -> Any:
    """Raw code, Lua and worker messages only. Data uses :func:`num_font`."""
    ensure_ctk()
    return ctk.CTkFont(family=theme.MONO, size=size, weight="bold" if bold else "normal")


# ---------------------------------------------------------------------------
# Containers — the elevation ladder made concrete
# ---------------------------------------------------------------------------


def panel(parent: Any, *, level: int = 1, border: bool = True, **kw: Any) -> Any:
    """A panel: one bg step up from the app background, with a hairline border."""
    ensure_ctk()
    kw.setdefault("fg_color", elevation(level))
    kw.setdefault("corner_radius", theme.R_MD)
    kw.setdefault("border_width", 1 if border else 0)
    if border:
        kw.setdefault("border_color", theme.BORDER)
    return ctk.CTkFrame(parent, **kw)


WHEEL_SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")
_WHEEL_REGIONS: list[Any] = []
_WHEEL_DISPATCH_INSTALLED = False


def pointer_wheel_targets(widget: Any) -> list[Any]:
    """Tk widgets that actually sit under the cursor for one CTk control.

    A ``CTkLabel`` draws its rounded plate on ``_canvas`` and its text on a
    real ``tk.Label`` (``_label``) gridded on top. The pointer hits that text
    label, not the CTk frame the app bound.
    """
    found: list[Any] = []
    for attr in ("_canvas", "_label", "_textbox", "_entry", "_button_canvas"):
        inner = getattr(widget, attr, None)
        if inner is not None and all(inner is not item for item in found):
            found.append(inner)
    return found or [widget]


def widget_ids(*widgets: Any) -> set[int]:
    """Identity set for a widget plus the inner canvas/text it paints."""
    ids: set[int] = set()
    for widget in widgets:
        if widget is None:
            continue
        ids.add(id(widget))
        for attr in ("_canvas", "_label"):
            inner = getattr(widget, attr, None)
            if inner is not None:
                ids.add(id(inner))
    return ids


def wheel_zone(
    widget: Any, *, body_ids: set[int], bar_ids: set[int]
) -> tuple[str, int] | None:
    """``("body"|"bar", depth)`` for the nearest registered scroller.

    Depth is how many parents sit between the pointer and that scroller.
    The nearest match wins, so a list inside another list scrolls itself.
    A scrollbar match is closer than the list body and is left to the bar.
    """
    current = widget
    depth = 0
    while current is not None and depth < 48:
        ident = id(current)
        if ident in bar_ids:
            return ("bar", depth)
        if ident in body_ids:
            return ("body", depth)
        current = getattr(current, "master", None)
        depth += 1
    return None


class _WheelRegion:
    __slots__ = ("body_ids", "bar_ids", "handler", "alive")

    def __init__(
        self,
        body_ids: set[int],
        bar_ids: set[int],
        handler: Callable[..., Any],
    ) -> None:
        self.body_ids = body_ids
        self.bar_ids = bar_ids
        self.handler = handler
        self.alive = True


def bind_pointer_wheel(
    widget: Any, handler: Callable[..., Any], bound: set[int]
) -> None:
    """Bind wheel notches on the widgets the pointer can actually hit."""
    for target in pointer_wheel_targets(widget):
        ident = id(target)
        if ident in bound:
            continue
        bound.add(ident)
        for sequence in WHEEL_SEQUENCES:
            try:
                target.bind(sequence, handler, add="+")
            except Exception:
                pass


def bind_pointer_wheel_tree(
    widget: Any, handler: Callable[..., Any], bound: set[int]
) -> None:
    """Bind ``widget`` and every descendant once. Safe to call again on resize."""
    stack = [widget]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        bind_pointer_wheel(current, handler, bound)
        try:
            stack.extend(current.winfo_children())
        except Exception:
            pass


def register_wheel_region(
    body: Sequence[Any],
    bars: Sequence[Any],
    handler: Callable[..., Any],
    anchor: Any,
) -> _WheelRegion:
    """Scroll ``handler`` when the pointer is over ``body``, even if focus is not.

    Windows often delivers ``<MouseWheel>`` to the focused widget. Direct binds
    cover the row under the cursor; this dispatch covers a wheel notch that
    arrived somewhere else while the cursor is still inside the list.
    """
    region = _WheelRegion(widget_ids(*body), widget_ids(*bars), handler)
    _WHEEL_REGIONS.append(region)
    _install_wheel_dispatch(anchor)
    return region


def release_wheel_region(region: _WheelRegion | None) -> None:
    if region is None:
        return
    region.alive = False
    try:
        _WHEEL_REGIONS.remove(region)
    except ValueError:
        pass


def _install_wheel_dispatch(anchor: Any) -> None:
    global _WHEEL_DISPATCH_INSTALLED
    if _WHEEL_DISPATCH_INSTALLED or anchor is None:
        return
    target = getattr(anchor, "_canvas", None) or anchor
    try:
        for sequence in WHEEL_SEQUENCES:
            target.bind_all(sequence, _dispatch_wheel, add="+")
    except Exception:
        return
    _WHEEL_DISPATCH_INSTALLED = True


def _widget_under_pointer(event: Any) -> Any:
    owner = getattr(event, "widget", None)
    try:
        found = owner.winfo_containing(int(event.x_root), int(event.y_root))
    except Exception:
        found = None
    return found if found is not None else owner


def _dispatch_wheel(event: Any) -> str | None:
    widget = _widget_under_pointer(event)
    if widget is None:
        return None
    best_zone: str | None = None
    best_handler: Callable[..., Any] | None = None
    best_depth = 10**9
    for region in _WHEEL_REGIONS:
        if not region.alive:
            continue
        hit = wheel_zone(widget, body_ids=region.body_ids, bar_ids=region.bar_ids)
        if hit is None:
            continue
        zone, depth = hit
        if depth < best_depth:
            best_depth = depth
            best_zone = zone
            best_handler = region.handler
    if best_zone == "body" and best_handler is not None:
        return best_handler(event)
    return None


def themed_scroll(parent: Any, *, fill: str | None = None, height: int = 160,
                  stable_children: bool = False) -> Any:
    """Vertical scroller whose canvas uses a theme token, never Tk's default black.

    The stock CTk scroller leaves an unpainted canvas that shows as a solid
    black rectangle on this HUD. Pack children into the returned widget's
    ``.inner`` frame. Callers that pack this widget should not use
    ``expand=True`` on a page that has leftover space — that grows empty
    canvas pixels into a black slab.
    """
    ensure_ctk()
    color = fill or theme.CARD
    wrap = ctk.CTkFrame(
        parent, fg_color=color, corner_radius=0, height=int(height),
    )
    canvas = ctk.CTkCanvas(
        wrap,
        bg=color,
        highlightthickness=0,
        highlightbackground=color,
        highlightcolor=color,
        bd=0,
        height=int(height),
    )
    bar = ctk.CTkScrollbar(
        wrap,
        command=canvas.yview,
        width=12,
        fg_color=color,
        button_color=theme.BORDER,
        button_hover_color=theme.CARD_HOVER,
    )
    inner = ctk.CTkFrame(canvas, fg_color=color, corner_radius=0)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(
        yscrollcommand=bar.set,
        bg=color,
        highlightbackground=color,
        highlightcolor=color,
    )

    def _paint() -> None:
        canvas.configure(
            bg=color, highlightbackground=color, highlightcolor=color,
        )

    _inner_width = [0]
    _last_region = [None]

    def _fit(_event: Any = None) -> None:
        # Configure.event.width is often 1px before layout; use the real
        # canvas (or wrap minus the bar) so rows are not collapsed slivers.
        try:
            canvas_w = int(canvas.winfo_width() or 0)
            wrap_w = int(wrap.winfo_width() or 0)
        except Exception:
            canvas_w = wrap_w = 0
        width = canvas_w if canvas_w > 4 else max(0, wrap_w - 16)
        if width > 4 and width != _inner_width[0]:
            _inner_width[0] = width
            canvas.itemconfigure(window, width=width)
        region = canvas.bbox("all") or (0, 0, 0, 0)
        if region != _last_region[0]:
            canvas.configure(scrollregion=region)
            _last_region[0] = region
        if not stable_children:
            _attach_wheel(inner)

    def _region(_event: Any = None) -> None:
        _fit(_event)

    def _content_overflows() -> bool:
        bbox = canvas.bbox("all")
        if not bbox:
            return False
        try:
            view_h = int(canvas.winfo_height() or 0)
        except Exception:
            view_h = 0
        return (bbox[3] - bbox[1]) > max(1, view_h)

    pending_wheel = [0, None]

    def _flush_wheel() -> None:
        steps, pending_wheel[0], pending_wheel[1] = pending_wheel[0], 0, None
        if canvas.winfo_exists():
            canvas.yview_scroll(max(-24, min(24, steps)), "units")

    def _wheel(event: Any) -> str | None:
        # Tk does not bubble wheel events. The name and rating text sit on
        # top of the canvas, so the handler is bound on those widgets too.
        if not _content_overflows() and canvas.yview() == (0.0, 1.0):
            return None
        try:
            num = int(getattr(event, "num", 0) or 0)
        except (TypeError, ValueError):
            num = 0
        try:
            delta = int(getattr(event, "delta", 0) or 0)
        except (TypeError, ValueError):
            delta = 0
        if num == 4 or delta > 0:
            steps = -1 if num == 4 or delta < 120 else int(-delta / 120)
            if steps == 0:
                steps = -1
        elif num == 5 or delta < 0:
            steps = 1 if num == 5 or delta > -120 else int(-delta / 120)
            if steps == 0:
                steps = 1
        else:
            return None
        try:
            if stable_children:
                pending_wheel[0] += steps * 3
                if pending_wheel[1] is None:
                    pending_wheel[1] = canvas.after(16, _flush_wheel)
            else:
                canvas.yview_scroll(steps, "units")
        except Exception:
            return None
        return "break"

    wheel_bound: set[int] = set()

    def _attach_wheel(widget: Any) -> None:
        """Bind wheel notches on rows added after the scroller was created."""
        bind_pointer_wheel_tree(widget, _wheel, wheel_bound)

    inner.bind("<Configure>", _region)
    canvas.bind("<Configure>", _fit)
    wrap.bind("<Configure>", _fit)
    bind_pointer_wheel(wrap, _wheel, wheel_bound)
    bind_pointer_wheel(canvas, _wheel, wheel_bound)
    bind_pointer_wheel(inner, _wheel, wheel_bound)
    wheel_region = register_wheel_region((wrap, canvas, inner), (bar,), _wheel, canvas)
    canvas.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    wrap.pack_propagate(False)
    wrap.inner = inner  # type: ignore[attr-defined]
    wrap.canvas = canvas  # type: ignore[attr-defined]
    wrap.sync_scroll = _fit  # type: ignore[attr-defined]
    wrap.fit_inner = _fit  # type: ignore[attr-defined]
    wrap.bind_wheel_children = _attach_wheel  # type: ignore[attr-defined]
    if stable_children:
        canvas.configure(yscrollincrement=12)
    def _drop(_event: Any = None) -> None:
        if pending_wheel[1] is not None:
            canvas.after_cancel(pending_wheel[1])
            pending_wheel[1] = None
        release_wheel_region(wheel_region)

    try:
        canvas.bind("<Destroy>", _drop, add="+")
    except Exception:
        pass
    try:
        wrap.after_idle(_fit)
    except Exception:
        pass
    return wrap


def card(
    parent: Any,
    *,
    level: int = 2,
    hover: bool = False,
    on_click: Callable[[], None] | None = None,
    **kw: Any,
) -> Any:
    """A card / row surface. Optionally hoverable and clickable.

    Hover is wired on the frame *and* propagated by the caller's children where
    it matters; CTk does not bubble ``<Enter>`` through child widgets, which is
    the reason v1's hover states only worked on the padding.
    """
    ensure_ctk()
    base = elevation(level)
    kw.setdefault("fg_color", base)
    kw.setdefault("corner_radius", theme.R_SM)
    kw.setdefault("border_width", 1)
    kw.setdefault("border_color", theme.BORDER)
    frame = ctk.CTkFrame(parent, **kw)
    if hover or on_click is not None:
        hot = elevation(level + 1)

        def _enter(_e: Any = None) -> None:
            try:
                frame.configure(fg_color=hot)
            except Exception:
                pass

        def _leave(_e: Any = None) -> None:
            try:
                frame.configure(fg_color=base)
            except Exception:
                pass

        bind_recursive(frame, "<Enter>", _enter)
        bind_recursive(frame, "<Leave>", _leave)
        if on_click is not None:
            bind_recursive(frame, "<Button-1>", lambda _e=None: on_click())
    return frame


def bind_recursive(widget: Any, sequence: str, fn: Callable[..., Any]) -> None:
    """Bind ``sequence`` on ``widget`` and every current descendant.

    CTk composites each "widget" out of a canvas plus real Tk children, so a
    single ``bind`` on the frame misses every pixel the user actually aims at.
    v1 worked around this per call site; this does it once.
    """
    try:
        widget.bind(sequence, fn, add="+")
    except Exception:
        pass
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        bind_recursive(child, sequence, fn)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def text_label(
    parent: Any,
    text: str,
    *,
    size: int = theme.T_BODY,
    bold: bool = False,
    color: str | None = None,
    mono: bool = False,
    display: bool = False,
    anchor: str = "w",
    **kw: Any,
) -> Any:
    ensure_ctk()
    if display:
        font = display_font(size)
    elif mono:
        font = mono_font(size, bold=bold)
    else:
        font = ui_font(size, bold=bold)
    return ctk.CTkLabel(
        parent,
        text=str(text),
        font=font,
        text_color=color or theme.TEXT,
        anchor=anchor,
        **kw,
    )


def section_title(parent: Any, text: str, *, detail: str = "", **kw: Any) -> Any:
    """A panel's heading: body-size semibold in TEXT, optional muted detail after it.

    This replaced the accent "eyebrow" micro-label. A heading carries its own
    weight; a coloured label above it only competed with the content.
    """
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    text_label(row, text, size=theme.T_BODY, bold=True).pack(side="left")
    if detail:
        muted_label(row, detail, size=theme.T_SMALL).pack(side="left", padx=(theme.SP2, 0))
    row.title_label = row.winfo_children()[0]  # type: ignore[attr-defined]
    return row


def muted_label(
    parent: Any,
    text: str,
    *,
    size: int = theme.T_CAPTION,
    color: str | None = None,
    **kw: Any,
) -> Any:
    """Muted text by default, with an explicit colour override when needed."""
    return text_label(
        parent,
        text,
        size=size,
        color=color or theme.MUTED,
        **kw,
    )


def section_header(
    parent: Any,
    title: str,
    subtitle: str = "",
    *,
    icon: str = "",
    trailing: Callable[[Any], Any] | None = None,
) -> Any:
    """Page header: display-face title, optional muted context line, actions right.

    ``trailing`` is a builder so the caller can hang an action on the right of
    the header without this function knowing what an action is. The subtitle is
    for live context (club, counts, freshness), not for restating the page's
    purpose.
    """
    ensure_ctk()
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="x", pady=(0, theme.SP4))
    row = ctk.CTkFrame(frame, fg_color="transparent")
    row.pack(fill="x")

    if icon:
        from ..icons import ctk_icon

        image = ctk_icon(icon, 24, tint=theme.ACCENT)
        if image is not None:
            ctk.CTkLabel(row, text="", image=image, width=28).pack(
                side="left", padx=(0, theme.SP3), anchor="n", pady=(2, 0)
            )

    col = ctk.CTkFrame(row, fg_color="transparent")
    col.pack(side="left", fill="x", expand=True)
    title_label = text_label(col, title, size=theme.T_TITLE, display=True)
    title_label.pack(anchor="w")
    frame.title_label = title_label  # type: ignore[attr-defined]
    subtitle_label = None
    if subtitle:
        subtitle_label = muted_label(col, subtitle, size=theme.T_SMALL)
        subtitle_label.pack(anchor="w", pady=(2, 0))
    frame.subtitle_label = subtitle_label  # type: ignore[attr-defined]

    if trailing is not None:
        holder = ctk.CTkFrame(row, fg_color="transparent")
        holder.pack(side="right", anchor="n")
        try:
            built = trailing(holder)
            if built is not None:
                built.pack(side="right")
        except Exception:
            pass
    return frame


def divider(parent: Any, *, orient: str = "h", pad: int = 0) -> Any:
    """A 1 px rule in ``BORDER`` — quiet splits inside a panel."""
    ensure_ctk()
    if orient == "v":
        return ctk.CTkFrame(parent, width=1, fg_color=theme.BORDER, corner_radius=0)
    frame = ctk.CTkFrame(parent, height=1, fg_color=theme.BORDER, corner_radius=0)
    if pad:
        frame.pack_configure(pady=pad)
    return frame


def hairline(parent: Any) -> Any:
    """A 2 px ``ACCENT`` bar — HUD chrome, not a quiet divider."""
    ensure_ctk()
    return ctk.CTkFrame(parent, height=2, fg_color=theme.ACCENT, corner_radius=0)


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

#: §6.7 — exactly one control per screen may use ``primary`` (SUCCESS solid).
#: ``accent`` and ``danger`` are tonal (tinted fill, coloured text) so a
#: secondary emphasis never competes with the one solid primary.
BUTTON_KINDS = ("primary", "accent", "secondary", "ghost", "danger")


def button_style(kind: str) -> dict[str, Any]:
    """Colours and font for a button kind. Pure apart from the font object."""
    if kind == "primary":
        fill, fg = theme.tone_colors("ok")
        return dict(fg_color=fill, hover_color=hover_for(fill), text_color=fg,
                    border_width=0, font=ui_font(theme.T_BODY, bold=True))
    if kind == "accent":
        return dict(fg_color=soft_fill("info"), hover_color=shade(theme.ACCENT, 0.32),
                    text_color=theme.ACCENT, border_width=1,
                    border_color=shade(theme.ACCENT, 0.55),
                    font=ui_font(theme.T_BODY, bold=True))
    if kind == "danger":
        return dict(fg_color=shade(theme.DANGER, 0.2), hover_color=shade(theme.DANGER, 0.3),
                    text_color=theme.DANGER_TEXT, border_width=1,
                    border_color=shade(theme.DANGER, 0.6),
                    font=ui_font(theme.T_BODY, bold=True))
    if kind == "ghost":
        return dict(fg_color="transparent", hover_color=theme.CARD_HOVER,
                    border_width=1, border_color=theme.BORDER,
                    text_color=theme.TEXT, font=ui_font(theme.T_SMALL))
    return dict(fg_color=theme.CARD, hover_color=theme.CARD_HOVER,
                border_width=1, border_color=theme.BORDER,
                text_color=theme.TEXT, font=ui_font(theme.T_SMALL, bold=True))


def button(
    parent: Any,
    text: str,
    command: Callable[[], None] | None = None,
    *,
    kind: str = "secondary",
    width: int | None = None,
    height: int = theme.BTN_MD,
    disabled_reason: str = "",
    icon: str = "",
    **kw: Any,
) -> Any:
    """C1. ``disabled_reason`` is not decoration — A9 makes it mandatory.

    "Disabled with no explanation" is classified as a bug in §8.1, so passing a
    reason both disables the control and attaches the tooltip that explains it.
    ``icon`` names a pack icon drawn before the label in the button's text colour.
    """
    ensure_ctk()
    kw.setdefault("corner_radius", theme.R_SM)
    kw["text"] = str(text)
    kw["command"] = command or (lambda: None)
    kw["height"] = height
    if width is not None:
        kw["width"] = width
    style = button_style(kind)
    for key, value in style.items():
        kw.setdefault(key, value)
    if icon:
        from ..icons import ctk_icon

        image = ctk_icon(icon, 16, tint=str(kw.get("text_color") or theme.TEXT))
        if image is not None:
            kw.setdefault("image", image)
            kw.setdefault("compound", "left")

    btn = ctk.CTkButton(parent, **kw)
    btn._companion_kind = kind  # type: ignore[attr-defined]
    btn._companion_icon = icon  # type: ignore[attr-defined]
    if disabled_reason:
        set_disabled(btn, disabled_reason)
    return btn


def restyle_button(btn: Any, kind: str) -> None:
    """Switch an existing button's kind (e.g. a CTA that becomes primary)."""
    try:
        style = button_style(kind)
        icon = getattr(btn, "_companion_icon", "")
        if icon:
            from ..icons import ctk_icon

            style["image"] = ctk_icon(icon, 16, tint=str(style.get("text_color")))
        disabled = str(btn.cget("state")) == "disabled"
        btn._companion_kind = kind  # type: ignore[attr-defined]
        if disabled and hasattr(btn, "_companion_enabled_style"):
            btn._companion_enabled_style = {
                key: style.get(key, btn._companion_enabled_style.get(key))
                for key in btn._companion_enabled_style
            }
            return
        btn.configure(**style)
    except Exception:
        pass


def brand_window(window: Any) -> None:
    """App icon and a dark Windows title bar, so a dialog never flashes white."""
    try:
        from ..icons import icons_dir

        icon = icons_dir().parent / "app.ico"
        if icon.is_file():
            window.iconbitmap(str(icon))
    except Exception:
        pass
    try:
        import ctypes
        import sys

        if sys.platform.startswith("win"):
            window.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
            value = ctypes.c_int(1)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
    except Exception:
        pass


def install_window_branding(root: Any) -> None:
    """Brand the main window now and every Toplevel the moment it maps."""
    brand_window(root)
    seen: set[str] = set()

    def _on_map(event: Any) -> None:
        window = getattr(event, "widget", None)
        try:
            if window is None or window.winfo_toplevel() is not window:
                return
            name = str(window)
        except Exception:
            return
        if name in seen:
            return
        seen.add(name)
        brand_window(window)

    try:
        root.bind_class("Toplevel", "<Map>", _on_map, add="+")
    except Exception:
        pass


def install_focus_ring(root: Any) -> None:
    """Every entry shows a 2 px accent ring while it has keyboard focus (A2).

    CustomTkinter draws no focus indicator. Binding the Tk ``Entry`` class once
    covers every CTkEntry in the app, including ones built before this ran.
    A search box draws its border on a wrapper frame; the entry names that
    frame as ``_focus_target``.
    """
    def _owner(widget: Any) -> Any:
        master = getattr(widget, "master", None)
        if master is None or ctk is None or not isinstance(master, ctk.CTkEntry):
            return None
        return getattr(master, "_focus_target", None) or master

    def _focus_in(event: Any) -> None:
        owner = _owner(getattr(event, "widget", None))
        if owner is None:
            return
        try:
            if not hasattr(owner, "_ring_rest"):
                owner._ring_rest = (owner.cget("border_color"), owner.cget("border_width"))
            owner.configure(border_color=theme.FOCUS, border_width=2)
        except Exception:
            pass

    def _focus_out(event: Any) -> None:
        owner = _owner(getattr(event, "widget", None))
        rest = getattr(owner, "_ring_rest", None) if owner is not None else None
        if rest is None:
            return
        try:
            owner.configure(border_color=rest[0], border_width=rest[1])
        except Exception:
            pass

    try:
        root.bind_class("Entry", "<FocusIn>", _focus_in, add="+")
        root.bind_class("Entry", "<FocusOut>", _focus_out, add="+")
    except Exception:
        pass


def _sync_entry_with(entry: Any, variable: Any) -> None:
    """Two-way binding that leaves CTkEntry's placeholder working.

    CTkEntry disables its placeholder whenever ``textvariable`` is passed, which
    is why the squad filter and the palette rendered as blank boxes. The entry
    therefore owns its text and mirrors it into ``variable``; traces on the
    variable keep firing exactly as before.
    """
    guard = {"busy": False}

    def push(_event: Any = None) -> None:
        if guard["busy"]:
            return
        try:
            text = "" if getattr(entry, "_placeholder_text_active", False) else entry.get()
        except Exception:
            return
        if variable.get() != text:
            guard["busy"] = True
            try:
                variable.set(text)
            finally:
                guard["busy"] = False

    def pull(*_args: Any) -> None:
        if guard["busy"]:
            return
        value = str(variable.get() or "")
        current = "" if getattr(entry, "_placeholder_text_active", False) else entry.get()
        if value == current:
            return
        guard["busy"] = True
        try:
            entry.delete(0, "end")
            if value:
                entry.insert(0, value)
            else:
                try:
                    if entry.focus_get() is not getattr(entry, "_entry", None):
                        entry._activate_placeholder()
                except Exception:
                    pass
        finally:
            guard["busy"] = False

    for sequence in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<FocusOut>"):
        try:
            entry.bind(sequence, lambda e: entry.after_idle(push), add="+")
        except Exception:
            pass
    trace = variable.trace_add("write", pull)
    entry._companion_variable = variable  # type: ignore[attr-defined]

    def _drop(_event: Any = None) -> None:
        try:
            variable.trace_remove("write", trace)
        except Exception:
            pass

    try:
        entry.bind("<Destroy>", _drop, add="+")
    except Exception:
        pass
    pull()


def entry(
    parent: Any,
    *,
    variable: Any = None,
    placeholder: str = "",
    height: int = theme.BTN_MD,
    **kw: Any,
) -> Any:
    """A text field whose placeholder survives a bound variable. See :func:`_sync_entry_with`."""
    ensure_ctk()
    kw.setdefault("fg_color", theme.CARD)
    kw.setdefault("border_color", theme.BORDER_STRONG)
    kw.setdefault("border_width", 1)
    kw.setdefault("text_color", theme.TEXT)
    kw.setdefault("placeholder_text_color", theme.MUTED)
    kw.setdefault("font", ui_font(theme.T_BODY))
    kw.setdefault("corner_radius", theme.R_SM)
    field = ctk.CTkEntry(parent, height=height, placeholder_text=placeholder or None, **kw)
    if variable is not None:
        _sync_entry_with(field, variable)
    return field


def search_box(
    parent: Any,
    *,
    variable: Any = None,
    placeholder: str = "Search",
    on_submit: Callable[[], Any] | None = None,
    height: int = theme.BTN_MD,
    width: int | None = None,
) -> Any:
    """Search field: magnifier, borderless entry, and a clear control while it has text.

    Returns the wrapper frame; ``.entry`` is the CTkEntry for focus and binding.
    """
    ensure_ctk()
    frame = ctk.CTkFrame(
        parent, fg_color=theme.CARD, corner_radius=theme.R_SM,
        border_width=1, border_color=theme.BORDER_STRONG, height=height,
        **({"width": width} if width else {}),
    )
    frame.pack_propagate(False)
    from ..icons import ctk_icon

    image = ctk_icon("search", 16, tint=theme.MUTED)
    if image is not None:
        ctk.CTkLabel(frame, text="", image=image, width=20).pack(
            side="left", padx=(theme.SP2 + 2, 0)
        )
    field = entry(
        frame, variable=variable, placeholder=placeholder, height=height - 4,
        border_width=0, fg_color=theme.CARD,
    )
    field.pack(side="left", fill="both", expand=True, padx=(2, 2), pady=2)
    field._focus_target = frame  # type: ignore[attr-defined]
    clear = ctk.CTkButton(
        frame, text="", width=24, height=24, fg_color="transparent",
        hover_color=theme.CARD_HOVER, corner_radius=theme.R_XS,
        image=ctk_icon("close", 12, tint=theme.MUTED),
        command=lambda: (_clear(), field.focus_set()),
    )
    tooltip(clear, "Clear")

    def _clear() -> None:
        if variable is not None:
            variable.set("")
        else:
            field.delete(0, "end")

    def _show_clear(*_a: Any) -> None:
        text = variable.get() if variable is not None else field.get()
        try:
            if text and not clear.winfo_manager():
                clear.pack(side="right", padx=(0, theme.SP1))
            elif not text and clear.winfo_manager():
                clear.pack_forget()
        except Exception:
            pass

    if variable is not None:
        trace = variable.trace_add("write", _show_clear)
        frame.bind("<Destroy>", lambda _e: _untrace(variable, trace), add="+")
    else:
        field.bind("<KeyRelease>", _show_clear, add="+")
    if on_submit is not None:
        field.bind("<Return>", lambda _e: on_submit(), add="+")
    def _escape(_e: Any = None) -> str | None:
        text = variable.get() if variable is not None else field.get()
        if not text:
            return None  # let Esc close the dialog or drawer
        _clear()
        return "break"

    field.bind("<Escape>", _escape, add="+")
    _show_clear()
    frame.entry = field  # type: ignore[attr-defined]
    return frame


def _untrace(variable: Any, trace: str) -> None:
    try:
        variable.trace_remove("write", trace)
    except Exception:
        pass


def set_disabled(widget: Any, reason: str) -> None:
    """Disable a control and say why (A9). Empty ``reason`` re-enables it.

    CTk keeps a button's saturated ``fg_color`` when its state is disabled.
    That made a disabled primary action look clickable in the released app.
    Preserve the authored style once, then render every unavailable action as
    a calm muted control until it becomes available again.
    """
    try:
        icon = getattr(widget, "_companion_icon", "")
        if reason:
            if not hasattr(widget, "_companion_enabled_style"):
                keys = ["fg_color", "hover_color", "text_color", "border_color", "border_width"]
                if icon:
                    keys.append("image")
                widget._companion_enabled_style = {key: widget.cget(key) for key in keys}
            muted: dict[str, Any] = {}
            if icon:
                from ..icons import ctk_icon

                muted["image"] = ctk_icon(icon, 16, tint=theme.MUTED_DIM)
            widget.configure(
                state="disabled",
                fg_color=theme.CARD,
                hover_color=theme.CARD,
                text_color=theme.MUTED_DIM,
                border_color=theme.BORDER,
                border_width=1,
                **muted,
            )
        else:
            style = getattr(widget, "_companion_enabled_style", None)
            if isinstance(style, dict):
                widget.configure(**style)
            widget.configure(state="normal")
    except Exception:
        pass
    tooltip(widget, reason)


def icon_button(
    parent: Any,
    glyph: str = "",
    command: Callable[[], None] | None = None,
    *,
    icon: str = "",
    tooltip_text: str = "",
    size: int = theme.BTN_MD,
    width: int | None = None,
    tone: str = "muted",
    badge_count: int = 0,
    label: str = "",
) -> Any:
    """A chrome control drawn from the icon pack, tinted by tone.

    ``tooltip_text`` carries the accessible name. ``label`` adds short text after
    the icon (a shortcut hint such as ``Ctrl K``). ``badge_count`` renders the
    in-flight count on the control itself, so the chrome's count cannot drift
    from a separate widget. ``glyph`` is only a fallback when the PNG is missing.
    """
    ensure_ctk()
    colour = theme.ACCENT if badge_count else theme.tone_fg(tone)
    image = None
    if icon:
        try:
            from ..icons import ctk_icon

            image = ctk_icon(icon, 18, tint=colour)
        except Exception:
            image = None
    parts = [label] if label else []
    if badge_count:
        parts.append(str(badge_count))
    text = "  ".join(parts) if parts else ("" if image is not None else str(glyph or ""))
    if image is not None and text:
        text = " " + text
    natural = size if not text else size + 8 + 8 * len(text)
    btn = ctk.CTkButton(
        parent,
        text=text,
        image=image,
        compound="left",
        command=command or (lambda: None),
        width=width if width is not None else natural,
        height=size,
        corner_radius=theme.R_SM,
        fg_color=shade(theme.ACCENT, 0.16) if badge_count else "transparent",
        hover_color=theme.CARD_HOVER,
        border_width=1,
        border_color=shade(theme.ACCENT, 0.55) if badge_count else theme.BORDER,
        text_color=colour if badge_count else theme.MUTED,
        font=ui_font(theme.T_SMALL, bold=bool(badge_count)),
    )
    btn._icon_name = icon  # type: ignore[attr-defined]
    if tooltip_text:
        tooltip(btn, tooltip_text)
    return btn


# ---------------------------------------------------------------------------
# Chips
# ---------------------------------------------------------------------------


def pill(
    parent: Any,
    text: str,
    *,
    tone: str = "muted",
    solid: bool = False,
    glyph: str = "",
) -> Any:
    """A capsule driven by a **presenter tone name** (§6.6 C2-adjacent).

    A5: colour is never the only signal, so the caller is expected to put a word
    (``ARMED``, ``QUEUED``) or a glyph in the text — this widget will not invent
    meaning that only exists as a hue.
    """
    ensure_ctk()
    fill, fg = theme.tone_colors(tone)
    if solid:
        bg, text_color, border = fill, fg, fill
    else:
        bg, text_color, border = soft_fill(tone), theme.tone_fg(tone), fill
    frame = ctk.CTkFrame(
        parent,
        fg_color=bg,
        corner_radius=theme.R_PILL,
        border_width=1,
        border_color=border,
    )
    label = f"{glyph} {text}".strip() if glyph else str(text)
    label_widget = ctk.CTkLabel(
        frame,
        text=label,
        font=ui_font(theme.T_MICRO, bold=True),
        text_color=text_color,
    )
    label_widget.pack(padx=theme.SP2 + 2, pady=2)
    frame._pill_label = label_widget  # type: ignore[attr-defined]
    frame._pill_solid = bool(solid)  # type: ignore[attr-defined]
    return frame


def update_pill(
    frame: Any,
    text: str,
    *,
    tone: str = "muted",
    solid: bool | None = None,
    glyph: str = "",
) -> None:
    """Update an existing :func:`pill` in place (text, tone colours, glyph)."""
    if frame is None:
        return
    use_solid = bool(getattr(frame, "_pill_solid", False) if solid is None else solid)
    fill, fg = theme.tone_colors(tone)
    if use_solid:
        bg, text_color, border = fill, fg, fill
    else:
        bg, text_color, border = soft_fill(tone), theme.tone_fg(tone), fill
    label = f"{glyph} {text}".strip() if glyph else str(text)
    try:
        frame.configure(fg_color=bg, border_color=border)
        label_widget = getattr(frame, "_pill_label", None)
        if label_widget is not None:
            label_widget.configure(text=label, text_color=text_color)
        frame._pill_solid = use_solid  # type: ignore[attr-defined]
    except Exception:
        pass


def status_pill(parent: Any, text: str, *, tone: str = "muted") -> Any:
    """Connection state: a drawn dot plus a word, so colour is never the only signal (A5)."""
    ensure_ctk()
    frame = ctk.CTkFrame(
        parent, fg_color=soft_fill(tone), corner_radius=theme.R_PILL,
        border_width=1, border_color=shade(theme.tone_colors(tone)[0], 0.7), height=26,
    )
    dot = ctk.CTkFrame(frame, width=8, height=8, corner_radius=4,
                       fg_color=theme.tone_fg(tone))
    dot.pack(side="left", padx=(theme.SP3, 0))
    label = ctk.CTkLabel(frame, text=str(text), font=ui_font(theme.T_SMALL, bold=True),
                         text_color=theme.tone_fg(tone), height=22)
    label.pack(side="left", padx=(theme.SP2, theme.SP3))
    frame._status_dot = dot  # type: ignore[attr-defined]
    frame._pill_label = label  # type: ignore[attr-defined]
    return frame


def update_status_pill(frame: Any, text: str, *, tone: str = "muted") -> None:
    if frame is None:
        return
    try:
        frame.configure(fg_color=soft_fill(tone),
                        border_color=shade(theme.tone_colors(tone)[0], 0.7))
        frame._status_dot.configure(fg_color=theme.tone_fg(tone))
        frame._pill_label.configure(text=str(text), text_color=theme.tone_fg(tone))
    except Exception:
        pass


def badge(parent: Any, text: str, *, tone: str = "muted") -> Any:
    """A square-cornered chip — for counts and categories, not for status."""
    ensure_ctk()
    fill, _fg = theme.tone_colors(tone)
    frame = ctk.CTkFrame(
        parent,
        fg_color=soft_fill(tone),
        corner_radius=theme.R_XS,
        border_width=1,
        border_color=fill,
    )
    ctk.CTkLabel(
        frame,
        text=str(text),
        font=ui_font(theme.T_MICRO, bold=True),
        text_color=theme.tone_fg(tone),
    ).pack(padx=theme.SP2, pady=1)
    return frame


def stat_chip(
    parent: Any,
    label: str,
    value: Any,
    *,
    tone: str | None = None,
    provenance: str = "read",
) -> Any:
    """Compact labelled value (OVR / POT / SM / WF / avg age).

    ``value is None`` renders as an em dash in ``PROV_UNKNOWN`` — a stat chip is
    exactly the place v1 showed a stale number from the previous player.
    """
    ensure_ctk()
    frame = ctk.CTkFrame(
        parent,
        fg_color=theme.CARD,
        corner_radius=theme.R_XS,
        border_width=1,
        border_color=theme.BORDER,
    )
    ctk.CTkLabel(
        frame,
        text=str(label),
        font=ui_font(theme.T_MICRO, bold=True),
        text_color=theme.MUTED,
    ).pack(padx=theme.SP2, pady=(theme.SP1, 0))
    color = provenance_color("unknown") if value is None else (
        theme.tone_fg(tone) if tone else provenance_color(provenance)
    )
    ctk.CTkLabel(
        frame,
        text=display_value(value),
        font=display_font(theme.T_HEADING),
        text_color=color,
    ).pack(padx=theme.SP2, pady=(0, theme.SP1))
    return frame


def key_value_row(
    parent: Any,
    key: str,
    value: Any,
    *,
    provenance: str = "read",
    note: str = "",
    key_width: int = 150,
) -> Any:
    """One labelled field. The unit the diff sheet and detail panes are built from.

    The provenance colour is the load-bearing part: ``edited`` in ACCENT is the
    user's promise that this field *will* be written, and ``unknown`` in
    MUTED_DIM with an em dash is the promise that it will not.
    """
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    text_label(row, key, size=theme.T_SMALL, color=theme.MUTED, width=key_width).pack(
        side="left", anchor="w"
    )
    text_label(
        row,
        display_value(value),
        size=theme.T_SMALL,
        bold=provenance == "edited",
        color=provenance_color("unknown" if value is None else provenance),
    ).pack(side="left", anchor="w")
    if note:
        muted_label(row, f"  {note}", size=theme.T_CAPTION).pack(side="left", anchor="w")
    return row


# ---------------------------------------------------------------------------
# Tooltip — required by A9, and there is no CTk tooltip
# ---------------------------------------------------------------------------


def tooltip(widget: Any, text: str, *, delay_ms: int = 400) -> None:
    """Attach (or update) a hover tooltip.

    Deliberately plain-Tk: a ``CTkToplevel`` per tooltip is expensive and CTk
    re-themes it asynchronously, which produced a visible white flash in v1's
    only tooltip attempt.

    Re-calling with a new ``text`` updates the copy in place without stacking
    more ``<Enter>`` bindings — important for chrome that repaints every tick.
    """
    if tk is None:
        return
    widget._tooltip_text = str(text or "")  # type: ignore[attr-defined]
    if not text:
        return
    if getattr(widget, "_tooltip_bound", False):
        return
    widget._tooltip_bound = True  # type: ignore[attr-defined]

    state: dict[str, Any] = {"win": None, "after": None}

    def _hide(_e: Any = None) -> None:
        job = state.get("after")
        if job is not None:
            try:
                widget.after_cancel(job)
            except Exception:
                pass
            state["after"] = None
        win = state.get("win")
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            state["win"] = None

    def _show() -> None:
        state["after"] = None
        if state.get("win") is not None:
            return
        copy = str(getattr(widget, "_tooltip_text", "") or "")
        if not copy:
            return
        try:
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.configure(background=theme.BORDER)
            tk.Label(
                win,
                text=copy,
                background=theme.PANEL,
                foreground=theme.TEXT,
                font=(theme.FONT, -theme.T_SMALL),
                padx=theme.SP2,
                pady=theme.SP1,
                justify="left",
                wraplength=340,
            ).pack(padx=1, pady=1)
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            win.wm_geometry(f"+{x}+{y}")
            state["win"] = win
        except Exception:
            state["win"] = None

    def _enter(_e: Any = None) -> None:
        _hide()
        try:
            state["after"] = widget.after(delay_ms, _show)
        except Exception:
            pass

    for seq, fn in (("<Enter>", _enter), ("<Leave>", _hide), ("<Destroy>", _hide),
                    ("<Button-1>", _hide)):
        try:
            widget.bind(seq, fn, add="+")
        except Exception:
            pass


def debounce(widget: Any, delay_ms: int, fn: Callable[..., Any]) -> Callable[..., None]:
    """Cancel a prior ``after()`` on ``widget`` and reschedule ``fn``.

    Returns a callback that accepts ``*args``/``**kwargs`` (suitable for
    ``trace_add``, ``bind``, etc.) and forwards them to ``fn`` after ``delay_ms``.
    """
    state: dict[str, Any] = {"after": None}

    def wrapped(*args: Any, **kwargs: Any) -> None:
        job = state.get("after")
        if job is not None:
            try:
                widget.after_cancel(job)
            except Exception:
                pass
            state["after"] = None

        def run() -> None:
            state["after"] = None
            fn(*args, **kwargs)

        try:
            state["after"] = widget.after(int(delay_ms), run)
        except Exception:
            fn(*args, **kwargs)

    return wrapped


def hstack(parent: Any, gap: int = theme.SP2, **kw: Any) -> Any:
    """Transparent horizontal container. Saves four lines at ~60 call sites."""
    ensure_ctk()
    kw.setdefault("fg_color", "transparent")
    frame = ctk.CTkFrame(parent, **kw)
    frame._gap = gap  # type: ignore[attr-defined]
    return frame


def pack_row(container: Any, widgets: Sequence[Any], *, gap: int = theme.SP2,
             side: str = "left") -> None:
    """Pack ``widgets`` into ``container`` with a consistent gap."""
    for i, w in enumerate(widgets):
        if w is None:
            continue
        pad = (0, gap) if i < len(widgets) - 1 else (0, 0)
        w.pack(side=side, padx=pad)
