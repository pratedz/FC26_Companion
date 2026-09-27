"""Window placement: the companion must never open over the game.

None of these tests open a window — they exercise the geometry maths against
synthetic monitor layouts, including the real one on this machine (a secondary
monitor at negative X, which is the layout that breaks naive placement code).
"""

from __future__ import annotations

import re

from companion.ui.placement import (
    DEFAULT_H,
    DEFAULT_W,
    MIN_H,
    MIN_W,
    Monitor,
    enumerate_monitors,
    geometry_for,
    is_on_screen,
    preferred_monitor,
)

PRIMARY = Monitor(0, 0, 1920, 1040, primary=True)
LEFT_SECONDARY = Monitor(-1920, 1, 0, 1041, primary=False)   # this machine
RIGHT_SECONDARY = Monitor(1920, 0, 3840, 1040, primary=False)
SMALL_SECONDARY = Monitor(1920, 0, 2720, 640, primary=False)

GEOM_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


def _parse(geom: str) -> tuple[int, int, int, int]:
    m = GEOM_RE.match(geom)
    assert m, f"malformed Tk geometry: {geom!r}"
    return int(m[1]), int(m[2]), int(m[3]), int(m[4])


def test_prefers_secondary_over_primary():
    assert preferred_monitor([PRIMARY, RIGHT_SECONDARY]) is RIGHT_SECONDARY
    assert preferred_monitor([RIGHT_SECONDARY, PRIMARY]) is RIGHT_SECONDARY


def test_prefers_secondary_at_negative_x():
    """The real layout here: monitor 2 sits to the LEFT of primary."""
    assert preferred_monitor([PRIMARY, LEFT_SECONDARY]) is LEFT_SECONDARY


def test_falls_back_to_primary_when_alone():
    assert preferred_monitor([PRIMARY]) is PRIMARY


def test_no_monitors_is_not_a_crash():
    assert preferred_monitor([]) is None
    assert geometry_for(None) == f"{DEFAULT_W}x{DEFAULT_H}"


def test_picks_the_roomiest_secondary():
    assert preferred_monitor([PRIMARY, SMALL_SECONDARY, RIGHT_SECONDARY]) is RIGHT_SECONDARY


def test_window_lands_inside_the_secondary():
    w, h, x, y = _parse(geometry_for(LEFT_SECONDARY))
    assert LEFT_SECONDARY.left <= x
    assert x + w <= LEFT_SECONDARY.right
    assert LEFT_SECONDARY.top <= y
    assert y + h <= LEFT_SECONDARY.bottom


def test_never_lands_on_the_primary():
    """The game is on the primary. Not one pixel of the window may touch it."""
    for secondary in (LEFT_SECONDARY, RIGHT_SECONDARY):
        w, _h, x, _y = _parse(geometry_for(secondary))
        assert x + w <= PRIMARY.left or x >= PRIMARY.right


def test_clamps_to_a_small_monitor():
    """A monitor narrower than MIN_W still gets a fully visible window."""
    w, h, x, y = _parse(geometry_for(SMALL_SECONDARY, 2400, 1600))
    assert w <= SMALL_SECONDARY.width and h <= SMALL_SECONDARY.height
    assert x >= SMALL_SECONDARY.left and y >= SMALL_SECONDARY.top
    assert x + w <= SMALL_SECONDARY.right and y + h <= SMALL_SECONDARY.bottom


def test_normal_monitor_respects_minimums():
    w, h, _x, _y = _parse(geometry_for(RIGHT_SECONDARY))
    assert w >= MIN_W and h >= MIN_H


def test_is_on_screen():
    mons = [PRIMARY, LEFT_SECONDARY]
    assert is_on_screen(100, 100, mons)
    assert is_on_screen(-1000, 500, mons)
    assert not is_on_screen(5000, 5000, mons)
    assert not is_on_screen(-5000, 0, mons)


def test_real_machine_layout_is_readable():
    """Not an assertion about hardware — just that enumeration never throws."""
    mons = enumerate_monitors()
    assert isinstance(mons, list)
    for m in mons:
        assert m.width > 0 and m.height > 0
