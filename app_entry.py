"""Windows GUI entry point for frozen .exe (no console by default).

Primary UI is the CustomTkinter desktop app. Pass --web for the optional
local browser UI, or any other CLI flags for headless/control paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure package imports work when frozen and when run as script
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ensure_stdio() -> None:
    """Windowed PyInstaller sets stdout/stderr to None — restore to NUL/log.

    print() and library logs must not crash on None streams.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        # Prefer a small log file next to the exe when frozen
        log_path = None
        if getattr(sys, "frozen", False):
            log_path = Path(sys.executable).resolve().parent / "app.log"
        if log_path is not None:
            fh = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
            if sys.stdout is None:
                sys.stdout = fh  # type: ignore[assignment]
            if sys.stderr is None:
                sys.stderr = fh  # type: ignore[assignment]
            return
    except Exception:
        pass
    try:
        import os

        devnull = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = devnull  # type: ignore[assignment]
        if sys.stderr is None:
            sys.stderr = devnull  # type: ignore[assignment]
    except Exception:
        pass


def main() -> int:
    _ensure_stdio()
    try:
        # Any CLI flags → full main.py dispatcher (--gui, --web, --search-cards, …)
        if len(sys.argv) > 1:
            from main import main as cli_main

            return int(cli_main(sys.argv[1:]))

        # Double-click / no args → primary desktop GUI
        from src.gui import run_gui

        run_gui()
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            log = Path(sys.executable).resolve().parent / "app.log"
            if not getattr(sys, "frozen", False):
                log = _ROOT / "app.log"
            with log.open("a", encoding="utf-8") as f:
                import traceback

                f.write("FATAL app_entry:\n")
                traceback.print_exc(file=f)
                f.write(str(e) + "\n")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
