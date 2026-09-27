"""Patch chrome header/ops to a minimal layout."""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "src" / "ui" / "chrome.py"
text = p.read_text(encoding="utf-8")
start = text.index("def _build_header(app: Any) -> None:")
end = text.index("def _build_footer(app: Any) -> None:")

new = '''# Secondary actions under Tools (calm top chrome)
_TOOLS_MENU_LABEL = "Tools…"
_TOOLS_ACTIONS = (
    "Best match",
    "Batch squad",
    "Force drain",
    "Queue",
    "History",
    "Health",
    "Copy bridge",
    "Copy last Lua",
    "How to arm",
    "Install worker only",
    "Remove auto-arm",
)


def _on_tools_menu(app: Any, choice: str) -> None:
    """Dispatch Tools menu; reset label so the same action can re-fire."""
    try:
        menu = getattr(app, "_tools_menu", None)
        if menu is not None:
            menu.set(_TOOLS_MENU_LABEL)
    except Exception:
        pass
    if not choice or choice == _TOOLS_MENU_LABEL:
        return
    if choice == "Remove auto-arm":
        try:
            from .. import product as product_mod

            out = product_mod.remove_inject_autoarm()
            messagebox.showinfo("Remove auto-arm", out.get("next") or str(out))
            app._last_chrome_key = None
            app._refresh_sync_chrome()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Remove auto-arm", str(e))
        return
    handlers = {
        "Best match": getattr(app, "_best_match_for_target", None),
        "Batch squad": getattr(app, "_batch_apply_squad", None),
        "Force drain": getattr(app, "_force_drain", None),
        "Queue": getattr(app, "_show_queue_inspector", None),
        "History": getattr(app, "_show_job_history", None),
        "Health": getattr(app, "_show_health", None),
        "Copy bridge": getattr(app, "_copy_bridge_script", None),
        "Copy last Lua": getattr(app, "_copy_last_apply_lua", None),
        "How to arm": getattr(app, "_show_arm_guide", None),
        "Install worker only": getattr(app, "_enable_auto_apply", None),
    }
    fn = handlers.get(choice)
    if callable(fn):
        fn()


def _build_header(app: Any) -> None:
    """Minimal header: brand · status pill · one Arm CTA."""
    header = ctk.CTkFrame(app, fg_color=PANEL, corner_radius=0, height=56)
    header.pack(fill="x", side="top")
    header.pack_propagate(False)

    left = ctk.CTkFrame(header, fg_color="transparent")
    left.pack(side="left", padx=16, pady=8)

    title_row = ctk.CTkFrame(left, fg_color="transparent")
    title_row.pack(anchor="w")
    mark = ctk.CTkFrame(
        title_row,
        fg_color=CARD,
        width=30,
        height=30,
        corner_radius=8,
        border_width=1,
        border_color=ACCENT,
    )
    mark.pack(side="left", padx=(0, 8))
    mark.pack_propagate(False)
    ctk.CTkLabel(
        mark,
        text="LE",
        font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
        text_color=ACCENT,
    ).place(relx=0.5, rely=0.5, anchor="center")
    ctk.CTkLabel(
        title_row,
        text="Companion",
        font=ctk.CTkFont(family=FONT_UI, size=18, weight="bold"),
        text_color=TEXT,
    ).pack(side="left")
    ctk.CTkLabel(
        title_row,
        text=f"v{__version__}",
        font=ctk.CTkFont(family=FONT_UI, size=11),
        text_color=MUTED,
    ).pack(side="left", padx=(8, 0))

    right = ctk.CTkFrame(header, fg_color="transparent")
    right.pack(side="right", padx=16)
    brow = ctk.CTkFrame(right, fg_color="transparent")
    brow.pack(anchor="e")

    app._sync_pill = ctk.CTkFrame(
        brow,
        fg_color=PILL_OFF_BG,
        corner_radius=16,
        border_width=2,
        border_color=WARNING,
    )
    app._sync_pill.pack(side="left", padx=(0, 10))
    app._sync_pill_label = ctk.CTkLabel(
        app._sync_pill,
        text="  OFF  ",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=WARNING,
    )
    app._sync_pill_label.pack(padx=10, pady=6)

    app._inject_btn = w.btn(
        brow,
        "Arm",
        app._one_click_inject_arm,
        kind="primary",
        width=88,
        height=32,
        icon="bridge",
    )
    app._inject_btn.pack(side="left", padx=2)
    app._sync_btn = app._inject_btn  # _refresh_sync_chrome reuses this

    app._bridge_status_label = ctk.CTkLabel(
        right,
        textvariable=app.bridge_status_var,
        font=ctk.CTkFont(size=10),
        text_color=MUTED,
        anchor="e",
    )
    app._bridge_status_label.pack(anchor="e", pady=(2, 0))
    app._refresh_sync_chrome()


def _build_ops_bar(app: Any) -> None:
    """Slim ops: target/queue line + Undo + Tools menu."""
    bar = ctk.CTkFrame(app, fg_color=PANEL, corner_radius=0, height=36)
    bar.pack(fill="x", side="top")
    bar.pack_propagate(False)
    row = ctk.CTkFrame(bar, fg_color="transparent")
    row.pack(fill="x", padx=16, pady=4)
    app._ops_line = ctk.CTkLabel(
        row,
        text="Target · —  ·  Queue · 0 pending",
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        anchor="w",
    )
    app._ops_line.pack(side="left", fill="x", expand=True)

    app._tools_menu = ctk.CTkOptionMenu(
        row,
        values=[_TOOLS_MENU_LABEL, *_TOOLS_ACTIONS],
        command=lambda c: _on_tools_menu(app, c),
        width=108,
        height=28,
        fg_color=CARD,
        button_color=CARD,
        button_hover_color=ACCENT_HOVER,
        dropdown_fg_color=PANEL,
        dropdown_hover_color=CARD,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
    )
    app._tools_menu.set(_TOOLS_MENU_LABEL)
    app._tools_menu.pack(side="right", padx=(4, 0))
    w.btn(
        row, "Undo", app._undo_last_apply, kind="ghost", width=72, height=28, icon="back"
    ).pack(side="right", padx=2)


'''

p.write_text(text[:start] + new + text[end:], encoding="utf-8")
print("patched", p, "lines", len(p.read_text(encoding="utf-8").splitlines()))
