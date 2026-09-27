"""Windows entry point for the v2 companion exe.

Double-click -> the desktop GUI. Any argument -> the v2 CLI.

v1's ``app_entry.py`` is untouched and still builds ``LE_Profile_Executor.exe``;
this produces ``FC26_Companion.exe`` alongside it, so both can be installed at
once (the prime directive: v1 keeps working until v2 is better at everything).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ensure_stdio() -> None:
    """Windowed PyInstaller sets stdout/stderr to None; print() would crash."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_path = (
        Path(sys.executable).resolve().parent / "companion.log"
        if getattr(sys, "frozen", False)
        else _ROOT / "companion.log"
    )
    try:
        fh = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        import os

        fh = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = fh  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = fh  # type: ignore[assignment]


def main() -> int:
    _ensure_stdio()
    try:
        if len(sys.argv) > 1:
            from companion.cli import main as cli_main

            return int(cli_main(sys.argv[1:]))

        from companion.ui.shell import run_gui

        return int(run_gui() or 0)
    except Exception:  # noqa: BLE001 — last-resort crash log
        import traceback

        log = (
            Path(sys.executable).resolve().parent / "companion.log"
            if getattr(sys, "frozen", False)
            else _ROOT / "companion.log"
        )
        try:
            with log.open("a", encoding="utf-8") as f:
                f.write("FATAL app_entry_v2:\n")
                traceback.print_exc(file=f)
        except OSError:
            pass
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
