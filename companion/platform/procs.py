r"""Process liveness and enumeration. No psutil, no ``tasklist``, no console flash.

Two jobs:

**1. Is this pid alive?** ``session.json`` records the pid of the FC 26 process
that armed the bridge, and the ARM marker is pid-checked rather than
TTL-expired (``core/transport/v3.py`` §3.5). Everything downstream — the status
pill, the doctor screen, whether a submitted job can possibly be picked up —
rests on this one boolean, so it is worth getting exactly right:

* ``OpenProcess`` failing is **not** proof the process is dead.
  ``ERROR_ACCESS_DENIED`` means it exists and we may not look at it (FC 26
  launched elevated, the companion not). The naive version returns "dead" there
  and the UI tells the user the bridge is off while it is happily draining.
  Only ``ERROR_INVALID_PARAMETER`` actually means "no such pid".
* ``GetExitCodeProcess`` == ``STILL_ACTIVE`` (259) is ambiguous: a process that
  *exited with code 259* is indistinguishable from a running one. The
  unambiguous test is ``WaitForSingleObject(h, 0)`` — a process handle is
  signalled exactly when the process has terminated — so that is tried first
  and the exit code is only the fallback for handles opened without
  ``SYNCHRONIZE``.
* ctypes restypes are declared. ``kernel32.OpenProcess`` defaults to returning
  ``c_int``, which truncates a 64-bit ``HANDLE``; the truncated value then gets
  passed to ``CloseHandle`` and leaks the real one.

``core/transport/v3.py`` carries a private ``_default_pid_probe`` (it must not
import upward from ``core`` into ``platform``). This module is the canonical
one and :func:`is_pid_alive` is signature-compatible with that module's
``PidProbe`` protocol, so ``FileTransport(paths, pid_probe=procs.is_pid_alive)``
is a drop-in from the composition root.

**2. What is running?** ``CreateToolhelp32Snapshot`` — one snapshot, walked in
process, no subprocess and therefore no console window blinking on a user's
screen every poll. Callers that ask several questions take one snapshot and
pass it around; nothing here caches behind your back.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

_IS_WINDOWS = os.name == "nt"

#: The game. Anything that edits the live DB is dead in the water without it.
FC26_PROCESS = "FC26.exe"
#: LE's launcher. It owns injection and anticheat (SI-1) — the companion only
#: ever *observes* it.
LE_LAUNCHER_PROCESS = "Launcher.exe"
#: Names worth checking when asking "is Live Editor up?".
LE_PROCESS_NAMES: tuple[str, ...] = (LE_LAUNCHER_PROCESS, "FCLiveEditor.exe")

_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH_LONG = 32768


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """One row of a process snapshot. ``exe`` is filled in on demand only."""

    pid: int
    name: str
    parent_pid: int = 0
    threads: int = 0

    @property
    def stem(self) -> str:
        """Name without the ``.exe`` — so callers can match either spelling."""
        return self.name[:-4] if self.name.lower().endswith(".exe") else self.name


# --- ctypes plumbing --------------------------------------------------------


@lru_cache(maxsize=1)
def _kernel32():
    """Load kernel32 once, with declared signatures. Raises off Windows."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE

    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL

    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL

    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD

    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return k32


@lru_cache(maxsize=1)
def _invalid_handle() -> int:
    import ctypes

    return ctypes.c_void_p(-1).value  # type: ignore[return-value]


@lru_cache(maxsize=1)
def _processentry32w():
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ulong_ptr),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    return PROCESSENTRY32W


# --- liveness ---------------------------------------------------------------


def is_pid_alive(pid: int) -> bool:
    """True iff ``pid`` names a running process.

    Signature-compatible with ``core.transport.v3.PidProbe``. Never raises:
    a probe that throws would take down the status poll that calls it every
    second.

    Access-denied counts as **alive** — the process exists, we simply cannot
    inspect it. Reporting "dead" there is the failure mode that tells a user
    running FC 26 as administrator that their bridge is off.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False

    if not _IS_WINDOWS:  # pragma: no cover - project is Windows-only
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    try:
        import ctypes

        k32 = _kernel32()
        handle = k32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                waited = k32.WaitForSingleObject(handle, 0)
            finally:
                k32.CloseHandle(handle)
            if waited == _WAIT_TIMEOUT:
                return True          # not signalled => still running
            if waited == _WAIT_OBJECT_0:
                return False         # signalled => terminated
            return _exit_code_says_alive(pid)

        err = ctypes.get_last_error()
        if err == _ERROR_ACCESS_DENIED:
            return True              # it exists; we are simply not allowed to look
        if err == _ERROR_INVALID_PARAMETER:
            return False             # the only error that really means "no such pid"
        # Rare: SYNCHRONIZE denied but query granted. Then fall back to the
        # snapshot, which needs no handle at all.
        if _exit_code_says_alive(pid):
            return True
        return any(p.pid == pid for p in list_processes())
    except Exception:  # noqa: BLE001 - a liveness probe must not propagate
        return False


def _exit_code_says_alive(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        k32 = _kernel32()
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == _STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def process_exit_code(pid: int) -> int | None:
    """Exit code of a finished process; ``None`` while it runs or is unknowable.

    Ambiguity is resolved in favour of the wait handle: if the process has not
    terminated we return ``None`` even when the raw code reads 259.
    """
    if not _IS_WINDOWS or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = _kernel32()
        handle = k32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            if k32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0:
                return None
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return int(code.value)
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def process_start_time(pid: int) -> float | None:
    """Unix epoch seconds when *pid* started, or ``None`` if inaccessible."""
    if not _IS_WINDOWS or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = _kernel32()
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not k32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            # FILETIME is 100 ns since 1601-01-01 UTC.
            return ticks / 10_000_000.0 - 11_644_473_600.0
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


# --- enumeration ------------------------------------------------------------


def list_processes() -> tuple[ProcessInfo, ...]:
    """Snapshot every visible process. Empty tuple if the snapshot fails.

    One ``CreateToolhelp32Snapshot`` + ``Process32NextW`` walk: ~1 ms for the
    185 processes on this machine, no subprocess, no window. Ordering is
    whatever Windows returns — do not rely on it.
    """
    if not _IS_WINDOWS:  # pragma: no cover
        return ()
    try:
        import ctypes

        k32 = _kernel32()
        entry_type = _processentry32w()
        k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(entry_type)]
        k32.Process32FirstW.restype = ctypes.c_int
        k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(entry_type)]
        k32.Process32NextW.restype = ctypes.c_int

        snapshot = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == _invalid_handle():
            return ()
        out: list[ProcessInfo] = []
        try:
            entry = entry_type()
            entry.dwSize = ctypes.sizeof(entry_type)
            ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                out.append(
                    ProcessInfo(
                        pid=int(entry.th32ProcessID),
                        name=str(entry.szExeFile),
                        parent_pid=int(entry.th32ParentProcessID),
                        threads=int(entry.cntThreads),
                    )
                )
                ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snapshot)
        return tuple(out)
    except Exception:  # noqa: BLE001
        return ()


def process_path(pid: int) -> Path | None:
    """Full image path of ``pid``, or ``None`` (dead, or access denied).

    Used to tell *this* LE ``Launcher.exe`` from some other program's launcher:
    names are not unique, paths are.
    """
    if not _IS_WINDOWS or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = _kernel32()
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(_MAX_PATH_LONG)
            buf = ctypes.create_unicode_buffer(_MAX_PATH_LONG)
            if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return Path(buf.value) if buf.value else None
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def _matches(info: ProcessInfo, wanted: str) -> bool:
    name = info.name.lower()
    target = wanted.lower()
    return name == target or info.stem.lower() == target or name == f"{target}.exe"


def find_process_by_name(
    name: str,
    *,
    processes: Sequence[ProcessInfo] | None = None,
) -> tuple[ProcessInfo, ...]:
    """Every process matching ``name``, case-insensitively, ``.exe`` optional.

    Pass ``processes`` (from a previous :func:`list_processes`) to answer
    several questions from one snapshot instead of re-enumerating per lookup.
    """
    if not name:
        return ()
    table = processes if processes is not None else list_processes()
    return tuple(p for p in table if _matches(p, name))


def first_process_by_name(
    name: str,
    *,
    processes: Sequence[ProcessInfo] | None = None,
) -> ProcessInfo | None:
    """The first match for ``name``, or ``None``."""
    hits = find_process_by_name(name, processes=processes)
    return hits[0] if hits else None


def is_process_running(
    name: str,
    *,
    processes: Sequence[ProcessInfo] | None = None,
) -> bool:
    """True when at least one process is named ``name``."""
    return bool(find_process_by_name(name, processes=processes))


def find_game(*, processes: Sequence[ProcessInfo] | None = None) -> ProcessInfo | None:
    """The FC 26 process, if the game is running."""
    return first_process_by_name(FC26_PROCESS, processes=processes)


def find_le_launcher(
    *,
    install_root: Path | None = None,
    processes: Sequence[ProcessInfo] | None = None,
) -> ProcessInfo | None:
    """The Live Editor launcher, if it is running.

    ``Launcher.exe`` is a generic name; when ``install_root`` is given, matches
    are confirmed by image path so an unrelated launcher cannot masquerade as
    LE. A match whose path cannot be read (access denied) is accepted — being
    unable to look is not evidence against.
    """
    table = processes if processes is not None else list_processes()
    for candidate in LE_PROCESS_NAMES:
        for info in find_process_by_name(candidate, processes=table):
            if install_root is None:
                return info
            exe = process_path(info.pid)
            if exe is None:
                return info
            try:
                exe.resolve().relative_to(Path(install_root).resolve())
            except (ValueError, OSError):
                continue
            return info
    return None


def describe(*, processes: Iterable[ProcessInfo] | None = None) -> dict[str, object]:
    """Plain data for the doctor screen — no Win32 types leak upward."""
    table = tuple(processes) if processes is not None else list_processes()
    game = find_game(processes=table)
    launcher = find_le_launcher(processes=table)
    return {
        "supported": _IS_WINDOWS,
        "count": len(table),
        "game_running": game is not None,
        "game_pid": game.pid if game else 0,
        "game_started_at": process_start_time(game.pid) if game else None,
        "le_running": launcher is not None,
        "le_pid": launcher.pid if launcher else 0,
        "le_name": launcher.name if launcher else "",
    }


__all__ = [
    "FC26_PROCESS",
    "LE_LAUNCHER_PROCESS",
    "LE_PROCESS_NAMES",
    "ProcessInfo",
    "describe",
    "find_game",
    "find_le_launcher",
    "find_process_by_name",
    "first_process_by_name",
    "is_pid_alive",
    "is_process_running",
    "list_processes",
    "process_exit_code",
    "process_start_time",
    "process_path",
]
