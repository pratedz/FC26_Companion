"""Headless wallpaper path, crop, and pref helpers."""

from __future__ import annotations

import pytest

from companion.ui import wallpaper


def test_cover_box_crops_wider_source_on_the_sides():
    assert wallpaper.cover_box(1920, 1080, 800, 800) == (420, 0, 1500, 1080)


def test_cover_box_crops_taller_source_top_and_bottom():
    assert wallpaper.cover_box(800, 800, 1920, 1080) == (0, 175, 800, 625)


def test_cover_box_matching_aspect_uses_the_full_source():
    assert wallpaper.cover_box(1920, 1080, 1280, 720) == (0, 0, 1920, 1080)


def test_cover_box_rejects_zero_dimensions():
    box = wallpaper.cover_box(0, 0, 0, 0)
    assert box[2] > box[0] and box[3] > box[1]


def test_wallpaper_pref_defaults_on():
    assert wallpaper.pref_enabled(None) is True
    assert wallpaper.pref_enabled({}) is True
    assert wallpaper.pref_enabled({"wallpaper": True}) is True
    assert wallpaper.pref_enabled({"wallpaper": False}) is False
    assert wallpaper.pref_enabled({"wallpaper": "off"}) is False
    assert wallpaper.pref_enabled({"wallpaper": "0"}) is False


def test_shipped_stadium_art_resolves():
    wallpaper.clear_wallpaper_cache()
    path = wallpaper.resolve_wallpaper_path()
    assert path is not None
    assert path.name == wallpaper.WALLPAPER_NAME
    assert path.is_file()


def test_atmosphere_darkens_the_source():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow is required for atmosphere processing")
    src = Image.new("RGB", (32, 32), (180, 180, 180))
    out = wallpaper.apply_atmosphere(src, brightness=0.45)
    pixel = out.getpixel((16, 16))
    assert max(pixel) < 180
    corner = out.getpixel((0, 0))
    assert sum(corner) <= sum(pixel)


def test_shell_wires_wallpaper_pref_and_settings_toggle():
    from pathlib import Path

    shell = Path("companion/ui/shell.py").read_text(encoding="utf-8")
    assert "self._wallpaper.mount()" in shell
    assert "self._apply_wallpaper(self._pref_wallpaper())" in shell
    assert 'PrefChanged("wallpaper"' in shell
    assert "Stadium wallpaper" in shell
    assert 'fg_color="transparent" if on else theme.BG' in shell
