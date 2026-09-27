"""Editor tab — full PlayerEditSurface + AI fill."""

from __future__ import annotations

import threading
from typing import Any, Dict, List

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    messagebox = None  # type: ignore

from ... import editor_presets
from ... import grok_client
from ... import ovr_formula
from ... import player_apply
from ... import player_schema
from ... import target_players
from ...ui_theme import (
    ACCENT,
    BG,
    BORDER,
    CARD,
    ENTRY_H,
    LIST_BG,
    MUTED,
    RADIUS_SM,
    TEXT,
)
from .. import widgets as w

def _build_editor_tab(app: Any) -> None:
    """Shell first; heavy PlayerEditSurface deferred so tab switch / preload stay light."""
    root = ctk.CTkScrollableFrame(app.tab_editor, fg_color=BG)
    root.pack(fill="both", expand=True, padx=2, pady=2)
    app._editor_scroll = root

    # Same Find target block as Cards
    app._build_target_picker(root, compact=True)

    # Action bar — secondary only; Apply is dock-only
    bar = w.panel(root)
    bar.pack(fill="x", pady=(0, 10))
    row = ctk.CTkFrame(bar, fg_color="transparent")
    row.pack(fill="x", padx=14, pady=12)

    w.label(row, "Build label", muted=True, size=11).grid(row=0, column=0, sticky="w")
    w.entry(row, app.editor_name_var, width=160).grid(row=1, column=0, padx=(0, 10), sticky="w")

    app._editor_apply_btn = None
    w.btn(row, "Load from target", app._editor_load_from_target, kind="ghost", width=130).grid(
        row=1, column=1, padx=4
    )
    w.btn(
        row,
        "Add team →",
        lambda: _editor_to_add_team(app),
        kind="ghost",
        width=100,
    ).grid(row=1, column=2, padx=4)
    w.btn(row, "Preset", app._editor_apply_preset, kind="secondary", width=90).grid(
        row=1, column=3, padx=4
    )
    ctk.CTkComboBox(
        row,
        variable=app.editor_preset_var,
        width=150,
        height=ENTRY_H,
        values=editor_presets.preset_ids(),
        corner_radius=RADIUS_SM,
        fg_color=LIST_BG,
        border_color=BORDER,
        button_color=CARD,
        text_color=TEXT,
    ).grid(row=1, column=4, padx=4)
    w.btn(row, "Recalc OVR", app._editor_recalc_ovr, kind="secondary", width=100).grid(
        row=1, column=5, padx=4
    )
    w.btn(row, "Clear form", app._editor_clear, kind="ghost", width=100).grid(
        row=1, column=6, padx=4
    )
    w.btn(
        row,
        "Cards →",
        lambda: app._goto("  Cards  "),
        kind="ghost",
        width=90,
    ).grid(row=1, column=7, padx=4)

    # AI panel
    ai = w.panel(root)
    ai.pack(fill="x", pady=(0, 10))
    head = ctk.CTkFrame(ai, fg_color="transparent")
    head.pack(fill="x", padx=14, pady=(12, 4))
    title_row = ctk.CTkFrame(head, fg_color="transparent")
    title_row.pack(side="left")
    try:
        from ... import icons as _icons

        _ai_img = _icons.ctk_icon("ai", size=18)
    except Exception:
        _ai_img = None
    if _ai_img is not None:
        ctk.CTkLabel(title_row, text="", image=_ai_img, width=22).pack(side="left", padx=(0, 6))
    w.label(title_row, "AI create", bold=True, size=14).pack(side="left")
    ctk.CTkLabel(
        head,
        textvariable=app.grok_status_var,
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
    ).pack(side="right")

    w.label(
        ai,
        "Describe a player. Codex fills the form for preview — you choose topics, then Apply from the dock.",
        muted=True,
        size=11,
    ).pack(anchor="w", padx=14)

    airow = ctk.CTkFrame(ai, fg_color="transparent")
    airow.pack(fill="x", padx=14, pady=10)
    w.entry(airow, app.ai_prompt_var, width=400).pack(
        side="left", fill="x", expand=True, padx=(0, 8)
    )
    for child in airow.winfo_children():
        if isinstance(child, ctk.CTkEntry):
            child.pack_configure(fill="x", expand=True)
    w.btn(
        airow,
        "Connect Codex…",
        app._grok_connect_popup,
        kind="secondary",
        width=140,
        icon="connect",
    ).pack(side="left", padx=4)
    w.btn(airow, "AI fill", app._ai_fill_async, kind="accent", width=120, icon="ai").pack(
        side="left", padx=4
    )

    app.editor_preview = ctk.CTkLabel(
        root,
        text="Preview · empty form",
        text_color=ACCENT,
        font=ctk.CTkFont(size=12, weight="bold"),
        anchor="w",
        wraplength=1100,
        justify="left",
    )
    app.editor_preview.pack(fill="x", padx=8, pady=10)

    # Heavy accordion lives in a host — build after paint so tab switch stays instant
    app._editor_surface_host = ctk.CTkFrame(root, fg_color="transparent")
    app._editor_surface_host.pack(fill="both", expand=True)
    app._editor_surface = None
    app._editor_vars = {}
    app._ps_vars = {}
    app._cat_vars = {}
    # Longer idle gap than 1ms — lets segmented control + scroll canvas settle first
    app.after(40, app._finish_editor_surface)



def _finish_editor_surface(app: Any) -> None:
    """Create full PlayerEditSurface once (headers + lazy bodies)."""
    if getattr(app, "_editor_surface", None) is not None:
        return
    host = getattr(app, "_editor_surface_host", None)
    if host is None:
        return
    try:
        from ..player_edit import PlayerEditSurface

        app._editor_surface = PlayerEditSurface(host, compact=False)
        app._editor_vars = app._editor_surface.field_vars
        app._ps_vars = app._editor_surface.ps_vars
        app._cat_vars = app._editor_surface.cat_vars
    except Exception as e:  # noqa: BLE001
        try:
            app.status.set(f"Editor surface · {e}")
        except Exception:
            pass



def _ensure_editor_surface(app: Any) -> None:
    """Guarantee editor form exists before fill/apply."""
    if getattr(app, "_editor_surface", None) is None:
        app._finish_editor_surface()



def _editor_enabled_categories(app: Any) -> List[str]:
    app._ensure_editor_surface()
    surf = getattr(app, "_editor_surface", None)
    if surf is not None:
        return surf.enabled_categories()
    return list(player_schema.default_enabled_categories())



def _editor_to_add_team(app: Any) -> None:
    """Hand Editor form to Add team (From Editor / AI source)."""
    try:
        app._ensure_tab("  Add team  ")
    except Exception:
        pass
    try:
        from . import add_team as add_team_tab

        add_team_tab.set_source(app, add_team_tab._SOURCE_EDITOR)
        add_team_tab._pull_editor_into_label(app)
    except Exception:
        pass
    try:
        app._goto("  Add team  ")
    except Exception:
        pass
    try:
        app.status.set("Add team · Editor form loaded as source (safe free-agent path)")
    except Exception:
        pass


def _editor_cats_defaults(app: Any) -> None:
    app._ensure_editor_surface()
    defaults = player_schema.default_enabled_categories()
    for cid, var in app._cat_vars.items():
        var.set(cid in defaults)



def _editor_cats_all(app: Any) -> None:
    app._ensure_editor_surface()
    for var in app._cat_vars.values():
        var.set(True)



def _editor_cats_none(app: Any) -> None:
    app._ensure_editor_surface()
    for var in app._cat_vars.values():
        var.set(False)



def _editor_collect_card(app: Any) -> Dict[str, Any]:
    app._ensure_editor_surface()
    surf = getattr(app, "_editor_surface", None)
    if surf is None:
        card = player_schema.empty_player_card()
        card["name"] = app.editor_name_var.get().strip()
        app._editor_card = card
        return card
    card = surf.collect_card(name=app.editor_name_var.get().strip())
    app._editor_card = card
    return card



def _editor_fill_form(app: Any, card: Dict[str, Any]) -> None:
    app._ensure_editor_surface()
    card = player_schema.normalize_player_card(card)
    app._editor_card = card
    if card.get("name"):
        app.editor_name_var.set(str(card["name"]))
    surf = getattr(app, "_editor_surface", None)
    if surf is not None:
        surf.load_card(card, name_var=app.editor_name_var)
    try:
        app.editor_preview.configure(
            text="Preview · " + player_schema.format_card_summary(card)
        )
    except Exception:
        pass



def _editor_clear(app: Any) -> None:
    app._editor_fill_form(player_schema.empty_player_card())
    app.editor_name_var.set("")
    app.status.set("Editor cleared")



def _editor_apply_preset(app: Any) -> None:
    try:
        pid = app.editor_preset_var.get().strip() or "max_99"
        card = editor_presets.apply_preset(pid)
        app._editor_fill_form(card)
        app.editor_name_var.set(str(card.get("name") or pid))
        app.status.set(f"Preset · {editor_presets.preset_label(pid)}")
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("Preset", str(e))


def _editor_recalc_ovr(app: Any) -> None:
    """CE-style OVR recalculate + Best-At from attributes (pure formula)."""
    try:
        card = _editor_collect_card(app)
        card = player_schema.normalize_player_card(card)
        updated = ovr_formula.apply_calculated_ovr_to_card(card)
        best = ovr_formula.format_best_at_line(updated, top_n=3)
        ovr = updated.get("overallrating")
        app._editor_fill_form(updated)
        app.status.set(f"OVR recalculated · {ovr} · {best}")
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("Recalc OVR", str(e))



def _editor_load_from_target(app: Any) -> None:
    """Load squad player (if exported) into editor form by Target ID."""
    try:
        tid = int((app.target_var.get() or "").strip())
    except ValueError:
        messagebox.showwarning("Load", "Set Target ID first (Squad board or Find target).")
        return
    squad = target_players.load_squad()
    hit = None
    for p in squad.get("players") or []:
        if int(p.get("playerid") or 0) == tid:
            hit = p
            break
    if not hit:
        messagebox.showinfo(
            "Load",
            f"Player id {tid} not in exported squad.\n"
            "Export squad first, or fill the form manually / from Cards.",
        )
        return
    card = player_schema.empty_player_card()
    card["name"] = hit.get("name") or f"id{tid}"
    if hit.get("overallrating") is not None:
        card["overallrating"] = hit.get("overallrating")
    if hit.get("potential") is not None:
        card["potential"] = hit.get("potential")
    # only meta from squad export — full attrs need LE; still useful base
    app._editor_fill_form(card)
    app.editor_name_var.set(str(card["name"]))
    app.status.set(f"Loaded squad shell · {card['name']} · expand attrs as needed")



def _editor_apply(app: Any) -> None:
    app._ensure_editor_surface()
    if not app._require_live_worker(action="Apply from Editor"):
        return
    app._set_apply_status(
        "Editor Apply · checking target + topics…",
        prog=0.02,
        state="busy",
        step=1,
    )
    target = app._require_target_id()
    if target is None:
        app._set_apply_status(
            "✗ No Target ID · Find target or type Player ID first",
            prog=0,
            state="err",
            step=0,
        )
        return
    cats = app._editor_enabled_categories()
    if not cats:
        app._set_apply_status(
            "✗ No topics checked · enable at least one topic to write",
            prog=0,
            state="err",
            step=0,
        )
        return
    card = app._editor_collect_card()
    try:
        from ... import product as product_mod

        try:
            product_mod.set_sticky_categories(cats)
        except Exception:
            pass
        lua = player_apply.generate_apply_player_lua(
            card, target, enabled_categories=cats
        )
        app.editor_preview.configure(
            text="Queued · " + player_schema.format_card_summary(card)
        )
        detail = (
            f"{player_schema.format_card_summary(card)}\n"
            f"Topics: {', '.join(cats)}\n→ player id {target}"
        )
        fields = player_schema.card_to_field_updates(
            player_schema.normalize_player_card(dict(card)),
            enabled_categories=cats,
        )
        from .. import apply_flow

        def job(on_tick: Any) -> Any:
            return product_mod.turbo_apply_lua(
                lua,
                stem=f"edit_{target}",
                detail=detail,
                kind="editor",
                target_id=int(target),
                wait=True,
                on_tick=on_tick,
                snapshot_fields=fields,
                to_generated=True,
            )

        apply_flow.run_apply_job(
            app,
            job,
            detail=detail,
            busy_msg="Editor apply…",
            with_ticks=True,
            detailed=True,
        )
    except Exception as e:  # noqa: BLE001
        app._set_apply_status(f"✗ Editor apply failed · {e}", prog=0, state="err", step=0)

# ── Codex auth popup ──────────────────────────────────────────────



def _ai_fill_async(app: Any) -> None:
    if app._busy:
        return
    if not grok_client.is_connected():
        messagebox.showinfo(
            "Connect Codex",
            "Sign in with your Codex subscription first (same as Codex Build).",
        )
        app._grok_connect_popup()
        return
    prompt = app.ai_prompt_var.get().strip()
    if not prompt:
        messagebox.showwarning("Empty prompt", "Describe the player you want.")
        return
    app._busy = True
    app.status.set("Codex is generating…")
    app.grok_status_var.set("Generating · grok-4.5…")

    def work() -> None:
        try:
            card = grok_client.generate_player_from_prompt(prompt)
            app.after(0, lambda: app._ai_fill_done(card))
        except Exception as exc:  # noqa: BLE001
            failure = exc
            app.after(0, lambda: app._ai_fill_err(failure))
        finally:
            app._busy = False

    threading.Thread(target=work, daemon=True).start()



def _ai_fill_done(app: Any, card: Dict[str, Any]) -> None:
    app._editor_fill_form(card)
    app.grok_status_var.set(grok_client.auth_status())
    app.status.set("AI filled form · review topics, then Apply")
    messagebox.showinfo(
        "Preview ready",
        "Codex filled the form.\n\n"
        f"{player_schema.format_card_summary(card)}\n\n"
        "Check the topics you want to write, confirm Target, then Apply topics → Lua.",
    )



def _ai_fill_err(app: Any, e: Exception) -> None:
    app.grok_status_var.set(grok_client.auth_status())
    app.status.set(f"AI failed · {e}")
    messagebox.showerror("Codex error", str(e))

# ── catalog ──────────────────────────────────────────────────────

