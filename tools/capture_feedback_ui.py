"""Capture full frontend screenshots for the feedback pack (including scrolled regions).

Run from project root:
  python tools/capture_feedback_ui.py

Saves PNGs under feedback/screenshots/
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "feedback" / "screenshots"


def _find_scrollables(widget: Any, found: Optional[List[Any]] = None) -> List[Any]:
    if found is None:
        found = []
    try:
        cls = widget.__class__.__name__
        if cls == "CTkScrollableFrame" or hasattr(widget, "_parent_canvas"):
            found.append(widget)
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            _find_scrollables(child, found)
    except Exception:
        pass
    return found


def _scroll_to(widget: Any, fraction: float) -> None:
    """Move CTkScrollableFrame / canvas to fraction 0..1."""
    try:
        canvas = getattr(widget, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_moveto(max(0.0, min(1.0, fraction)))
            return
    except Exception:
        pass
    try:
        widget.yview_moveto(max(0.0, min(1.0, fraction)))  # type: ignore[attr-defined]
    except Exception:
        pass


def _scroll_listbox(lb: Any, fraction: float) -> None:
    try:
        lb.yview_moveto(max(0.0, min(1.0, fraction)))
    except Exception:
        pass


def _grab(app: Any, path: Path) -> None:
    from PIL import ImageGrab

    app.update_idletasks()
    app.update()
    time.sleep(0.2)
    x = int(app.winfo_rootx())
    y = int(app.winfo_rooty())
    w = int(app.winfo_width())
    h = int(app.winfo_height())
    # Slight inset avoids capturing neighboring OS chrome
    bbox = (x + 2, y + 2, x + max(w, 100) - 2, y + max(h, 100) - 2)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    print(f"  saved {path.name:40} {img.size[0]}x{img.size[1]}")


def _show_tab(app: Any, name: str) -> None:
    app._ensure_tab(name)
    app.tabs.set(name)
    app.update_idletasks()
    app.update()
    time.sleep(0.35)


def _capture_scroll_series(
    app: Any,
    stem: str,
    scroll_widget: Any,
    fractions: Tuple[float, ...] = (0.0, 0.45, 1.0),
) -> None:
    for i, frac in enumerate(fractions):
        _scroll_to(scroll_widget, frac)
        app.update_idletasks()
        app.update()
        time.sleep(0.25)
        suffix = {0.0: "top", 0.45: "mid", 0.5: "mid", 1.0: "bottom"}.get(frac, f"p{i}")
        if len(fractions) == 1:
            _grab(app, OUT / f"{stem}.png")
        else:
            _grab(app, OUT / f"{stem}_{suffix}.png")
    # reset
    _scroll_to(scroll_widget, 0.0)


def main() -> int:
    # Wipe old shots so pack is consistent
    if OUT.is_dir():
        for p in OUT.glob("*.png"):
            try:
                p.unlink()
            except OSError:
                pass
    OUT.mkdir(parents=True, exist_ok=True)

    from src.gui import PremiumApp

    print("Launching LE Companion for capture…")
    app = PremiumApp()
    # Tall window so more content fits; still scroll long tabs
    app.geometry("1280x960+20+20")
    app.minsize(1100, 800)
    try:
        app.attributes("-topmost", True)
    except Exception:
        pass
    app.lift()
    app.focus_force()
    app.update()
    time.sleep(0.6)

    # ── Chrome reference (Home top) ──────────────────────────────────
    print("Home")
    _show_tab(app, "  Home  ")
    scrolls = _find_scrollables(app.tab_home)
    if scrolls:
        _capture_scroll_series(app, "01_home", scrolls[0], (0.0, 0.55, 1.0))
    else:
        _grab(app, OUT / "01_home_top.png")

    # ── Boost ────────────────────────────────────────────────────────
    print("Boost")
    _show_tab(app, "  Boost  ")
    scrolls = _find_scrollables(app.tab_profiles)
    if scrolls:
        _capture_scroll_series(app, "02_boost", scrolls[0], (0.0, 0.5, 1.0))
    else:
        _grab(app, OUT / "02_boost_top.png")

    # ── Cards (multi region: main + edit scroll) ─────────────────────
    print("Cards")
    _show_tab(app, "  Cards  ")
    _grab(app, OUT / "03_cards_main.png")
    # Scroll edit host if present
    edit_scrolls = [
        s for s in _find_scrollables(app.tab_cards)
        if s.winfo_height() and s.winfo_height() < app.winfo_height() * 0.5
    ]
    # Prefer the short edit scrollable at bottom
    if edit_scrolls:
        # capture edit area scrolled
        for i, frac in enumerate((0.0, 0.5, 1.0)):
            _scroll_to(edit_scrolls[-1], frac)
            app.update()
            time.sleep(0.25)
            _grab(app, OUT / f"03_cards_edit_{['top','mid','bottom'][i]}.png")
        _scroll_to(edit_scrolls[-1], 0.0)
    else:
        all_s = _find_scrollables(app.tab_cards)
        if all_s:
            _capture_scroll_series(app, "03_cards_edit", all_s[-1], (0.0, 1.0))

    # ── Squad ────────────────────────────────────────────────────────
    print("Squad")
    _show_tab(app, "  Squad  ")
    _grab(app, OUT / "04_squad_top.png")
    if hasattr(app, "squad_board"):
        _scroll_listbox(app.squad_board, 0.0)
        app.update()
        _grab(app, OUT / "04_squad_list_top.png")
        _scroll_listbox(app.squad_board, 0.55)
        app.update()
        time.sleep(0.2)
        _grab(app, OUT / "04_squad_list_mid.png")
        _scroll_listbox(app.squad_board, 1.0)
        app.update()
        time.sleep(0.2)
        _grab(app, OUT / "04_squad_list_bottom.png")

    # ── Editor ───────────────────────────────────────────────────────
    print("Editor")
    _show_tab(app, "  Editor  ")
    scrolls = _find_scrollables(app.tab_editor)
    if scrolls:
        _capture_scroll_series(app, "05_editor", scrolls[0], (0.0, 0.25, 0.5, 0.75, 1.0))
    else:
        _grab(app, OUT / "05_editor_top.png")

    # ── Catalog ──────────────────────────────────────────────────────
    print("Catalog")
    _show_tab(app, "  Catalog  ")
    _grab(app, OUT / "06_catalog.png")

    # ── About ────────────────────────────────────────────────────────
    print("About")
    _show_tab(app, "  About  ")
    _grab(app, OUT / "07_about.png")

    # ── Full chrome callouts on Home top ─────────────────────────────
    print("Chrome overview")
    _show_tab(app, "  Home  ")
    hs = _find_scrollables(app.tab_home)
    if hs:
        _scroll_to(hs[0], 0.0)
    app.update()
    time.sleep(0.2)
    _grab(app, OUT / "00_chrome_header_ops_apply_dock.png")

    try:
        app.attributes("-topmost", False)
    except Exception:
        pass
    app.destroy()

    n = len(list(OUT.glob("*.png")))
    print(f"\nDone: {n} screenshots → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
