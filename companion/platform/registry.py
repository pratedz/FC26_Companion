r"""``HKLM\SOFTWARE\Live Editor\FC 26`` — the only registry reader in v2.

The Live Editor installer records where it put itself. Measured on this
machine (LE v26.3.5), the key holds three REG_SZ values:

    Data Dir     C:\FC 26 Live Editor                     <- le_config.json, mods/, lua/autorun/
    Mods Dir     C:\FC 26 Live Editor\mods
    Install Dir  C:\Users\prated\Desktop\FC 26 LE v26.3.5 <- FCLiveEditor.DLL, Launcher.exe

Two facts that shaped this module:

1. **Data Dir is not Install Dir.** They are different directories by default
   and the user can move either. v1 read only ``Data Dir`` and then *guessed*
   the install root by walking up to six parent directories from the app folder
   looking for ``FCLiveEditor.DLL`` (``src/paths.py::le_root``). The registry
   answers that question directly — the walk is kept here only as a fallback
   for installs old enough to predate the ``Install Dir`` value.

2. **The legacy default is a fallback, not a path.** ``src/paths.py:143``
   buried ``Path("C:/FC 26 Live Editor")`` inside the resolver, so a user who
   relocated LE got a silently wrong directory that happened to exist on most
   machines. Here it is :data:`LEGACY_DEFAULT_DATA_DIR`, it is tried last, and
   every resolution carries a :class:`Source` saying where the answer came from
   so the doctor screen can show "fallback" instead of implying the registry
   agreed.

Everything is read-only. v2 never writes to the registry: LE owns those values
(SI-1/SI-2), and a companion that edits them is a companion that can break the
Launcher's anticheat handshake.

Registry views: a 32-bit Python reading ``HKLM\SOFTWARE`` lands in
``Wow6432Node`` and sees nothing, because the LE installer is 64-bit. Both
views are queried explicitly rather than trusting the interpreter's bitness.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised by the platform, not by tests
    import winreg as _winreg
except ImportError:  # non-Windows: every read degrades to "unset"
    _winreg = None  # type: ignore[assignment]


# --- the key and its values -------------------------------------------------

LE_KEY = r"SOFTWARE\Live Editor\FC 26"

VALUE_DATA_DIR = "Data Dir"
VALUE_MODS_DIR = "Mods Dir"
VALUE_INSTALL_DIR = "Install Dir"
#: Pre-26.x installers wrote the install root as ``Dir``. LE's own
#: ``LiveEditorClearRegistry.reg`` still deletes both names, so both still exist
#: in the wild.
VALUE_LEGACY_INSTALL_DIR = "Dir"

#: FALLBACK ONLY — the path the LE installer *defaults* to. Never treat a hit
#: here as evidence that LE is installed there; it is a guess of last resort,
#: reported as :attr:`Source.LEGACY_DEFAULT` so the UI can say so.
LEGACY_DEFAULT_DATA_DIR = Path(r"C:\FC 26 Live Editor")

#: Escape hatches for portable installs, CI and tests — checked before the
#: registry so a developer can point the app at a copied LE tree without
#: touching HKLM (which needs admin).
ENV_DATA_DIR = "FC26_LE_DATA_DIR"
ENV_INSTALL_DIR = "FC26_LE_INSTALL_DIR"


class Source(str, Enum):
    """Where a resolved path came from. The doctor screen renders this."""

    ENV = "env"
    HKLM = "registry:HKLM"
    HKCU = "registry:HKCU"
    PROBE = "probe"                  # found by inspecting the filesystem
    LEGACY_DEFAULT = "legacy-default"
    UNSET = "unset"                  # nothing found


@dataclass(frozen=True, slots=True)
class Resolution:
    """A path plus its provenance. ``path is None`` means "not found"."""

    path: Path | None
    source: Source = Source.UNSET
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.path is not None

    @property
    def trusted(self) -> bool:
        """True when the answer came from a recorded value, not a guess."""
        return self.source in (Source.ENV, Source.HKLM, Source.HKCU)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else "",
            "source": self.source.value,
            "detail": self.detail,
            "found": self.found,
            "trusted": self.trusted,
        }


# --- raw registry access ----------------------------------------------------


def available() -> bool:
    """True when a registry exists to read (i.e. we are on Windows)."""
    return _winreg is not None


def _hives() -> tuple[tuple[Any, Source], ...]:
    if _winreg is None:
        return ()
    # HKLM first: the installer runs elevated and writes there. HKCU is checked
    # because a per-user or sandboxed install can only write there.
    return (
        (_winreg.HKEY_LOCAL_MACHINE, Source.HKLM),
        (_winreg.HKEY_CURRENT_USER, Source.HKCU),
    )


def _views() -> tuple[int, ...]:
    if _winreg is None:
        return ()
    # 64-bit view first (where the LE installer writes), then the 32-bit
    # Wow6432Node view. Harmlessly ignored on 32-bit Windows.
    return (_winreg.KEY_WOW64_64KEY, _winreg.KEY_WOW64_32KEY)


def read_value(name: str, *, key: str = LE_KEY) -> tuple[str, Source] | None:
    """Read one REG_SZ under ``key``; ``None`` when absent everywhere.

    Returns the value *and* the hive it came from. Every hive/view combination
    is tried; the first non-empty string wins.
    """
    if _winreg is None:
        return None
    for hive, source in _hives():
        for view in _views():
            try:
                with _winreg.OpenKey(hive, key, 0, _winreg.KEY_READ | view) as handle:
                    value, _kind = _winreg.QueryValueEx(handle, name)
            except OSError:
                continue
            text = str(value).strip().strip('"') if value else ""
            if text:
                return text, source
    return None


def read_all(*, key: str = LE_KEY) -> dict[str, str]:
    """Every value under the LE key, for diagnostics. Never raises."""
    out: dict[str, str] = {}
    if _winreg is None:
        return out
    for hive, _source in _hives():
        for view in _views():
            try:
                with _winreg.OpenKey(hive, key, 0, _winreg.KEY_READ | view) as handle:
                    index = 0
                    while True:
                        try:
                            name, value, _kind = _winreg.EnumValue(handle, index)
                        except OSError:
                            break
                        index += 1
                        if name not in out and value not in (None, ""):
                            out[name] = str(value)
            except OSError:
                continue
    return out


# --- what an LE directory looks like ----------------------------------------


def looks_like_install_root(path: Path | None) -> bool:
    """True for a directory holding the LE binaries.

    ``FCLiveEditor.DLL`` and ``Launcher.exe`` are the two files LE cannot run
    without; ``lua/libs`` and ``lua/scripts`` ship alongside them. Ported from
    ``src/paths.py::_looks_like_le_root``.
    """
    if path is None:
        return False
    try:
        if (path / "FCLiveEditor.DLL").is_file() or (path / "Launcher.exe").is_file():
            return True
        if (path / "lua" / "libs").is_dir() or (path / "lua" / "scripts").is_dir():
            return True
    except OSError:
        return False
    return False


def looks_like_data_dir(path: Path | None) -> bool:
    """True for LE's *data* directory (config + mods + autorun), not its binaries.

    Distinguishing marker is ``le_config.json``, which LE writes on first run;
    ``mods/``, ``extensions/`` and ``lua/autorun/`` are the directories it
    creates around it. Deliberately does not accept ``lua/`` alone — the
    install root has one of those too.
    """
    if path is None:
        return False
    try:
        if (path / "le_config.json").is_file():
            return True
        if (path / "lua" / "autorun").is_dir():
            return True
        if (path / "mods").is_dir() and (path / "extensions").is_dir():
            return True
    except OSError:
        return False
    return False


def _env_path(name: str) -> Path | None:
    raw = (os.environ.get(name) or "").strip().strip('"')
    return Path(raw) if raw else None


def _existing_dir(raw: str | Path | None) -> Path | None:
    if raw in (None, ""):
        return None
    try:
        path = Path(str(raw)).expanduser()
        return path if path.is_dir() else None
    except OSError:
        return None


# --- data dir ---------------------------------------------------------------


def resolve_data_dir(*, allow_fallback: bool = True) -> Resolution:
    r"""Resolve LE's data directory, reporting where the answer came from.

    Order: ``FC26_LE_DATA_DIR`` env override, ``HKLM``/``HKCU`` ``Data Dir``,
    then :data:`LEGACY_DEFAULT_DATA_DIR` — and only when that default actually
    exists *and looks like a data dir*, which is the check v1 skipped.

    A registry value that no longer exists on disk is reported as unset rather
    than returned: a stale ``Data Dir`` pointing at a deleted folder made v1's
    autorun installer write into a directory LE never reads.
    """
    env = _env_path(ENV_DATA_DIR)
    if env is not None:
        if env.is_dir():
            return Resolution(env, Source.ENV, f"{ENV_DATA_DIR} override")
        return Resolution(None, Source.UNSET, f"{ENV_DATA_DIR}={env} is not a directory")

    hit = read_value(VALUE_DATA_DIR)
    if hit is not None:
        raw, source = hit
        path = _existing_dir(raw)
        if path is not None:
            return Resolution(path, source, f'{VALUE_DATA_DIR}="{raw}"')
        if not allow_fallback:
            return Resolution(None, Source.UNSET, f'{VALUE_DATA_DIR}="{raw}" does not exist')

    if allow_fallback:
        default = _existing_dir(LEGACY_DEFAULT_DATA_DIR)
        if default is not None and looks_like_data_dir(default):
            return Resolution(
                default,
                Source.LEGACY_DEFAULT,
                "registry value missing; using the installer's default location",
            )

    return Resolution(None, Source.UNSET, f"no {VALUE_DATA_DIR} value under HKLM/HKCU\\" + LE_KEY)


def le_data_dir() -> Path | None:
    """LE's data directory, or ``None``.

    The thin accessor the rest of the app calls (``app/commands/doctor.py``
    calls exactly this). Use :func:`resolve_data_dir` when the provenance
    matters — e.g. to warn that the path is a guess.
    """
    return resolve_data_dir().path


def le_mods_dir() -> Path | None:
    """LE's mods directory. Registry first, else ``<data dir>/mods``."""
    hit = read_value(VALUE_MODS_DIR)
    if hit is not None:
        path = _existing_dir(hit[0])
        if path is not None:
            return path
    data = le_data_dir()
    if data is not None:
        candidate = data / "mods"
        if candidate.is_dir():
            return candidate
    return None


# --- install root -----------------------------------------------------------


def _probe_install_root(hint: Path) -> Path | None:
    """Walk up from ``hint`` looking for the LE binaries (v1's heuristic).

    Only used when the registry has no ``Install Dir``. The companion normally
    lives one level under the LE root, so six levels is generous.
    """
    try:
        current = hint.resolve()
    except OSError:
        return None
    if looks_like_install_root(current):
        return current
    for _ in range(6):
        parent = current.parent
        if parent == current:
            break
        if looks_like_install_root(parent):
            return parent
        current = parent
    return None


def resolve_install_root(hint: Path | None = None) -> Resolution:
    """Resolve the directory holding ``FCLiveEditor.DLL`` / ``Launcher.exe``.

    Order: ``FC26_LE_INSTALL_DIR`` env override, registry ``Install Dir`` (then
    the legacy ``Dir`` name), then a parent walk from ``hint`` (default: this
    package's app root). Registry values are validated with
    :func:`looks_like_install_root` — an ``Install Dir`` that no longer holds
    the DLL is a moved install, not an answer.
    """
    env = _env_path(ENV_INSTALL_DIR)
    if env is not None:
        if env.is_dir():
            return Resolution(env, Source.ENV, f"{ENV_INSTALL_DIR} override")
        return Resolution(None, Source.UNSET, f"{ENV_INSTALL_DIR}={env} is not a directory")

    for name in (VALUE_INSTALL_DIR, VALUE_LEGACY_INSTALL_DIR):
        hit = read_value(name)
        if hit is None:
            continue
        raw, source = hit
        path = _existing_dir(raw)
        if path is not None and looks_like_install_root(path):
            return Resolution(path, source, f'{name}="{raw}"')

    start = hint if hint is not None else Path(__file__).resolve().parents[2]
    probed = _probe_install_root(Path(start))
    if probed is not None:
        return Resolution(probed, Source.PROBE, f"walked up from {start}")

    return Resolution(None, Source.UNSET, f"no {VALUE_INSTALL_DIR} value and no LE binaries above {start}")


def le_install_root(hint: Path | None = None) -> Path | None:
    """The LE install root, or ``None``. See :func:`resolve_install_root`."""
    return resolve_install_root(hint).path


# --- diagnostics ------------------------------------------------------------


def describe(hint: Path | None = None) -> dict[str, Any]:
    """Everything this module can say, as plain data for the doctor screen."""
    data = resolve_data_dir()
    install = resolve_install_root(hint)
    return {
        "registry_available": available(),
        "key": LE_KEY,
        "values": read_all(),
        "data_dir": data.to_dict(),
        "install_dir": install.to_dict(),
        "mods_dir": str(le_mods_dir() or ""),
        "legacy_default": str(LEGACY_DEFAULT_DATA_DIR),
    }


__all__ = [
    "ENV_DATA_DIR",
    "ENV_INSTALL_DIR",
    "LEGACY_DEFAULT_DATA_DIR",
    "LE_KEY",
    "Resolution",
    "Source",
    "VALUE_DATA_DIR",
    "VALUE_INSTALL_DIR",
    "VALUE_LEGACY_INSTALL_DIR",
    "VALUE_MODS_DIR",
    "available",
    "describe",
    "le_data_dir",
    "le_install_root",
    "le_mods_dir",
    "looks_like_data_dir",
    "looks_like_install_root",
    "read_all",
    "read_value",
    "resolve_data_dir",
    "resolve_install_root",
]
