"""Player's AI mode offers Club's planner requests and shares saved requests with it."""

from __future__ import annotations

import importlib.util
import json

import pytest

from companion.app.commands.player import lock_player
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.ai_requests import (
    CLUB_REQUESTS,
    PLAYER_BUILD_IDEAS,
    PLAYER_REQUESTS,
    load_requests,
    player_requests,
    save_request,
    saved_requests_path,
)
from companion.ui.surfaces import planner_controller


def test_club_planner_keeps_its_requests() -> None:
    assert planner_controller.REQUEST_PRESETS is CLUB_REQUESTS
    assert list(CLUB_REQUESTS) == [
        "Skills and quick step", "Improve playstyles", "Shirts only", "Young prospects",
    ]
    assert planner_controller.load_requests is load_requests
    assert planner_controller.save_request is save_request


def test_player_requests_are_club_requests_worded_for_one_player() -> None:
    assert {"Skills and quick step", "Improve playstyles", "Young prospect"} == set(PLAYER_REQUESTS)
    # A single-player proposal cannot change squad numbers, so no shirt work.
    assert "Shirts only" not in PLAYER_REQUESTS
    for text in PLAYER_REQUESTS.values():
        lowered = text.lower()
        for group_words in ("jersey", "shirt", "these players", "all players"):
            assert group_words not in lowered
    assert "Quick Step+" in PLAYER_REQUESTS["Skills and quick step"]
    assert "no ceiling" in PLAYER_REQUESTS["Improve playstyles"]
    assert "19" in PLAYER_REQUESTS["Young prospect"]


def test_player_request_order_is_club_then_ideas_then_saved() -> None:
    items = player_requests({"Messi 10": "make him a false nine"})
    groups = [item.group for item in items]
    assert groups == (
        ["club"] * len(PLAYER_REQUESTS) + ["idea"] * len(PLAYER_BUILD_IDEAS) + ["saved"]
    )
    assert (items[-1].name, items[-1].text) == ("Messi 10", "make him a false nine")
    assert all(item.text.strip() for item in items)


def test_saved_requests_are_one_file_for_both_surfaces(tmp_path) -> None:
    path = saved_requests_path(tmp_path)
    assert path.name == "planner_requests.json"
    save_request(path, "  Pace merchant ", " make him faster ")
    # Club's planner reads what Player saved, and the other way round.
    assert planner_controller.load_requests(path) == {"Pace merchant": "make him faster"}
    planner_controller.save_request(path, "Wall", "boost defending")
    assert load_requests(path) == {"Pace merchant": "make him faster", "Wall": "boost defending"}
    assert json.loads(path.read_text(encoding="utf-8"))["Wall"] == "boost defending"


def test_saving_needs_a_name_and_a_request(tmp_path) -> None:
    path = saved_requests_path(tmp_path)
    with pytest.raises(ValueError):
        save_request(path, " ", "make him faster")
    with pytest.raises(ValueError):
        save_request(path, "Pace", "  ")
    assert not path.exists()


def test_a_broken_saved_file_reads_as_empty(tmp_path) -> None:
    path = saved_requests_path(tmp_path)
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_requests(path) == {}
    path.write_text("{not json", encoding="utf-8")
    assert load_requests(path) == {}
    path.write_text('{"ok": "go", "blank": "  ", "num": 3}', encoding="utf-8")
    assert load_requests(path) == {"ok": "go"}


@pytest.mark.gui
@pytest.mark.skipif(
    importlib.util.find_spec("customtkinter") is None,
    reason="customtkinter not installed",
)
def test_player_ai_mode_lists_requests_and_fills_the_prompt(tmp_path) -> None:
    import customtkinter as ctk

    from companion.ui.surfaces import player

    root = ctk.CTk()
    root.geometry("800x600")
    svc = Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
    )
    save_request(saved_requests_path(svc.paths.root), "Weak foot", "Give him a 5 star weak foot.")
    lock_player(
        svc,
        158023,
        record={"playerid": 158023, "name": "Lionel Messi", "position": "RW",
                "overallrating": 88, "potential": 90},
    )

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    try:
        view = {"player_source": "presets"}
        surface = player.build(root, svc, view)
        surface.pack(fill="both", expand=True)
        root.update()
        tiles = {w._request.name: w for w in walk(surface) if hasattr(w, "_request")}
        assert {"Skills and quick step", "Improve playstyles", "Young prospect",
                *PLAYER_BUILD_IDEAS, "Weak foot"} == set(tiles)
        assert tiles["Weak foot"]._request.group == "saved"
        assert tiles["Skills and quick step"]._request.group == "club"
        words = {str(w.cget("text")) for w in walk(tiles["Weak foot"]) if isinstance(w, ctk.CTkLabel)}
        assert "Saved" in words

        prompt = next(w for w in walk(surface) if isinstance(w, ctk.CTkEntry)
                      and "era/physique" in str(w.cget("placeholder_text") or ""))
        # A click is bound on the tile's canvas and on its labels.
        assert tiles["Improve playstyles"]._canvas.bind("<Button-1>")
        tiles["Improve playstyles"]._activate()
        root.update()
        assert prompt.get() == PLAYER_REQUESTS["Improve playstyles"]
        assert view["grok_prompt"] == PLAYER_REQUESTS["Improve playstyles"]
        assert str(tiles["Improve playstyles"].cget("border_color")) != str(
            tiles["Young prospect"].cget("border_color")
        )
        # Keyboard reach: the tile frame itself takes focus and answers Enter.
        assert str(tiles["Weak foot"].tk.call(str(tiles["Weak foot"]), "cget", "-takefocus")) == "1"
        assert tiles["Weak foot"].tk.call("bind", str(tiles["Weak foot"]), "<Return>")
    finally:
        root.destroy()
