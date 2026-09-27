"""One night-stadium wallpaper behind the HUD.

CustomTkinter cannot blur or composite glass. The premium look is a dim
original photo placed behind solid chrome and cards. This module is import-safe
without Tk or Pillow: path/crop/pref helpers stay pure; the layer fails soft
and leaves ``theme.BG``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from . import theme

try:
    from PIL import Image, ImageChops, ImageEnhance
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageChops = None  # type: ignore[assignment]
    ImageEnhance = None  # type: ignore[assignment]

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

WALLPAPER_NAME = "night_stadium.jpg"
BRIGHTNESS = 0.45
RESIZE_MS = 80

_DIR: Path | None = None
_PREPARED: Any = None


def wallpaper_dir() -> Path:
    """Folder that should contain ``night_stadium.jpg`` (dev, frozen, onedir)."""
    global _DIR
    if _DIR is not None:
        return _DIR
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "assets" / "wallpaper")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets" / "wallpaper")
        candidates.append(exe_dir / "_internal_v2" / "assets" / "wallpaper")
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / "wallpaper")
    for path in candidates:
        try:
            if (path / WALLPAPER_NAME).is_file():
                _DIR = path
                return path
        except OSError:
            continue
    _DIR = candidates[-1]
    return _DIR


def resolve_wallpaper_path() -> Path | None:
    path = wallpaper_dir() / WALLPAPER_NAME
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def pref_enabled(values: Mapping[str, Any] | None) -> bool:
    """Default on. Only an explicit off-like value disables the picture."""
    if not values:
        return True
    raw = values.get("wallpaper", True)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "off", "no"}
    return bool(raw)


def cover_box(
    src_w: int, src_h: int, dst_w: int, dst_h: int
) -> tuple[int, int, int, int]:
    """Source crop rectangle that fills ``dst`` without letterboxing."""
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    dst_w = max(1, int(dst_w))
    dst_h = max(1, int(dst_h))
    src_aspect = src_w / src_h
    dst_aspect = dst_w / dst_h
    if src_aspect > dst_aspect:
        new_w = max(1, int(round(src_h * dst_aspect)))
        left = max(0, (src_w - new_w) // 2)
        return (left, 0, min(src_w, left + new_w), src_h)
    new_h = max(1, int(round(src_w / dst_aspect)))
    top = max(0, (src_h - new_h) // 2)
    return (0, top, src_w, min(src_h, top + new_h))


def apply_atmosphere(image: Any, *, brightness: float = BRIGHTNESS) -> Any:
    """Darken and vignette so the photo is atmosphere, not a poster."""
    if Image is None or ImageEnhance is None or ImageChops is None:
        return image
    im = image.convert("RGB")
    im = ImageEnhance.Brightness(im).enhance(max(0.15, min(1.0, float(brightness))))
    mask = _vignette_mask(im.size)
    return ImageChops.multiply(im, Image.merge("RGB", (mask, mask, mask)))


def crop_cover(image: Any, width: int, height: int) -> Any:
    box = cover_box(image.size[0], image.size[1], width, height)
    cropped = image.crop(box)
    if cropped.size != (width, height) and Image is not None:
        cropped = cropped.resize((width, height), Image.Resampling.BILINEAR)
    return cropped


def load_prepared() -> Any:
    """Cached darkened source image, or None when art/Pillow is missing."""
    global _PREPARED
    if _PREPARED is not None:
        return _PREPARED
    if Image is None:
        return None
    path = resolve_wallpaper_path()
    if path is None:
        return None
    try:
        im = Image.open(path)
        im.load()
        _PREPARED = apply_atmosphere(im)
        return _PREPARED
    except Exception:
        return None


def clear_wallpaper_cache() -> None:
    global _DIR, _PREPARED
    _DIR = None
    _PREPARED = None


def _vignette_mask(size: tuple[int, int]) -> Any:
    """Soft radial falloff: centre stays, corners sink into the HUD black."""
    small = Image.new("L", (64, 64))
    pixels = small.load()
    for y in range(64):
        for x in range(64):
            dx = (x - 31.5) / 31.5
            dy = (y - 31.5) / 31.5
            falloff = min(1.0, (dx * dx + dy * dy) ** 0.5)
            pixels[x, y] = int(255 * max(0.28, 1.0 - 0.58 * falloff))
    return small.resize(size, Image.Resampling.BICUBIC)


class WallpaperLayer:
    """Full-window ``place``d label. Missing art leaves the solid background."""

    def __init__(self, parent: Any) -> None:
        self.parent = parent
        self.label: Any = None
        self.enabled = False
        self._source: Any = None
        self._ctk: Any = None
        self._size = (0, 0)
        self._on_resize: Any = None

    def mount(self) -> None:
        if ctk is None:
            return
        self._source = load_prepared()
        if self._source is None:
            return
        self.label = ctk.CTkLabel(
            self.parent, text="", fg_color=theme.BG, anchor="center",
        )
        try:
            self.label.lower()
        except Exception:
            pass
        from .widgets.primitives import debounce

        self._on_resize = debounce(self.parent, RESIZE_MS, self.refresh)
        try:
            self.parent.bind("<Configure>", self._on_resize, add="+")
        except Exception:
            pass

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on) and self.label is not None
        if self.label is None:
            return
        if self.enabled:
            try:
                self.label.place(x=0, y=0, relwidth=1, relheight=1)
                self.label.lower()
            except Exception:
                pass
            self._size = (0, 0)
            self.refresh()
        else:
            try:
                self.label.place_forget()
            except Exception:
                pass

    def refresh(self, *_args: Any, **_kwargs: Any) -> None:
        if not self.enabled or self.label is None or self._source is None or ctk is None:
            return
        try:
            width = max(int(self.parent.winfo_width()), 1)
            height = max(int(self.parent.winfo_height()), 1)
        except Exception:
            return
        if width < 8 or height < 8:
            return
        if (width, height) == self._size:
            return
        try:
            cropped = crop_cover(self._source, width, height)
            self._ctk = ctk.CTkImage(
                light_image=cropped, dark_image=cropped, size=(width, height),
            )
            self.label.configure(image=self._ctk)
            self._size = (width, height)
        except Exception:
            return
