"""Layout gate for the redesign: real windows, real data, hidden from the desktop.

For every surface at 1040x720, 1280x820 and 1600x960, with and without the
Activity drawer:

* every shown grid column ends inside its grid;
* every mapped button is inside the window and is not squeezed below the width
  its label needs (a squeezed CTkButton silently clips its text);
* at most one button wears the solid primary fill.

    python tools/check_layout.py            # prints layout-ok or the failures
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import capture_ui as cu  # noqa: E402

SIZES = ((1040, 720), (1280, 820), (1600, 960))
SURFACES = ("club", "player", "add_player", "injury", "automations", "library")


def _buttons(root: Any) -> list[Any]:
    import customtkinter as ctk

    return [w for w in cu._walk(root) if isinstance(w, ctk.CTkButton) and cu._mapped(w)]


def _check_surface(shell: Any, tag: str) -> list[str]:
    from companion.ui import theme

    problems: list[str] = []
    window = shell.root
    win_x, win_w = window.winfo_rootx(), window.winfo_width()
    primaries = []
    for btn in _buttons(window):
        try:
            text = str(btn.cget("text") or btn.cget("image") or "icon")
            x, w = btn.winfo_rootx(), btn.winfo_width()
            if x < win_x - 1 or x + w > win_x + win_w + 1:
                problems.append(f"{tag}: button {text!r} outside the window")
            if btn.winfo_reqwidth() > w + 3:
                problems.append(f"{tag}: button {text!r} squeezed ({w}px < {btn.winfo_reqwidth()}px)")
            if str(btn.cget("fg_color")) == theme.SUCCESS:
                primaries.append(text)
        except Exception:
            continue
    if len(primaries) > 1:
        problems.append(f"{tag}: {len(primaries)} solid primaries {primaries}")
    for grid in cu.GRIDS:
        try:
            if not (grid.widget.winfo_exists() and grid.widget.winfo_ismapped()):
                continue
            width = grid._content_width()
            for column, x, w in grid._layout(width):
                if x + min(w, column.width) > width + 2:
                    problems.append(
                        f"{tag}: grid column {column.key!r} ends at {x + w}px in a {width}px grid"
                    )
        except Exception:
            continue
    return problems


def main() -> int:
    cu._user32.SetProcessDPIAware()
    cu._patch_windows()
    problems: list[str] = []
    shell, svc, root_dir = cu._new_shell(cu.DEFAULT_INSTALL, "live")
    try:
        for width, height in SIZES:
            shell.root.geometry(f"{width}x{height}+0+0")
            cu._settle(shell.root, 0.4)
            for drawer in (False, True):
                if drawer:
                    shell.open_drawer()
                else:
                    shell.close_drawer()
                for key in SURFACES:
                    shell._show(key)
                    cu._settle(shell.root, 0.5)
                    tag = f"{width}x{height}{' +drawer' if drawer else ''} {key}"
                    problems.extend(_check_surface(shell, tag))
            shell.close_drawer()
    finally:
        cu._dispose(shell, svc, root_dir)

    for scenario in ("player_open", "player_edited", "sign_results", "club_checked"):
        for width, height in ((1040, 720), (1280, 820)):
            shell, svc, root_dir = cu._new_shell(cu.DEFAULT_INSTALL, "live")
            try:
                shell.root.geometry(f"{width}x{height}+0+0")
                cu._settle(shell.root, 0.4)
                cu._scenario(scenario, shell, svc)
                cu._settle(shell.root, 0.8)
                problems.extend(_check_surface(shell, f"{width}x{height} {scenario}"))
            finally:
                cu._dispose(shell, svc, root_dir)

    if problems:
        for line in problems:
            print(line)
        return 1
    print("layout-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
