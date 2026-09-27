"""LE Companion — premium CustomTkinter UI (shared target, clean layout)."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from . import apply_service
from . import grok_client
from . import le_apply
from . import paths
from . import player_schema
from . import squad_export
from . import target_players
from . import ui_perf
from .ui import apply_chrome
from .ui import chrome
from .ui.tabs import about as about_tab
from .ui.tabs import add_team as add_team_tab
from .ui.tabs import boost as boost_tab
from .ui.tabs import cards as cards_tab
from .ui.tabs import catalog as catalog_tab
from .ui.tabs import editor as editor_tab
from .ui.tabs import home as home_tab
from .ui.tabs import squad as squad_tab
from . import __version__
from .ui_theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BTN_H,
    CARD,
    CARD_HOVER,
    LIST_BG,
    MUTED,
    PANEL,
    RADIUS,
    RADIUS_SM,
    TEXT,
)


def _ensure_ctk() -> None:
    if ctk is None:
        raise RuntimeError(
            "customtkinter is required.\nInstall: python -m pip install customtkinter pillow"
        )


# ── small widgets ────────────────────────────────────────────────────


def _label(parent: Any, text: str, muted: bool = False, bold: bool = False, size: int = 12) -> Any:
    from .ui import widgets as _w

    return _w.label(parent, text, muted=muted, bold=bold, size=size)


def _btn(
    parent: Any,
    text: str,
    command: Callable[[], None],
    *,
    kind: str = "secondary",
    width: Optional[int] = None,
    height: int = BTN_H,
    icon: Optional[str] = None,
    icon_size: int = 18,
) -> Any:
    from .ui import widgets as _w

    return _w.btn(
        parent,
        text,
        command,
        kind=kind,
        width=width,
        height=height,
        icon=icon,
        icon_size=icon_size,
    )


def _entry(parent: Any, var: Any, width: int = 160, show: Optional[str] = None) -> Any:
    from .ui import widgets as _w

    return _w.entry(parent, var, width=width, show=show)


def _panel(parent: Any) -> Any:
    from .ui import widgets as _w

    return _w.panel(parent)


def _listbox(parent: Any, height: int = 6) -> Any:
    from .ui import widgets as _w

    return _w.listbox(parent, height=height)


class PremiumApp(ctk.CTk):
    def __init__(self) -> None:
        _ensure_ctk()
        # Do NOT install smooth-scroll monkey-patch — it freezes CTk on scroll.
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        # Keep scaling at 1.0 — CTkImage + non-1.0 scaling blurs under Windows DPI.
        try:
            ctk.set_widget_scaling(1.0)
            ctk.set_window_scaling(1.0)
        except Exception:
            pass
        try:
            ui_perf.tune_root(self)
        except Exception:
            pass

        self.title(f"LE Companion  ·  v{__version__}")
        self.geometry("1240x860")
        self.minsize(1040, 720)
        self.configure(fg_color=BG)

        # Shared state
        self._hits: List[Dict[str, Any]] = []
        self._target_hits: List[Dict[str, Any]] = []
        self._busy = False
        self._apply_busy = False  # separate from search/AI so Apply always shows status
        self._search_gen = 0
        self._enrich_gen = 0
        self._tabs_built: set = set()
        self._card_surface = None
        self._editor_surface = None
        self._selected_idx = 0
        self._editor_vars: Dict[str, Any] = {}
        self._cat_vars: Dict[str, Any] = {}
        self._ps_vars: Dict[str, Any] = {}
        self._editor_card: Dict[str, Any] = player_schema.empty_player_card()
        self._target_lists: List[Any] = []
        self._squad_labels: List[Any] = []
        self._apply_btn: Any = None
        self._apply_box: Any = None
        self._apply_steps_var = ctk.StringVar(
            value="① Write  ·  ② Queue  ·  ③ Bridge  ·  ④ Game"
        )

        try:
            from . import companion_config as _ccfg

            _cfg0 = _ccfg.load_config()
            _sticky_y = str(_cfg0.get("sticky_year") or "26").strip() or "26"
            _def_tid = int(_cfg0.get("default_target_playerid") or 0)
        except Exception:
            _sticky_y = "26"
            _def_tid = 0
        self.target_var = ctk.StringVar(value=str(_def_tid) if _def_tid > 0 else "")
        self.target_name_var = ctk.StringVar(value="")
        self.year_var = ctk.StringVar(value=_sticky_y)
        self.q_var = ctk.StringVar(value="")
        self.filter_ovr_min = ctk.StringVar(value="")
        self.filter_ovr_max = ctk.StringVar(value="")
        self.filter_pos = ctk.StringVar(value="")
        self.filter_fav_only = ctk.BooleanVar(value=False)
        self.editor_name_var = ctk.StringVar(value="")
        self.editor_preset_var = ctk.StringVar(value="max_99")
        self._last_compare_before: Optional[Dict[str, Any]] = None
        # Card apply topics (same categories as Editor)
        self._card_cat_vars: Dict[str, Any] = {}
        self.ai_prompt_var = ctk.StringVar(
            value="Cristiano Ronaldo 2008 Manchester United, overall max 92, ST"
        )
        self.grok_status_var = ctk.StringVar(value=grok_client.auth_status())
        self.status = ctk.StringVar(value="Ready · start Live Editor + Career Mode first")
        self.bridge_status_var = ctk.StringVar(
            value=le_apply.apply_status_line(short=True)
        )
        self.apply_status_var = ctk.StringVar(
            value="IDLE · set Target + card, then Apply (worker must be LIVE)"
        )

        self._build_header()
        self._build_ops_bar()
        self._build_body()
        apply_chrome.build_apply_dock(self, self)
        self._build_footer()
        # Defer filesystem / install work so first paint is instant
        self.after(100, self._startup_deferred)
        try:
            from . import ui_perf as _perf

            _tick_ms = int(getattr(_perf, "BRIDGE_TICK_MS", 5000) or 5000)
        except Exception:
            _tick_ms = 5000
        self.after(_tick_ms, self._tick_bridge_status)
        self.after(600, self._maybe_first_run)
        self.after(4000, self._prebuild_card_index_bg)
        # Prebuild heavy tabs in idle so first click is free — start after first paint
        self.after(700, self._preload_tabs_idle)
        self.bind_all("<F5>", lambda _e: self._copy_bridge_script())
        self.bind_all("<Control-Return>", lambda _e: self._apply_card())
        self.bind_all("<Control-f>", lambda _e: self._focus_card_search())

    # ── chrome ───────────────────────────────────────────────────────

    def _build_header(self) -> None:
        return chrome._build_header(self)

    def _build_ops_bar(self) -> None:
        return chrome._build_ops_bar(self)

    def _turbo_install_arm(self) -> None:
        return chrome._turbo_install_arm(self)

    def _one_click_inject_arm(self) -> None:
        return chrome._one_click_inject_arm(self)

    def _focus_card_search(self) -> None:
        return chrome._focus_card_search(self)

    def _maybe_first_run(self) -> None:
        return chrome._maybe_first_run(self)

    def _prebuild_card_index_bg(self) -> None:
        return chrome._prebuild_card_index_bg(self)

    def _force_drain(self) -> None:
        return chrome._force_drain(self)

    def _show_health(self) -> None:
        return chrome._show_health(self)

    def _show_job_history(self) -> None:
        return chrome._show_job_history(self)

    def _show_queue_inspector(self) -> None:
        return chrome._show_queue_inspector(self)

    def _clear_queue_all(self) -> None:
        return chrome._clear_queue_all(self)

    def _undo_last_apply(self) -> None:
        return chrome._undo_last_apply(self)

    def _build_body(self) -> None:
        # CRITICAL: pass command= to CTkTabview — do NOT replace
        # _segmented_button.command (that breaks tab frame switching).
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=BG,
            segmented_button_fg_color=PANEL,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=CARD,
            segmented_button_unselected_hover_color=CARD_HOVER,
            text_color="#042f2e",
            text_color_disabled=MUTED,
            corner_radius=RADIUS,
            border_width=0,
            command=self._on_tab_change,
        )
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(10, 6))
        try:
            self.tabs._segmented_button.configure(text_color=TEXT)  # type: ignore[attr-defined]
        except Exception:
            pass

        self.tab_home = self.tabs.add("  Home  ")
        self.tab_profiles = self.tabs.add("  Boost  ")
        self.tab_cards = self.tabs.add("  Cards  ")
        self.tab_add_team = self.tabs.add("  Add team  ")
        self.tab_squad = self.tabs.add("  Squad  ")
        self.tab_editor = self.tabs.add("  Editor  ")
        self.tab_catalog = self.tabs.add("  Catalog  ")
        self.tab_about = self.tabs.add("  About  ")

        # Lazy-build: Home first; others on first visit
        self._build_home_tab()
        self._tabs_built.add("  Home  ")

    def _build_footer(self) -> None:
        return chrome._build_footer(self)

    def _build_target_picker(self, parent: Any, compact: bool = False) -> Any:
        """
        Shared Find-target UI for Cards + Editor.
        Uses self.target_name_var / self.target_var and updates both lists.
        """
        box = _panel(parent)
        box.pack(fill="x", padx=0, pady=(0, 10))

        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 4))
        _label(head, "Target player", bold=True, size=14).pack(side="left")
        squad_lbl = ctk.CTkLabel(
            head,
            text=target_players.squad_status_line(),
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
            anchor="e",
        )
        squad_lbl.pack(side="right")
        self._squad_labels.append(squad_lbl)

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(4, 6))

        col = 0
        _label(row, "Search name", muted=True, size=11).grid(row=0, column=col, sticky="w")
        name_e = _entry(row, self.target_name_var, width=170)
        name_e.grid(row=1, column=col, padx=(0, 8), sticky="w")
        name_e.bind("<Return>", lambda _e: self._find_target())
        col += 1

        _btn(row, "Find", self._find_target, kind="accent", width=80).grid(
            row=1, column=col, padx=4
        )
        col += 1
        _btn(row, "Export squad", self._export_squad, kind="ghost", width=110).grid(
            row=1, column=col, padx=4
        )
        col += 1

        _label(row, "Player ID", muted=True, size=11).grid(
            row=0, column=col, sticky="w", padx=(16, 0)
        )
        _entry(row, self.target_var, width=120).grid(row=1, column=col, padx=(16, 4), sticky="w")
        col += 1

        if not compact:
            _label(
                box,
                "Export your Career Mode squad once, then search by name. Click a match to lock the ID.",
                muted=True,
                size=11,
            ).pack(anchor="w", padx=14, pady=(0, 4))

        list_wrap = ctk.CTkFrame(box, fg_color=LIST_BG, corner_radius=RADIUS_SM)
        list_wrap.pack(fill="x", padx=14, pady=(0, 12))
        lb = _listbox(list_wrap, height=4 if compact else 5)
        lb.pack(fill="x", padx=6, pady=6)
        lb.bind("<<ListboxSelect>>", self._on_target_select)
        lb.insert("end", "  Export squad → type a name → Find target → ID fills in.")
        self._target_lists.append(lb)
        return box

    def _refresh_squad_status(self) -> None:
        """Update ALL squad status labels (Cards + Editor share the same file)."""
        line = target_players.squad_status_line()
        for lbl in list(self._squad_labels):
            try:
                if lbl.winfo_exists():
                    lbl.configure(text=line)
            except Exception:
                pass

    def _sync_target_lists(self, lines: List[str]) -> None:
        for lb in self._target_lists:
            try:
                lb.delete(0, "end")
                for line in lines:
                    lb.insert("end", line)
                if lines and not lines[0].strip().startswith("Export") and not lines[0].strip().startswith("No "):
                    lb.selection_clear(0, "end")
                    lb.selection_set(0)
                    lb.activate(0)
            except Exception:
                pass

    def _find_target(self) -> None:
        q = self.target_name_var.get().strip()
        year = self.year_var.get().strip() or "26"
        squad = target_players.load_squad()
        hits = target_players.search_target(
            q,
            limit=50,
            include_catalog_fallback=True,
            year=year if year not in ("", "local", "futbin") else "26",
        )
        self._target_hits = hits
        self._refresh_squad_status()

        if not hits:
            if not squad.get("loaded"):
                self._sync_target_lists(
                    ["  No squad yet. Click Export squad (Career Mode + LE), then Find again."]
                )
                self.status.set("No squad export — run Export squad first")
            else:
                self._sync_target_lists([f'  No match for "{q}". Try a shorter name.'])
                self.status.set("No players matched")
            return

        lines = ["  " + target_players.format_target_line(p, i) for i, p in enumerate(hits)]
        self._sync_target_lists(lines)
        self._apply_target_hit(0)
        n_squad = sum(1 for h in hits if h.get("source") == "squad")
        if n_squad:
            self.status.set(f"Target locked · {n_squad} squad match(es)")
        else:
            self.status.set(f"{len(hits)} catalog ID(s) · verify before apply")
            messagebox.showwarning(
                "Not from your squad",
                "No match in the exported squad.\n"
                "Showing catalog IDs — they may not match your save.\n\n"
                "For a precise ID: Export squad in Career Mode, then Find again.",
            )

    def _on_target_select(self, _event: Any = None) -> None:
        # Which list fired? Check all
        for lb in self._target_lists:
            try:
                sel = lb.curselection()
                if sel and self._target_hits:
                    idx = int(sel[0])
                    if 0 <= idx < len(self._target_hits):
                        self._apply_target_hit(idx)
                        return
            except Exception:
                continue

    def _apply_target_hit(self, idx: int) -> None:
        p = self._target_hits[idx]
        pid = p.get("playerid")
        if pid is None:
            return
        self.target_var.set(str(int(pid)))
        name = p.get("name") or "?"
        try:
            self.target_name_var.set(str(name))
        except Exception:
            pass
        self.status.set(f"Target · {name} · id {pid}")
        try:
            from .ui.tabs import cards as cards_tab

            cards_tab.update_cards_pipeline(self)
        except Exception:
            pass

    def _export_squad(self) -> None:
        """
        Same action from Cards and Editor (shared method).
        Queues export Lua with ABSOLUTE path → bridge runs it → current_squad.json
        """
        try:
            p0 = target_players.current_squad_path()
            try:
                mtime0 = p0.stat().st_mtime if p0.is_file() else 0.0
            except OSError:
                mtime0 = 0.0
            info = squad_export.queue_export_squad()
            alive = le_apply.bridge_alive()
            if alive:
                self.status.set("Export squad queued · waiting for bridge…")
            else:
                self.status.set(
                    "Export queued · WORKER OFF · Install worker → Copy bridge → "
                    "LE Lua Engine → Execute once, then Export again"
                )
            # Poll only accepts a file newer than pre-export mtime
            self._poll_squad_export(attempts=25, mtime0=mtime0)
        except Exception as e:  # noqa: BLE001
            self.status.set(f"Export failed · {e}")

    def _poll_squad_export(self, attempts: int = 20, mtime0: float = 0.0) -> None:
        p = target_players.current_squad_path()
        # Fail status from bridge when not in Career Mode
        try:
            st = paths.queue_dir() / "_export_status.txt"
            if st.is_file() and st.stat().st_mtime > (mtime0 or 0) - 1:
                txt = st.read_text(encoding="utf-8", errors="replace").strip()
                if txt.upper().startswith("FAIL"):
                    self.status.set(f"Export failed · {txt}")
                    return
        except Exception:
            pass
        if p.is_file():
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            # Require a NEW write (not the stale file shipped with the app)
            if mtime > float(mtime0 or 0) + 0.05:
                self._refresh_squad_status()
                s = target_players.load_squad()
                n = s.get("count") or 0
                team = s.get("teamname") or "?"
                self.status.set(f"Squad exported · {n} players · {team}")
                try:
                    self._refresh_squad_board()
                except Exception:
                    pass
                try:
                    if "Add team" in str(self.tabs.get() or ""):
                        add_team_tab.refresh_readiness(self)
                    elif hasattr(self, "_add_team_chip_live"):
                        add_team_tab.refresh_readiness(self)
                except Exception:
                    pass
                return
        if attempts <= 0:
            self._refresh_squad_status()
            self.status.set(
                "Export not finished · ensure LIVE worker + Career Mode loaded"
            )
            return
        self.after(
            1000,
            lambda a=attempts - 1, m=mtime0: self._poll_squad_export(a, mtime0=m),
        )

    def _require_target_id(self) -> Optional[int]:
        try:
            pid = int(self.target_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Choose a target",
                "Set the player to edit first.\n\n"
                "Search by name → Find target\n"
                "or type a Player ID manually.",
            )
            return None
        if pid <= 0:
            messagebox.showerror("Choose a target", "Player ID must be a positive number.")
            return None
        return pid

    # ── home (experience-first) ──────────────────────────────────────

    def _goto(self, tab_name: str) -> None:
        try:
            self._ensure_tab(tab_name)
            self.tabs.set(tab_name)
        except Exception:
            pass


    def _startup_deferred(self) -> None:
        """Heavy FS work after first paint (prevents freeze on open)."""
        def work() -> None:
            try:
                le_apply.ensure_sync_installed()
            except Exception:
                pass
            try:
                warns = apply_service.layout_warnings()
            except Exception:
                warns = []
            # Auto-clipboard bridge when OFF so user can paste without clicking Copy
            clip_info: dict = {}
            try:
                clip_info = apply_service.auto_clipboard_bridge_if_off()
            except Exception:
                clip_info = {}

            def ui() -> None:
                try:
                    self.bridge_status_var.set(le_apply.apply_status_line(short=True))
                    if warns:
                        self.status.set(" · ".join(warns)[:100])
                    elif clip_info.get("copied"):
                        self.status.set(
                            "Bridge auto-copied · LE → Lua Engine → Ctrl+V → Execute"
                        )
                        try:
                            self._set_apply_status(
                                "✓ Bridge AUTO-COPIED to clipboard\n"
                                "LE → Features → Lua Engine → Ctrl+V → Execute once\n"
                                "(or click Go LIVE — copies again)",
                                prog=0.4,
                                state="warn",
                                step=1,
                            )
                        except Exception:
                            pass
                    self._refresh_sync_chrome()
                except Exception:
                    pass

            self.after(0, ui)

        threading.Thread(target=work, daemon=True).start()

    def _on_tab_change(self) -> None:
        """CTkTabview command — tab already switched; build only if not preloaded."""
        try:
            name = str(self.tabs.get())
        except Exception:
            return
        # Tab-aware dock label (Add team vs Apply)
        try:
            from .ui import apply_chrome as _ac

            self.after(10, lambda: _ac.update_dock_for_active_tab(self))
        except Exception:
            pass
        if "Add team" in name and name in getattr(self, "_tabs_built", set()):
            try:
                self.after(20, lambda: add_team_tab.on_tab_activated(self))
            except Exception:
                pass
        if name in getattr(self, "_tabs_built", set()):
            return
        # Yield so segmented button can paint before heavy first build
        self.after(1, lambda n=name: self._ensure_tab(n, quiet=False))

    def _preload_tabs_idle(self) -> None:
        """Build remaining tabs one-at-a-time on idle after first paint.

        First open of Squad/Editor was laggy because build ran on click.
        Preload order prioritizes common paths from Home/Cards.
        """
        if getattr(self, "_preload_done", False):
            return
        queue = getattr(self, "_preload_queue", None)
        if queue is None:
            # Cards + Squad first (user path), Editor last among frequent tabs
            self._preload_queue = [
                "  Boost  ",  # packs + profile cards — open early so first click is free
                "  Cards  ",
                "  Add team  ",
                "  Squad  ",
                "  Catalog  ",
                "  About  ",
                "  Editor  ",  # heaviest — last so earlier tabs free first
            ]
            queue = self._preload_queue

        while queue and queue[0] in getattr(self, "_tabs_built", set()):
            queue.pop(0)
        if not queue:
            self._preload_done = True
            return

        # Don't fight an in-progress user-triggered build
        if getattr(self, "_tab_building", None):
            self.after(120, self._preload_tabs_idle)
            return

        name = queue.pop(0)
        try:
            self._ensure_tab(name, quiet=True)
        except Exception:
            pass

        if queue:
            # Extra breathing room after heavy tabs so scroll stays responsive
            try:
                from . import ui_perf as _perf

                delay = (
                    int(getattr(_perf, "TAB_PRELOAD_HEAVY_GAP_MS", 320) or 320)
                    if ("Editor" in name or "Cards" in name or "Boost" in name)
                    else int(getattr(_perf, "TAB_PRELOAD_GAP_MS", 140) or 140)
                )
            except Exception:
                delay = 280 if ("Editor" in name or "Cards" in name) else 120
            self.after(delay, self._preload_tabs_idle)
        else:
            self._preload_done = True

    def _ensure_tab(self, name: str, quiet: bool = False) -> None:
        if name in getattr(self, "_tabs_built", set()):
            return
        building = getattr(self, "_tab_building", None)
        if building == name:
            return
        if building is not None and building != name:
            # Another tab mid-build — retry shortly (user click wins next)
            self.after(50, lambda n=name, q=quiet: self._ensure_tab(n, quiet=q))
            return
        self._tab_building = name
        tab_map = {
            "  Home  ": self._build_home_tab,
            "  Boost  ": self._build_profiles_tab,
            "  Cards  ": self._build_cards_tab,
            "  Add team  ": self._build_add_team_tab,
            "  Squad  ": self._build_squad_tab,
            "  Editor  ": self._build_editor_tab,
            "  Catalog  ": self._build_catalog_tab,
            "  About  ": self._build_about_tab,
        }
        fn = tab_map.get(name)
        if not fn:
            self._tab_building = None
            return
        try:
            if not quiet:
                self.status.set(f"Loading {name.strip()}…")
            # Never update_idletasks mid-build — freezes CTk
            fn()
            self._tabs_built.add(name)
            if not quiet:
                self.status.set("Ready")
        except Exception as e:  # noqa: BLE001
            if not quiet:
                self.status.set(f"Tab error · {e}")
        finally:
            self._tab_building = None

    def _build_home_tab(self) -> None:
        home_tab.build_home_tab(self)


    @staticmethod
    def _boost_category_icon(category: str) -> str:
        return boost_tab.category_icon(category)

    def _build_profiles_tab(self) -> None:
        return boost_tab._build_profiles_tab(self)

    def _boost_fill_cards(self, batch: int = 6, gen: Optional[int] = None) -> None:
        return boost_tab._boost_fill_cards(self, batch=batch, gen=gen)

    def _run_profile(self, profile_id: str) -> None:
        return boost_tab._run_profile(self, profile_id)

    def _run_pack(self, pack_id: str) -> None:
        return boost_tab._run_pack(self, pack_id)

    def _best_match_for_target(self) -> None:
        return boost_tab._best_match_for_target(self)

    def _batch_apply_squad(self) -> None:
        return boost_tab._batch_apply_squad(self)

    def _restore_last_snapshot(self) -> None:
        return boost_tab._restore_last_snapshot(self)

    def _build_cards_tab(self) -> None:
        cards_tab.build_cards_tab(self)

    def _build_add_team_tab(self) -> None:
        add_team_tab.build_add_team_tab(self)


    def _search_async(self) -> None:
        return cards_tab._search_async(self)

    def _set_search_progress(self, frac: float, msg: str) -> None:
        return cards_tab._set_search_progress(self, frac, msg)

    def _show_hits(self, hits: List[Dict[str, Any]]) -> None:
        return cards_tab._show_hits(self, hits)

    def _apply_card_filters(self) -> None:
        return cards_tab._apply_card_filters(self)

    def _show_favorites_list(self) -> None:
        return cards_tab._show_favorites_list(self)

    def _toggle_favorite(self) -> None:
        return cards_tab._toggle_favorite(self)

    def _on_variant_select(self, _event: Any = None) -> None:
        return cards_tab._on_variant_select(self, _event=_event)

    def _select_variant_index(self, idx: int, *, reset_chat: bool = True) -> None:
        return cards_tab._select_variant_index(self, idx, reset_chat=reset_chat)

    def _on_variant_enriched(self, idx: int, card: Dict[str, Any]) -> None:
        return cards_tab._on_variant_enriched(self, idx, card)

    def _ensure_card_surface(self) -> Any:
        return cards_tab._ensure_card_surface(self)

    def _load_card_into_editor_boxes(self, card: Dict[str, Any]) -> None:
        return cards_tab._load_card_into_editor_boxes(self, card)

    def _mark_card_editor_dirty(self, *_args: Any) -> None:
        return cards_tab._mark_card_editor_dirty(self, *_args)

    def _collect_card_from_editor_boxes(self) -> Dict[str, Any]:
        return cards_tab._collect_card_from_editor_boxes(self)

    def _card_ai_chat_send(self) -> None:
        return cards_tab._card_ai_chat_send(self)

    def _card_chat_append(self, who: str, text: str) -> None:
        return cards_tab._card_chat_append(self, who, text)

    def _card_ai_chat_done(self, updated: Dict[str, Any], user_msg: str) -> None:
        return cards_tab._card_ai_chat_done(self, updated, user_msg)

    def _card_ai_chat_fail(self, e: Exception) -> None:
        return cards_tab._card_ai_chat_fail(self, e)

    def _target_as_before_card(self) -> Optional[Dict[str, Any]]:
        return cards_tab._target_as_before_card(self)

    def _card_enabled_categories(self) -> List[str]:
        return cards_tab._card_enabled_categories(self)

    def _update_card_detail(self, card: Dict[str, Any]) -> None:
        return cards_tab._update_card_detail(self, card)

    def _show_compare(self) -> None:
        return cards_tab._show_compare(self)

    def _enrich_selected_card(self) -> None:
        return cards_tab._enrich_selected_card(self)

    def _enrich_done(self, idx: int, card: Dict[str, Any]) -> None:
        return cards_tab._enrich_done(self, idx, card)

    def _card_to_editor(self) -> None:
        return cards_tab._card_to_editor(self)

    def _ai_from_card(self) -> None:
        return cards_tab._ai_from_card(self)

    def _ai_from_card_done(self, card: Dict[str, Any], source: Dict[str, Any]) -> None:
        return cards_tab._ai_from_card_done(self, card, source)

    def _ai_from_card_err(self, e: Exception) -> None:
        return cards_tab._ai_from_card_err(self, e)

    def _search_err(self, e: Exception) -> None:
        return cards_tab._search_err(self, e)

    def _refresh_sync_chrome(self) -> None:
        return chrome._refresh_sync_chrome(self)

    def _require_live_worker(self, *, action: str = "Apply") -> bool:
        return chrome._require_live_worker(self, action=action)

    def _tick_bridge_status(self) -> None:
        return chrome._tick_bridge_status(self)

    def _copy_bridge_script(self) -> None:
        return chrome._copy_bridge_script(self)

    def _copy_last_apply_lua(self) -> None:
        return chrome._copy_last_apply_lua(self)

    def _enable_auto_apply(self) -> None:
        return chrome._enable_auto_apply(self)

    def _set_apply_steps(self, step: int) -> None:
        return chrome._set_apply_steps(self, step)

    def _set_apply_status(
        self,
        msg: str,
        *,
        prog: Optional[float] = None,
        state: str = "info",
        step: Optional[int] = None,
        paint: bool = True,
    ) -> None:
        return chrome._set_apply_status(
            self, msg, prog=prog, state=state, step=step, paint=paint
        )

    def _set_apply_btn_text(self, text: str) -> None:
        return chrome._set_apply_btn_text(self, text)

    def _show_arm_guide(self) -> None:
        return chrome._show_arm_guide(self)

    def _queue_and_wait_bridge(
        self,
        lua: str,
        *,
        stem: str,
        success_title: str,
        detail: str,
        kind: str = "apply",
        target_id: Optional[int] = None,
    ) -> None:
        return chrome._queue_and_wait_bridge(
            self,
            lua,
            stem=stem,
            success_title=success_title,
            detail=detail,
            kind=kind,
            target_id=target_id,
        )

    def _apply_card(self) -> None:
        return cards_tab._apply_card(self)

    def _build_squad_tab(self) -> None:
        return squad_tab._build_squad_tab(self)

    def _refresh_squad_board(self) -> None:
        return squad_tab._refresh_squad_board(self)

    def _on_squad_board_select(self, _e: Any = None) -> None:
        return squad_tab._on_squad_board_select(self, _e=_e)

    def _squad_go_cards(self) -> None:
        return squad_tab._squad_go_cards(self)

    def _build_editor_tab(self) -> None:
        return editor_tab._build_editor_tab(self)

    def _finish_editor_surface(self) -> None:
        return editor_tab._finish_editor_surface(self)

    def _ensure_editor_surface(self) -> None:
        return editor_tab._ensure_editor_surface(self)

    def _editor_enabled_categories(self) -> List[str]:
        return editor_tab._editor_enabled_categories(self)

    def _editor_cats_defaults(self) -> None:
        return editor_tab._editor_cats_defaults(self)

    def _editor_cats_all(self) -> None:
        return editor_tab._editor_cats_all(self)

    def _editor_cats_none(self) -> None:
        return editor_tab._editor_cats_none(self)

    def _editor_collect_card(self) -> Dict[str, Any]:
        return editor_tab._editor_collect_card(self)

    def _editor_fill_form(self, card: Dict[str, Any]) -> None:
        return editor_tab._editor_fill_form(self, card)

    def _editor_clear(self) -> None:
        return editor_tab._editor_clear(self)

    def _editor_apply_preset(self) -> None:
        return editor_tab._editor_apply_preset(self)

    def _editor_recalc_ovr(self) -> None:
        return editor_tab._editor_recalc_ovr(self)

    def _editor_load_from_target(self) -> None:
        return editor_tab._editor_load_from_target(self)

    def _editor_apply(self) -> None:
        return editor_tab._editor_apply(self)

    def _grok_connect_popup(self) -> None:
        return chrome._grok_connect_popup(self)

    def _ai_fill_async(self) -> None:
        return editor_tab._ai_fill_async(self)

    def _ai_fill_done(self, card: Dict[str, Any]) -> None:
        return editor_tab._ai_fill_done(self, card)

    def _ai_fill_err(self, e: Exception) -> None:
        return editor_tab._ai_fill_err(self, e)

    def _build_catalog_tab(self) -> None:
        return catalog_tab._build_catalog_tab(self)

    def _probe(self) -> None:
        return catalog_tab._probe(self)

    def _sync_futgg(self, years: list) -> None:
        return catalog_tab._sync_futgg(self, years)

    def _append_fut(self, msg: str) -> None:
        return catalog_tab._append_fut(self, msg)

    def _sync_player_dialog(self) -> None:
        return catalog_tab._sync_player_dialog(self)

    def _show_json(self, data: Any, status: str) -> None:
        return catalog_tab._show_json(self, data, status)

    def _imp_html(self) -> None:
        return catalog_tab._imp_html(self)

    def _imp_json(self) -> None:
        return catalog_tab._imp_json(self)

    def _build_about_tab(self) -> None:
        return about_tab._build_about_tab(self)

def run_gui() -> None:
    try:
        app = PremiumApp()
        app.mainloop()
    except Exception as e:  # noqa: BLE001
        import tkinter as tk
        from tkinter import messagebox as mb

        root = tk.Tk()
        root.withdraw()
        mb.showerror("LE Companion", f"UI failed to start:\n{e}")
        root.destroy()
        raise
