"""Write generated Lua to queue/, generated/, and optionally the clipboard."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from . import paths
from . import lua_resolve
from . import profiles as profiles_mod


def _safe_stem(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", name.strip(), flags=re.UNICODE)
    return s.strip("_") or "script"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_lua(
    lua_source: str,
    *,
    stem: str = "action",
    to_queue: bool = True,
    to_generated: bool = True,
    clipboard: bool = False,
    queue_path: Optional[Union[str, Path]] = None,
    generated_path: Optional[Union[str, Path]] = None,
    pending_mode: str = "rebuild",
) -> dict:
    """
    Persist Lua source.

    pending_mode:
      - "rebuild": scan queue/ and rewrite _pending.txt (safe default)
      - "set": write _pending as this job only (turbo path after clear_stale)

    Returns dict with keys: queue_file, generated_file, clipboard (bool).
    """
    result = {
        "queue_file": None,
        "generated_file": None,
        "clipboard": False,
    }
    # Normalize newlines; Windows LE paste is happier with CRLF on clipboard.
    text = lua_source.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    text_crlf = text.replace("\n", "\r\n")
    safe = _safe_stem(stem)
    stamp = _timestamp()

    if to_generated:
        gdir = Path(generated_path) if generated_path else paths.generated_dir()
        gdir.mkdir(parents=True, exist_ok=True)
        gfile = gdir / f"{safe}_{stamp}.lua"
        gfile.write_text(text, encoding="utf-8", newline="\n")
        result["generated_file"] = str(gfile)

    if to_queue:
        qdir = Path(queue_path) if queue_path else paths.queue_dir()
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / "done").mkdir(parents=True, exist_ok=True)
        # Unique job name; bridge reads queue/_pending.txt (no Windows dir shell).
        qname = f"{safe}_{stamp}.lua"
        qfile = qdir / qname
        qfile.write_text(text, encoding="utf-8", newline="\n")
        result["queue_file"] = str(qfile)
        # Single-slot runner FIRST (bridge always tries this)
        try:
            (qdir / "_run_now.lua").write_text(text, encoding="utf-8", newline="\n")
        except OSError:
            pass
        try:
            from . import le_apply as _le_apply

            pending = qdir / "_pending.txt"
            if pending_mode == "set":
                # Turbo: queue was just cleared — one write, no glob scan
                _le_apply.invalidate_pending_cache()
                pending.write_text(qname + "\n", encoding="utf-8", newline="\n")
                names = [qname]
            else:
                # One rebuild; append only if race left us out (no second full scan)
                names = _le_apply.rebuild_pending()
                if qname not in names:
                    with pending.open("a", encoding="utf-8", newline="\n") as f:
                        f.write(qname + "\n")
                    names = list(names) + [qname]
                    _le_apply.invalidate_pending_cache()
            (qdir / "_wake.txt").write_text(
                f"wake {qname}\n", encoding="utf-8", newline="\n"
            )
            result["pending_names"] = names
        except Exception:
            pending = qdir / "_pending.txt"
            try:
                pending.write_text(qname + "\n", encoding="utf-8", newline="\n")
            except OSError:
                pass

    if clipboard:
        result["clipboard"] = copy_to_clipboard(text_crlf)

    return result


def enqueue_profile(
    profile_id: str,
    *,
    clipboard: bool = False,
    to_queue: bool = True,
    to_generated: bool = True,
) -> dict:
    """Resolve a profile to Lua and write it out."""
    prof = profiles_mod.get_profile(profile_id)
    lua = lua_resolve.resolve_profile_lua(prof)
    out = write_lua(
        lua,
        stem=prof.id,
        to_queue=to_queue,
        to_generated=to_generated,
        clipboard=clipboard,
    )
    out["profile_id"] = prof.id
    out["label"] = prof.label
    return out


def normalize_lua_for_le_clipboard(text: str) -> str:
    """Normalize Lua for Live Editor paste: UTF-8, CRLF, trailing newline, no BOM.

    LE on Windows pastes more reliably with CRLF. Avoid tabs-only corruption by
    keeping spaces as written; strip only trailing spaces per line.
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Strip trailing whitespace on each line (keeps indentation leading spaces)
    lines = [ln.rstrip(" \t") for ln in t.split("\n")]
    t = "\n".join(lines)
    if t and not t.endswith("\n"):
        t += "\n"
    # CRLF for Windows LE Lua Engine paste
    return t.replace("\n", "\r\n")


def _copy_win32_clipboard(payload: str) -> bool:
    """Set CF_UNICODETEXT via Win32 APIs — no console / PowerShell window."""
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = payload.encode("utf-16-le") + b"\x00\x00"
        h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_global:
            return False
        locked = kernel32.GlobalLock(h_global)
        if not locked:
            kernel32.GlobalFree(h_global)
            return False
        ctypes.memmove(locked, data, len(data))
        kernel32.GlobalUnlock(h_global)
        if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
            kernel32.GlobalFree(h_global)
            return False
        # Ownership transferred to clipboard — do not free h_global
        return True
    finally:
        user32.CloseClipboard()


def _no_window_flags() -> int:
    # CREATE_NO_WINDOW — never flash a console for helper tools
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the system clipboard. Returns True on success.

    Windows: Win32 clipboard API first (no window). PowerShell only as silent
    fallback with CREATE_NO_WINDOW. Then tkinter. No visible console flashes.
    """
    payload = text
    try:
        if "\n" in text or "\r" in text or text.lstrip().startswith("--") or "function" in text[:200]:
            payload = normalize_lua_for_le_clipboard(text)
    except Exception:
        payload = text

    if sys.platform == "win32":
        try:
            if _copy_win32_clipboard(payload):
                return True
        except Exception:
            pass
        # Silent PowerShell fallback (no console window)
        try:
            import base64

            b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
            ps = (
                "$b=[Convert]::FromBase64String('" + b64 + "'); "
                "$t=[Text.Encoding]::UTF8.GetString($b); "
                "Set-Clipboard -Value $t"
            )
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-STA",
                    "-Command",
                    ps,
                ],
                capture_output=True,
                timeout=25,
                check=False,
                creationflags=_no_window_flags(),
            )
            if completed.returncode == 0:
                return True
        except Exception:
            pass

    # tkinter — ONLY on the main thread. Creating a second Tcl interpreter from
    # a worker thread while CustomTkinter's root is live corrupts Tcl state and
    # produces the intermittent Tcl_AsyncDelete / "main thread is not in main
    # loop" crashes. Most callers reach this from background threads
    # (go_live -> copy_bridge_to_clipboard), which is exactly the unsafe case.
    if threading.current_thread() is threading.main_thread():
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(payload)
            root.update()  # keep on clipboard after destroy
            root.destroy()
            return True
        except Exception:
            pass

    try:
        if sys.platform == "darwin":
            completed = subprocess.run(
                ["pbcopy"],
                input=payload,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            return completed.returncode == 0
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                completed = subprocess.run(
                    cmd,
                    input=payload,
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if completed.returncode == 0:
                    return True
            except FileNotFoundError:
                continue
        return False
    except Exception:
        return False
