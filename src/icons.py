"""Load project icons (assets/icons/*.png) for CustomTkinter — performance-first.

Root cause of lag: previous path created a NEW CTkImage on every call (PhotoImage
churn) and re-opened large sheet PNGs with LANCZOS on the UI thread.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Tuple

from . import paths

try:
    from PIL import Image
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ctk = None  # type: ignore

# Master switch — set False to force text-only UI (debug freezes)
ICONS_ENABLED = True

# Never load these as UI icons (photo logos / heavy assets)
_BLOCKED = frozenset({"app", "app_alt", "app_icon", "app_icon_bright", "app_icon_source"})

_icons_dir_cache: Optional[Path] = None
_ctk_cache: dict[Tuple[str, int], Any] = {}


def icons_dir() -> Path:
    global _icons_dir_cache
    if _icons_dir_cache is not None:
        return _icons_dir_cache
    candidates = [
        paths.app_root() / "assets" / "icons",
        paths.resource_root() / "assets" / "icons",
    ]
    for d in candidates:
        try:
            if d.is_dir() and next(d.glob("*.png"), None) is not None:
                _icons_dir_cache = d
                return d
        except OSError:
            continue
    _icons_dir_cache = candidates[0]
    return _icons_dir_cache


def icon_path(name: str) -> Optional[Path]:
    stem = name.replace(".png", "").strip()
    if not stem or stem.lower() in _BLOCKED:
        return None
    p = icons_dir() / f"{stem}.png"
    return p if p.is_file() else None


def app_ico_path() -> Optional[Path]:
    """Window .ico only (not in-UI logo)."""
    for base in (paths.app_root(), paths.resource_root()):
        for name in ("app.ico", "app_alt.ico"):
            p = base / "assets" / name
            if p.is_file():
                return p
    return None


@lru_cache(maxsize=256)
def _pil(name: str, size: Tuple[int, int]) -> Any:
    if Image is None:
        return None
    p = icon_path(name)
    if not p:
        return None
    try:
        im = Image.open(p)
        # Load once; convert only if needed
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        else:
            im.load()
        w, h = im.size
        tw, th = size
        if (w, h) != (tw, th):
            # BILINEAR is far cheaper than LANCZOS; fine for 16–28px chrome icons
            im = im.resize((tw, th), Image.Resampling.BILINEAR)
        return im
    except Exception:
        return None


def ctk_icon(name: str, size: int = 20) -> Any:
    """Return cached CTkImage or None. Safe to call from UI thread repeatedly."""
    if not ICONS_ENABLED or ctk is None or Image is None or not name:
        return None
    stem = name.replace(".png", "").strip()
    if stem.lower() in _BLOCKED:
        return None
    # Cap size — huge icons thrash GPU/Tk
    size = max(12, min(int(size), 32))
    key = (stem, size)
    hit = _ctk_cache.get(key)
    if hit is not None:
        return hit
    im = _pil(stem, (size, size))
    if im is None:
        return None
    try:
        img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
        _ctk_cache[key] = img
        return img
    except Exception:
        return None


def clear_icon_cache() -> None:
    global _icons_dir_cache
    _ctk_cache.clear()
    _pil.cache_clear()
    _icons_dir_cache = None


def list_icon_names() -> list[str]:
    d = icons_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.png") if p.stem.lower() not in _BLOCKED)
