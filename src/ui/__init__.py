"""Modular CustomTkinter UI package for LE Companion."""

from __future__ import annotations

from . import apply_chrome, apply_flow, chrome, player_edit, widgets

__all__ = ["widgets", "player_edit", "apply_chrome", "apply_flow", "chrome"]

# Optional polish modules — import only if present so missing ones never crash.
for _name in ("badge", "skeleton", "stepper", "empty_state", "collapsible"):
    try:
        globals()[_name] = __import__(f"{__name__}.{_name}", fromlist=[_name])
    except ImportError:  # pragma: no cover
        continue
    __all__.append(_name)

del _name
