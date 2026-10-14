"""Cached PNG icons from ``assets/icons`` for CustomTkinter chrome.

Mirrors the v1 performance rule: one CTkImage per (name, size), never rebuild
PhotoImages on every paint. Safe to call with no display / no Pillow — returns
``None`` so callers can fall back to a text glyph.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ctk = None  # type: ignore[assignment]

_CACHE: dict[tuple[Any, ...], Any] = {}


def _root_id() -> int:
    """CTkImage photo images belong to one Tk interpreter; never share them across roots."""
    try:
        import tkinter

        return id(tkinter._default_root)
    except Exception:
        return 0
_DIR: Path | None = None


def icons_dir() -> Path:
    global _DIR
    if _DIR is not None:
        return _DIR
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "assets" / "icons")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets" / "icons")
        # onedir layout: assets may live next to _internal_v2
        candidates.append(exe_dir / "_internal_v2" / "assets" / "icons")
    # Dev checkout: companion/ui/icons.py → project root
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / "icons")
    for path in candidates:
        try:
            if path.is_dir() and next(path.glob("*.png"), None) is not None:
                _DIR = path
                return path
        except OSError:
            continue
    _DIR = candidates[-1]
    return _DIR


def _tinted(im: Any, tint: str) -> Any:
    """The pack is drawn in one cyan; any token colour is the same alpha mask."""
    s = tint.lstrip("#")
    rgb = tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))
    solid = Image.new("RGBA", im.size, (*rgb, 255))
    solid.putalpha(im.getchannel("A"))
    return solid


def ctk_icon(name: str, size: int = 18, *, tint: str = "") -> Any:
    """Return a cached ``CTkImage`` for ``assets/icons/<name>.png``, or None.

    ``tint`` recolours the stroke to a theme token, so inactive navigation can be
    muted and an active item can carry the accent without a second icon set.
    The source is reduced to twice the display size with Lanczos; CTk then
    scales it for the display, which keeps 2 px strokes crisp at 100-200 %.
    """
    if ctk is None or Image is None or not name:
        return None
    stem = str(name).replace(".png", "").strip()
    if not stem:
        return None
    size = max(12, min(int(size), 48))
    key = (stem, size, tint, _root_id())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    path = icons_dir() / f"{stem}.png"
    if not path.is_file():
        return None
    try:
        im = Image.open(path)
        im = im.convert("RGBA") if im.mode != "RGBA" else im.copy()
        if tint:
            im = _tinted(im, tint)
        if im.size != (size * 2, size * 2):
            im = im.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
        img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
    except Exception:
        return None
    _CACHE[key] = img
    return img


def check_image(checked: bool, *, size: int = 16, box: str = "", fill: str = "",
                mark: str = "") -> Any:
    """A drawn checkbox for pooled grid rows, where a CTkCheckBox per cell is too heavy.

    Colours are passed in by the caller from ``theme`` so this module stays
    free of colour literals.
    """
    if ctk is None or Image is None:
        return None
    key = (f"check:{int(checked)}:{box}:{fill}:{mark}", size, "", _root_id())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from PIL import ImageDraw

        def rgb(token: str) -> tuple[int, int, int, int]:
            s = token.lstrip("#")
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)

        big = size * 8
        im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        inset, radius, stroke = big // 8, big // 5, max(2, big // 11)
        rect = (inset, inset, big - inset, big - inset)
        if checked:
            d.rounded_rectangle(rect, radius=radius, fill=rgb(fill))
            pts = [(big * 0.30, big * 0.52), (big * 0.44, big * 0.66), (big * 0.71, big * 0.36)]
            d.line(pts, fill=rgb(mark), width=stroke * 3 // 2, joint="curve")
        else:
            d.rounded_rectangle(rect, radius=radius, outline=rgb(box), width=stroke)
        im = im.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
        img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
    except Exception:
        return None
    _CACHE[key] = img
    return img


def clear_icon_cache() -> None:
    global _DIR
    _CACHE.clear()
    _DIR = None
