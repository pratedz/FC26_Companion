"""Install / load companion inject-side DLL (LECompanionInject).

Does not touch FakeEAAC or replace FCLiveEditor.DLL. Copies the native
protocol DLL next to LE scripts and under app dist_native for LoadLibrary tests.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

DLL_NAME = "LECompanionInject.dll"
NATIVE_DIR_REL = Path("native") / "le_companion_inject"


def native_source_dir() -> Path:
    return paths.app_root() / NATIVE_DIR_REL


def dist_native_dir() -> Path:
    d = paths.app_root() / "dist_native"
    d.mkdir(parents=True, exist_ok=True)
    return d


def built_dll_path() -> Path:
    return dist_native_dir() / DLL_NAME


def le_install_dll_path() -> Path:
    """Preferred install location under LE tree (next to companion bridge scripts)."""
    return paths.le_root() / "lua" / "scripts" / DLL_NAME


def find_dll() -> Optional[Path]:
    candidates = [
        built_dll_path(),
        le_install_dll_path(),
        paths.app_root() / DLL_NAME,
        native_source_dir() / DLL_NAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def build_dll(*, capture: bool = True) -> Dict[str, Any]:
    """Run native build.bat; return status without faking a binary."""
    bat = native_source_dir() / "build.bat"
    if not bat.is_file():
        return {
            "ok": False,
            "error": "build.bat missing",
            "path": str(bat),
            "toolchain_missing": False,
        }
    try:
        proc = subprocess.run(
            ["cmd", "/c", str(bat)],
            cwd=str(native_source_dir()),
            capture_output=capture,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e), "toolchain_missing": False}

    out = (proc.stdout or "") + (proc.stderr or "")
    dll = built_dll_path()
    toolchain_missing = proc.returncode == 2 or "no C toolchain" in out.lower()
    return {
        "ok": proc.returncode == 0 and dll.is_file(),
        "returncode": proc.returncode,
        "output": out,
        "dll": str(dll) if dll.is_file() else None,
        "toolchain_missing": toolchain_missing,
        "source_dir": str(native_source_dir()),
    }


def install_inject_dll() -> Dict[str, Any]:
    """Copy built (or found) DLL into LE lua/scripts/. Does not swap EAAC."""
    src = find_dll()
    built: Optional[Dict[str, Any]] = None
    if src is None:
        built = build_dll()
        src = find_dll()
    dest = le_install_dll_path()
    if src is None or not src.is_file():
        return {
            "ok": False,
            "error": "LECompanionInject.dll not built; run native/le_companion_inject/build.bat",
            "dest": str(dest),
            "build": built,
            "fakeeaac_touched": False,
            "replaces_fcliveeditor": False,
        }
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    # Also keep a copy under app dist_native
    try:
        shutil.copy2(src, built_dll_path())
    except OSError:
        pass
    return {
        "ok": dest.is_file(),
        "source": str(src),
        "installed": str(dest),
        "fakeeaac_touched": False,
        "replaces_fcliveeditor": False,
        "le_owns_anticheat": True,
        "build": built,
    }


def _load_library(dll_path: Path) -> ctypes.CDLL:
    # Ensure dependencies resolve next to DLL
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(dll_path.parent.resolve()))
        except OSError:
            pass
    return ctypes.CDLL(str(dll_path.resolve()))


class InjectSide:
    """ctypes wrapper around LECompanionInject exports."""

    def __init__(self, dll_path: Optional[Path] = None) -> None:
        path = dll_path or find_dll()
        if path is None or not path.is_file():
            raise FileNotFoundError(
                "LECompanionInject.dll not found. Build with native/le_companion_inject/build.bat"
            )
        self.path = Path(path)
        self._dll = _load_library(self.path)
        self._setup_prototypes()

    def _setup_prototypes(self) -> None:
        d = self._dll
        d.LECompanion_SetQueueDir.argtypes = [ctypes.c_char_p]
        d.LECompanion_SetQueueDir.restype = ctypes.c_int
        d.LECompanion_GetQueueDir.argtypes = [ctypes.c_char_p, ctypes.c_int]
        d.LECompanion_GetQueueDir.restype = ctypes.c_int
        d.LECompanion_Arm.argtypes = []
        d.LECompanion_Arm.restype = ctypes.c_int
        d.LECompanion_ProcessQueue.argtypes = [ctypes.c_int]
        d.LECompanion_ProcessQueue.restype = ctypes.c_int
        d.LECompanion_GetLastResult.argtypes = [ctypes.c_char_p, ctypes.c_int]
        d.LECompanion_GetLastResult.restype = ctypes.c_int
        d.LECompanion_ProtocolVersion.argtypes = []
        d.LECompanion_ProtocolVersion.restype = ctypes.c_int
        d.LECompanion_GetModuleInfo.argtypes = [ctypes.c_char_p, ctypes.c_int]
        d.LECompanion_GetModuleInfo.restype = ctypes.c_int
        if hasattr(d, "LECompanion_EnableDryRun"):
            d.LECompanion_EnableDryRun.argtypes = []
            d.LECompanion_EnableDryRun.restype = ctypes.c_int

    def set_queue_dir(self, queue_dir: Path) -> None:
        p = str(Path(queue_dir).resolve())
        rc = self._dll.LECompanion_SetQueueDir(p.encode("utf-8"))
        if rc != 0:
            raise RuntimeError(f"LECompanion_SetQueueDir failed rc={rc}")

    def arm(self) -> None:
        rc = self._dll.LECompanion_Arm()
        if rc != 0:
            raise RuntimeError(f"LECompanion_Arm failed rc={rc}")

    def enable_dry_run(self) -> None:
        if not hasattr(self._dll, "LECompanion_EnableDryRun"):
            raise RuntimeError("LECompanion_EnableDryRun not exported (rebuild DLL)")
        rc = self._dll.LECompanion_EnableDryRun()
        if rc != 0:
            raise RuntimeError(f"LECompanion_EnableDryRun failed rc={rc}")

    def process_queue(self, force: bool = True) -> int:
        """Dry-run drain only; requires _protocol_dry_run in queue dir."""
        return int(self._dll.LECompanion_ProcessQueue(1 if force else 0))

    def last_result(self) -> str:
        buf = ctypes.create_string_buffer(1024)
        self._dll.LECompanion_GetLastResult(buf, 1024)
        return buf.value.decode("utf-8", errors="replace")

    def protocol_version(self) -> int:
        return int(self._dll.LECompanion_ProtocolVersion())

    def module_info(self) -> str:
        buf = ctypes.create_string_buffer(512)
        self._dll.LECompanion_GetModuleInfo(buf, 512)
        return buf.value.decode("utf-8", errors="replace")


def native_protocol_self_check(queue_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Drive real DLL exports on an isolated dry-run queue (never production)."""
    from . import protocol as proto

    dll_path = find_dll()
    if dll_path is None:
        built = build_dll()
        dll_path = find_dll()
        if dll_path is None:
            return {
                "ok": False,
                "skipped": True,
                "reason": "dll_not_built",
                "build": built,
            }

    # Always isolate unless caller passed a non-production path
    if queue_dir is None:
        q = proto.make_isolated_selfcheck_queue()
    else:
        q = Path(queue_dir)
        try:
            if q.resolve() == paths.queue_dir().resolve():
                return {
                    "ok": False,
                    "skipped": False,
                    "error": "refused_production_queue",
                    "detail": "native_protocol_self_check must not use production queue",
                }
        except OSError:
            pass
        proto.enable_dry_run(q)

    q = proto.ensure_queue_layout(q)
    # Clear only isolated queue contents (not production)
    for name in list(proto.list_job_files(q)):
        try:
            (q / name).unlink()
        except OSError:
            pass
    rn = q / proto.RUN_NOW_NAME
    if rn.is_file():
        try:
            rn.unlink()
        except OSError:
            pass

    body = "-- LE_NATIVE_DLL_SELF_CHECK\nreturn true\n"
    issued = proto.write_job(q, body, stem="native_dll_check", as_run_now=True)
    side = InjectSide(dll_path)
    side.set_queue_dir(q)
    side.enable_dry_run()
    side.arm()
    n = side.process_queue(True)
    result = side.last_result()
    parsed = proto.parse_result_line(result)
    info = side.module_info()
    ok = (
        n >= 1
        and parsed.get("ok") is True
        and side.protocol_version() == proto.PROTOCOL_VERSION
        and "no-fakeeaac" in info.lower()
    )
    job_done = proto.observe_job_done(q, issued["job_name"]) or proto.observe_job_done(
        q, proto.RUN_NOW_NAME
    )
    ok = ok and job_done
    return {
        "ok": ok,
        "skipped": False,
        "isolated": True,
        "queue_dir": str(q.resolve()),
        "dll": str(dll_path),
        "processed": n,
        "result": result,
        "parsed": parsed,
        "module_info": info,
        "protocol_version": side.protocol_version(),
        "issued": issued,
        "job_done": job_done,
        "dry_run": True,
    }


def integration_status() -> Dict[str, Any]:
    """Evidence-friendly snapshot of dual-mode install state."""
    le = paths.le_root()
    return {
        "le_root": str(le),
        "launcher": str(le / "Launcher.exe"),
        "launcher_exists": (le / "Launcher.exe").is_file(),
        "fcliveeditor_dll": str(le / "FCLiveEditor.DLL"),
        "fcliveeditor_exists": (le / "FCLiveEditor.DLL").is_file(),
        "fakeeaac_dir": str(le / "FakeEAACLauncher"),
        "fakeeaac_exists": (le / "FakeEAACLauncher" / "EAAntiCheat.GameServiceLauncher.exe").is_file(),
        "companion_does_not_replace_fcliveeditor": True,
        "companion_does_not_swap_fakeeaac": True,
        "bridge_script": str(le / "lua" / "scripts" / "00_le_companion_bridge.lua"),
        "bridge_installed": (le / "lua" / "scripts" / "00_le_companion_bridge.lua").is_file(),
        "inject_dll_installed": le_install_dll_path().is_file(),
        "inject_dll_path": str(le_install_dll_path()),
        "built_dll": str(built_dll_path()) if built_dll_path().is_file() else None,
        "native_source": str(native_source_dir()),
        "native_source_complete": (native_source_dir() / "le_companion_inject.c").is_file()
        and (native_source_dir() / "build.bat").is_file(),
    }
