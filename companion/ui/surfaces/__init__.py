"""The primary surfaces + Activity drawer.

Each module exposes ``build(parent, svc, vm=None) -> widget``. The registry
(``companion.ui.nav``) imports them lazily so a broken surface cannot take
the window down at cold start.
"""

from __future__ import annotations

__all__ = [
    "activity",
    "add_player",
    "automations",
    "club",
    "injury",
    "library",
    "player",
]
