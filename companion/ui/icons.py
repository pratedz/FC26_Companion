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

_CACHE: dict[tuple[str, int], Any] = {}
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


def ctk_icon(name: str, size: int = 18) -> Any:
    """Return a cached ``CTkImage`` for ``assets/icons/<name>.png``, or None."""
    if ctk is None or Image is None or not name:
        return None
    stem = str(name).replace(".png", "").strip()
    if not stem:
        return None
    size = max(12, min(int(size), 32))
    key = (stem, size)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    path = icons_dir() / f"{stem}.png"
    if not path.is_file():
        return None
    try:
        im = Image.open(path)
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        else:
            im.load()
        if im.size != (size, size):
            im = im.resize((size, size), Image.Resampling.BILINEAR)
        img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
    except Exception:
        return None
    _CACHE[key] = img
    return img


def clear_icon_cache() -> None:
    global _DIR
    _CACHE.clear()
    _DIR = None
