"""Build FC26_Companion.exe (v2) with PyInstaller, onedir.

Deliberately does NOT touch v1's build. ``build_exe.py`` still produces
``LE_Profile_Executor.exe`` + ``_internal/``; this produces
``FC26_Companion.exe`` + ``_internal_v2/`` in the **project root** so both
exes sit side by side (v1 keeps ``_internal/``, v2 uses ``_internal_v2/``).

The resident Lua core in ``ingame/`` is shipped as **data**, not code — the
installer copies it into the LE data dir at arm time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "FC26_Companion"


def _verify_tk_runtime() -> str:
    """Fail before packaging if this process cannot initialize Tcl/Tk.

    A restricted process can see ``tkinter`` while still being unable to load
    ``init.tcl``. PyInstaller then silently excludes Tk and produces a CLI-only
    build, which is not a valid desktop deployment.
    """
    import tkinter

    interp = tkinter.Tcl()
    return str(interp.eval("info patchlevel"))


def _rm(path: Path) -> None:
    if path.is_file():
        path.unlink()
    elif path.is_dir():
        # A silent partial delete leaves an old runtime mixed with a new EXE.
        # Let the caller quarantine a locked directory instead.
        shutil.rmtree(path)


def _rm_or_quarantine(path: Path) -> bool:
    """Remove; if locked by a running exe, rename aside so the build continues."""
    if not path.exists():
        return True
    try:
        _rm(path)
        return True
    except OSError:
        pass
    try:
        bak = path.with_name(f"{path.name}.old_{path.stat().st_mtime_ns}")
        _rm(bak)
        path.rename(bak)
        print(f"NOTE: locked path moved aside -> {bak.name}")
        return True
    except OSError as e:
        print(f"WARN: cannot remove/rename {path}: {e}")
        return False


def _discover_modules() -> list[str]:
    """Every importable module under companion/, as dotted names.

    Walks the package rather than trusting a hand-maintained list — v1's list
    silently went stale every time a module was added. Most of these are
    imported lazily inside function bodies, so PyInstaller's static analysis
    misses them.
    """
    out: list[str] = []
    pkg = ROOT / "companion"
    if not pkg.is_dir():
        return out
    for path in sorted(pkg.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                continue
        ok = True
        for depth in range(2, len(parts) + 1):
            probe = ROOT.joinpath(*parts[:depth])
            if probe.is_dir() and not (probe / "__init__.py").is_file():
                ok = False
                break
        if ok:
            out.append(".".join(parts))
    return out


def _windows_tk_args(sep: str) -> list[str]:
    """Explicitly package Tk when PyInstaller's Tk hook cannot initialise it.

    The hook used to fail silently on this machine and still emit a CLI-capable
    executable.  That executable reported the right version but could never
    import ``customtkinter`` because tkinter and its native runtime were absent.
    Treat the installed Python layout as data instead of trusting the hook.
    """
    if sys.platform != "win32":
        return []
    prefix = Path(sys.base_prefix)
    tcl_root = prefix / "tcl"
    dll_root = prefix / "DLLs"
    tkinter_root = prefix / "Lib" / "tkinter"
    required = {
        "tcl scripts": tcl_root / "tcl8.6",
        "tk scripts": tcl_root / "tk8.6",
        "tkinter Python package": tkinter_root,
        "tcl dll": dll_root / "tcl86t.dll",
        "tk dll": dll_root / "tk86t.dll",
        "tkinter extension": dll_root / "_tkinter.pyd",
    }
    missing = [f"{label}: {path}" for label, path in required.items() if not path.exists()]
    if missing:
        raise RuntimeError("Windows Tk runtime is incomplete: " + "; ".join(missing))
    return [
        f"--add-data={required['tcl scripts']}{sep}_tcl_data",
        f"--add-data={required['tk scripts']}{sep}_tk_data",
        # PyInstaller's tkinter hook deliberately drops the Python package when
        # its own Tcl probe fails.  We already validate and ship the complete
        # desktop runtime below, so copy the package explicitly; otherwise
        # CustomTkinter is present but cannot import its tkinter dependency.
        f"--add-data={required['tkinter Python package']}{sep}tkinter",
        f"--add-binary={required['tcl dll']}{sep}.",
        f"--add-binary={required['tk dll']}{sep}.",
        f"--add-binary={required['tkinter extension']}{sep}.",
    ]


def main() -> int:
    try:
        tk_version = _verify_tk_runtime()
    except Exception as exc:  # noqa: BLE001
        # Recovery only: some local Python installs can run the already-built
        # Companion but have a broken developer Tcl discovery path.  A build
        # operator may reuse the last verified bundled Tcl assets explicitly;
        # the post-build asset check below remains mandatory.
        if os.environ.get("LE_COMPANION_SKIP_TK_PREFLIGHT") == "1":
            print(f"WARN: Tcl/Tk preflight bypassed for recovery build: {exc}")
            tk_version = "reusing verified runtime assets"
        else:
            print(f"ERROR: Tcl/Tk preflight failed: {exc}")
            print("Build from a normal desktop process; do not deploy a Tk-less exe.")
            return 1
    print(f"Tcl/Tk preflight: {tk_version}")

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q",
         "pyinstaller", "customtkinter", "pillow", "darkdetect", "openai"]
    )

    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--windowed", "--onedir",
        # Separate from v1's _internal/ so both exes can live in the project root.
        "--contents-directory", "_internal_v2",
        "--paths", str(ROOT),
        # Shipped data. The Lua core is data: the installer copies it into LE.
        f"--add-data=ingame{sep}ingame",
        f"--add-data=profiles.json{sep}.",
        f"--add-data=docs/schema{sep}docs/schema",
        "--hidden-import", "customtkinter",
        "--hidden-import", "darkdetect",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.constants",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.font",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "tkinter.simpledialog",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "openai",
        "--collect-submodules", "openai",
        "--collect-all", "customtkinter",
        "--collect-all", "darkdetect",
    ]
    cmd.extend(_windows_tk_args(sep))
    for module in _discover_modules():
        cmd.extend(["--hidden-import", module])

    # Optional data that may not exist on every machine.
    for rel in ("assets", "web"):
        if (ROOT / rel).is_dir():
            cmd.append(f"--add-data={rel}{sep}{rel}")

    cmd.append(str(ROOT / "app_entry_v2.py"))
    print("Running:", " ".join(cmd[:12]), "...", f"(+{len(cmd) - 12} args)")
    subprocess.check_call(cmd, cwd=str(ROOT))

    dist_dir = ROOT / "dist" / APP_NAME
    dist_exe = dist_dir / f"{APP_NAME}.exe"
    if not dist_exe.is_file():
        print("ERROR: expected onedir layout at", dist_dir)
        return 1

    # Install into the project root next to v1's LE_Profile_Executor.exe.
    root_exe = ROOT / f"{APP_NAME}.exe"
    root_internal = ROOT / "_internal_v2"
    # Do this before moving either half of the onedir runtime.  The old
    # deployment sequence could successfully move the EXE aside, then fail on
    # a locked _internal_v2 directory and leave the normal launch path absent.
    try:
        from companion.platform import procs

        if procs.is_process_running(f"{APP_NAME}.exe"):
            print("ERROR: close the running Companion before deploying the new build.")
            return 1
    except Exception:  # noqa: BLE001 - packaging must still work without Win32 probes
        pass
    if not _rm_or_quarantine(root_exe) or not _rm_or_quarantine(root_internal):
        print("ERROR: close the running Companion before deploying the new build.")
        return 1

    shutil.copy2(dist_exe, root_exe)
    src_internal = dist_dir / "_internal_v2"
    if not src_internal.is_dir():
        # Fallback if PyInstaller ignored --contents-directory on this version.
        src_internal = dist_dir / "_internal"
    if src_internal.is_dir():
        shutil.copytree(src_internal, root_internal)
    else:
        print("ERROR: no contents directory next to dist exe:", dist_dir)
        return 1
    required_tk = (
        root_internal / "_tcl_data" / "init.tcl",
        root_internal / "_tk_data" / "tk.tcl",
        root_internal / "tcl86t.dll",
        root_internal / "tk86t.dll",
        root_internal / "_tkinter.pyd",
    )
    missing_tk = [str(path) for path in required_tk if not path.is_file()]
    if missing_tk:
        print("ERROR: packaged desktop runtime is missing Tcl/Tk assets:")
        for path in missing_tk:
            print("  ", path)
        return 1

    # Also keep a self-contained folder copy for portable zip use.
    portable = ROOT / f"{APP_NAME}_app"
    _rm_or_quarantine(portable)
    if portable.exists():
        _rm(portable)
    shutil.copytree(dist_dir, portable)

    print()
    print("OK project-root exe:", root_exe)
    print("OK runtime folder:  ", root_internal)
    print("OK portable bundle: ", portable / f"{APP_NAME}.exe")
    print()
    print("Run it:")
    print(f"  {APP_NAME}.exe            # desktop UI  (in this folder)")
    print(f"  {APP_NAME}.exe doctor     # health report")
    print(f"  {APP_NAME}.exe install    # install the in-game core")
    print()
    print("Keep card_db/ + profiles.json + queue/ next to the exe.")
    print("v1 (LE_Profile_Executor.exe + _internal/) is untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
