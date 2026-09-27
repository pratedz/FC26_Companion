"""Resolve paths relative to the LE install and this companion app.

Works for:
  - normal `python main.py`
  - frozen PyInstaller .exe (onefile / onedir)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def app_root() -> Path:
    """Directory containing profiles.json / card_db / queue.

    Frozen: folder that holds the .exe (not the temp _MEIPASS extract).
    Dev: LE_Profile_Executor/.
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Bundled read-only assets (profiles.json fallback when frozen onefile)."""
    if _is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return app_root()


def _looks_like_le_root(p: Path) -> bool:
    try:
        if (p / "FCLiveEditor.DLL").is_file() or (p / "Launcher.exe").is_file():
            return True
        if (p / "lua" / "libs").is_dir() or (p / "lua" / "scripts").is_dir():
            return True
    except OSError:
        return False
    return False


def le_root() -> Path:
    """FC 26 Live Editor install root (walk parents from app folder)."""
    start = app_root()
    if _looks_like_le_root(start):
        return start
    cur = start
    for _ in range(6):
        parent = cur.parent
        if parent == cur:
            break
        if _looks_like_le_root(parent):
            return parent
        cur = parent
    # Fallback: historical layout (companion sits one level under LE)
    return start.parent


def profiles_path() -> Path:
    # Prefer writable next to exe, fall back to bundled resource
    p = app_root() / "profiles.json"
    if p.is_file():
        return p
    bundled = resource_root() / "profiles.json"
    if bundled.is_file():
        return bundled
    return p


def queue_dir() -> Path:
    d = app_root() / "queue"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generated_dir() -> Path:
    d = app_root() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def card_db_dir() -> Path:
    """Card catalog root. Prefer app_root/card_db; create only if missing when used."""
    d = app_root() / "card_db"
    if not d.is_dir():
        # Do not mkdir here — empty dir fakes "catalog present" for portable installs.
        # Callers that write downloads should mkdir themselves.
        pass
    return d


def ensure_card_db_dir() -> Path:
    d = app_root() / "card_db"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bridge_dir() -> Path:
    """Writable bridge dir next to exe (may be empty until seeded)."""
    return app_root() / "bridge"


def bridge_source_path() -> Path:
    """Resolve le_profile_bridge.lua — app_root first, then frozen _internal bundle."""
    name = "le_profile_bridge.lua"
    app_p = app_root() / "bridge" / name
    if app_p.is_file():
        return app_p
    res_p = resource_root() / "bridge" / name
    if res_p.is_file():
        return res_p
    return app_p  # expected path for error messages


def le_data_dir() -> Optional[Path]:
    """LE's data directory (holds le_config.json, mods/, extensions/, lua/autorun/).

    This is NOT the install directory. LE records both under
    HKLM\\SOFTWARE\\Live Editor\\FC 26; the registry is authoritative because the
    user can relocate it. Falls back to the documented default.
    """
    try:
        import winreg  # noqa: PLC0415

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Live Editor\FC 26") as key:
                    value, _ = winreg.QueryValueEx(key, "Data Dir")
                    if value:
                        p = Path(str(value))
                        if p.is_dir():
                            return p
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    fallback = Path("C:/FC 26 Live Editor")
    return fallback if fallback.is_dir() else None


def le_autorun_dirs() -> list[Path]:
    """Candidate directories LE scans for auto-executed Lua at startup.

    FCLiveEditor.DLL logs "Executing scripts from lua/autorun" at DEBUG level.
    LE itself creates <data dir>/lua/autorun, which is the observed scan root;
    the install root is included as a cheap fallback.
    """
    out: list[Path] = []
    data = le_data_dir()
    if data is not None:
        out.append(data / "lua" / "autorun")
    try:
        out.append(le_root() / "lua" / "autorun")
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def cards_csv_path() -> Path:
    return le_root() / "player_presets" / "cards.csv"


def base_players_csv_path() -> Path:
    return le_root() / "player_presets" / "base_players.csv"


def stock_script_path(relative: str) -> Path:
    """Resolve a path like lua/scripts/foo.lua under LE root (no .. escape)."""
    rel = relative.replace("\\", "/").lstrip("/")
    root = le_root().resolve()
    script = (root / rel).resolve()
    try:
        script.relative_to(root)
    except ValueError as e:
        raise ValueError(f"script path escapes LE root: {relative!r}") from e
    return script


def done_dir() -> Path:
    """Queue files moved here after the LE bridge executes them."""
    d = app_root() / "queue" / "done"
    d.mkdir(parents=True, exist_ok=True)
    return d
