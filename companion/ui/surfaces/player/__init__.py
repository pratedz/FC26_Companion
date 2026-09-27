"""Player - target, read and stage edits for one real player."""

from __future__ import annotations

from typing import Any

from ....app.presenters import editor_view, header_view
from ... import theme
from .._common import surface_root

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

from .card_picker import _build_card_picker
from .manual import _build_quick_editor
from .presets import grok_player_worker
from .target_picker import (
    _build_target_picker,
    _choose_another_player,
    _has_verified_live_squad,
)
from .workstation import (
    PLAYER_SOURCES,
    _build_page_header,
    _build_session_panel,
    _build_session_toolbar,
    _build_workstation_strip,
)

__all__ = [
    "PLAYER_SOURCES",
    "build",
    "grok_player_worker",
    "_build_card_picker",
    "_build_quick_editor",
    "_build_target_picker",
    "_choose_another_player",
    "_has_verified_live_squad",
]


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    state = svc.store.snapshot()
    target = header_view(state)["target"]
    view = vm if isinstance(vm, dict) else {}
    view.setdefault("squad_query", "")
    view.setdefault("card_query", "")
    view.setdefault("grok_prompt", "")
    view.setdefault("player_source", "cards")
    pending_card = None
    take_card = getattr(getattr(svc, "ui", None), "take_player_card", None)
    if target["locked"] and callable(take_card):
        pending_card = take_card()
    if pending_card:
        view["player_source"] = "cards"
        view["pending_library_card"] = pending_card

    if not target["locked"]:
        _build_target_picker(root, svc, view)
        return root

    ed = editor_view(state)
    _build_page_header(root, svc, target)
    _build_session_toolbar(root, svc, ed)

    # The chosen editing method is the page's main content. The large target
    # information card used to push it below the fold in a windowed app.
    center = ctk.CTkFrame(root, fg_color=theme.BG, corner_radius=0, border_width=0)
    center.pack(fill="both", expand=True)

    def show_source(key: str) -> None:
        if key not in ("cards", "presets"):
            key = "cards"
        view["player_source"] = key
        for child in list(body.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        latest = editor_view(svc.store.snapshot())
        if key == "presets":
            _build_session_panel(body, svc, latest, view)
        else:
            _build_card_picker(body, svc, view)
        session.set_active(key)

    session = _build_workstation_strip(
        center,
        svc,
        ed,
        active=str(view.get("player_source") or "cards"),
        on_select=show_source,
    )
    body = ctk.CTkFrame(center, fg_color=theme.BG, corner_radius=0, border_width=0)
    body.pack(fill="both", expand=True)
    show_source(str(view.get("player_source") or "cards"))
    return root
