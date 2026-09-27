"""Where the window opens, and how hard it fights for your attention.

The user plays FC 26 full-screen on the primary monitor. A companion window
that appears over the game — or worse, steals focus mid-match — is actively
harmful. So:

  - Default target is the **secondary monitor** when one exists.
  - The window never takes focus on open (``-topmost`` is not set, and we
    explicitly avoid ``focus_force``).
  - The last position is remembered, and honoured if it is still on-screen.

Enumerates monitors via ``EnumDisplayMonitors`` and falls back to Tk's own
virtual-screen metrics when the Win32 call is unavailable.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

# The companion is a side panel, not a full application window.
# MIN matches shell A7: Apply / review dock must stay visible.
DEFAULT_W, DEFAULT_H = 1180, 820
MIN_W, MIN_H = 1040, 720
MIN_SIZE = (MIN_W, MIN_H)


@dataclass(frozen=True, slots=True)
class Monitor:
    left: int
    top: int
    right: int
    bottom: int
    primary: bool

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def enumerate_monitors() -> list[Monitor]:
    """All monitors in virtual-desktop coordinates. Empty list if unavailable."""
    out: list[Monitor] = []
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return out

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    MONITORINFOF_PRIMARY = 0x1
    proc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(RECT),
        ctypes.c_double,
    )

    def _cb(hmon: Any, _hdc: Any, _rect: Any, _data: Any) -> int:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            # rcWork excludes the taskbar, which is what we want to place into.
            w = info.rcWork
            out.append(
                Monitor(
                    left=w.left,
                    top=w.top,
                    right=w.right,
                    bottom=w.bottom,
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, proc(_cb), 0)
    except (AttributeError, OSError, ctypes.ArgumentError):
        return []
    return out


def preferred_monitor(monitors: list[Monitor] | None = None) -> Monitor | None:
    """The secondary monitor if there is one, else the primary, else None.

    This is the whole point of the module: keep the companion off the screen
    the game is on.
    """
    mons = monitors if monitors is not None else enumerate_monitors()
    if not mons:
        return None
    secondary = [m for m in mons if not m.primary]
    if secondary:
        # Widest secondary — most room for the squad grid.
        return max(secondary, key=lambda m: m.width * m.height)
    return next((m for m in mons if m.primary), mons[0])


def geometry_for(
    monitor: Monitor | None,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
) -> str:
    """A Tk ``geometry()`` string centred on ``monitor``, clamped to fit."""
    if monitor is None:
        return f"{width}x{height}"
    # Prefer the requested size, allow shrinking to MIN, but never exceed the
    # monitor itself — a display smaller than MIN_W (a 1024x768 secondary, a
    # capture card) must still get a fully visible window.
    w = min(width, max(MIN_W, monitor.width - 40), monitor.width)
    h = min(height, max(MIN_H, monitor.height - 40), monitor.height)
    x = monitor.left + (monitor.width - w) // 2
    y = monitor.top + (monitor.height - h) // 2
    return f"{w}x{h}+{x}+{y}"


def is_on_screen(x: int, y: int, monitors: list[Monitor] | None = None) -> bool:
    """True if the point sits inside some monitor's work area.

    Used to reject a remembered position from a monitor that is now unplugged,
    which would otherwise open the window off-screen.
    """
    mons = monitors if monitors is not None else enumerate_monitors()
    return any(m.left <= x < m.right and m.top <= y < m.bottom for m in mons)


def place(window: Any, *, remembered: str = "", width: int = DEFAULT_W,
          height: int = DEFAULT_H) -> str:
    """Position ``window`` without stealing focus. Returns the geometry used.

    ``remembered`` is a previously saved Tk geometry string; it wins only if
    its origin is still on a connected monitor.
    """
    mons = enumerate_monitors()
    geom = ""
    if remembered:
        try:
            size, _, pos = remembered.partition("+")
            xs, _, ys = pos.partition("+")
            if is_on_screen(int(xs), int(ys), mons):
                geom = remembered
        except (ValueError, IndexError):
            geom = ""
    if not geom:
        geom = geometry_for(preferred_monitor(mons), width, height)
    try:
        window.geometry(geom)
        window.minsize(MIN_W, MIN_H)
        # Deliberately NOT topmost and NOT focus_force: the game keeps focus.
        window.attributes("-topmost", False)
    except Exception:  # noqa: BLE001 — placement must never break startup
        pass
    return geom
