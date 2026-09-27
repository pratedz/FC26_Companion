"""Test guards for the v2 suite.

**No test may open a window.** The user plays FC 26 on the primary monitor
while this suite runs; a Tk window stealing focus mid-match is unacceptable.
Any test that genuinely needs a real toplevel must be marked ``@pytest.mark.gui``
and is skipped unless ``COMPANION_ALLOW_GUI_TESTS=1`` is set explicitly.

Headless-by-default is also just better testing: the whole point of the v2
architecture is that views render from ``AppState`` via presenters, so the
ViewModels and presenters are testable without a display at all.
"""

from __future__ import annotations

import os

import pytest

ALLOW_GUI = os.environ.get("COMPANION_ALLOW_GUI_TESTS") == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "gui: needs a real Tk toplevel; skipped unless COMPANION_ALLOW_GUI_TESTS=1",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if ALLOW_GUI:
        return
    skip = pytest.mark.skip(
        reason="opens a window; set COMPANION_ALLOW_GUI_TESTS=1 to run"
    )
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_stray_toplevels(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Make an accidental window a loud failure rather than a surprise popup.

    Anything that tries to construct a root window without the ``gui`` marker
    fails the test instead of appearing over the user's game.
    """
    if ALLOW_GUI or "gui" in request.keywords:
        yield
        return

    def _blocked(*_a, **_kw):
        raise RuntimeError(
            "This test tried to open a window. Test the ViewModel/presenter "
            "instead, or mark the test @pytest.mark.gui."
        )

    # Import *before* patching. customtkinter subclasses tkinter.Toplevel at
    # import time; replacing Toplevel with a function first makes
    # `class Dialog(Toplevel)` raise TypeError and breaks every non-GUI test
    # when the suite is collected without a prior customtkinter import.
    mods: list[tuple[object, tuple[str, ...]]] = []
    for mod_name, attrs in (
        ("tkinter", ("Tk", "Toplevel")),
        ("customtkinter", ("CTk", "CTkToplevel")),
    ):
        try:
            mod = __import__(mod_name)
        except Exception:  # noqa: BLE001 — missing or broken GUI deps are fine
            continue
        mods.append((mod, attrs))
    for mod, attrs in mods:
        for attr in attrs:
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, _blocked, raising=False)
    yield
