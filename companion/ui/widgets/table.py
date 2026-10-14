"""C8 — the data grid behind the squad board and every result list.

WHY this exists
---------------
v1's squad screen is a ``tk.Listbox`` rendering fixed-width monospace text
(``src/ui/widgets.py::listbox``). It cannot sort, cannot multi-select
meaningfully, cannot colour an OVR, cannot show a headshot, and truncates every
name to whatever the column padding allowed. It is the worst screen in the app
and it is the *only* screen containing the user's real save data. §7.1 replaces
it, and this module is that replacement.

Design, in two halves:

**:class:`TableModel` is pure.** Sorting, filtering, selection, range-select and
cursor movement have no Tk import path at all. That is not tidiness — selection
semantics (shift-range from an anchor, ctrl-toggle, select-all-*visible*) are
where grids get subtly wrong, and they are only cheap to test if they are not
entangled with widgets. Every rule in §3.3's control table is asserted directly
against this class.

**:class:`DataGrid` is "virtualised enough".** CTk has no virtualised grid
(§7.1), so this keeps a *fixed pool* of row widgets sized to the viewport and
rebinds their content as ``first_index`` moves. One code path serves 45 players
and 45,000: widget count is O(visible), never O(rows). The alternative — build
all rows below 200 and window above it (P-8) — needs two code paths and two
sets of bugs, and the pool costs nothing at 45 rows.

Selection state is keyed by a **row key**, not a row index, so sorting or
filtering does not silently re-point a checkbox at a different player. That is
the multi-select equivalent of v1's five-parallel-result-lists invalidation bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .. import theme
from .primitives import (
    EM_DASH,
    bind_pointer_wheel,
    display_font,
    ensure_ctk,
    num_font,
    provenance_color,
    register_wheel_region,
    release_wheel_region,
    shade,
    truncate,
    ui_font,
)

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

Row = Mapping[str, Any]

#: A6: rows are tall enough to aim at; the whole row is the click band.
ROW_HEIGHT = 30
HEADER_HEIGHT = 30
CHECK_WIDTH = 32
#: OVR cells render as a centred tier chip of this width, never column-wide.
OVR_CHIP_WIDTH = 34
#: Rows advanced per mouse-wheel notch (flushed once per idle cycle).
WHEEL_ROWS = 3
#: Opt-in ``fill_available`` pool: enough rows for a windowed panel, not a stadium.
FILL_POOL_MIN = 6
FILL_POOL_MAX = 24


def shown_pool_for_height(
    height: int,
    *,
    row_height: int = ROW_HEIGHT,
    minimum: int = FILL_POOL_MIN,
    maximum: int = FILL_POOL_MAX,
) -> int:
    """How many pooled rows fit in ``height`` pixels of grid body."""
    step = row_height if row_height > 0 else ROW_HEIGHT
    rows = int(height) // step
    if rows < minimum:
        return minimum
    if rows > maximum:
        return maximum
    return rows


def configure_body_height(
    widget: Any,
    host: Any,
    *,
    event_height: int = 0,
    host_height: int = 0,
) -> int | None:
    """Visible body height from a resize, or None when the event is unrelated.

    ``CTkFrame.bind`` attaches the callback to the frame's inner canvas, so the
    event widget is that canvas rather than the frame the grid created.
    """
    canvas = getattr(host, "_canvas", None)
    if widget is not host and (canvas is None or widget is not canvas):
        return None
    # The frame's allocated height is the space the list can fill. The inner
    # canvas can report a different size while CustomTkinter is still drawing.
    allocated = int(host_height or 0)
    height = allocated if allocated > 1 else int(event_height or 0)
    return height if height > 1 else None


@dataclass(frozen=True, slots=True)
class Column:
    """One grid column.

    ``format`` receives the whole row (not just the cell) so a column can render
    something composed — "2029" from a contract dict, "86" from a nested card —
    without the model needing to flatten anything first.

    ``tone`` likewise receives the row and returns a presenter tone name, which
    is how a fitness column goes amber without this module knowing what fitness
    is.
    """

    key: str
    title: str
    width: int = 90
    align: str = "w"          # w | e | center
    numeric: bool = False     # tabular font, right-aligned, numeric sort
    sortable: bool = True
    stretch: bool = False     # absorbs leftover width (exactly one, usually NAME)
    format: Callable[[Row], str] | None = None
    tone: Callable[[Row], str] | None = None
    kind: str = ""  # "" | "ovr" (tier chip) | "pos" (position-group colour)
    #: 0 = always shown. When the grid is too narrow, the highest priority
    #: column is hidden first, so a name is never squeezed to make room for AGE.
    priority: int = 0
    #: Cap for a stretch column. On a wide window an uncapped NAME pushed the
    #: ratings 900 px away from the names they belong to.
    max_width: int = 0

    @property
    def anchor(self) -> str:
        if self.align == "center" or self.kind == "ovr":
            return "center"
        return "e" if (self.align == "e" or self.numeric) else "w"

    def text(self, row: Row) -> str:
        """The rendered cell text. ``None`` becomes an em dash, never ``"None"``."""
        if self.format is not None:
            try:
                return str(self.format(row))
            except Exception:
                return EM_DASH
        value = row.get(self.key)
        if value is None:
            return EM_DASH
        return str(value)


def _sort_value(row: Row, column: Column) -> tuple[bool, Any]:
    """``(known, comparable)`` for one cell. Unknowns are flagged, not coerced.

    A player whose OVR was never read must not sort to the top of "best first".
    Folding the unknown flag into the sort key does not achieve that — reversing
    the sort reverses the flag too, and the unknowns march to the front. So the
    caller *partitions* on ``known`` and only reverses the known half; see
    :meth:`TableModel._recompute`. An unknown is not a zero (P3).
    """
    value = row.get(column.key)
    if value is None:
        return (False, 0.0 if column.numeric else "")
    if column.numeric:
        try:
            return (True, float(value))
        except (TypeError, ValueError):
            return (False, 0.0)
    return (True, str(value).casefold())


def inclusive_span(keys: Sequence[Any], anchor: Any, target: Any) -> tuple[Any, ...] | None:
    """Keys from ``anchor`` to ``target`` in ``keys`` order, inclusive.

    Either endpoint missing from the visible list means there is no range.
    Hidden rows are simply absent from ``keys``.
    """
    try:
        start = list(keys).index(anchor)
        end = list(keys).index(target)
    except ValueError:
        return None
    lo, hi = sorted((start, end))
    return tuple(keys[lo : hi + 1])


class TableModel:
    """Rows + columns + sort + filter + selection. No Tk.

    ``key_field`` names the stable identity of a row (``playerid`` for the squad
    board). Without it, selection falls back to the row's position in the
    *source* list, which is stable enough for static data but wrong the moment
    the caller re-reads the squad.
    """

    def __init__(
        self,
        columns: Sequence[Column],
        rows: Iterable[Row] = (),
        *,
        key_field: str = "",
        multi_select: bool = True,
        sort_key: str = "",
        sort_desc: bool = False,
    ) -> None:
        self.columns: tuple[Column, ...] = tuple(columns)
        self.key_field = key_field
        self.multi_select = multi_select
        self.sort_key = sort_key
        self.sort_desc = sort_desc
        self.filter_text: str = ""
        self.filter_fn: Callable[[Row], bool] | None = None
        self.checked: set[Any] = set()
        self.cursor: int = -1
        # Last clicked or ticked player, by identity. A later sort or filter
        # must not turn that into a different row.
        self._anchor_key: Any = None
        self._anchor: int = -1
        self._rows: tuple[Row, ...] = tuple(rows)
        self._visible: tuple[Row, ...] = ()
        self._recompute()

    # ---- data ---------------------------------------------------------

    @property
    def rows(self) -> tuple[Row, ...]:
        return self._rows

    def set_rows(self, rows: Iterable[Row]) -> None:
        """Replace the data, keeping any selection whose key still exists.

        Dropping selection wholesale on refresh is what made v1's squad filter
        feel destructive; keeping stale keys would be worse. Intersecting is the
        only honest option.
        """
        self._rows = tuple(rows)
        live = {self.row_key(r) for r in self._rows}
        self.checked &= live
        self._recompute()
        self.cursor = min(self.cursor, len(self._visible) - 1)

    def row_key(self, row: Row) -> Any:
        if self.key_field:
            return row.get(self.key_field)
        return id(row)

    # ---- filter / sort ------------------------------------------------

    def set_filter(self, text: str) -> None:
        self.filter_text = str(text or "")
        self._recompute()
        self.cursor = min(self.cursor, len(self._visible) - 1)

    def set_filter_fn(self, fn: Callable[[Row], bool] | None) -> None:
        """Facet filters (§3.3 POS / OVR / AGE / ★) compose on top of the text box."""
        self.filter_fn = fn
        self._recompute()
        self.cursor = min(self.cursor, len(self._visible) - 1)

    def sort_by(self, key: str) -> None:
        """Click a header: same column toggles direction, a new column resets it.

        Numeric columns start descending (best first) because "click OVR" means
        "show me the best", and text columns start ascending.
        """
        column = self.column(key)
        if column is None or not column.sortable:
            return
        if self.sort_key == key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = key
            self.sort_desc = bool(column.numeric)
        self._recompute()

    def column(self, key: str) -> Column | None:
        for c in self.columns:
            if c.key == key:
                return c
        return None

    def _matches(self, row: Row) -> bool:
        if self.filter_fn is not None and not self.filter_fn(row):
            return False
        needle = self.filter_text.strip().casefold()
        if not needle:
            return True
        for column in self.columns:
            if needle in column.text(row).casefold():
                return True
        return False

    def _recompute(self) -> None:
        rows = [r for r in self._rows if self._matches(r)]
        column = self.column(self.sort_key) if self.sort_key else None
        if column is not None:
            keyed = [(_sort_value(r, column), r) for r in rows]
            known = [(k[1], r) for k, r in keyed if k[0]]
            unknown = [r for k, r in keyed if not k[0]]
            known.sort(key=lambda pair: pair[0], reverse=self.sort_desc)
            # Unknowns always trail, in either direction.
            rows = [r for _k, r in known] + unknown
        self._visible = tuple(rows)

    def visible_rows(self) -> tuple[Row, ...]:
        return self._visible

    @property
    def visible_count(self) -> int:
        return len(self._visible)

    def row_at(self, index: int) -> Row | None:
        if 0 <= index < len(self._visible):
            return self._visible[index]
        return None

    # ---- selection ----------------------------------------------------

    def is_checked(self, row: Row) -> bool:
        return self.row_key(row) in self.checked

    def _remember_anchor(self, index: int) -> None:
        row = self.row_at(index)
        if row is None:
            return
        self._anchor_key = self.row_key(row)
        self._anchor = index
        self.cursor = index

    def _anchor_index(self) -> int:
        if self._anchor_key is None:
            return -1
        for index, row in enumerate(self._visible):
            if self.row_key(row) == self._anchor_key:
                return index
        return -1

    def _select_visible_range(self, index: int) -> bool:
        """Select the inclusive span in the current visible order.

        Filtered-out rows are not in ``_visible``, so they stay out of the range.
        The anchor player is left where it is, so the next Shift-click grows
        from the same place.
        """
        anchor = self._anchor_index()
        if not self.multi_select or anchor < 0 or not (0 <= index < len(self._visible)):
            return False
        keys = [self.row_key(row) for row in self._visible]
        span = inclusive_span(keys, keys[anchor], keys[index])
        if span is None:
            return False
        self.checked = set(span)
        self.cursor = index
        return True

    def click(self, index: int, *, shift: bool = False, ctrl: bool = False) -> None:
        """Plain click selects one, ``Shift`` ranges, ``Ctrl`` toggles."""
        if not (0 <= index < len(self._visible)):
            return
        self.cursor = index
        if not self.multi_select:
            self.checked = {self.row_key(self._visible[index])}
            self._remember_anchor(index)
            return
        if shift and self._select_visible_range(index):
            return
        if ctrl:
            self.toggle(index)
            return
        self.checked = {self.row_key(self._visible[index])}
        self._remember_anchor(index)

    def toggle(self, index: int, *, shift: bool = False) -> None:
        """``Space`` / checkbox click. Single-select models replace instead."""
        row = self.row_at(index)
        if row is None:
            return
        if shift and self._select_visible_range(index):
            return
        key = self.row_key(row)
        if not self.multi_select:
            self.checked = set() if key in self.checked else {key}
            self._remember_anchor(index)
            return
        if key in self.checked:
            self.checked.discard(key)
        else:
            self.checked.add(key)
        self._remember_anchor(index)

    def select_all_visible(self) -> None:
        """``Ctrl+A``. Visible, not *all* — selecting rows a filter is hiding is
        exactly how a bulk edit hits players the user never saw."""
        if not self.multi_select:
            return
        self.checked = {self.row_key(r) for r in self._visible}

    def clear_selection(self) -> None:
        self.checked.clear()
        self._anchor = -1
        self._anchor_key = None

    def selected_rows(self) -> tuple[Row, ...]:
        return tuple(r for r in self._visible if self.row_key(r) in self.checked)

    @property
    def selected_count(self) -> int:
        return len(self.checked)

    def move_cursor(self, delta: int) -> int:
        """Arrow / page navigation. Clamps; never wraps (wrapping loses people)."""
        if not self._visible:
            self.cursor = -1
            return -1
        target = 0 if self.cursor < 0 else self.cursor + delta
        self.cursor = max(0, min(len(self._visible) - 1, target))
        return self.cursor

    def cursor_row(self) -> Row | None:
        return self.row_at(self.cursor)

    def header_caption(self, key: str) -> str:
        """Column title plus its sort indicator — A5 (never colour alone)."""
        column = self.column(key)
        if column is None:
            return ""
        if self.sort_key != key:
            return column.title
        return f"{column.title} {'▼' if self.sort_desc else '▲'}"


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


class DataGrid:
    """A pooled, scroll-windowed grid. Composition, not subclassing.

    Not a ``CTkFrame`` subclass so this module imports on a machine with no
    customtkinter — the model above is then still testable. ``.widget`` is the
    frame to pack; ``pack``/``grid``/``place`` forward to it for convenience.
    """

    def __init__(
        self,
        parent: Any,
        model: TableModel,
        *,
        show_checkboxes: bool = True,
        visible_rows: int = 14,
        fill_available: bool = False,
        select_on_click: bool = True,
        selection_on_click: bool | None = None,
        on_row_click: Callable[[Row], None] | None = None,
        on_row_activate: Callable[[Row], None] | None = None,
        on_selection_change: Callable[[tuple[Row, ...]], None] | None = None,
        on_sort: Callable[[str, bool], None] | None = None,
        empty_text: str = "",
    ) -> None:
        ensure_ctk()
        self.model = model
        #: Shown in the row area when there are no rows, so an empty list says why.
        self.empty_text = empty_text
        self._empty_label: Any = None
        self.show_checkboxes = show_checkboxes and model.multi_select
        # Roster grids can opt into cursor-only clicks: checkbox/Space then
        # remains the sole owner of the checked bulk-selection set.
        # ``selection_on_click`` is the descriptive public spelling. Keep the
        # shorter alias for callers introduced during the transition.
        self.select_on_click = (
            select_on_click if selection_on_click is None else selection_on_click
        )
        self.on_row_click = on_row_click
        self.on_row_activate = on_row_activate
        self.on_selection_change = on_selection_change
        self.on_sort = on_sort
        self.first_index = 0
        requested = max(1, int(visible_rows))
        self.fill_available = bool(fill_available)
        self._max_pool = FILL_POOL_MAX if self.fill_available else requested
        self._pool_size = (
            FILL_POOL_MIN if self.fill_available else requested
        )
        self._rows: list[dict[str, Any]] = []
        self._sel_fill = shade(theme.ACCENT, 0.22)
        #: The row the detail panel and the keyboard are on, when not ticked.
        self._cursor_fill = shade(theme.ACCENT, 0.12)
        self._layout_cache: tuple[int, tuple[tuple[Column, int, int], ...]] | None = None
        self._resize_job: Any = None
        # Incremental paint: last written (text, colour, width) per (slot, col).
        # On scroll we rotate this cache so only newly-visible rows miss.
        self._painted: dict[tuple[int, str], tuple[Any, ...]] = {}
        self._row_fill: dict[int, str] = {}
        self._check_state: dict[int, tuple[str, str]] = {}
        self._header_painted: dict[str, tuple[Any, ...]] = {}
        self._cell_placed: set[tuple[int, str]] = set()
        self._geom_dirty = True
        self._last_width = 0
        self._wheel_accum = 0
        self._wheel_job: Any = None
        self._wheel_bound: set[int] = set()
        self._wheel_region: Any = None
        self._paint_count = 0  # test seam: increments once per _paint call

        self.widget = ctk.CTkFrame(
            parent,
            fg_color=theme.PANEL,
            corner_radius=theme.R_MD,
            border_width=1,
            border_color=theme.BORDER,
        )
        self._build_header()
        self._build_body()
        self._bind_keys()
        try:
            self.widget.bind("<Destroy>", lambda _e: self._cancel_wheel(), add="+")
        except Exception:
            pass
        try:
            import tkinter

            # Columns must follow the grid's real width: the drawer opening, a
            # resize or the detail panel collapsing all change it after paint.
            # The row area is what _content_width measures, and it is resized
            # after the outer frame, so it is the one whose event matters.
            tkinter.Misc.bind(self.canvas_host, "<Configure>", self._on_resize, add="+")
        except Exception:
            pass
        self.refresh()

    # ---- geometry forwarding -----------------------------------------

    def pack(self, **kw: Any) -> None:
        self.widget.pack(**kw)

    def pack_forget(self) -> None:
        """Mirror Tk's pack API for views that disclose a grid on demand."""
        self.widget.pack_forget()

    def grid(self, **kw: Any) -> None:
        self.widget.grid(**kw)

    def place(self, **kw: Any) -> None:
        self.widget.place(**kw)

    # ---- layout -------------------------------------------------------

    def _layout(self, total_width: int) -> tuple[tuple[Column, int, int], ...]:
        """``(column, x, width)`` for the columns that fit, in unscaled pixels.

        Leftover width goes to the stretch column. When the grid is narrower
        than the columns need, optional columns (``priority > 0``) drop out,
        highest priority first, before anything is clipped.
        """
        if self._layout_cache is not None and self._layout_cache[0] == total_width:
            return self._layout_cache[1]
        lead = CHECK_WIDTH if self.show_checkboxes else 0
        shown = list(self.model.columns)

        def needed(columns: list[Column]) -> int:
            return lead + sum(c.width for c in columns) + theme.SP2

        while needed(shown) > total_width:
            optional = [c for c in shown if c.priority > 0]
            if not optional:
                break
            shown.remove(max(optional, key=lambda c: c.priority))
        slack = max(0, total_width - needed(shown))
        stretch = next((c for c in shown if c.stretch), None)
        extra = 0
        if stretch is not None and stretch.max_width:
            capped = min(slack, max(0, stretch.max_width - stretch.width))
            # Width the capped name cannot use is shared by the other columns,
            # so the grid still reaches its right edge without a dead gap.
            others = [c for c in shown if c is not stretch]
            extra = (slack - capped) // len(others) if others else 0
            slack = capped
        out: list[tuple[Column, int, int]] = []
        x = lead
        for column in shown:
            w = column.width + (slack if column is stretch else extra)
            out.append((column, x, w))
            x += w
        layout = tuple(out)
        self._layout_cache = (total_width, layout)
        return layout

    def _offsets(self, total_width: int) -> list[tuple[int, int]]:
        """(x, width) per *shown* column. Kept for callers that only need geometry."""
        return [(x, w) for _c, x, w in self._layout(total_width)]

    def _content_width(self) -> int:
        """Usable width in CTk units: the row area left of the scrollbar.

        ``winfo_width`` is in device pixels while CTk scales ``place(x=)`` by
        the UI scale, so the measurement is divided back before laying out.
        """
        try:
            px = int(self.canvas_host.winfo_width())
        except Exception:
            px = 0
        if px <= 1:
            return sum(c.width for c in self.model.columns) + CHECK_WIDTH
        try:
            scale = float(self.widget._get_widget_scaling())
        except Exception:
            scale = 1.0
        return int(px / max(scale, 0.5))

    def _on_resize(self, _event: Any = None) -> None:
        if self._resize_job is not None:
            return

        def run() -> None:
            self._resize_job = None
            try:
                if not self.widget.winfo_exists():
                    return
            except Exception:
                return
            if self._content_width() != self._last_width:
                self._geom_dirty = True
                self._paint()

        try:
            self._resize_job = self.widget.after_idle(run)
        except Exception:
            self._resize_job = None

    def _build_header(self) -> None:
        self.header = ctk.CTkFrame(
            self.widget, height=HEADER_HEIGHT, fg_color=theme.GRID_HEADER, corner_radius=0
        )
        self.header.pack(fill="x", padx=1, pady=(1, 0))
        self.header.pack_propagate(False)
        self._header_labels: dict[str, Any] = {}

        if self.show_checkboxes:
            self._all_box = ctk.CTkLabel(
                self.header, text="", image=self._check_image(False),
                width=CHECK_WIDTH, height=HEADER_HEIGHT, cursor="hand2",
            )
            self._all_box.place(x=0, y=0)
            self._all_box.bind("<Button-1>", lambda _e: self._toggle_all())
            self._bind_wheel_widget(self._all_box)

        for column in self.model.columns:
            label = ctk.CTkLabel(
                self.header,
                text=column.title,
                font=ui_font(theme.T_MICRO, bold=True),
                text_color=theme.MUTED,
                anchor=column.anchor,
                height=HEADER_HEIGHT,
            )
            self._header_labels[column.key] = label
            if column.sortable:
                label.bind("<Button-1>", lambda _e, k=column.key: self._sort(k))
                label.configure(cursor="hand2")
            self._bind_wheel_widget(label)

        from .primitives import divider  # local: avoids a cycle at module scope

        divider(self.widget).pack(fill="x", padx=1)

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self.widget, fg_color=theme.PANEL, corner_radius=0)
        body.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        self.canvas_host = ctk.CTkFrame(body, fg_color=theme.PANEL, corner_radius=0)
        self.canvas_host.pack(side="left", fill="both", expand=True)
        self.scrollbar = ctk.CTkScrollbar(
            body,
            command=self._on_scroll,
            width=12,
            fg_color=theme.PANEL,
            button_color=theme.BORDER,
            button_hover_color=theme.CARD_HOVER,
            corner_radius=0,
        )
        self.scrollbar.pack(side="right", fill="y", padx=(2, 2), pady=2)

        for i in range(self._max_pool):
            self._rows.append(self._make_row(i))
        self._sync_shown_pool_pack()

        for target in (self.widget, self.header, body, self.canvas_host):
            self._bind_wheel_widget(target)
        self._wheel_region = register_wheel_region(
            (self.widget, self.header, body, self.canvas_host),
            (self.scrollbar,),
            self._on_wheel,
            getattr(self.canvas_host, "_canvas", self.canvas_host),
        )
        if self.fill_available:
            try:
                # CTkFrame.bind listens on the inner canvas. Also listen on the
                # frame itself so the allocated height, not the 6-row minimum,
                # is what decides how many players are shown.
                import tkinter

                tkinter.Misc.bind(
                    self.canvas_host, "<Configure>", self._on_body_configure, add="+"
                )
            except Exception:
                pass
            try:
                self.canvas_host.bind("<Configure>", self._on_body_configure, add="+")
            except Exception:
                pass
            try:
                self.widget.after_idle(self._fit_shown_pool_from_host)
            except Exception:
                pass

    def _bind_wheel_widget(self, widget: Any) -> None:
        """Wheel-scroll from the widget the cursor is actually on.

        CTk puts a canvas behind every frame and a text label on top of every
        cell. Binding the frame only scrolls the gaps. The name, position, and
        rating text never saw the notch, so the wheel appeared to work only on
        the scrollbar.
        """
        bind_pointer_wheel(widget, self._on_wheel, self._wheel_bound)

    def _slot_for_frame(self, frame: Any) -> int:
        """Resolve the current visual slot of a pooled row frame after rotations."""
        for index, row in enumerate(self._rows):
            if row["frame"] is frame:
                return index if index < self._pool_size else -1
        return -1

    def _make_row(self, slot: int) -> dict[str, Any]:
        frame = ctk.CTkFrame(
            self.canvas_host, height=ROW_HEIGHT, fg_color=theme.CARD, corner_radius=0
        )
        frame.pack(fill="x")
        frame.pack_propagate(False)

        cells: dict[str, Any] = {}
        check = None
        if self.show_checkboxes:
            check = ctk.CTkLabel(frame, text="", image=self._check_image(False),
                                 width=CHECK_WIDTH, height=ROW_HEIGHT, cursor="hand2")
            check.bind(
                "<Button-1>",
                lambda event, f=frame: self._on_check(self._slot_for_frame(f), event),
            )
            self._bind_wheel_widget(check)
        for column in self.model.columns:
            if column.kind == "ovr":
                font = display_font(theme.T_BODY)
                height = ROW_HEIGHT - 8
            else:
                font = num_font(theme.T_SMALL) if column.numeric else ui_font(theme.T_SMALL)
                height = ROW_HEIGHT
            cells[column.key] = ctk.CTkLabel(
                frame,
                text="",
                font=font,
                text_color=theme.TEXT,
                anchor=column.anchor,
                height=height,
            )

        row = {"frame": frame, "cells": cells, "check": check, "index": -1}

        def _click(event: Any, f: Any = frame) -> None:
            slot_now = self._slot_for_frame(f)
            if slot_now >= 0:
                self._on_click(slot_now, event)

        def _double(event: Any, f: Any = frame) -> None:
            slot_now = self._slot_for_frame(f)
            if slot_now >= 0:
                self._on_activate(slot_now)

        def _enter(_event: Any, f: Any = frame) -> None:
            slot_now = self._slot_for_frame(f)
            if slot_now >= 0:
                self._hover(slot_now, True)

        def _leave(_event: Any, f: Any = frame) -> None:
            slot_now = self._slot_for_frame(f)
            if slot_now >= 0:
                self._hover(slot_now, False)

        for widget in [frame, *cells.values()]:
            widget.bind("<Button-1>", _click, add="+")
            widget.bind("<Double-Button-1>", _double, add="+")
            widget.bind("<Enter>", _enter, add="+")
            widget.bind("<Leave>", _leave, add="+")
            self._bind_wheel_widget(widget)
        return row

    def _bind_keys(self) -> None:
        """A3/A1 keyboard navigation, bound on the grid rather than globally."""
        w = self.widget
        try:
            w.configure(takefocus=True)
        except Exception:
            pass
        bindings = {
            "<Down>": lambda _e: self._nav(1),
            "<Up>": lambda _e: self._nav(-1),
            "<Next>": lambda _e: self._nav(self._pool_size),
            "<Prior>": lambda _e: self._nav(-self._pool_size),
            "<Home>": lambda _e: self._nav(-10**9),
            "<End>": lambda _e: self._nav(10**9),
            "<space>": lambda _e: self._key_toggle(),
            "<Return>": lambda _e: self._key_activate(),
            "<Control-a>": lambda _e: self._key_select_all(),
        }
        import tkinter

        # CTkFrame.bind attaches to its inner canvas, but focus_set() focuses
        # the frame itself, so keys bound through CTk never fired. Bind both.
        for seq, fn in bindings.items():
            for target in (w, getattr(w, "_canvas", None)):
                if target is None:
                    continue
                try:
                    tkinter.Misc.bind(target, seq, fn, add="+")
                except Exception:
                    pass
        try:
            w.bind("<Button-1>", lambda _e: w.focus_set(), add="+")
        except Exception:
            pass

        def _ring(on: bool) -> None:
            try:
                w.configure(border_color=theme.FOCUS if on else theme.BORDER)
            except Exception:
                pass

        try:
            tkinter.Misc.bind(w, "<FocusIn>", lambda _e: _ring(True), add="+")
            tkinter.Misc.bind(w, "<FocusOut>", lambda _e: _ring(False), add="+")
        except Exception:
            pass

    # ---- events -------------------------------------------------------

    def _sort(self, key: str) -> None:
        self.model.sort_by(key)
        self.first_index = 0
        self.refresh()
        if self.on_sort is not None:
            self.on_sort(self.model.sort_key, self.model.sort_desc)

    def _index_for(self, slot: int) -> int:
        return self.first_index + slot

    def _on_click(self, slot: int, event: Any) -> int | None:
        if slot < 0:
            return None
        index = self._index_for(slot)
        if index >= self.model.visible_count:
            return None
        state = int(getattr(event, "state", 0) or 0)
        shift = bool(state & 0x0001)
        ctrl = bool(state & 0x0004)
        if self.select_on_click or (shift and self.model.multi_select):
            self.model.click(index, shift=shift, ctrl=ctrl and self.select_on_click)
            self._emit_selection()
        else:
            self.model.cursor = index
            if self.model.multi_select:
                self.model._remember_anchor(index)
        self._paint()
        row = self.model.row_at(index)
        if row is not None and self.on_row_click is not None:
            self.on_row_click(row)
        try:
            self.widget.focus_set()
        except Exception:
            pass
        return None

    def _on_check(self, slot: int, event: Any = None) -> str:
        if slot < 0:
            return "break"
        index = self._index_for(slot)
        if index < self.model.visible_count:
            state = int(getattr(event, "state", 0) or 0)
            self.model.toggle(index, shift=bool(state & 0x0001))
            self._paint()
            self._emit_selection()
        return "break"

    def _on_activate(self, slot: int) -> None:
        if slot < 0:
            return
        row = self.model.row_at(self._index_for(slot))
        if row is not None and self.on_row_activate is not None:
            self.on_row_activate(row)

    def _toggle_all(self) -> None:
        if self.model.selected_count >= self.model.visible_count:
            self.model.clear_selection()
        else:
            self.model.select_all_visible()
        self._paint()
        self._emit_selection()

    def _hover(self, slot: int, on: bool) -> None:
        if slot < 0 or slot >= self._pool_size:
            return
        row = self._rows[slot]
        if row["index"] < 0:
            return
        model_row = self.model.row_at(row["index"])
        selected = model_row is not None and self.model.is_checked(model_row)
        fill = self._sel_fill if selected else (
            theme.CARD_HOVER if on else self._rest_fill(row["index"])
        )
        if self._row_fill.get(slot) == fill:
            return
        try:
            row["frame"].configure(fg_color=fill)
            self._row_fill[slot] = fill
        except Exception:
            pass

    def _cancel_wheel(self) -> None:
        release_wheel_region(self._wheel_region)
        self._wheel_region = None
        job = self._wheel_job
        self._wheel_job = None
        self._wheel_accum = 0
        if job is None:
            return
        try:
            self.widget.after_cancel(job)
        except Exception:
            pass

    def _on_body_configure(self, event: Any) -> None:
        """Grow or shrink the shown pool so windowed panels fill leftover height."""
        if not self.fill_available:
            return
        try:
            host_height = int(self.canvas_host.winfo_height())
        except Exception:
            host_height = 0
        height = configure_body_height(
            getattr(event, "widget", None),
            self.canvas_host,
            event_height=int(getattr(event, "height", 0) or 0),
            host_height=host_height,
        )
        if height is None:
            return
        self._fit_shown_pool(height)

    def _fit_shown_pool_from_host(self) -> None:
        try:
            height = int(self.canvas_host.winfo_height())
        except Exception:
            return
        self._fit_shown_pool(height)

    def _fit_shown_pool(self, height: int) -> None:
        if not self.fill_available or height <= 1:
            return
        row_height = ROW_HEIGHT
        if self._rows:
            try:
                measured = int(self._rows[0]["frame"].winfo_height())
            except Exception:
                measured = 0
            if measured > 1:
                row_height = measured
        n = shown_pool_for_height(height, row_height=row_height)
        n = min(n, self._max_pool)
        visible = self.model.visible_count
        if visible > 0:
            n = min(n, visible)
        if n == self._pool_size:
            return
        self._set_shown_pool(n)

    def _set_shown_pool(self, n: int) -> None:
        self._pool_size = max(1, min(int(n), len(self._rows)))
        self._sync_shown_pool_pack()
        self.first_index = min(self.first_index, self._max_first())
        self._clear_paint_cache()
        self._paint()

    def _sync_shown_pool_pack(self) -> None:
        shown = self._pool_size
        for i, row in enumerate(self._rows):
            frame = row["frame"]
            try:
                if i < shown:
                    if not frame.winfo_manager():
                        frame.pack(fill="x")
                else:
                    frame.pack_forget()
            except Exception:
                pass

    def _on_wheel(self, event: Any) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            notches = int(delta / 120) or (1 if delta > 0 else -1)
        else:  # X11 button-4/5
            notches = 1 if getattr(event, "num", 5) == 4 else -1
        # Positive notch (wheel up) moves the window toward lower indices.
        self._wheel_accum -= notches * WHEEL_ROWS
        if self._wheel_job is None:
            try:
                self._wheel_job = self.widget.after_idle(self._flush_wheel)
            except Exception:
                self._flush_wheel()
        return "break"

    def _flush_wheel(self) -> None:
        self._wheel_job = None
        step = self._wheel_accum
        self._wheel_accum = 0
        if step:
            self._scroll_to(self.first_index + step)

    def _on_scroll(self, *args: Any) -> None:
        """CTkScrollbar command: ``("moveto", frac)`` or ``("scroll", n, what)``."""
        if not args:
            return
        how = args[0]
        total = self.model.visible_count
        if how == "moveto" and len(args) > 1:
            self._scroll_to(int(float(args[1]) * total))
        elif how == "scroll" and len(args) > 2:
            amount = int(args[1])
            self._scroll_to(
                self.first_index + amount * (self._pool_size if args[2] == "pages" else 1)
            )

    def _nav(self, delta: int) -> str:
        index = self.model.move_cursor(delta)
        if index >= 0:
            self._ensure_visible(index)
            if self.select_on_click:
                self.model.click(index)
            self._paint()
            row = self.model.cursor_row()
            if self.select_on_click and row is not None and self.on_row_click is not None:
                self.on_row_click(row)
            if self.select_on_click:
                self._emit_selection()
        return "break"

    def _key_toggle(self) -> str:
        if self.model.cursor >= 0:
            self.model.toggle(self.model.cursor)
            self._paint()
            self._emit_selection()
        return "break"

    def _key_activate(self) -> str:
        row = self.model.cursor_row()
        if row is not None and self.on_row_activate is not None:
            self.on_row_activate(row)
        return "break"

    def _key_select_all(self) -> str:
        self.model.select_all_visible()
        self._paint()
        self._emit_selection()
        return "break"

    def _emit_selection(self) -> None:
        if self.on_selection_change is not None:
            self.on_selection_change(self.model.selected_rows())

    # ---- painting -----------------------------------------------------

    def _zebra(self, index: int) -> str:
        return theme.CARD if index % 2 == 0 else theme.ROW_ALT

    def _rest_fill(self, index: int) -> str:
        """Zebra, except the cursor row, which the detail panel is describing."""
        if index == self.model.cursor and index >= 0:
            return self._cursor_fill
        return self._zebra(index)

    @staticmethod
    def _check_image(checked: bool) -> Any:
        from ..icons import check_image

        return check_image(
            checked, size=16, box=theme.BORDER_STRONG, fill=theme.ACCENT, mark=theme.BG,
        )

    def _max_first(self) -> int:
        return max(0, self.model.visible_count - self._pool_size)

    def _scroll_to(self, first: int) -> None:
        first = max(0, min(self._max_first(), int(first)))
        if first == self.first_index:
            return
        delta = first - self.first_index
        self.first_index = first
        if 0 < abs(delta) < self._pool_size:
            # Rotate pooled frames with the window so already-correct pixels
            # stay on the matching widget; only the newly revealed row(s) miss
            # the paint cache and need a configure().
            self._rotate_rows(delta)
        else:
            self._clear_paint_cache()
        self._paint()

    def _rotate_rows(self, delta: int) -> None:
        """Move pooled row frames in pack order to match a small scroll."""
        shown = self._pool_size
        visible = self._rows[:shown]
        hidden = self._rows[shown:]
        if delta > 0:
            for _ in range(delta):
                row = visible.pop(0)
                visible.append(row)
                try:
                    row["frame"].pack_forget()
                    row["frame"].pack(fill="x")
                except Exception:
                    pass
        else:
            for _ in range(-delta):
                row = visible.pop()
                visible.insert(0, row)
                try:
                    row["frame"].pack_forget()
                    if len(visible) > 1:
                        row["frame"].pack(fill="x", before=visible[1]["frame"])
                    else:
                        row["frame"].pack(fill="x")
                except Exception:
                    pass
        self._rows = visible + hidden
        for row in hidden:
            try:
                row["frame"].pack_forget()
            except Exception:
                pass
        self._shift_paint_cache(delta)

    def _shift_paint_cache(self, delta: int) -> None:
        """Reuse paint state for rows that remain visible after a small scroll."""
        if not delta:
            return
        pool = self._pool_size
        shifted: dict[tuple[int, str], tuple[Any, ...]] = {}
        for (slot, key), val in self._painted.items():
            new_slot = slot - delta
            if 0 <= new_slot < pool:
                shifted[(new_slot, key)] = val
        self._painted = shifted
        self._row_fill = {
            slot - delta: fill
            for slot, fill in self._row_fill.items()
            if 0 <= slot - delta < pool
        }
        # Header checkbox lives at key -1; never shift it with row slots.
        self._check_state = {
            (slot - delta if slot >= 0 else slot): state
            for slot, state in self._check_state.items()
            if slot < 0 or 0 <= slot - delta < pool
        }
        self._cell_placed = {
            (slot - delta, key)
            for (slot, key) in self._cell_placed
            if 0 <= slot - delta < pool
        }

    def _clear_paint_cache(self) -> None:
        self._painted.clear()
        self._row_fill.clear()
        self._check_state.clear()
        self._cell_placed.clear()
        self._header_painted.clear()
        self._geom_dirty = True

    def _ensure_visible(self, index: int) -> None:
        if index < self.first_index:
            self._scroll_to(index)
        elif index >= self.first_index + self._pool_size:
            self._scroll_to(index - self._pool_size + 1)

    def refresh(self) -> None:
        """Re-read the model. Call after ``set_rows``/``set_filter``/``sort_by``."""
        self.first_index = min(self.first_index, self._max_first())
        self._clear_paint_cache()
        self._paint()

    def _paint(self) -> None:
        self._paint_count += 1
        width = self._content_width()
        if width != self._last_width:
            self._geom_dirty = True
            self._last_width = width
        layout = self._layout(width)
        placed = {column.key: (x, w) for column, x, w in layout}

        for key, label in self._header_labels.items():
            column = self.model.column(key)
            if column is None:
                continue
            if key not in placed:
                if self._geom_dirty:
                    label.place_forget()
                    self._header_painted.pop(key, None)
                continue
            x, w = placed[key]
            caption = self.model.header_caption(key)
            cell_w = max(10, w - theme.SP2)
            sorted_on = self.model.sort_key == key
            header_now = (caption, cell_w, sorted_on)
            if self._header_painted.get(key) != header_now:
                label.configure(
                    text=caption, width=cell_w,
                    text_color=theme.TEXT if sorted_on else theme.MUTED,
                )
                self._header_painted[key] = header_now
            if self._geom_dirty:
                label.place(x=x + theme.SP1, y=0)

        if self.show_checkboxes:
            all_on = (
                self.model.visible_count > 0
                and self.model.selected_count >= self.model.visible_count
            )
            box_now = ("☑" if all_on else "☐", theme.ACCENT if all_on else theme.MUTED)
            if self._check_state.get(-1) != box_now:
                self._all_box.configure(image=self._check_image(all_on))
                self._check_state[-1] = box_now

        total = self.model.visible_count
        for slot, row in enumerate(self._rows[: self._pool_size]):
            index = self.first_index + slot
            model_row = self.model.row_at(index)
            row["index"] = index if model_row is not None else -1
            frame = row["frame"]
            if model_row is None:
                if self._row_fill.get(slot) != theme.PANEL:
                    frame.configure(fg_color=theme.PANEL)
                    self._row_fill[slot] = theme.PANEL
                if row["check"] is not None:
                    row["check"].place_forget()
                    self._check_state.pop(slot, None)
                for col_key, cell in row["cells"].items():
                    cell.place_forget()
                    self._cell_placed.discard((slot, col_key))
                    self._painted.pop((slot, col_key), None)
                continue

            selected = self.model.is_checked(model_row)
            fill = self._sel_fill if selected else self._rest_fill(index)
            if self._row_fill.get(slot) != fill:
                # A rotated frame often already wears this fill. Configuring it
                # anyway makes CTk re-colour every cell label in the row.
                if str(frame.cget("fg_color")) != fill:
                    frame.configure(fg_color=fill)
                self._row_fill[slot] = fill
            if row["check"] is not None:
                check_now = (
                    "☑" if selected else "☐",
                    theme.ACCENT if selected else theme.MUTED_DIM,
                )
                if self._check_state.get(slot) != check_now:
                    row["check"].configure(image=self._check_image(selected))
                    self._check_state[slot] = check_now
                row["check"].place(x=0, y=0)
            for column in self.model.columns:
                cell = row["cells"][column.key]
                cache_key = (slot, column.key)
                if column.key not in placed:
                    if cache_key in self._cell_placed:
                        cell.place_forget()
                        self._cell_placed.discard(cache_key)
                        self._painted.pop(cache_key, None)
                    continue
                x, w = placed[column.key]
                text = column.text(model_row)
                colour = theme.TEXT
                chip_fill = "transparent"
                if text == EM_DASH:
                    # P3: an unread cell is dimmed, not merely blank.
                    colour = provenance_color("unknown")
                elif column.kind == "ovr":
                    try:
                        number = int(float(str(text).replace("—", "").strip()))
                    except (TypeError, ValueError):
                        number = None
                    chip_fill, colour = theme.ovr_colors(number)
                    if number is None:
                        chip_fill = "transparent"
                        colour = provenance_color("unknown")
                elif column.kind == "pos":
                    colour = theme.pos_color(text)
                elif column.tone is not None:
                    try:
                        colour = theme.tone_fg(column.tone(model_row))
                    except Exception:
                        colour = theme.TEXT
                if column.kind == "ovr":
                    cell_w = min(OVR_CHIP_WIDTH, max(10, w - theme.SP2))
                    cell_x = x + max(0, (w - cell_w) // 2)
                    cell_y = 4
                    shown = text
                else:
                    cell_w = max(10, w - theme.SP2)
                    cell_x = x + theme.SP1
                    cell_y = 0
                    shown = truncate(text, max(3, cell_w // 7))
                now = (shown, colour, cell_w, chip_fill)
                if self._painted.get(cache_key) != now:
                    cell.configure(
                        text=shown,
                        text_color=colour,
                        width=cell_w,
                        fg_color=chip_fill,
                        corner_radius=theme.R_XS if chip_fill != "transparent" else 0,
                    )
                    self._painted[cache_key] = now
                if self._geom_dirty or cache_key not in self._cell_placed:
                    cell.place(x=cell_x, y=cell_y)
                    self._cell_placed.add(cache_key)

        self._geom_dirty = False
        self._paint_empty(total == 0)

        if total <= self._pool_size or total == 0:
            self.scrollbar.set(0.0, 1.0)
        else:
            top = self.first_index / total
            self.scrollbar.set(top, min(1.0, top + self._pool_size / total))

    def _paint_empty(self, empty: bool) -> None:
        if not self.empty_text:
            return
        if self._empty_label is None:
            self._empty_label = ctk.CTkLabel(
                self.canvas_host, text=self.empty_text, font=ui_font(theme.T_SMALL),
                text_color=theme.MUTED, fg_color=theme.PANEL, wraplength=420, justify="center",
            )
        try:
            if empty:
                self._empty_label.configure(text=self.empty_text)
                self._empty_label.place(relx=0.5, rely=0.4, anchor="center")
                self._empty_label.lift()
            else:
                self._empty_label.place_forget()
        except Exception:
            pass

    def set_empty_text(self, text: str) -> None:
        self.empty_text = str(text or "")
        self._paint_empty(self.model.visible_count == 0)

    # ---- convenience --------------------------------------------------

    def set_rows(self, rows: Iterable[Row]) -> None:
        anchor = None
        current = self.model.row_at(self.first_index)
        if current is not None:
            anchor = self.model.row_key(current)
        self.model.set_rows(rows)
        self.first_index = 0
        if anchor is not None:
            for index in range(self.model.visible_count):
                row = self.model.row_at(index)
                if row is not None and self.model.row_key(row) == anchor:
                    self.first_index = index
                    break
            self.first_index = min(self.first_index, self._max_first())
        self.refresh()

    def set_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self.first_index = 0
        self.refresh()
