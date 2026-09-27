"""The selected Player editor must fit in the windowed Companion viewport."""

from __future__ import annotations

import importlib.util

import pytest

from companion.app.commands.player import lock_player
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport


@pytest.mark.gui
@pytest.mark.skipif(
    importlib.util.find_spec("customtkinter") is None,
    reason="customtkinter not installed",
)
def test_player_modes_show_primary_controls_without_fullscreen(tmp_path):
    import customtkinter as ctk

    from companion.ui.surfaces import player

    root = ctk.CTk()
    root.geometry("800x600")  # roughly the Player content area at the app minimum
    svc = Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
    )
    lock_player(
        svc,
        158023,
        record={
            "playerid": 158023,
            "name": "Lionel Messi",
            "position": "RW",
            "overallrating": 88,
            "potential": 90,
        },
    )

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    def control(text):
        return next(
            widget
            for widget in walk(surface)
            if isinstance(widget, (ctk.CTkButton, ctk.CTkLabel))
            and str(widget.cget("text") or "") == text
            and widget.winfo_ismapped()
        )

    def visible(text):
        widget = control(text)
        bottom = widget.winfo_rooty() - root.winfo_rooty() + widget.winfo_height()
        assert bottom <= root.winfo_height(), f"{text} is below the window ({bottom}px)"

    try:
        view = {}
        surface = player.build(root, svc, view)
        surface.pack(fill="both", expand=True)
        root.update()
        assert not any(
            isinstance(widget, ctk.CTkLabel)
            and str(widget.cget("text") or "") == "Player information"
            for widget in walk(surface)
        )
        for label in ("Edit Lionel Messi", "Card Library", "Search cards", "Stage card & review now"):
            visible(label)
        assert view["player_source"] == "cards"
        assert not any(
            isinstance(widget, (ctk.CTkButton, ctk.CTkLabel))
            and str(widget.cget("text") or "") in {"Manual Edit", "Use preset"}
            for widget in walk(surface)
        )

        control("AI").invoke()
        root.update()
        visible("Propose changes")
        assert view["player_source"] == "presets"
    finally:
        root.destroy()
