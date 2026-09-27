"""Sign search results must keep a usable on-screen height.

Real-Tk checks are gated like ``test_ui_shell`` (``@needs_tk`` / ``gui``).
Source guards always run so a headless suite still catches the collapse
regression and a result grid that is built but never shown.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport

_HAS_CTK = importlib.util.find_spec("customtkinter") is not None
_ROOT = Path(__file__).resolve().parents[2]
_SIGN_INIT = _ROOT / "companion" / "ui" / "surfaces" / "sign" / "__init__.py"
_SIGN_SEARCH = _ROOT / "companion" / "ui" / "surfaces" / "sign" / "search.py"


def needs_tk(fn):
    """Same gate as ``test_ui_shell``: customtkinter present + ``gui`` opt-in."""
    fn = pytest.mark.skipif(not _HAS_CTK, reason="customtkinter not installed")(fn)
    return pytest.mark.gui(fn)


def _services(tmp_path: Path) -> Services:
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
    )


def test_sign_results_source_guards_visibility():
    """Headless guard: keep results beside the bag and show the card grid."""
    init_src = _SIGN_INIT.read_text(encoding="utf-8")
    assert "results.pack_propagate(False)" not in init_src
    assert 'results.pack(fill="both", expand=True)' in init_src
    assert 'on_checkout=lambda: review_rows(sign_list_items(view.get("sign_list")))' in init_src

    from companion.ui.surfaces.sign import search as search_mod

    show_src = inspect.getsource(search_mod.LibrarySearchPanel.show_result_grid)
    assert 'self.grid.pack(fill="both", expand=True' in show_src
    assert _SIGN_SEARCH.is_file()


@needs_tk
def test_sign_results_visible(tmp_path: Path):
    """Fail if the Sign results area lays out with no usable height."""
    import customtkinter as ctk

    from companion.ui.surfaces import add_player
    from companion.ui.surfaces.sign.search import LibrarySearchPanel

    root = ctk.CTk()
    root.geometry("1100x720")
    svc = _services(tmp_path)
    panels: list[LibrarySearchPanel] = []
    real_init = LibrarySearchPanel.__init__

    def _capture(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        panels.append(self)

    LibrarySearchPanel.__init__ = _capture  # type: ignore[method-assign]
    try:
        surface = add_player.build(root, svc)
        surface.pack(fill="both", expand=True)
        root.update()

        assert panels, "LibrarySearchPanel was not constructed by Sign build"
        search = panels[0]
        search.replace_rows([{"name": "Lionel Messi", "overall": 93, "year": 26}])
        root.update()

        assert search.grid.widget.winfo_manager() == "pack"
        assert search.grid.widget.winfo_height() >= 120
        assert search.grid.model.visible_rows()[0]["name"] == "Lionel Messi"
        assert search.grid._rows[0]["cells"]["name"].cget("text") == "Lionel Messi"
    finally:
        LibrarySearchPanel.__init__ = real_init  # type: ignore[method-assign]
        try:
            root.destroy()
        except Exception:
            pass
