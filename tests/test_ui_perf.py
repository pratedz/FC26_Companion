"""Contracts that keep tab/scroll UI from freezing (real shipped ui_perf)."""

from __future__ import annotations

import inspect
import time
import unittest

from src import ui_perf


class UiPerfContracts(unittest.TestCase):
    def test_smooth_scroll_stays_off(self) -> None:
        self.assertFalse(ui_perf.smooth_scroll_is_enabled())
        self.assertFalse(ui_perf.SMOOTH_SCROLL_ENABLED)
        # Public installer is a no-op even if someone asks for enabled=True
        ui_perf.install_smooth_scroll(enabled=True)
        self.assertFalse(ui_perf.smooth_scroll_is_enabled())

    def test_soft_refresh_never_full_update(self) -> None:
        self.assertFalse(ui_perf.soft_refresh_uses_full_update())
        src = inspect.getsource(ui_perf.soft_refresh)
        # Allow docstring/comments that mention update(); ban real call form
        self.assertNotRegex(src, r"(?m)^\s*root\.update\(\)")
        self.assertIn("update_idletasks", src)

    def test_soft_refresh_is_throttled(self) -> None:
        class _Root:
            def __init__(self) -> None:
                self.calls = 0

            def update_idletasks(self) -> None:
                self.calls += 1

        root = _Root()
        # Reset throttle clock
        ui_perf._last_soft = 0.0  # type: ignore[attr-defined]
        ui_perf.soft_refresh(root, force=True)
        self.assertEqual(root.calls, 1)
        ui_perf.soft_refresh(root, force=False)
        ui_perf.soft_refresh(root, force=False)
        # Within SOFT_MIN_INTERVAL only first force + maybe none more
        self.assertEqual(root.calls, 1)
        time.sleep(max(0.05, float(ui_perf.SOFT_MIN_INTERVAL) + 0.02))
        ui_perf.soft_refresh(root, force=False)
        self.assertEqual(root.calls, 2)

    def test_batch_constants_are_small(self) -> None:
        """Idle batches stay small so one click cannot build entire trees."""
        self.assertLessEqual(ui_perf.BOOST_FIRST_BATCH, 4)
        self.assertLessEqual(ui_perf.BOOST_BATCH, 4)
        self.assertGreaterEqual(ui_perf.BOOST_BATCH_GAP_MS, 8)
        self.assertGreaterEqual(ui_perf.SOFT_MIN_INTERVAL, 0.2)
        self.assertGreaterEqual(ui_perf.BRIDGE_TICK_MS, 3000)


class TabBuilderDeferral(unittest.TestCase):
    """Structural: heavy tabs schedule idle work rather than only sync storms."""

    def test_boost_schedules_idle_fill(self) -> None:
        from src.ui.tabs import boost as boost_tab

        src = inspect.getsource(boost_tab._build_profiles_tab)
        self.assertIn("after(", src)
        self.assertIn("_boost_fill_cards", src)
        # Must not call fill_cards synchronously with a large batch only
        self.assertNotIn("app._boost_fill_cards(batch=6", src)

    def test_editor_defers_surface(self) -> None:
        from src.ui.tabs import editor as editor_tab

        src = inspect.getsource(editor_tab._build_editor_tab)
        self.assertIn("after(", src)
        self.assertIn("_finish_editor_surface", src)

    def test_cards_defers_edit_step(self) -> None:
        from src.ui.tabs import cards as cards_tab

        build = inspect.getsource(cards_tab.build_cards_tab)
        show = inspect.getsource(cards_tab.show_cards_edit)
        self.assertIn("_build_browse", build)
        self.assertNotIn("_build_edit_step", build)
        self.assertIn("_build_edit_step", show)

    def test_gui_tab_change_yields(self) -> None:
        import src.gui as gui_mod

        src = inspect.getsource(gui_mod.PremiumApp._on_tab_change)
        self.assertIn("after(", src)
        self.assertIn("_ensure_tab", src)


if __name__ == "__main__":
    unittest.main()
