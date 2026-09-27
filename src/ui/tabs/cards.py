"""Cards tab — one guided pipeline (source + destination + single primary action).

Flow:
  ① Destination (career target OR create new) + Source (search → variant)
  ② Fine-tune (optional editor / AI) then write

Always-visible pipeline strip:  Source card  →  Destination
Primary CTA: Import (LE path). Dock Apply = field-level write after fine-tune.
"""

from __future__ import annotations

import inspect
from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import target_players
from ...ui_theme import (
    ACCENT,
    BG,
    BORDER,
    CARD,
    ENTRY_H,
    FONT_UI,
    LIST_BG,
    MUTED,
    PANEL,
    RADIUS_SM,
    SP2,
    SP3,
    SP4,
    SUCCESS,
    TEXT,
)
from .. import widgets as w
from ..empty_state import empty_state
from ..player_edit import PlayerEditSurface

import threading
from typing import Dict, List, Optional

try:
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    messagebox = None  # type: ignore

from ... import card_catalog
from ... import card_compare
from ... import card_enrich
from ... import card_to_lua
from ... import favorites
from ... import grok_client
from ... import player_schema
from ... import target_players
from ...card_types import CardDict, CardRow  # CardDict/CardRow for handler boundaries

try:
    from ..stepper import step_header
except ImportError:  # pragma: no cover
    step_header = None  # type: ignore

_SECTION_HEADER = getattr(w, "section_header", None)
_EMPTY_STATE_PARAMS = inspect.signature(empty_state).parameters


def build_cards_tab(app: Any) -> None:
    root = ctk.CTkFrame(app.tab_cards, fg_color=BG)
    root.pack(fill="both", expand=True)
    app._card_root = root
    app._card_chat_history = []
    app._card_working = {}
    app._card_editor_dirty = False
    app._cards_step = "browse"  # browse | edit
    app._cards_edit_built = False

    # ── Step containers (only one visible) ──────────────────────────
    app._cards_browse = ctk.CTkFrame(root, fg_color=BG)
    app._cards_edit = ctk.CTkFrame(root, fg_color=BG)

    # Browse only on first paint — edit chrome is deferred until first Edit open
    _build_browse(app, app._cards_browse)
    app._cards_browse.pack(fill="both", expand=True)


def show_cards_browse(app: Any) -> None:
    app._cards_step = "browse"
    try:
        app._cards_edit.pack_forget()
    except Exception:
        pass
    app._cards_browse.pack(fill="both", expand=True)


def show_cards_edit(app: Any) -> None:
    """Switch Cards tab to the edit step (build chrome if needed)."""
    app._cards_step = "edit"
    try:
        app._cards_browse.pack_forget()
    except Exception:
        pass
    if not getattr(app, "_cards_edit_built", False):
        # Heavy edit chrome + surface only when user actually opens Edit
        try:
            for child in list(app._cards_edit.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        _build_edit_step(app, app._cards_edit)
        app._cards_edit_built = True
    app._cards_edit.pack(fill="both", expand=True)


def _browse_steps() -> list[str]:
    return ["Pick source & destination", "Fine-tune (optional)"]


def _pack_step_chrome(parent: Any, *, active: int) -> None:
    steps = _browse_steps()
    if step_header is not None:
        step_header(parent, steps, active).pack(fill="x", padx=SP4, pady=(SP3, SP2))
        return
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    ctk.CTkLabel(
        row,
        text=f"① {steps[0]}",
        font=ctk.CTkFont(
            family=FONT_UI,
            size=13 if active == 0 else 12,
            weight="bold" if active == 0 else "normal",
        ),
        text_color=ACCENT if active == 0 else MUTED,
    ).pack(side="left")
    ctk.CTkLabel(
        row,
        text=f"  →  ② {steps[1]}",
        font=ctk.CTkFont(
            family=FONT_UI,
            size=13 if active == 1 else 12,
            weight="bold" if active == 1 else "normal",
        ),
        text_color=ACCENT if active == 1 else MUTED,
    ).pack(side="left")


def _pipeline_source_label(app: Any) -> str:
    """Human label for current card/template source."""
    try:
        if getattr(app, "_cards_step", "browse") == "edit":
            card = _selected_import_card(app)
            if card:
                name = str(card.get("name") or "draft")
                ovr = card.get("overallrating") or "?"
                return f"{name} · OVR {ovr} (editor)"
    except Exception:
        pass
    hits = getattr(app, "_hits", None) or []
    idx = int(getattr(app, "_selected_idx", -1) or -1)
    if hits and 0 <= idx < len(hits):
        c = hits[idx]
        try:
            return card_catalog.format_card_line(c, idx).strip()
        except Exception:
            return str(c.get("name") or "card")
    q = ""
    try:
        q = (app.q_var.get() or "").strip()
    except Exception:
        pass
    if q:
        return f"Search “{q}” — pick a variant"
    return "No card selected · search a name"


def _pipeline_dest_label(app: Any) -> str:
    """Human label for destination (career target or create)."""
    _ensure_import_vars(app)
    mode = (app.import_mode_var.get() or "apply").strip().lower()
    if mode == "create":
        return "Add as new club player · safe free-agent overwrite + transfer"
    tid = ""
    try:
        tid = (app.target_var.get() or "").strip()
    except Exception:
        tid = ""
    tname = ""
    try:
        tname = (app.target_name_var.get() or "").strip()
    except Exception:
        tname = ""
    if tid.isdigit() and int(tid) > 0:
        if tname:
            return f"Overwrite Career · {tname} (id {tid})"
        return f"Overwrite Career player id {tid}"
    if tname:
        return f"Find Career · “{tname}” (need a player id)"
    return "Set Target Career player (Find squad name or type id)"


def update_cards_pipeline(app: Any) -> None:
    """Keep the sticky pipeline strip + primary CTA in sync with selection."""
    _ensure_import_vars(app)
    src = _pipeline_source_label(app)
    dest = _pipeline_dest_label(app)
    mode = (app.import_mode_var.get() or "apply").strip().lower()
    line = f"SOURCE  {src}   →   DEST  {dest}"
    try:
        if getattr(app, "cards_pipeline_var", None) is not None:
            app.cards_pipeline_var.set(line)
    except Exception:
        pass
    try:
        if getattr(app, "selected_label", None) is not None:
            app.selected_label.configure(text=f"Source · {src}"[:90])
    except Exception:
        pass
    # Primary button label mirrors mode
    btn = getattr(app, "_import_btn", None)
    if btn is not None:
        try:
            if mode == "create":
                btn.configure(text="Import · Add to club (safe)")
            else:
                btn.configure(text="Import · Apply to target")
        except Exception:
            pass
    # Destination fields enable/disable for create
    for name in ("_cards_target_frame",):
        fr = getattr(app, name, None)
        if fr is None:
            continue
        try:
            state = "disabled" if mode == "create" else "normal"
            for child in fr.winfo_children():
                try:
                    child.configure(state=state)
                except Exception:
                    pass
        except Exception:
            pass
    try:
        _refresh_import_preflight(app)
    except Exception:
        pass


def _section_title(parent: Any, title: str, **pack_kw: Any) -> Any:
    if _SECTION_HEADER is not None:
        return _SECTION_HEADER(parent, title).pack(**pack_kw)
    return w.label(parent, title, bold=True, size=13).pack(**pack_kw)


def _ensure_import_vars(app: Any) -> None:
    """Lazy-init Import / pipeline vars (browse rebuild-safe)."""
    if not hasattr(app, "import_mode_var"):
        app.import_mode_var = ctk.StringVar(value="apply")
    if not hasattr(app, "import_copy_name"):
        app.import_copy_name = ctk.BooleanVar(value=True)
    if not hasattr(app, "import_copy_head"):
        app.import_copy_head = ctk.BooleanVar(value=True)
    if not hasattr(app, "import_copy_birth"):
        app.import_copy_birth = ctk.BooleanVar(value=True)
    if not hasattr(app, "import_preflight_var"):
        app.import_preflight_var = ctk.StringVar(
            value="① Destination  ② Source card  ③ Import"
        )
    if not hasattr(app, "cards_pipeline_var"):
        app.cards_pipeline_var = ctk.StringVar(
            value="SOURCE  (none)   →   DEST  (set target or Create)"
        )
    if not hasattr(app, "_import_copy_cbs"):
        app._import_copy_cbs = {}
    if not hasattr(app, "_pipeline_traces_wired"):
        app._pipeline_traces_wired = False


def _wire_pipeline_traces(app: Any) -> None:
    if getattr(app, "_pipeline_traces_wired", False):
        return
    app._pipeline_traces_wired = True
    for var_name in ("import_mode_var", "target_var", "target_name_var", "q_var"):
        try:
            v = getattr(app, var_name, None)
            if v is not None:
                v.trace_add("write", lambda *_a: update_cards_pipeline(app))
        except Exception:
            pass


def _selected_import_card(app: Any) -> Optional[Dict[str, Any]]:
    """Card for Import: edit-step draft when editing, else browse selection."""
    # Prefer live editor boxes when on the Edit step (includes AI/user tweaks).
    if getattr(app, "_cards_step", "browse") == "edit":
        try:
            card = _collect_card_from_editor_boxes(app)
            if card and (card.get("name") or card.get("overallrating") is not None):
                return card
        except Exception:
            pass
    try:
        if getattr(app, "_hits", None) and 0 <= int(app._selected_idx) < len(app._hits):
            return dict(app._hits[int(app._selected_idx)])
    except Exception:
        pass
    if isinstance(getattr(app, "_editor_card", None), dict):
        ec = dict(app._editor_card)
        if ec.get("name") or ec.get("overallrating") is not None:
            return ec
    return None


def _refresh_import_preflight(app: Any) -> None:
    _ensure_import_vars(app)
    try:
        from ... import import_player
    except Exception:
        app.import_preflight_var.set("Import module unavailable")
        return

    card = _selected_import_card(app)
    mode = (app.import_mode_var.get() or "apply").strip().lower()
    if mode not in ("apply", "create"):
        mode = "apply"

    # Create + Head: default Head OFF unless user already forced it after pick
    if mode == "create" and not getattr(app, "_import_head_user_forced", False):
        try:
            if app.import_copy_head.get():
                app.import_copy_head.set(False)
        except Exception:
            pass

    if not card:
        app.import_preflight_var.set("Select a catalog card first")
        return

    try:
        enrich = import_player.resolve_enrich(card)
        avail = enrich.available()
    except Exception as e:  # noqa: BLE001
        app.import_preflight_var.set(f"Enrich failed · {e}")
        return

    cbs = getattr(app, "_import_copy_cbs", {}) or {}
    mapping = (
        ("name", app.import_copy_name, avail.get("name")),
        ("head", app.import_copy_head, avail.get("head")),
        ("birth", app.import_copy_birth, avail.get("birthdate")),
    )
    for key, var, ok in mapping:
        cb = cbs.get(key)
        if cb is not None:
            try:
                if ok:
                    cb.configure(state="normal")
                else:
                    var.set(False)
                    cb.configure(state="disabled")
            except Exception:
                pass
        elif not ok:
            try:
                var.set(False)
            except Exception:
                pass

    # Auto-enable Copy when fresh card has data (Apply). Create keeps Head off.
    card_key = (
        str(card.get("name") or ""),
        str(card.get("baseId") or card.get("playerid") or ""),
        str(card.get("overallrating") or ""),
        mode,
    )
    prev_key = getattr(app, "_import_card_key", None)
    if card_key != prev_key:
        app._import_card_key = card_key
        app._import_head_user_forced = False
        for key, var, ok in mapping:
            if not ok:
                continue
            if key == "head" and mode == "create":
                try:
                    var.set(False)
                except Exception:
                    pass
                continue
            try:
                var.set(True)
            except Exception:
                pass

    tid: Optional[int] = None
    try:
        raw = (app.target_var.get() or "").strip()
        if raw:
            tid = int(raw)
    except ValueError:
        tid = None

    try:
        plan = import_player.build_import_plan(
            import_player.ImportRequest(
                mode=mode,
                card=card,
                target_playerid=tid if mode == "apply" else None,
                copy_name=bool(app.import_copy_name.get()),
                copy_head=bool(app.import_copy_head.get()),
                copy_birthdate=bool(app.import_copy_birth.get()),
            )
        )
        note = plan.preflight
        if plan.warnings:
            note = f"{note} · ⚠ {'; '.join(plan.warnings)}"
        app.import_preflight_var.set(note)
    except ValueError as e:
        app.import_preflight_var.set(str(e))
    except Exception as e:  # noqa: BLE001
        app.import_preflight_var.set(f"Plan error · {e}")


def _run_import_card(app: Any) -> None:
    _ensure_import_vars(app)

    def _fail(msg: str) -> None:
        app.status.set(msg)
        messagebox.showerror("Import Player", msg)

    if getattr(app, "_busy", False):
        _fail("Busy — wait for search/AI to finish, then Import again.")
        return
    if getattr(app, "_apply_busy", False):
        _fail("Already applying — wait a few seconds, then Import again.")
        return

    card = _selected_import_card(app)
    if not card:
        _fail("Select a catalog card (or open Edit on a card) first.")
        return

    mode = (app.import_mode_var.get() or "apply").strip().lower()
    if mode not in ("apply", "create"):
        mode = "apply"

    tid: Optional[int] = None
    if mode == "apply":
        try:
            tid = int((app.target_var.get() or "").strip())
        except ValueError:
            tid = None
        if tid is None or tid <= 0:
            _fail("Apply mode needs a Target Player ID (Find squad name or type id).")
            return

    try:
        from ... import product as product_mod
        from .. import apply_flow

        copy_name = bool(app.import_copy_name.get())
        copy_head = bool(app.import_copy_head.get())
        copy_birth = bool(app.import_copy_birth.get())
        name = str(card.get("name") or "card")
        if mode == "apply":
            detail = f"Import Apply {name} → id {tid}"
        else:
            detail = f"Import Create {name}"
            if copy_head:
                detail += " · Head ON (freeze risk)"

        def job(_on_tick: Any) -> Any:
            return product_mod.import_card(
                card,
                mode=mode,
                target_playerid=tid,
                copy_name=copy_name,
                copy_head=copy_head,
                copy_birthdate=copy_birth,
                wait=True,
            )

        apply_flow.run_apply_job(
            app,
            job,
            detail=detail,
            busy_msg="Import Player…",
            detailed=False,
            copy_bridge_on_wait=False,
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"Import failed · {e}")


def _build_browse(app: Any, parent: Any) -> None:
    """Guided pipeline: Destination → Source → one primary Import CTA."""
    _ensure_import_vars(app)
    _wire_pipeline_traces(app)
    _pack_step_chrome(parent, active=0)

    # ── Sticky pipeline strip (always in sync) ───────────────────────
    pipe = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS_SM, border_width=1, border_color=ACCENT)
    pipe.pack(fill="x", padx=SP3, pady=(0, SP2))
    ctk.CTkLabel(
        pipe,
        text="PIPELINE",
        text_color=ACCENT,
        font=ctk.CTkFont(family=FONT_UI, size=10, weight="bold"),
    ).pack(anchor="w", padx=SP4, pady=(SP2, 0))
    app._cards_pipeline_label = ctk.CTkLabel(
        pipe,
        textvariable=app.cards_pipeline_var,
        text_color=TEXT,
        font=ctk.CTkFont(family=FONT_UI, size=13, weight="bold"),
        anchor="w",
        justify="left",
        wraplength=920,
    )
    app._cards_pipeline_label.pack(fill="x", padx=SP4, pady=(0, SP2))
    app._import_preflight_label = ctk.CTkLabel(
        pipe,
        textvariable=app.import_preflight_var,
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        anchor="w",
        justify="left",
        wraplength=920,
    )
    app._import_preflight_label.pack(fill="x", padx=SP4, pady=(0, SP3))

    # ── ① DESTINATION ────────────────────────────────────────────────
    dest = w.panel(parent)
    dest.pack(fill="x", padx=SP3, pady=(0, SP2))
    dhead = ctk.CTkFrame(dest, fg_color="transparent")
    dhead.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    _section_title(dhead, "① Destination — where stats go", anchor="w", side="left")
    sq = ctk.CTkLabel(
        dhead,
        text=target_players.squad_status_line(),
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
    )
    sq.pack(side="right")
    app._squad_labels.append(sq)

    mode_row = ctk.CTkFrame(dest, fg_color="transparent")
    mode_row.pack(fill="x", padx=SP4, pady=(0, SP2))
    for label, val in (
        ("Overwrite Career player", "apply"),
        ("Add as new club player (safe FA)", "create"),
    ):
        ctk.CTkRadioButton(
            mode_row,
            text=label,
            variable=app.import_mode_var,
            value=val,
            text_color=TEXT,
            font=ctk.CTkFont(size=12),
            fg_color=SUCCESS,
            hover_color=SUCCESS,
            border_color=BORDER,
            command=lambda: update_cards_pipeline(app),
        ).pack(side="left", padx=(0, SP3))

    app._cards_target_frame = ctk.CTkFrame(dest, fg_color="transparent")
    app._cards_target_frame.pack(fill="x", padx=SP4, pady=(0, SP2))
    trow = app._cards_target_frame
    w.label(trow, "Squad name", muted=True, size=11).grid(row=0, column=0, sticky="w")
    ne = w.entry(trow, app.target_name_var, width=180)
    ne.grid(row=1, column=0, padx=(0, SP2), sticky="w")
    ne.bind("<Return>", lambda _e: app._find_target())
    w.btn(trow, "Find", app._find_target, kind="accent", width=80, icon="target").grid(
        row=1, column=1, padx=(0, SP4)
    )
    w.label(trow, "Player ID", muted=True, size=11).grid(row=0, column=2, sticky="w")
    w.entry(trow, app.target_var, width=120).grid(row=1, column=2, sticky="w")

    tlist_wrap = ctk.CTkFrame(dest, fg_color=LIST_BG, corner_radius=RADIUS_SM)
    tlist_wrap.pack(fill="x", padx=SP4, pady=(0, SP3))
    tlb = w.listbox(tlist_wrap, height=2)
    tlb.pack(fill="x", padx=SP2, pady=SP2)
    tlb.bind("<<ListboxSelect>>", app._on_target_select)
    tlb.insert("end", "  Find by squad name — pick a Career player (for Overwrite mode)")
    app._target_lists.append(tlb)

    # ── ② SOURCE ─────────────────────────────────────────────────────
    search = w.panel(parent)
    search.pack(fill="x", padx=SP3, pady=(0, SP2))
    shead = ctk.CTkFrame(search, fg_color="transparent")
    shead.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    _section_title(shead, "② Source — card / template to use", anchor="w", side="left")
    app.selected_label = ctk.CTkLabel(
        shead,
        text="Source · none",
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
        anchor="e",
    )
    app.selected_label.pack(side="right")

    srow = ctk.CTkFrame(search, fg_color="transparent")
    srow.pack(fill="x", padx=SP4, pady=(0, SP2))
    w.label(srow, "Name", muted=True, size=11).grid(row=0, column=0, sticky="w")
    se = w.entry(srow, app.q_var, width=220)
    se.grid(row=1, column=0, padx=(0, SP2), sticky="w")
    se.bind("<Return>", lambda _e: app._search_async())
    app._card_search_entry = se
    w.label(srow, "Year", muted=True, size=11).grid(row=0, column=1, sticky="w")
    ctk.CTkComboBox(
        srow,
        variable=app.year_var,
        width=90,
        height=ENTRY_H,
        values=["", "local", "18", "19", "20", "21", "22", "23", "24", "25", "26", "futbin"],
        corner_radius=RADIUS_SM,
        fg_color=LIST_BG,
        border_color=BORDER,
        button_color=CARD,
        text_color=TEXT,
    ).grid(row=1, column=1, padx=(0, SP2), sticky="w")
    w.btn(srow, "Search", app._search_async, kind="primary", width=100, icon="search").grid(
        row=1, column=2, padx=SP2
    )
    w.btn(srow, "★ Fav", app._toggle_favorite, kind="ghost", width=70, icon="favorite").grid(
        row=1, column=3, padx=2
    )
    w.btn(srow, "Fav list", app._show_favorites_list, kind="ghost", width=80).grid(
        row=1, column=4, padx=2
    )
    w.btn(
        srow,
        "AI tweak →",
        lambda: _go_edit_then_ai(app),
        kind="secondary",
        width=100,
    ).grid(row=1, column=5, padx=(SP2, 0))

    frow = ctk.CTkFrame(search, fg_color="transparent")
    frow.pack(fill="x", padx=SP4, pady=(0, SP2))
    w.label(frow, "OVR min", muted=True, size=10).pack(side="left")
    w.entry(frow, app.filter_ovr_min, width=48).pack(side="left", padx=(SP2, SP3))
    w.label(frow, "max", muted=True, size=10).pack(side="left")
    w.entry(frow, app.filter_ovr_max, width=48).pack(side="left", padx=(SP2, SP3))
    w.label(frow, "Pos", muted=True, size=10).pack(side="left")
    w.entry(frow, app.filter_pos, width=48).pack(side="left", padx=(SP2, SP3))
    w.btn(frow, "Filter", app._apply_card_filters, kind="ghost", width=70, height=28).pack(side="left")

    prog = ctk.CTkFrame(search, fg_color="transparent")
    prog.pack(fill="x", padx=SP4, pady=(0, SP2))
    app.search_prog = ctk.CTkProgressBar(prog, height=6, progress_color=ACCENT, fg_color=LIST_BG)
    app.search_prog.pack(fill="x", side="left", expand=True)
    app.search_prog.set(0)
    app.search_prog_label = ctk.CTkLabel(
        prog, text="", text_color=MUTED, font=ctk.CTkFont(size=11), width=140
    )
    app.search_prog_label.pack(side="right", padx=(SP3, 0))

    # Variants list
    list_panel = w.panel(parent)
    list_panel.pack(fill="both", expand=True, padx=SP3, pady=(0, SP2))
    lhead = ctk.CTkFrame(list_panel, fg_color="transparent")
    lhead.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    _section_title(lhead, "Variants — pick one row (this is your Source)", anchor="w", side="left")
    w.btn(
        lhead,
        "Fine-tune stats →",
        lambda: _go_edit(app),
        kind="accent",
        width=140,
        height=28,
        icon="edit",
    ).pack(side="right", padx=SP2)
    w.btn(
        lhead,
        "Add team →",
        lambda: _handoff_add_team(app),
        kind="ghost",
        width=100,
        height=28,
    ).pack(side="right", padx=(0, SP2))
    w.btn(
        lhead,
        "Editor →",
        lambda: _handoff_editor(app),
        kind="ghost",
        width=90,
        height=28,
    ).pack(side="right", padx=(0, SP2))

    list_frame = ctk.CTkFrame(list_panel, fg_color=LIST_BG, corner_radius=RADIUS_SM)
    list_frame.pack(fill="both", expand=True, padx=SP4, pady=(0, SP2))

    app._variants_empty_host = ctk.CTkFrame(list_frame, fg_color=LIST_BG)
    app._variants_empty_host.place(relx=0, rely=0, relwidth=1, relheight=1)
    es_kw: dict[str, Any] = {
        "title": "No source card yet",
        "body": "Search a player name → pick a variant. That becomes SOURCE in the pipeline above.",
        "action_label": "Focus search",
        "action": lambda: app._focus_card_search(),
        "icon_text": "⌕",
    }
    if "icon_name" in _EMPTY_STATE_PARAMS:
        es_kw["icon_name"] = "card"
    empty_state(app._variants_empty_host, **es_kw)

    app.variant_list = w.listbox(list_frame, height=12)
    scroll = ctk.CTkScrollbar(list_frame, command=app.variant_list.yview)
    app.variant_list.configure(yscrollcommand=scroll.set)
    app.variant_list.pack(side="left", fill="both", expand=True, padx=(SP3, 0), pady=SP3)
    scroll.pack(side="right", fill="y", padx=(0, SP3), pady=SP3)
    app.variant_list.bind("<<ListboxSelect>>", app._on_variant_select)
    app.variant_list.bind("<Double-Button-1>", lambda _e: _run_import_card(app))

    # ── ③ PRIMARY ACTION (single CTA) ────────────────────────────────
    act = w.panel(parent)
    act.pack(fill="x", padx=SP3, pady=(0, SP3))
    ahead = ctk.CTkFrame(act, fg_color="transparent")
    ahead.pack(fill="x", padx=SP4, pady=(SP3, SP2))
    _section_title(ahead, "③ Write to game", anchor="w", side="left")
    ctk.CTkLabel(
        ahead,
        text="LE Import · Name / Head / Birth · double-click variant also Imports",
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
    ).pack(side="right")

    copy_row = ctk.CTkFrame(act, fg_color="transparent")
    copy_row.pack(fill="x", padx=SP4, pady=(0, SP2))
    w.label(copy_row, "Also copy", muted=True, size=11).pack(side="left", padx=(0, SP2))

    def _on_copy_toggle(which: str = "") -> None:
        if which == "head":
            try:
                if app.import_copy_head.get():
                    app._import_head_user_forced = True
            except Exception:
                pass
        update_cards_pipeline(app)

    for key, var, text in (
        ("name", app.import_copy_name, "Name"),
        ("head", app.import_copy_head, "Head"),
        ("birth", app.import_copy_birth, "Birthdate"),
    ):
        cb = ctk.CTkCheckBox(
            copy_row,
            text=text,
            variable=var,
            text_color=TEXT,
            font=ctk.CTkFont(size=12),
            fg_color=SUCCESS,
            hover_color=SUCCESS,
            border_color=BORDER,
            checkbox_width=16,
            checkbox_height=16,
            command=(lambda k=key: _on_copy_toggle(k)),
        )
        cb.pack(side="left", padx=(0, SP3))
        app._import_copy_cbs[key] = cb

    brow = ctk.CTkFrame(act, fg_color="transparent")
    brow.pack(fill="x", padx=SP4, pady=(0, SP3))
    app._import_btn = w.btn(
        brow,
        "Import · Apply to target",
        lambda: _run_import_card(app),
        kind="primary",
        width=200,
        height=36,
        icon="download",
    )
    app._import_btn.pack(side="left")
    w.btn(
        brow,
        "Fine-tune first →",
        lambda: _go_edit(app),
        kind="secondary",
        width=140,
        height=36,
    ).pack(side="left", padx=SP3)
    w.btn(
        brow,
        "Send → Add team",
        lambda: _handoff_add_team(app),
        kind="accent",
        width=140,
        height=36,
    ).pack(side="left", padx=(0, SP2))
    w.btn(
        brow,
        "Dock Apply (fields only)",
        lambda: app._apply_card(),
        kind="ghost",
        width=160,
        height=36,
    ).pack(side="left")

    update_cards_pipeline(app)


def _handoff_add_team(app: Any) -> None:
    from .. import handoffs

    card = _selected_import_card(app)
    if not handoffs.send_card_to_add_team(app, card):
        app.status.set("Select a variant first · then Send → Add team")


def _handoff_editor(app: Any) -> None:
    from .. import handoffs

    card = _selected_import_card(app)
    if not handoffs.send_card_to_editor(app, card):
        app.status.set("Select a variant first · then Editor")


def _go_edit_then_ai(app: Any) -> None:
    """Open fine-tune step so AI can tweak the selected source card."""
    _go_edit(app)
    try:
        app._set_apply_status(
            "AI ready · type a tweak (e.g. pace 95) then Import when done",
            prog=0.2,
            state="info",
        )
    except Exception:
        pass


def _toggle_card_ai_log(app: Any) -> None:
    log = getattr(app, "card_chat_log", None)
    btn = getattr(app, "_card_ai_log_btn", None)
    if log is None:
        return
    visible = bool(getattr(app, "_card_chat_log_visible", False))
    if visible:
        try:
            log.pack_forget()
        except Exception:
            pass
        app._card_chat_log_visible = False
        if btn is not None:
            try:
                btn.configure(text="Show AI log")
            except Exception:
                pass
    else:
        try:
            log.pack(fill="x", padx=SP3, pady=(0, SP2))
        except Exception:
            pass
        app._card_chat_log_visible = True
        if btn is not None:
            try:
                btn.configure(text="Hide AI log")
            except Exception:
                pass


def _build_edit_step(app: Any, parent: Any) -> None:
    # Step chrome
    bar = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=0, height=48)
    bar.pack(fill="x", side="top")
    bar.pack_propagate(False)
    brow = ctk.CTkFrame(bar, fg_color="transparent")
    brow.pack(fill="x", padx=SP4, pady=SP2)
    w.btn(
        brow,
        "← Back to results",
        lambda: show_cards_browse(app),
        kind="ghost",
        width=140,
        height=32,
        icon="back",
    ).pack(side="left")
    ctk.CTkLabel(
        brow,
        text="② Fine-tune source  ·  then Import (or dock Apply for fields only)",
        font=ctk.CTkFont(family=FONT_UI, size=13, weight="bold"),
        text_color=TEXT,
    ).pack(side="left", padx=SP4)
    app._edit_step_title = ctk.CTkLabel(
        brow,
        text="",
        font=ctk.CTkFont(size=12),
        text_color=ACCENT,
    )
    app._edit_step_title.pack(side="right")

    # Pipeline reminder on edit step
    _ensure_import_vars(app)
    epipe = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=0)
    epipe.pack(fill="x")
    ctk.CTkLabel(
        epipe,
        textvariable=app.cards_pipeline_var,
        text_color=TEXT,
        font=ctk.CTkFont(size=12, weight="bold"),
        anchor="w",
    ).pack(fill="x", padx=SP4, pady=SP2)

    # AI row (progressive: optional, compact; log collapsed by default)
    ai = w.panel(parent)
    ai.pack(fill="x", padx=SP3, pady=SP2)
    arow = ctk.CTkFrame(ai, fg_color="transparent")
    arow.pack(fill="x", padx=SP3, pady=SP2)
    w.label(arow, "AI tweak (optional)", muted=True, size=11).pack(side="left")
    app.card_chat_var = ctk.StringVar(value="")
    ce = ctk.CTkEntry(
        arow,
        textvariable=app.card_chat_var,
        height=ENTRY_H,
        fg_color=LIST_BG,
        border_color=BORDER,
        text_color=TEXT,
        placeholder_text="e.g. pace 95 · add rapid playstyle…",
    )
    ce.pack(side="left", fill="x", expand=True, padx=SP3)
    ce.bind("<Return>", lambda _e: app._card_ai_chat_send())
    w.btn(arow, "Send", app._card_ai_chat_send, kind="secondary", width=80).pack(side="left")
    app._card_ai_log_btn = w.btn(
        arow,
        "Show AI log",
        lambda: _toggle_card_ai_log(app),
        kind="ghost",
        width=110,
        height=28,
    )
    app._card_ai_log_btn.pack(side="left", padx=(SP2, 0))
    w.btn(
        arow,
        "Import now",
        lambda: _run_import_card(app),
        kind="primary",
        width=110,
        height=28,
    ).pack(side="left", padx=(SP3, 0))

    app.card_chat_log = ctk.CTkTextbox(
        ai, height=56, font=ctk.CTkFont(size=11), fg_color=LIST_BG, text_color=TEXT
    )
    # Collapsed by default — do not pack until "Show AI log"
    app._card_chat_log_visible = False
    app.card_chat_log.insert(
        "1.0",
        "Chat updates the boxes below. When ready: Import now (LE path) or dock Apply (fields only).\n",
    )
    app.card_chat_log.configure(state="disabled")

    # FULL HEIGHT edit surface — the entire redesign win
    edit_wrap = ctk.CTkFrame(parent, fg_color=BG)
    edit_wrap.pack(fill="both", expand=True, padx=SP3, pady=(0, SP3))
    app._card_edit_wrap = edit_wrap
    # Surface created lazily / on first edit open
    app._card_surface = None
    app._card_edit_vars = {}
    app._card_ps_vars = {}
    app._card_cat_vars = {}
    app._card_meta_line = None


def _ensure_edit_surface(app: Any) -> None:
    """Create PlayerEditSurface inside the edit step. Requires edit chrome first."""
    if getattr(app, "_card_surface", None) is not None:
        return
    # Build edit step chrome if Edit was never opened (fixes silent no-op click)
    if not getattr(app, "_cards_edit_built", False):
        if not hasattr(app, "_cards_edit") or app._cards_edit is None:
            return
        try:
            for child in list(app._cards_edit.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        _build_edit_step(app, app._cards_edit)
        app._cards_edit_built = True
    wrap = getattr(app, "_card_edit_wrap", None)
    if wrap is None:
        return
    host = ctk.CTkScrollableFrame(wrap, fg_color=BG)
    host.pack(fill="both", expand=True)
    app._card_edit_host = host
    app._card_surface = PlayerEditSurface(host, compact=True)
    app._card_edit_vars = app._card_surface.field_vars
    app._card_ps_vars = app._card_surface.ps_vars
    app._card_cat_vars = app._card_surface.cat_vars
    app._card_meta_line = app._card_surface.meta_line


def _go_edit(app: Any) -> None:
    """Open Cards edit step for the selected variant.

    Order: show edit chrome → create surface → load card.
    Surfaces errors in status so a dead click is never silent.
    """
    try:
        if not getattr(app, "_hits", None):
            try:
                from tkinter import messagebox

                messagebox.showinfo("Edit", "Search and select a card variant first.")
            except Exception:
                pass
            try:
                app._set_apply_status(
                    "Edit · search and select a variant first", prog=0, state="warn"
                )
            except Exception:
                pass
            return
        sel = ()
        try:
            sel = app.variant_list.curselection()
        except Exception:
            pass
        if sel:
            idx = int(sel[0])
        else:
            idx = int(getattr(app, "_selected_idx", 0) or 0)
        if idx < 0 or idx >= len(app._hits):
            try:
                from tkinter import messagebox

                messagebox.showinfo("Edit", "Search and select a card variant first.")
            except Exception:
                pass
            return

        show_cards_edit(app)
        _ensure_edit_surface(app)

        dirty = bool(getattr(app, "_card_editor_dirty", False))
        surf = getattr(app, "_card_surface", None)
        if surf is not None and getattr(surf, "dirty", False):
            dirty = True
        if not (dirty and idx == getattr(app, "_selected_idx", -1)):
            app._select_variant_index(idx, reset_chat=False)

        card = app._hits[idx] if 0 <= idx < len(app._hits) else {}
        try:
            if getattr(app, "_edit_step_title", None) is not None:
                app._edit_step_title.configure(
                    text=f"{card.get('name')} · OVR {card.get('overallrating')} · y{card.get('year')}"
                )
        except Exception:
            pass
        try:
            app.update_idletasks()
        except Exception:
            pass
        try:
            app._set_apply_status(
                f"Fine-tune · {card.get('name') or 'card'} · then Import (or dock Apply)",
                prog=0.3,
                state="info",
            )
            app.status.set(f"Cards edit · {card.get('name') or 'selected'}"[:100])
            update_cards_pipeline(app)
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        try:
            from tkinter import messagebox

            messagebox.showerror("Edit failed", str(e))
        except Exception:
            pass
        try:
            app._set_apply_status(f"✗ Edit failed · {e}", prog=0, state="err")
        except Exception:
            pass


def set_variants_empty(app: Any, empty: bool) -> None:
    host = getattr(app, "_variants_empty_host", None)
    if host is None:
        return
    try:
        if empty:
            host.lift()
            host.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            host.place_forget()
    except Exception:
        pass

# ── Handlers (extracted from PremiumApp) ──

def _search_async(app: Any) -> None:
    if app._busy:
        return
    if not hasattr(app, "variant_list"):
        app._ensure_tab("  Cards  ")
    app._busy = True
    app._search_gen += 1
    gen = app._search_gen
    app.search_prog.set(0)
    app.search_prog_label.configure(text="Searching…")
    app.status.set("Searching cards…")
    app.variant_list.delete(0, "end")
    app.variant_list.insert("end", "  Loading…")
    try:
        set_variants_empty(app, False)
    except Exception:
        pass

    def on_progress(frac: float, msg: str) -> None:
        if gen != app._search_gen:
            return
        app.after(0, lambda: app._set_search_progress(frac, msg))

    # Tk variables must be read on the UI thread. StringVar.get() calls into
    # Tcl, and doing that from a worker is what produces the intermittent
    # "main thread is not in main loop" crashes.
    year_val = app.year_var.get().strip() or None
    query_val = app.q_var.get()

    def work() -> None:
        try:
            year = year_val
            hits = card_catalog.search_cards(
                query_val, year=year, limit=100, progress=on_progress
            )

            def done() -> None:
                if gen != app._search_gen:
                    return
                app._show_hits(hits)

            app.after(0, done)
        except Exception as exc:  # noqa: BLE001
            failure = exc

            def err() -> None:
                if gen == app._search_gen:
                    app._search_err(failure)

            app.after(0, err)
        finally:
            def fin() -> None:
                if gen == app._search_gen:
                    app._busy = False

            app.after(0, fin)

    threading.Thread(target=work, daemon=True).start()



def _set_search_progress(app: Any, frac: float, msg: str) -> None:
    try:
        app.search_prog.set(max(0.0, min(1.0, float(frac))))
    except Exception:
        pass
    app.search_prog_label.configure(text=(msg or "")[:40])
    app.status.set(msg)



def _show_hits(app: Any, hits: List[CardRow]) -> None:
    # Normal search leaves favorites-only mode
    try:
        app.filter_fav_only.set(False)
    except Exception:
        pass
    app._all_hits = list(hits)
    app._hits = list(hits)
    app._apply_card_filters()
    app.search_prog.set(1.0)
    app.search_prog_label.configure(text=f"{len(app._hits)} shown")
    app.status.set(f"{len(app._hits)} card(s) · pick a variant · Import to write")
    try:
        update_cards_pipeline(app)
    except Exception:
        pass



def _apply_card_filters(app: Any) -> None:
    base = list(getattr(app, "_all_hits", None) or app._hits or [])
    if app.filter_fav_only.get() and not base:
        base = favorites.load()
        app._all_hits = list(base)
    out: List[Dict[str, Any]] = []
    omin = app.filter_ovr_min.get().strip()
    omax = app.filter_ovr_max.get().strip()
    pos = (app.filter_pos.get() or "").strip().upper()
    omi: Optional[int]
    oma: Optional[int]
    try:
        omi = int(omin) if omin else None
    except ValueError:
        omi = None
        app.status.set("OVR min ignored · enter a whole number")
    try:
        oma = int(omax) if omax else None
    except ValueError:
        oma = None
        app.status.set("OVR max ignored · enter a whole number")
    if omi is not None and oma is not None and omi > oma:
        omi, oma = oma, omi
    # Cache favorite keys once (is_favorite re-reads JSON per card)
    fav_keys: Optional[set] = None
    if app.filter_fav_only.get():
        try:
            fav_keys = {favorites._key(c) for c in favorites.load()}  # type: ignore[attr-defined]
        except Exception:
            fav_keys = None
    for c in base:
        if app.filter_fav_only.get():
            if fav_keys is not None:
                if favorites._key(c) not in fav_keys:  # type: ignore[attr-defined]
                    continue
            elif not favorites.is_favorite(c):
                continue
        ovr = c.get("overallrating")
        try:
            oi = int(ovr) if ovr is not None else None
        except (TypeError, ValueError):
            oi = None
        if omi is not None and (oi is None or oi < omi):
            continue
        if oma is not None and (oi is None or oi > oma):
            continue
        if pos:
            abbr = ""
            try:
                abbr = str(card_catalog._pos_abbr(c) or "").upper()
            except Exception:
                abbr = ""
            p1 = str(c.get("preferredposition1") or c.get("position") or "").upper()
            if pos != abbr and pos not in p1 and pos not in abbr:
                continue
        out.append(c)
    app._hits = out
    app.variant_list.delete(0, "end")
    try:
        
        set_variants_empty(app, not bool(out))
        # Stay on browse only if not already editing
        if getattr(app, "_cards_step", "browse") != "edit":
            show_cards_browse(app)
    except Exception:
        pass
    try:
        if hasattr(app, "search_prog_label"):
            app.search_prog_label.configure(text=f"{len(out)} shown")
    except Exception:
        pass
    if not out:
        app._selected_idx = -1
        try:
            app.selected_label.configure(text="Source · none")
        except Exception:
            pass
        app.status.set("No cards match filters")
        try:
            update_cards_pipeline(app)
        except Exception:
            pass
        return
    for i, c in enumerate(out):
        star = "★ " if favorites.is_favorite(c) else "  "
        app.variant_list.insert("end", star + card_catalog.format_card_line(c, i))
    app.variant_list.selection_clear(0, "end")
    app.variant_list.selection_set(0)
    app.variant_list.activate(0)
    # Select first row but stay on Browse (fine-tune is optional)
    app._select_variant_index(0)



def _show_favorites_list(app: Any) -> None:
    app.filter_fav_only.set(True)
    app._all_hits = favorites.load()
    app._apply_card_filters()
    # Hint how to leave favorites-only mode
    if not app._hits:
        app.status.set("No favorites · Search clears favorites-only filter")



def _toggle_favorite(app: Any) -> None:
    if not app._hits:
        return
    idx = app._selected_idx
    if idx < 0 or idx >= len(app._hits):
        return
    card = app._hits[idx]
    now = favorites.toggle(card)
    app._set_apply_status(
        f"{'★ Added' if now else 'Removed'} favorite · {card.get('name')}",
        prog=1.0 if now else 0.3,
        state="ok" if now else "info",
    )
    app._apply_card_filters()



def _on_variant_select(app: Any, _event: Any = None) -> None:
    sel = app.variant_list.curselection()
    if not sel or not app._hits:
        return
    app._select_variant_index(int(sel[0]))
    try:
        update_cards_pipeline(app)
    except Exception:
        pass



def _select_variant_index(app: Any, idx: int, *, reset_chat: bool = True) -> None:
    """Load variant into edit boxes + auto-enrich FUT.GG (no extra buttons)."""
    if not app._hits or idx < 0 or idx >= len(app._hits):
        return
    app._selected_idx = idx
    app._card_editor_dirty = False
    card = app._hits[idx]
    try:
        app.selected_label.configure(
            text="Source · " + card_catalog.format_card_line(card, idx)
        )
    except Exception:
        pass
    try:
        update_cards_pipeline(app)
    except Exception:
        pass
    app._load_card_into_editor_boxes(card)
    if reset_chat:
        app._card_chat_history = []
        if hasattr(app, "card_chat_log"):
            try:
                app.card_chat_log.configure(state="normal")
                app.card_chat_log.delete("1.0", "end")
                app.card_chat_log.insert(
                    "1.0",
                    f"Editing: {card.get('name')} OVR {card.get('overallrating')}\n"
                    f"Chat to change stats, or type in the boxes. Then Import (or dock Apply).\n",
                )
                app.card_chat_log.configure(state="disabled")
            except Exception:
                pass
    # Background enrich — fills boxes again when ready
    if card.get("slug") or card.get("eaId") or card.get("resourceId"):
        app._enrich_gen += 1
        en_gen = app._enrich_gen

        def work() -> None:
            try:
                en = card_enrich.enrich_card_from_futgg(card)

                def done() -> None:
                    if en_gen != app._enrich_gen:
                        return
                    app._on_variant_enriched(idx, en)

                app.after(0, done)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()



def _on_variant_enriched(app: Any, idx: int, card: CardDict) -> None:
    if 0 <= idx < len(app._hits):
        app._hits[idx] = card
    if getattr(app, "_all_hits", None):
        for i, c in enumerate(app._all_hits):
            if (c.get("slug") and c.get("slug") == card.get("slug")) or (
                c.get("eaId") and c.get("eaId") == card.get("eaId")
            ):
                app._all_hits[i] = card
                break
    if idx == app._selected_idx:
        # Skip overwrite if user/AI already touched boxes after select
        surface_dirty = bool(
            getattr(getattr(app, "_card_surface", None), "dirty", False)
        )
        if getattr(app, "_card_editor_dirty", False) or surface_dirty:
            if hasattr(app, "_card_meta_line") and app._card_meta_line is not None:
                try:
                    reg, plus = player_schema.card_playstyle_labels(card)
                    bits = [
                        f"Club {card.get('club') or '—'}",
                        f"Nation {card.get('nation') or '—'}",
                        f"PS {', '.join(reg[:6]) or '—'}",
                        f"PS+ {', '.join(plus[:4]) or '—'}",
                    ]
                    app._card_meta_line.configure(text=" · ".join(bits))
                except Exception:
                    pass
        else:
            app._load_card_into_editor_boxes(card)
        try:
            app.selected_label.configure(
                text="Source · " + card_catalog.format_card_line(card, idx)
            )
        except Exception:
            pass
        try:
            update_cards_pipeline(app)
        except Exception:
            pass


def _ensure_card_surface(app: Any) -> Any:
    """Guarantee Cards PlayerEditSurface exists (canonical edit path)."""
    if getattr(app, "_card_surface", None) is None:
        try:
            # Need edit chrome + wrap before surface can mount
            if not getattr(app, "_cards_edit_built", False):
                if hasattr(app, "_cards_edit") and app._cards_edit is not None:
                    _build_edit_step(app, app._cards_edit)
                    app._cards_edit_built = True
            _ensure_edit_surface(app)
        except Exception:
            pass
    return getattr(app, "_card_surface", None)



def _load_card_into_editor_boxes(app: Any, card: CardDict) -> None:
    """Fill PlayerEditSurface from selected card (surface-only)."""
    surf = app._ensure_card_surface()
    if surf is None:
        app._card_working = card_to_lua.card_to_editor_card(dict(card))
        app._card_editor_dirty = False
        return
    surf.load_card(card)
    app._card_working = surf.working
    app._card_editor_dirty = False



def _mark_card_editor_dirty(app: Any, *_args: Any) -> None:
    if getattr(app, "_loading_editor", False):
        return
    app._card_editor_dirty = True



def _collect_card_from_editor_boxes(app: Any) -> CardRow:
    """Collect working card from PlayerEditSurface (surface-only)."""
    surf = app._ensure_card_surface()
    if surf is not None:
        base = None
        if app._hits and 0 <= app._selected_idx < len(app._hits):
            base = dict(app._hits[app._selected_idx])
        card = surf.collect_card(base=base)
        app._card_working = dict(surf.working)
        return card
    # Surface unavailable (tab not built) — fall back to selected hit
    if app._hits and 0 <= app._selected_idx < len(app._hits):
        return card_to_lua.card_to_editor_card(dict(app._hits[app._selected_idx]))
    return card_to_lua.card_to_editor_card(dict(app._card_working or {}))



def _ensure_ai_log_visible(app: Any) -> None:
    """AI log is collapsed by default — expand so replies are visible."""
    log = getattr(app, "card_chat_log", None)
    if log is None:
        return
    if bool(getattr(app, "_card_chat_log_visible", False)):
        return
    try:
        log.pack(fill="x", padx=SP3, pady=(0, SP2))
        app._card_chat_log_visible = True
        btn = getattr(app, "_card_ai_log_btn", None)
        if btn is not None:
            btn.configure(text="Hide AI log")
    except Exception:
        pass


def _card_ai_chat_send(app: Any) -> None:
    """Send AI tweak from Cards edit step — always show feedback."""
    try:
        msg = (getattr(app, "card_chat_var", ctk.StringVar(value="")).get() or "").strip()
    except Exception:
        msg = ""
    if not msg:
        try:
            app._set_apply_status("AI · type a request first (e.g. pace 95)", prog=0, state="warn")
        except Exception:
            pass
        return
    # Must be on edit step with a selection
    if getattr(app, "_cards_step", "browse") != "edit":
        _go_edit(app)
    if not app._hits or int(getattr(app, "_selected_idx", -1)) < 0:
        try:
            messagebox.showinfo("AI chat", "Select a card variant first, then Edit selected.")
        except Exception:
            pass
        return
    if not grok_client.is_connected():
        try:
            messagebox.showinfo(
                "Connect Codex",
                "AI chat needs Codex signed in.\n\nUse Connect Codex on Home, then try again.",
            )
        except Exception:
            pass
        try:
            app._grok_connect_popup()
        except Exception:
            pass
        try:
            app._set_apply_status("AI · Codex not connected", prog=0, state="warn")
        except Exception:
            pass
        return

    _ensure_ai_log_visible(app)
    try:
        card = app._collect_card_from_editor_boxes()
    except Exception as e:  # noqa: BLE001
        try:
            messagebox.showerror("AI chat", f"Could not read editor fields:\n{e}")
        except Exception:
            pass
        return

    try:
        app.card_chat_var.set("")
    except Exception:
        pass
    app._card_chat_append("You", msg)
    app._card_chat_append("AI", "Thinking…")
    try:
        app._set_apply_status("AI · thinking…", prog=0.4, state="busy")
        app.status.set("AI chat · waiting for Codex…")
    except Exception:
        pass
    app._busy = True
    hist = list(getattr(app, "_card_chat_history", []) or [])

    def work() -> None:
        try:
            updated = grok_client.chat_edit_card(card, msg, history=hist)
            app.after(0, lambda u=updated, m=msg: app._card_ai_chat_done(u, m))
        except Exception as e:  # noqa: BLE001
            err = e
            app.after(0, lambda err=err: app._card_ai_chat_fail(err))
        finally:
            app.after(0, lambda: setattr(app, "_busy", False))

    threading.Thread(target=work, daemon=True).start()


def _card_chat_append(app: Any, who: str, text: str) -> None:
    if not hasattr(app, "card_chat_log"):
        try:
            app.status.set(f"{who}: {text}"[:100])
        except Exception:
            pass
        return
    try:
        _ensure_ai_log_visible(app)
        app.card_chat_log.configure(state="normal")
        content = app.card_chat_log.get("1.0", "end")
        if "Thinking…" in content:
            lines = content.rstrip().split("\n")
            if lines and "Thinking…" in lines[-1]:
                app.card_chat_log.delete("1.0", "end")
                app.card_chat_log.insert("1.0", "\n".join(lines[:-1]) + "\n")
        app.card_chat_log.insert("end", f"{who}: {text}\n")
        app.card_chat_log.see("end")
        app.card_chat_log.configure(state="disabled")
    except Exception:
        try:
            app.status.set(f"{who}: {text}"[:100])
        except Exception:
            pass


def _card_ai_chat_done(app: Any, updated: CardDict, user_msg: str) -> None:
    try:
        app._card_chat_history.append({"role": "user", "content": user_msg})
        app._card_chat_history.append(
            {"role": "assistant", "content": "Updated card stats from your request."}
        )
        app._card_editor_dirty = True
        app._card_working = updated
        if app._hits and 0 <= app._selected_idx < len(app._hits):
            merged = dict(app._hits[app._selected_idx])
            merged.update(updated)
            app._hits[app._selected_idx] = merged
        # Stay on edit step and refresh boxes
        if getattr(app, "_cards_step", "") != "edit":
            show_cards_edit(app)
        _ensure_edit_surface(app)
        app._load_card_into_editor_boxes(updated)
        summary = (
            f"Updated. OVR {updated.get('overallrating')} · "
            f"PAC {updated.get('acceleration')}/{updated.get('sprintspeed')} · "
            "Review boxes → Apply to game."
        )
        app._card_chat_append("AI", summary)
        app._set_apply_status(
            "✓ AI updated the card · review stats → Apply to game",
            prog=1.0,
            state="ok",
        )
        app.status.set(
            f"AI done · OVR {updated.get('overallrating')} · Apply when ready"[:100]
        )
    except Exception as e:  # noqa: BLE001
        app._card_ai_chat_fail(e)


def _card_ai_chat_fail(app: Any, e: Exception) -> None:
    msg = str(e) or e.__class__.__name__
    app._card_chat_append("AI", f"Error: {msg}")
    try:
        app._set_apply_status(f"✗ AI chat failed · {msg}", prog=0, state="err")
        app.status.set(f"AI failed · {msg}"[:100])
    except Exception:
        pass
    try:
        messagebox.showerror("AI chat failed", msg)
    except Exception:
        pass



def _target_as_before_card(app: Any) -> Optional[CardDict]:
    """Best-effort before card from squad for current target id."""
    try:
        tid = int((app.target_var.get() or "").strip())
    except ValueError:
        return None
    squad = target_players.load_squad()
    for p in squad.get("players") or []:
        if int(p.get("playerid") or 0) == tid:
            return dict(p)
    return None



def _card_enabled_categories(app: Any) -> List[str]:
    surf = getattr(app, "_card_surface", None)
    if surf is not None:
        return surf.enabled_categories()
    if not app._card_cat_vars:
        return list(player_schema.default_enabled_categories())
    return [cid for cid, var in app._card_cat_vars.items() if var.get()]



def _update_card_detail(app: Any, card: CardDict) -> None:
    if not hasattr(app, "card_detail"):
        return
    before = app._target_as_before_card()
    head = card_catalog.format_card_line(card, 0)
    fav = "★ favorite" if favorites.is_favorite(card) else ""
    info = card_enrich.card_info_summary(card)
    cmp = card_compare.format_compare_lines(before, card, limit=12)
    body = (
        f"{head}  {fav}\n"
        f"{'enriched' if card.get('_enriched') else 'click Enrich from FUT.GG for full meta'}\n\n"
        f"{info}\n\n"
        f"Compare vs target:\n{cmp}"
    )
    try:
        app.card_detail.configure(state="normal")
        app.card_detail.delete("1.0", "end")
        app.card_detail.insert("1.0", body)
        app.card_detail.configure(state="disabled")
    except Exception:
        pass



def _show_compare(app: Any) -> None:
    if not app._hits or app._selected_idx < 0:
        messagebox.showinfo("Compare", "Select a card first.")
        return
    card = app._hits[app._selected_idx]
    before = app._target_as_before_card()
    text = card_compare.format_compare_lines(before, card, limit=30)
    info = card_enrich.card_info_summary(card)
    messagebox.showinfo(
        "Card info + compare",
        f"{info}\n\n--- Compare ---\n{text}",
    )



def _enrich_selected_card(app: Any) -> None:
    if not app._hits or app._selected_idx < 0:
        return
    idx = app._selected_idx
    card = app._hits[idx]
    app.status.set("Enriching from FUT.GG…")

    def work() -> None:
        try:
            enriched = card_enrich.enrich_card_from_futgg(card)
            app.after(0, lambda: app._enrich_done(idx, enriched))
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            app.after(0, lambda: app.status.set(f"Enrich failed · {err_msg}"))

    threading.Thread(target=work, daemon=True).start()



def _enrich_done(app: Any, idx: int, card: CardDict) -> None:
    app._on_variant_enriched(idx, card)
    ok = bool(card.get("_enriched"))
    app._set_apply_status(
        f"{'✓ Enriched from FUT.GG' if ok else '⚠ Offline / no slug — using cached fields'}\n"
        f"{card.get('name')} · PS={card.get('playstyles')} · +={card.get('playstyles_plus') or card.get('playstylesPlus')}",
        prog=1.0 if ok else 0.4,
        state="ok" if ok else "warn",
    )



def _card_to_editor(app: Any) -> None:
    if not app._hits or app._selected_idx < 0:
        return
    card = dict(app._hits[app._selected_idx])
    ed = card_to_lua.card_to_editor_card(card)
    app._editor_fill_form(ed)
    app.editor_name_var.set(str(ed.get("name") or card.get("name") or ""))
    try:
        app.tabs.set("  Editor  ")
    except Exception:
        pass
    app.status.set("Card sent to Editor · review topics then Apply")



def _ai_from_card(app: Any) -> None:
    if not app._hits or app._selected_idx < 0:
        messagebox.showinfo("AI", "Select a card first.")
        return
    if not grok_client.is_connected():
        messagebox.showinfo("Connect Codex", "Sign in with Codex first.")
        app._grok_connect_popup()
        return
    card = app._hits[app._selected_idx]
    prompt = card_enrich.ai_prompt_from_card(card)
    app.ai_prompt_var.set(prompt)
    app.status.set("Codex recommending from card…")
    app._busy = True

    def work() -> None:
        try:
            # enrich first if possible
            c2 = card_enrich.enrich_card_from_futgg(card)
            prompt2 = card_enrich.ai_prompt_from_card(c2)
            built = grok_client.generate_player_from_prompt(prompt2)
            # merge: prefer card attrs when present, AI fills gaps
            merged = dict(built)
            for k, v in c2.items():
                if v not in (None, "", []) and k not in (
                    "playstyles",
                    "playstyles_plus",
                    "playstylesPlus",
                ):
                    if k in player_schema.ALL_EDIT_FIELDS or k in (
                        "overallrating",
                        "potential",
                        "name",
                    ):
                        merged[k] = v
            # keep playstyle masks from card
            for k in ("trait1", "trait2", "icontrait1", "icontrait2"):
                if c2.get(k) not in (None, "", 0):
                    merged[k] = c2[k]
            app.after(0, lambda: app._ai_from_card_done(merged, c2))
        except Exception as exc:  # noqa: BLE001
            failure = exc
            app.after(0, lambda: app._ai_from_card_err(failure))
        finally:
            app.after(0, lambda: setattr(app, "_busy", False))

    threading.Thread(target=work, daemon=True).start()



def _ai_from_card_done(app: Any, card: CardDict, source: CardDict) -> None:
    app._editor_fill_form(player_schema.normalize_player_card(card))
    app.editor_name_var.set(str(card.get("name") or source.get("name") or ""))
    try:
        app.tabs.set("  Editor  ")
    except Exception:
        pass
    app._set_apply_status(
        f"✓ AI + card recommendation loaded in Editor\n"
        f"{source.get('name')} OVR {source.get('overallrating')} · review topics → Apply",
        prog=1.0,
        state="ok",
    )



def _ai_from_card_err(app: Any, e: Exception) -> None:
    app._set_apply_status(f"✗ AI from card failed · {e}", prog=0, state="err")
    messagebox.showerror("AI recommend", str(e))



def _search_err(app: Any, e: Exception) -> None:
    app._busy = False
    app.search_prog.set(0)
    app.search_prog_label.configure(text="Error")
    app.status.set(f"Search failed · {e}")
    messagebox.showerror("Search failed", str(e))



def _apply_card(app: Any) -> None:
    if not hasattr(app, "variant_list"):
        app._ensure_tab("  Cards  ")
    if not app._require_live_worker(action="Apply card"):
        return

    def _fail(msg: str) -> None:
        app._apply_busy = False
        app._set_apply_btn_text("Apply to game")
        app._set_apply_status(msg, prog=0, state="err", step=0)

    if not app._hits:
        _fail("✗ No card · Search a name, then click a variant in the list")
        return
    sel = app.variant_list.curselection()
    idx = int(sel[0]) if sel else app._selected_idx
    if idx < 0 or idx >= len(app._hits):
        _fail("✗ No card selected · click a row in the list first")
        return
    app._selected_idx = idx
    raw = (app.target_var.get() or "").strip()
    if not raw:
        _fail("✗ No Target ID · use Find target (squad) or type Player ID above")
        return
    try:
        target = int(raw)
    except ValueError:
        _fail(f"✗ Target ID must be a number (got “{raw}”)")
        return
    if target <= 0:
        _fail("✗ Target ID must be a positive number")
        return
    try:
        card = app._collect_card_from_editor_boxes()
    except Exception:
        card = app._hits[idx]
    cats = app._card_enabled_categories()
    if not cats:
        _fail("✗ No topics checked · enable Attributes / PlayStyles / Body etc.")
        return
    try:
        from ... import product as product_mod
        from .. import apply_flow

        try:
            product_mod.set_sticky_categories(cats)
        except Exception:
            pass
        detail = (
            f"{card.get('name')} OVR {card.get('overallrating')}  →  id {target}\n"
            f"Topics: {', '.join(cats)}"
        )

        def job(_on_tick: Any) -> Any:
            return product_mod.apply_card_to_target(
                card, target, enabled_categories=cats, wait=True
            )

        apply_flow.run_apply_job(
            app,
            job,
            detail=detail,
            busy_msg="Turbo apply…",
            detailed=False,
            copy_bridge_on_wait=False,
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"✗ Apply failed · {e}")


