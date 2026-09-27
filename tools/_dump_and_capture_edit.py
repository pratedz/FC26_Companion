"""Supplemental captures: expand Cards edit strip + Editor scroll positions."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "feedback" / "screenshots"

from PIL import ImageGrab  # noqa: E402
from src.gui import PremiumApp  # noqa: E402


def dump(w, depth=0, maxd=7):
    if depth > maxd:
        return
    try:
        name = w.__class__.__name__
        info = f"{name} h={w.winfo_height()} w={w.winfo_width()}"
        if hasattr(w, "_parent_canvas"):
            info += " HAS_CANVAS"
        print("  " * depth + info)
    except Exception as e:
        print("  " * depth + repr(e))
    try:
        for c in w.winfo_children():
            dump(c, depth + 1, maxd)
    except Exception:
        pass


def find_with_canvas(w, out=None):
    out = out if out is not None else []
    if hasattr(w, "_parent_canvas"):
        out.append(w)
    try:
        for c in w.winfo_children():
            find_with_canvas(c, out)
    except Exception:
        pass
    return out


def grab(app, name, sf=None, frac=None):
    if sf is not None and frac is not None:
        try:
            sf._parent_canvas.yview_moveto(frac)
        except Exception:
            pass
        app.update()
        time.sleep(0.3)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    img = ImageGrab.grab(bbox=(x + 2, y + 2, x + w - 2, y + h - 2), all_screens=True)
    path = OUT / name
    img.save(path)
    print("saved", name, img.size)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = PremiumApp()
    app.geometry("1280x1000+10+10")
    try:
        app.attributes("-topmost", True)
    except Exception:
        pass
    app.lift()
    app.update()
    time.sleep(0.5)

    app._ensure_tab("  Cards  ")
    app.tabs.set("  Cards  ")
    app.update()
    time.sleep(0.4)
    print("=== Cards tree ===")
    dump(app.tab_cards)

    canvases = find_with_canvas(app.tab_cards)
    print("canvases", len(canvases), [c.winfo_height() for c in canvases])

    # Expand short scroll hosts so attribute grids are visible
    for s in canvases:
        try:
            s.configure(height=560)
        except Exception as e:
            print("expand fail", e)
    app.update()
    time.sleep(0.35)

    target = canvases[-1] if canvases else None
    grab(app, "03_cards_edit_expanded_top.png", target, 0.0)
    grab(app, "03_cards_edit_expanded_mid.png", target, 0.35)
    grab(app, "03_cards_edit_expanded_attrs.png", target, 0.15)
    grab(app, "03_cards_edit_expanded_bottom.png", target, 1.0)

    app._ensure_tab("  Editor  ")
    app.tabs.set("  Editor  ")
    app.update()
    time.sleep(0.4)
    print("=== Editor tree ===")
    dump(app.tab_editor, maxd=5)
    es = find_with_canvas(app.tab_editor)
    print("editor canvases", len(es))
    if es:
        grab(app, "05_editor_topics_top.png", es[0], 0.0)
        grab(app, "05_editor_attrs.png", es[0], 0.18)
        grab(app, "05_editor_playstyles.png", es[0], 0.32)
        grab(app, "05_editor_body.png", es[0], 0.48)
        grab(app, "05_editor_career_face.png", es[0], 0.85)
        grab(app, "05_editor_end.png", es[0], 1.0)

    app.destroy()
    print("done")


if __name__ == "__main__":
    main()
