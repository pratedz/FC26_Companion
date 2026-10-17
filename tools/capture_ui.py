"""Screenshot every Companion surface without touching the game or the live install.

The window is built from the real ``Shell`` against a hermetic copy of the
install: ``state.sqlite`` is copied with SQLite's backup API, ``card_db`` is
reached through a junction (every catalog connection is ``mode=ro``) and all
jobs land in a ``FakeTransport``. The window is transparent, sits at the bottom
of the z-order and never takes focus, so a running FC 26 is not interrupted.

    python tools/capture_ui.py --out shots/before
    python tools/capture_ui.py --out shots/after --sizes 1040x720,1280x820,1600x960
    python tools/capture_ui.py --out shots/empty --state empty
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_INSTALL = Path.home() / "Desktop" / "Fc26 LE" / "FC26_Companion"
SURFACES = ("club", "player", "add_player", "injury", "automations", "library")
EXTRAS = ("activity", "settings", "palette", "review")
SCENARIOS = (
    "club_selected", "club_checked", "player_open", "player_edited", "player_ai", "player_card", "review_edited",
    "library_search", "sign_search", "sign_results", "sign_base", "sign_collection", "planner",
)

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
HWND_BOTTOM = 1
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
PW_RENDERFULLCONTENT = 2


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def _hwnd(widget: Any) -> int:
    return int(_user32.GetAncestor(widget.winfo_id(), GA_ROOT))


def _hide(widget: Any) -> None:
    """Transparent, never activated, behind everything. Called before first map."""
    try:
        widget.attributes("-alpha", 0.0)
    except Exception:
        pass
    try:
        widget.update_idletasks()
        hwnd = _hwnd(widget)
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        _user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_FLAGS)
    except Exception:
        pass


def _grab(widget: Any, out: Path) -> Path:
    from PIL import Image

    hwnd = _hwnd(widget)
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = _user32.GetWindowDC(hwnd)
    mdc = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    _gdi32.SelectObject(mdc, bmp)
    _user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
    info = _BitmapInfoHeader()
    info.biSize = ctypes.sizeof(info)
    info.biWidth, info.biHeight, info.biPlanes, info.biBitCount = w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    _gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0)
    _gdi32.DeleteObject(bmp)
    _gdi32.DeleteDC(mdc)
    _user32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _patch_windows() -> None:
    """Every CTk root and Toplevel the app creates is born hidden."""
    import customtkinter as ctk

    base_root, base_top = ctk.CTk, ctk.CTkToplevel

    class HiddenRoot(base_root):  # type: ignore[misc, valid-type]
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            _hide(self)

    class HiddenTop(base_top):  # type: ignore[misc, valid-type]
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            _hide(self)

        def grab_set(self) -> None:  # a modal grab on a hidden window is a trap
            return None

        def focus_force(self) -> None:
            return None

        def lift(self, *_a: Any) -> None:
            return None

        def attributes(self, *args: Any, **kw: Any) -> Any:
            if args and args[0] in ("-topmost", "-alpha") and len(args) > 1:
                return None
            return super().attributes(*args, **kw)

    ctk.CTk = HiddenRoot
    ctk.CTkToplevel = HiddenTop
    try:
        from tkinter import messagebox

        messagebox.askokcancel = lambda *a, **k: False  # type: ignore[assignment]
        messagebox.askyesno = lambda *a, **k: False  # type: ignore[assignment]
        messagebox.showinfo = lambda *a, **k: None  # type: ignore[assignment]
        messagebox.showwarning = lambda *a, **k: None  # type: ignore[assignment]
        messagebox.showerror = lambda *a, **k: None  # type: ignore[assignment]
    except Exception:
        pass

    from companion.ui.widgets import table

    original = table.DataGrid.__init__

    def tracked(self: Any, *a: Any, **kw: Any) -> None:
        original(self, *a, **kw)
        GRIDS.append(self)

    table.DataGrid.__init__ = tracked  # type: ignore[method-assign]


GRIDS: list[Any] = []


def _walk(widget: Any) -> list[Any]:
    found = [widget]
    try:
        for child in widget.winfo_children():
            found.extend(_walk(child))
    except Exception:
        pass
    return found


def _mapped(widget: Any) -> bool:
    try:
        return bool(widget.winfo_ismapped())
    except Exception:
        return False


def find_button(root: Any, text: str) -> Any:
    import customtkinter as ctk

    for widget in _walk(root):
        if isinstance(widget, ctk.CTkButton) and _mapped(widget):
            try:
                if str(widget.cget("text")).strip() == text:
                    return widget
            except Exception:
                continue
    return None


def find_entry(root: Any, hint: str = "") -> Any:
    import customtkinter as ctk

    for widget in _walk(root):
        if isinstance(widget, ctk.CTkEntry) and _mapped(widget):
            try:
                placeholder = str(widget.cget("placeholder_text") or "")
            except Exception:
                placeholder = ""
            if not hint or hint.lower() in placeholder.lower():
                return widget
    return None


def _type(entry: Any, text: str) -> None:
    try:
        entry.delete(0, "end")
    except Exception:
        pass
    entry.insert(0, text)
    try:
        entry.event_generate("<KeyRelease>")
    except Exception:
        pass


def _live_grid(root: Any) -> Any:
    for grid in reversed(GRIDS):
        try:
            if grid.widget.winfo_exists() and grid.widget.winfo_ismapped():
                return grid
        except Exception:
            continue
    return None


class _Click:
    state = 0


def _top_players(svc: Any, n: int) -> list[dict[str, Any]]:
    players = list(svc.store.snapshot().squad.players)
    players.sort(key=lambda p: -int(p.get("overallrating") or 0))
    return [dict(p) for p in players[:n]]


def _scenario(name: str, shell: Any, svc: Any) -> str:
    """Drive the real UI into a state. Returns what to capture: a surface key or 'toplevel'."""
    from companion.app import events as E
    from companion.app.commands.player import lock_player

    if name == "club_selected":
        shell._show("club")
        _settle(shell.root, 0.5)
        grid = _live_grid(shell.root)
        if grid is not None:
            grid._on_click(0, _Click())
        return "club"
    if name == "club_checked":
        shell._show("club")
        _settle(shell.root, 0.5)
        grid = _live_grid(shell.root)
        if grid is not None:
            for slot in (0, 2, 4):
                grid._on_check(slot, _Click())
        return "club"
    if name in ("player_open", "player_edited", "player_ai", "player_card"):
        best = _top_players(svc, 1)
        if best:
            p = best[0]
            lock_player(svc, int(p["playerid"]), record=p, name=str(p.get("name") or ""),
                        source="squad", teamid=svc.store.snapshot().squad.teamid)
            if name == "player_edited":
                svc.store.dispatch(E.FieldEdited("overallrating", int(p.get("overallrating") or 70) + 4))
                svc.store.dispatch(E.FieldEdited("potential", int(p.get("potential") or 70) + 3))
                svc.store.dispatch(E.FieldEdited("sprintspeed", 90))
        if name == "player_ai":
            shell._surface_vms.setdefault("player", {})["player_source"] = "presets"
        if name == "player_card" and best and svc.catalog is not None:
            # A chosen Library card, as if picked from the results: the preview state.
            hits = svc.catalog.search(str(best[0].get("name") or ""), limit=5)
            if hits:
                shell._surface_vms.setdefault("player", {})["pending_library_card"] = dict(hits[0])
        shell._show("player")
        return "player"
    if name == "review_edited":
        best = _top_players(svc, 1)
        if best:
            p = best[0]
            lock_player(svc, int(p["playerid"]), record=p, name=str(p.get("name") or ""),
                        source="squad", teamid=svc.store.snapshot().squad.teamid)
            svc.store.dispatch(E.FieldEdited("overallrating", int(p.get("overallrating") or 70) + 4))
            svc.store.dispatch(E.FieldEdited("sprintspeed", 90))
        shell._show("player")
        _settle(shell.root, 0.4)
        shell.review()
        return "toplevel"
    if name == "sign_results":
        shell._show("add_player")
        _settle(shell.root, 0.5)
        page = shell._surfaces.get("add_player")
        search = getattr(page, "sign_search", None)
        if search is not None:
            search.view["verified_only"] = False
            search._sync_shop_toggles()
            _type(search.query, "Mbappe")
            search.search()
            _settle(shell.root, 1.5)
            if search.model.rows:
                search.select(search.model.rows[0])
        return "add_player"
    if name in ("sign_base", "sign_collection"):
        # Sign's collection filter and Best/Base/All switch, driven like a click.
        shell._show("add_player")
        _settle(shell.root, 0.5)
        page = shell._surfaces.get("add_player")
        search = getattr(page, "sign_search", None)
        if search is not None:
            search.view["verified_only"] = False
            search._sync_shop_toggles()
            if name == "sign_base":
                _type(search.query, "Maldini")
                search.filters._pick_changed("base")
            else:
                search.set_year("26")
                search.filters._menu_changed("Team of the Year")
            _settle(shell.root, 1.5)
        return "add_player"
    if name in ("library_search", "sign_search"):
        key = "library" if name == "library_search" else "add_player"
        shell._show(key)
        _settle(shell.root, 0.5)
        entry = find_entry(shell.host)
        if entry is not None:
            _type(entry, "Mbappe")
        _settle(shell.root, 0.2)
        btn = find_button(shell.host, "Search")
        if btn is not None:
            btn.invoke()
        _settle(shell.root, 1.5)
        return key
    if name == "planner":
        from companion.ui.surfaces.planner import open_planner

        open_planner(svc, [_board(p) for p in _top_players(svc, 3)], mode="per_player",
                     club_name="Real Madrid")
        return "toplevel"
    raise SystemExit(f"unknown scenario {name}")


def _board(player: dict[str, Any]) -> dict[str, Any]:
    from companion.ui.surfaces._common import squad_board_row

    return squad_board_row(player)


def _hermetic_root(install: Path) -> Path:
    import _winapi

    tmp = Path(tempfile.mkdtemp(prefix="companion-capture-"))
    src_db = install / "state.sqlite"
    if src_db.is_file():
        live = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
        copy = sqlite3.connect(tmp / "state.sqlite")
        live.backup(copy)
        copy.close()
        live.close()
    for name in ("profiles.json",):
        if (install / name).is_file():
            shutil.copy2(install / name, tmp / name)
    if (install / "portraits").is_dir():
        shutil.copytree(install / "portraits", tmp / "portraits")
    if (install / "card_db").is_dir():
        _winapi.CreateJunction(str(install / "card_db"), str(tmp / "card_db"))
    return tmp


def _latest_squad(db: Any) -> tuple[str, dict[str, Any]] | None:
    row = db.latest_save_snapshot("squad")
    if row is None:
        return None
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    return row.save_uid, payload


def _services(root: Path, state: str) -> Any:
    from companion.app import events as E
    from companion.app.services import Services
    from companion.app.store import Store
    from companion.bootstrap import _hydrate_state, _try_catalog
    from companion.core.clock import SystemClock
    from companion.core.db import open_state_db
    from companion.core.executor import InlineExecutor
    from companion.core.paths import RealAppPaths
    from companion.core.transport.fake import FakeTransport
    from companion.core.transport.v3 import Pill

    paths = RealAppPaths(root)
    paths.ensure_layout()
    db = open_state_db(paths)
    store = Store(_hydrate_state(db))
    transport = FakeTransport()
    squad = _latest_squad(db) if state == "live" else None
    if squad is not None:
        save_uid, payload = squad
        live = replace(
            transport.liveness(),
            save_uid=save_uid,
            session_id=str(payload.get("session_id") or transport.liveness().session_id),
            core_version="2.6.31",
        )
    else:
        live = replace(
            transport.liveness(),
            pill=Pill.OFF,
            armed=False,
            pid=None,
            pid_alive=False,
            message="Start FC 26 with Live Editor, then load Career.",
            capabilities={},
        )
    transport.set_liveness(live)
    svc = Services(
        paths=paths,
        clock=SystemClock(),
        executor=InlineExecutor(),
        transport=transport,
        store=store,
        db=db,
        catalog=_try_catalog(paths, db),
    )
    svc.store.dispatch(E.LivenessChanged(live))
    if squad is not None:
        save_uid, payload = squad
        svc.store.dispatch(
            E.SquadSynced(
                save_uid=save_uid,
                session_id=live.session_id,
                teamid=payload.get("teamid"),
                players=list(payload.get("players") or ()),
                taken=datetime.now(timezone.utc),
                free_agents=list(payload.get("free_agents") or ()),
            )
        )
    return svc


def _settle(root: Any, seconds: float = 0.6) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def _toplevels(root: Any) -> list[Any]:
    import customtkinter as ctk

    return [w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel)]


def _close_toplevels(root: Any) -> None:
    for win in _toplevels(root):
        try:
            win.destroy()
        except Exception:
            pass


def _new_shell(install: Path, state: str) -> tuple[Any, Any, Path]:
    from companion.ui.shell import Shell

    root_dir = _hermetic_root(install)
    svc = _services(root_dir, state)
    shell = Shell(svc)
    shell._poll = lambda *a, **k: None  # type: ignore[method-assign]
    shell._render()
    return shell, svc, root_dir


def _dispose(shell: Any, svc: Any, root_dir: Path) -> None:
    try:
        shell.close()
    except Exception:
        pass
    try:
        svc.db.close()
    except Exception:
        pass
    shutil.rmtree(root_dir, ignore_errors=True)


def capture_scenarios(out: Path, sizes: list[tuple[int, int]], scenarios: list[str],
                      state: str, install: Path) -> list[Path]:
    """Each scenario gets a fresh app so one cannot leak state into the next."""
    _user32.SetProcessDPIAware()
    _patch_windows()
    saved: list[Path] = []
    for name in scenarios:
        for width, height in sizes:
            shell, svc, root_dir = _new_shell(install, state)
            try:
                shell.root.geometry(f"{width}x{height}+0+0")
                _settle(shell.root, 0.5)
                target = _scenario(name, shell, svc)
                _settle(shell.root, 0.9)
                tag = f"{width}x{height}"
                if target == "toplevel":
                    tops = _toplevels(shell.root)
                    for index, win in enumerate(tops):
                        suffix = "" if index == 0 else f"_{index}"
                        saved.append(_grab(win, out / f"{tag}_{name}{suffix}.png"))
                    saved.append(_grab(shell.root, out / f"{tag}_{name}_main.png"))
                else:
                    saved.append(_grab(shell.root, out / f"{tag}_{name}.png"))
            finally:
                _dispose(shell, svc, root_dir)
    return saved


def capture(out: Path, sizes: list[tuple[int, int]], surfaces: list[str],
            extras: list[str], state: str, install: Path) -> list[Path]:
    _user32.SetProcessDPIAware()
    _patch_windows()
    shell, svc, root_dir = _new_shell(install, state)
    saved: list[Path] = []
    try:
        for width, height in sizes:
            shell.root.geometry(f"{width}x{height}+0+0")
            _settle(shell.root, 0.5)
            tag = f"{width}x{height}"
            for key in surfaces:
                shell._show(key)
                _settle(shell.root, 0.8)
                saved.append(_grab(shell.root, out / f"{tag}_{key}.png"))
            for extra in extras:
                shell._show(surfaces[0] if surfaces else "club")
                _settle(shell.root, 0.3)
                if extra == "activity":
                    shell.open_drawer()
                    _settle(shell.root, 0.8)
                    saved.append(_grab(shell.root, out / f"{tag}_activity.png"))
                    shell.close_drawer()
                    continue
                if extra == "settings":
                    shell._open_settings()
                elif extra == "palette":
                    shell.open_palette()
                elif extra == "review":
                    shell.review()
                _settle(shell.root, 0.8)
                for index, win in enumerate(_toplevels(shell.root)):
                    suffix = "" if index == 0 else f"_{index}"
                    saved.append(_grab(win, out / f"{tag}_{extra}{suffix}.png"))
                _close_toplevels(shell.root)
                _settle(shell.root, 0.2)
            if "activity" not in extras:
                continue
            shell.open_drawer()
            for key in ("club", "player", "add_player", "library"):
                if key in surfaces:
                    shell._show(key)
                    _settle(shell.root, 0.8)
                    saved.append(_grab(shell.root, out / f"{tag}_{key}_drawer.png"))
            shell.close_drawer()
    finally:
        _dispose(shell, svc, root_dir)
    return saved


def _sizes(text: str) -> list[tuple[int, int]]:
    out = []
    for part in text.split(","):
        w, _, h = part.strip().lower().partition("x")
        out.append((int(w), int(h)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sizes", default="1280x820")
    parser.add_argument("--surfaces", default=",".join(SURFACES))
    parser.add_argument("--extras", default=",".join(EXTRAS))
    parser.add_argument("--state", choices=("live", "empty"), default="live")
    parser.add_argument("--install", type=Path, default=DEFAULT_INSTALL)
    parser.add_argument("--scenarios", default="",
                        help="comma list: " + ",".join(SCENARIOS))
    args = parser.parse_args(argv)
    surfaces = [s for s in args.surfaces.split(",") if s]
    extras = [e for e in args.extras.split(",") if e]
    if args.scenarios:
        names = list(SCENARIOS) if args.scenarios == "all" else args.scenarios.split(",")
        saved = capture_scenarios(args.out, _sizes(args.sizes), names, args.state, args.install)
    else:
        saved = capture(args.out, _sizes(args.sizes), surfaces, extras, args.state, args.install)
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
