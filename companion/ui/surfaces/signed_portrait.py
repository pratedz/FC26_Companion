"""Signed-player portrait with explicit install/restore actions."""
from __future__ import annotations

from typing import Any
import customtkinter as ctk
from PIL import Image

from ...app.commands import signed_portraits as portraits
from .. import theme
from ..widgets.primitives import button, muted_label, text_label
from ._common import monogram, set_status


def avatar(host: Any, svc: Any, pid: int, name: str, *, size: int = 56) -> None:
    """Replace the monogram when the verified source image becomes ready."""
    current = [""]
    timer: list[Any] = [None]
    label = text_label(host, monogram(name, fallback="PL")[:2], size=theme.T_HEADING, bold=True)
    label.pack(expand=True)

    def update() -> None:
        if not host.winfo_exists():
            return
        state = portraits.portrait_state(svc, pid)
        path = portraits.preview_path(svc, state)
        signature = str(path) if path else ""
        if not signature and current[0]:
            label.configure(text=monogram(name, fallback="PL")[:2], image=None)
            label._portrait_image = None
            current[0] = ""
        if signature and signature != current[0]:
            try:
                with Image.open(path) as image:
                    picture = ctk.CTkImage(light_image=image.copy(), dark_image=image.copy(), size=(size, size))
                label.configure(text="", image=picture)
                label._portrait_image = picture
                current[0] = signature
            except (OSError, ValueError):
                pass
        timer[0] = host.after(1500, update)
    def destroyed(event: Any) -> None:
        if event.widget is host and timer[0] is not None:
            host.after_cancel(timer[0])
    host.bind("<Destroy>", destroyed, add="+")
    update()


def controls(host: Any, svc: Any, pid: int) -> None:
    state = portraits.portrait_state(svc, pid)
    if not state:
        return
    scope = (state.get("save_uid"), state.get("source_playerid"))
    box = ctk.CTkFrame(host, fg_color="transparent")
    box.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    label = muted_label(box, "", size=10, wraplength=190, justify="left")
    label.pack(fill="x", pady=(0, theme.SP2))
    busy = [False]
    timer: list[Any] = [None]
    buttons: list[Any] = []

    def run(action: str) -> None:
        if busy[0]:
            return
        busy[0] = True
        def work(_token: Any) -> None:
            try:
                selected = portraits.portrait_state(svc, pid)
                if (selected.get("save_uid"), selected.get("source_playerid")) != scope:
                    raise ValueError("The Career signing changed; select this player again.")
                if action == "load":
                    now = portraits.portrait_state(svc, pid)
                    source = now.get("source_playerid")
                    if not source:
                        raise ValueError("This older signing has no verified portrait source ID.")
                    result = portraits.ensure_source(svc, source, retry=True)
                    message = ("Portrait ready." if result.get("state") == "ready" else
                               "Portrait queued — switch Career screens to let Live Editor export it."
                               if result.get("state") == "queued" else result.get("error", "Portrait unavailable."))
                elif action == "install":
                    portraits.install(svc, pid)
                    message = "Portrait installed. Reopen the squad screen; if FC cached the image, restart FC with LE."
                else:
                    portraits.restore(svc, pid)
                    message = "Previous portrait restored. Reopen the squad screen; restart FC with LE if needed."
            except (ValueError, OSError) as exc:
                message = str(exc)
            def finished() -> None:
                busy[0] = False
                set_status(svc, message)
                if box.winfo_exists():
                    label.configure(text=message)
                    update(schedule=False)
            svc.executor.on_ui_thread(finished)
        for control in buttons:
            control.configure(state="disabled")
        svc.executor.submit(f"portrait-action:{pid}", work)

    load = button(box, "Load signed portrait", lambda: run("load"), kind="secondary", height=28)
    load.pack(fill="x", pady=(0, 5))
    install = button(box, "Install portrait in game", lambda: run("install"), kind="secondary", height=28)
    install.pack(fill="x", pady=(0, 5))
    restore = button(box, "Restore previous portrait", lambda: run("restore"), kind="ghost", height=28)
    restore.pack(fill="x")
    buttons.extend((load, install, restore))
    muted_label(box, "Optional: changes this player ID's menu portrait across saves while LE runs.",
                size=10, wraplength=190, justify="left").pack(fill="x", pady=(theme.SP2, 0))

    def update(*, schedule: bool = True) -> None:
        if not box.winfo_exists():
            return
        state = portraits.portrait_state(svc, pid)
        ready = state.get("state") == "ready"
        if not busy[0]:
            load.configure(state="normal" if state.get("source_playerid") else "disabled")
            install.configure(state="normal" if ready else "disabled")
            restore.configure(state="normal" if portraits.installed(svc, pid) else "disabled")
            if not label.cget("text") or label.cget("text").startswith("Portrait:"):
                labels = {"ready": "Signed player's image ready", "queued": "Waiting for a Career event",
                          "missing": state.get("error", "Unavailable in this game"),
                          "unknown": "Source ID unavailable for this older signing"}
                label.configure(text="Portrait: " + labels.get(state.get("state"), "Load to check availability"))
        if schedule:
            timer[0] = box.after(1500, update)
    def destroyed(event: Any) -> None:
        if event.widget is box and timer[0] is not None:
            box.after_cancel(timer[0])
    box.bind("<Destroy>", destroyed, add="+")
    update()
