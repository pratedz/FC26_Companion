"""pytest plugin: every CTk window a UI test opens is transparent and never takes focus.

UI tests build real Tk windows. On a desktop where FC 26 is running they would
flash over the game and steal focus. Load with ``-p hidden_windows_plugin`` and
``tools/`` on ``PYTHONPATH`` (``tools/redesign_checks.py tests`` does both).
"""

from __future__ import annotations


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        return
    from capture_ui import _patch_windows

    _patch_windows()
