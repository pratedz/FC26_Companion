"""DataGrid scroll/paint performance contracts.

These tests count configure/place work and paint calls rather than wall-clock
time so they stay stable on any machine. They are headless: they exercise the
paint-cache and wheel-coalesce logic without opening a window.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from companion.ui.widgets.table import WHEEL_ROWS, Column, DataGrid, TableModel


def _bare_grid(*, pool: int = 14) -> DataGrid:
    """DataGrid without Tk — only the paint/wheel helpers are wired."""
    grid = object.__new__(DataGrid)
    grid._pool_size = pool
    grid.first_index = 0
    grid._painted = {}
    grid._row_fill = {}
    grid._check_state = {}
    grid._cell_placed = set()
    grid._header_painted = {}
    grid._geom_dirty = True
    grid._last_width = 0
    grid._wheel_accum = 0
    grid._wheel_job = None
    grid._paint_count = 0
    grid._rows = [{"frame": object(), "cells": {}, "check": None, "index": -1}
                  for _ in range(pool)]
    return grid


def test_shift_paint_cache_keeps_overlap_after_one_row_scroll():
    grid = _bare_grid(pool=4)
    grid._painted = {
        (0, "name"): ("P0", "#fff", 80),
        (1, "name"): ("P1", "#fff", 80),
        (2, "name"): ("P2", "#fff", 80),
        (3, "name"): ("P3", "#fff", 80),
    }
    grid._row_fill = {0: "a", 1: "b", 2: "c", 3: "d"}
    grid._check_state = {-1: ("☐", "#x"), 0: ("☐", "#m"), 1: ("☐", "#m")}
    grid._cell_placed = {(0, "name"), (1, "name"), (2, "name"), (3, "name")}

    grid._shift_paint_cache(1)

    assert grid._painted[(0, "name")] == ("P1", "#fff", 80)
    assert grid._painted[(1, "name")] == ("P2", "#fff", 80)
    assert grid._painted[(2, "name")] == ("P3", "#fff", 80)
    assert (3, "name") not in grid._painted  # newly revealed row
    assert grid._row_fill == {0: "b", 1: "c", 2: "d"}
    assert grid._check_state[-1] == ("☐", "#x")  # header checkbox untouched
    assert (3, "name") not in grid._cell_placed


def test_one_row_scroll_configure_budget_is_one_row():
    """After a 1-row scroll, only the newly revealed slot misses the cache."""
    pool = 14
    cols = ["name", "ovr", "pos", "age", "club", "nat", "role"]
    grid = _bare_grid(pool=pool)
    for slot in range(pool):
        for col in cols:
            grid._painted[(slot, col)] = (f"r{slot}-{col}", "#e6edf5", 80)
            grid._cell_placed.add((slot, col))

    grid._shift_paint_cache(1)

    misses = 0
    for slot in range(pool):
        for col in cols:
            # Simulate the desired content after scrolling so first_index+=1:
            # visual slot s shows what used to be at slot s+1 (cache hit),
            # except the last slot which is new.
            desired = (f"r{slot + 1}-{col}", "#e6edf5", 80) if slot + 1 < pool else (
                f"r{pool}-{col}", "#e6edf5", 80
            )
            if grid._painted.get((slot, col)) != desired:
                misses += 1

    # Only the newly revealed bottom row (7 columns) should miss.
    assert misses == len(cols)


def test_wheel_events_coalesce_to_one_paint():
    grid = _bare_grid()
    paints: list[int] = []
    scrolls: list[int] = []

    class _Widget:
        def after_idle(self, fn):
            self._fn = fn
            return "job-1"

        def after_cancel(self, _job):
            self._fn = None

    widget = _Widget()
    grid.widget = widget

    def _scroll_to(first: int) -> None:
        scrolls.append(first)
        paints.append(1)

    grid._scroll_to = _scroll_to  # type: ignore[method-assign]
    grid._max_first = lambda: 200  # type: ignore[method-assign]

    event = SimpleNamespace(delta=120, num=0)
    for _ in range(5):
        assert grid._on_wheel(event) == "break"

    # Still a single idle job; paint has not run yet.
    assert grid._wheel_job == "job-1"
    assert paints == []
    assert grid._wheel_accum == -5 * WHEEL_ROWS

    grid._flush_wheel()
    assert paints == [1]
    assert scrolls == [-5 * WHEEL_ROWS]
    assert grid._wheel_job is None
    assert grid._wheel_accum == 0


def test_wheel_notch_count_uses_delta_magnitude():
    grid = _bare_grid()
    grid.widget = SimpleNamespace(
        after_idle=lambda fn: "j",
        after_cancel=lambda _j: None,
    )
    grid._on_wheel(SimpleNamespace(delta=240, num=0))  # two notches
    assert grid._wheel_accum == -2 * WHEEL_ROWS


def test_wheel_x11_buttons():
    grid = _bare_grid()
    grid.widget = SimpleNamespace(
        after_idle=lambda fn: "j",
        after_cancel=lambda _j: None,
    )
    grid._on_wheel(SimpleNamespace(delta=0, num=4))  # scroll up
    assert grid._wheel_accum == -WHEEL_ROWS
    grid._wheel_accum = 0
    grid._wheel_job = None
    grid._on_wheel(SimpleNamespace(delta=0, num=5))  # scroll down
    assert grid._wheel_accum == WHEEL_ROWS


@pytest.mark.gui
@pytest.mark.skipif(
    __import__("importlib").util.find_spec("customtkinter") is None,
    reason="customtkinter not installed",
)
def test_datagrid_scroll_configure_count_real_widgets():
    """Opt-in real-Tk check: one-row scroll configures ~one row of cells."""
    import customtkinter as ctk

    from companion.ui.widgets.primitives import ensure_ctk

    ensure_ctk()
    root = ctk.CTk()
    root.withdraw()
    try:
        cols = [
            Column("name", "Name", width=120, stretch=True),
            Column("ovr", "OVR", width=40, numeric=True),
            Column("pos", "Pos", width=40),
            Column("age", "Age", width=40, numeric=True),
            Column("club", "Club", width=80),
            Column("nat", "Nat", width=40),
            Column("role", "Role", width=60),
        ]
        model = TableModel(cols, multi_select=False)
        model.set_rows(
            [
                {
                    "name": f"Player {i}",
                    "ovr": 70 + (i % 20),
                    "pos": "CM",
                    "age": 20 + (i % 15),
                    "club": "Club",
                    "nat": "XX",
                    "role": "Starter",
                    "playerid": i,
                }
                for i in range(200)
            ]
        )
        grid = DataGrid(root, model, visible_rows=14, show_checkboxes=False)
        root.update_idletasks()

        configure_calls = {"n": 0}
        for row in grid._rows:
            for cell in row["cells"].values():
                original = cell.configure

                def _counting_configure(*args, _orig=original, **kwargs):
                    configure_calls["n"] += 1
                    return _orig(*args, **kwargs)

                cell.configure = _counting_configure  # type: ignore[method-assign]

        configure_calls["n"] = 0
        before = grid._paint_count
        grid._scroll_to(grid.first_index + 1)
        assert grid._paint_count == before + 1
        # One newly revealed row × 7 columns, with a little slack for zebra/fill.
        assert configure_calls["n"] <= len(cols) + 4

        configure_calls["n"] = 0
        paint_before = grid._paint_count
        for _ in range(8):
            grid._on_wheel(SimpleNamespace(delta=-120, num=0))
        root.update_idletasks()
        # Coalesced: a single paint for the burst.
        assert grid._paint_count == paint_before + 1

        # The text sitting on a name/OVR cell must own the wheel binding.
        # Binding only the row frame leaves those cells dead to the wheel.
        sample = grid._rows[0]["cells"]["name"]
        for hit in (sample._label, sample._canvas, grid._rows[0]["frame"]._canvas):
            script = hit.bind("<MouseWheel>")
            assert script, hit
        root.deiconify()
        root.geometry("480x320+4000+4000")
        try:
            root.attributes("-alpha", 0)
        except Exception:
            pass
        root.update()
        before_index = grid.first_index
        sample._label.event_generate("<MouseWheel>", delta=-120, x=4, y=4)
        root.update()
        assert grid.first_index > before_index
    finally:
        try:
            root.destroy()
        except Exception:
            pass
