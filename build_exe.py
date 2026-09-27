"""Build LE_Profile_Executor.exe with PyInstaller (onedir for faster startup).

Onedir requires BOTH:
  LE_Profile_Executor.exe
  _internal/   (python312.dll + deps)

Copying only the .exe breaks with:
  Failed to load Python DLL ... _internal\\python312.dll

This script installs the full runtime into the project root next to card_db/.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _rm(path: Path) -> None:
    if path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _rm_or_quarantine(path: Path) -> bool:
    """Remove path; if locked (running exe), rename aside so install can continue."""
    if not path.exists():
        return True
    try:
        _rm(path)
        return True
    except OSError:
        pass
    # Windows: running Companion locks the exe / some _internal DLLs
    try:
        bak = path.with_name(path.name + f".old_{path.stat().st_mtime_ns}")
        if bak.exists():
            _rm(bak)
        path.rename(bak)
        print(f"NOTE: locked path moved aside → {bak.name} (close old Companion later)")
        return True
    except OSError as e:
        print(f"WARN: cannot remove/rename {path}: {e}")
        return False


def _discover_src_modules() -> list[str]:
    """Every importable module under src/, as dotted PyInstaller module names.

    Walks the package rather than trusting a hand-written list. Skips __pycache__
    and any directory without an __init__.py, so only real packages are emitted.
    """
    out: list[str] = []
    src = ROOT / "src"
    if not src.is_dir():
        return out
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                continue
        # Every intermediate directory must be a real package.
        ok = True
        for depth in range(2, len(parts) + 1):
            probe = ROOT.joinpath(*parts[:depth])
            if probe.is_dir() and not (probe / "__init__.py").is_file():
                ok = False
                break
        if ok:
            out.append(".".join(parts))
    return out


def main() -> int:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "pyinstaller", "customtkinter", "pillow"]
    )

    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "LE_Profile_Executor",
        "--windowed",
        "--onedir",
        "--paths",
        str(ROOT),
        f"--add-data=profiles.json{sep}.",
        f"--add-data=bridge{sep}bridge",
        f"--add-data=web{sep}web",
        "--hidden-import",
        "customtkinter",
        "--hidden-import",
        "src.web_api",
        "--hidden-import",
        "src.web_server",
        "--hidden-import",
        "src.health_check",
        "--hidden-import",
        "src.editor_presets",
        "--hidden-import",
        "src.profiles",
        "--hidden-import",
        "src.target_players",
        "--hidden-import",
        "src.player_schema",
        "--hidden-import",
        "src.player_apply",
        "--hidden-import",
        "src.import_player",
        "--hidden-import",
        "src.grok_client",
        "--hidden-import",
        "src.ui_theme",
        "--hidden-import",
        "src.ui.widgets",
        "--hidden-import",
        "src.ui.player_edit",
        "--hidden-import",
        "src.ui.apply_chrome",
        "--hidden-import",
        "src.ui.apply_flow",
        "--hidden-import",
        "src.ui.chrome",
        "--hidden-import",
        "src.ui.handoffs",
        "--hidden-import",
        "src.ui.badge",
        "--hidden-import",
        "src.ui.skeleton",
        "--hidden-import",
        "src.ui.stepper",
        "--hidden-import",
        "src.ui.empty_state",
        "--hidden-import",
        "src.ui.collapsible",
        "--hidden-import",
        "src.ui.tabs.home",
        "--hidden-import",
        "src.ui.tabs.cards",
        "--hidden-import",
        "src.ui.tabs.add_team",
        "--hidden-import",
        "src.ui.tabs.boost",
        "--hidden-import",
        "src.ui.tabs.squad",
        "--hidden-import",
        "src.ui.tabs.editor",
        "--hidden-import",
        "src.ui.tabs.catalog",
        "--hidden-import",
        "src.ui.tabs.about",
        "--hidden-import",
        "src.icons",
        "--hidden-import",
        "src.card_index",
        "--hidden-import",
        "src.card_types",
        "--hidden-import",
        "src.card_catalog",
        "--hidden-import",
        "src.card_to_lua",
        "--hidden-import",
        "src.card_enrich",
        "--hidden-import",
        "src.card_compare",
        "--hidden-import",
        "src.favorites",
        "--hidden-import",
        "src.product",
        "--hidden-import",
        "src.boost_queue",
        "--hidden-import",
        "src.add_player",
        "--hidden-import",
        "src.card_match",
        "--hidden-import",
        "src.snapshot_store",
        "--hidden-import",
        "src.protocol",
        "--hidden-import",
        "src.companion_config",
        "--hidden-import",
        "src.inject_side",
        "--hidden-import",
        "src.apply_service",
        "--hidden-import",
        "src.le_apply",
        "--hidden-import",
        "src.undo_apply",
        "--hidden-import",
        "src.job_history",
        "--hidden-import",
        "src.futbin_client",
        "--hidden-import",
        "src.futbin_models",
        "--hidden-import",
        "src.futbin_parse",
        "--hidden-import",
        "src.futbin_http",
        "--hidden-import",
        "src.futgg_client",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "PIL._tkinter_finder",
        "--collect-all",
        "customtkinter",
    ]
    # Most src modules are imported lazily inside function bodies, so
    # PyInstaller's static analysis misses them. The list above was maintained
    # by hand and silently went stale every time a module was added — discover
    # them instead so a new module can never be left out of a build.
    for module in _discover_src_modules():
        if module not in cmd:
            cmd.extend(["--hidden-import", module])
    cmd.append(str(ROOT / "app_entry.py"))
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    dist_dir = ROOT / "dist" / "LE_Profile_Executor"
    dist_exe = dist_dir / "LE_Profile_Executor.exe"
    dist_internal = dist_dir / "_internal"
    if not dist_exe.is_file() or not dist_internal.is_dir():
        print("ERROR: expected onedir layout at", dist_dir)
        return 1

    # 1) Full copy under LE_Profile_Executor_app/ (portable bundle)
    target_dir = ROOT / "LE_Profile_Executor_app"
    _rm_or_quarantine(target_dir)
    if target_dir.exists():
        _rm(target_dir)
    shutil.copytree(dist_dir, target_dir)
    print("OK portable:     ", target_dir / "LE_Profile_Executor.exe")

    # 2) Install full runtime at project root (exe + _internal) so double-click works
    #    next to card_db/, profiles.json, queue/
    root_exe = ROOT / "LE_Profile_Executor.exe"
    root_internal = ROOT / "_internal"
    root_ok = True
    if not _rm_or_quarantine(root_exe):
        root_ok = False
    if not _rm_or_quarantine(root_internal):
        root_ok = False
    try:
        if root_exe.exists():
            _rm(root_exe)
        if root_internal.exists():
            _rm(root_internal)
        shutil.copy2(dist_exe, root_exe)
        shutil.copytree(dist_internal, root_internal)
    except OSError as e:
        root_ok = False
        print(f"WARN: root install incomplete (close Companion): {e}")
        # Always leave a launchable NEW exe at root
        alt = ROOT / "LE_Profile_Executor_NEW.exe"
        try:
            shutil.copy2(dist_exe, alt)
            print("OK alt exe:      ", alt)
        except OSError as e2:
            print("ERROR: cannot write NEW exe either:", e2)
            return 1

    if root_ok and not (root_internal / "python312.dll").is_file():
        print("ERROR: python312.dll missing after install")
        return 1

    if root_ok:
        print("OK root exe:     ", root_exe)
        print("OK root _internal:", root_internal)
    else:
        print("PARTIAL root install — use LE_Profile_Executor_app\\LE_Profile_Executor.exe")
    print()
    print("Double-click:  LE_Profile_Executor.exe  (needs _internal/ next to it)")
    print("Or portable:   LE_Profile_Executor_app\\LE_Profile_Executor.exe")
    print("Keep card_db/ + profiles.json + bridge/ + queue/ next to the exe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
