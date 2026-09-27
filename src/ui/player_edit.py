"""Unified player edit surface used by Cards (compact) and Editor (full)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional  # Dict used for section_frames

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from .. import card_to_lua
from .. import futgg_client
from .. import player_schema
from ..ui_theme import (
    ACCENT,
    ACCENT_HOVER,
    BORDER,
    CARD,
    LIST_BG,
    MUTED,
    SP2,
    SP3,
    SUCCESS,
    TEXT,
)
from . import widgets as w
from .collapsible import CollapsibleSection

# Tiny text glyphs for attribute group headers (title-match only; no bitmap icons)
_ATTR_GROUP_ICONS: Dict[str, str] = {
    "Pace": "›",
    "Shooting": "◎",
    "Passing": "↗",
    "Dribbling": "◇",
    "Defending": "▣",
    "Physical": "◆",
    "Goalkeeping": "⬡",
}


def _attr_group_title(gname: str) -> str:
    icon = _ATTR_GROUP_ICONS.get(gname)
    return f"{icon}  {gname}" if icon else gname


class PlayerEditSurface:
    """Shared fields + playstyles + write-topic checkboxes."""

    def __init__(
        self,
        parent: Any,
        *,
        compact: bool = True,
        default_categories: Optional[set] = None,
        on_dirty: Optional[Callable[[], None]] = None,
    ) -> None:
        self.parent = parent
        self.compact = compact
        self.on_dirty = on_dirty
        self.field_vars: Dict[str, Any] = {}
        self.ps_vars: Dict[str, Any] = {}
        self.cat_vars: Dict[str, Any] = {}
        self.working: Dict[str, Any] = {}
        self.dirty = False
        self._ps_dirty = False  # user toggled PlayStyle checkboxes
        self._loading = False
        self.meta_line: Any = None
        self._ovr_chip: Any = None
        self._pot_chip: Any = None
        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.root.pack(fill="both", expand=True)

        default_on = default_categories or {
            "attributes",
            "ratings",
            "skills",
            "positions",
            "playstyles",
            "body",
            "movement",
        }

        if compact:
            self._build_compact(default_on)
        else:
            self._build_full(default_on)

    def _mark_dirty(self, *_args: Any) -> None:
        if self._loading:
            return
        self.dirty = True
        if self.on_dirty:
            try:
                self.on_dirty()
            except Exception:
                pass

    def _build_compact(self, default_on: set) -> None:
        edit_panel = w.panel(self.root)
        edit_panel.pack(fill="x", pady=(0, SP2))
        ehead = ctk.CTkFrame(edit_panel, fg_color="transparent")
        ehead.pack(fill="x", padx=SP3, pady=(SP2, 2))
        w.label(ehead, "Edit stats", bold=True, size=13).pack(side="left")
        w.label(
            ehead,
            "FUT.GG loads PS/PS+ when online · boxes stay compact",
            muted=True,
            size=10,
        ).pack(side="right")

        # OVR / POT summary chips (filled when load_card sets values)
        chip_row = ctk.CTkFrame(edit_panel, fg_color="transparent")
        chip_row.pack(fill="x", padx=SP3, pady=(0, 2))
        self._ovr_chip = ctk.CTkLabel(
            chip_row,
            text="OVR —",
            text_color=SUCCESS,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=LIST_BG,
            corner_radius=6,
            width=64,
            height=22,
        )
        self._ovr_chip.pack(side="left", padx=(0, SP2))
        self._pot_chip = ctk.CTkLabel(
            chip_row,
            text="POT —",
            text_color=ACCENT,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=LIST_BG,
            corner_radius=6,
            width=64,
            height=22,
        )
        self._pot_chip.pack(side="left")

        self.meta_line = ctk.CTkLabel(
            edit_panel,
            text="Pick a variant — club · nation · rarity · playstyles show here",
            text_color=MUTED,
            font=ctk.CTkFont(size=10),
            anchor="w",
            justify="left",
            wraplength=980,
        )
        self.meta_line.pack(fill="x", padx=SP3, pady=(0, SP2))

        # Write topics FIRST (feedback: discovery was near zero at bottom of nest)
        twrap = ctk.CTkFrame(
            edit_panel,
            fg_color=LIST_BG,
            corner_radius=6,
            border_width=1,
            border_color=ACCENT,
        )
        twrap.pack(fill="x", padx=SP2, pady=(2, SP2))
        trow2 = ctk.CTkFrame(twrap, fg_color="transparent")
        trow2.pack(fill="x", padx=SP2, pady=SP2)
        w.label(trow2, "Write:", muted=True, size=10).pack(side="left", padx=(0, SP2))
        for cat in player_schema.EDIT_CATEGORIES:
            var = ctk.BooleanVar(value=cat["id"] in default_on)
            self.cat_vars[cat["id"]] = var
            ctk.CTkCheckBox(
                trow2,
                text=str(cat["label"])[:10],
                variable=var,
                text_color=TEXT,
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                font=ctk.CTkFont(size=9),
                width=78,
                height=20,
                checkbox_width=14,
                checkbox_height=14,
            ).pack(side="left", padx=1)

        meta = ctk.CTkFrame(edit_panel, fg_color="transparent")
        meta.pack(anchor="w", padx=SP2, pady=(0, 2))
        meta_fields = [
            ("overallrating", "OVR"),
            ("potential", "POT"),
            ("skillmoves", "SM"),
            ("weakfootabilitytypecode", "WF"),
            ("preferredfoot", "Foot"),
            ("height", "H"),
            ("weight", "W"),
            ("bodytypecode", "Body"),
        ]
        for i, (field, lab) in enumerate(meta_fields):
            cell = ctk.CTkFrame(meta, fg_color="transparent")
            cell.grid(row=0, column=i, padx=3, pady=1, sticky="w")
            lab_color = SUCCESS if field == "overallrating" else (ACCENT if field == "potential" else MUTED)
            ctk.CTkLabel(
                cell, text=lab, text_color=lab_color, font=ctk.CTkFont(size=9), width=36
            ).pack(side="left")
            var = ctk.StringVar(value="")
            self.field_vars[field] = var
            try:
                var.trace_add("write", self._mark_dirty)
            except Exception:
                pass
            ctk.CTkEntry(
                cell,
                textvariable=var,
                width=40,
                height=22,
                font=ctk.CTkFont(size=11),
                fg_color=LIST_BG,
                border_color=BORDER,
                text_color=TEXT,
                border_width=1,
                corner_radius=4,
            ).pack(side="left", padx=(2, 0))

        attr_frame = ctk.CTkFrame(edit_panel, fg_color="transparent")
        attr_frame.pack(anchor="w", padx=SP2, pady=(2, 2))
        outfield = [g for g in player_schema.ATTR_GROUPS if g[0] != "Goalkeeping"]
        for gi, (gname, items) in enumerate(outfield):
            r0, c0 = divmod(gi, 3)
            box = ctk.CTkFrame(attr_frame, fg_color=LIST_BG, corner_radius=6, width=168)
            box.grid(row=r0, column=c0, sticky="nw", padx=3, pady=3)
            ctk.CTkLabel(
                box,
                text=_attr_group_title(gname),
                text_color=ACCENT,
                font=ctk.CTkFont(size=10, weight="bold"),
            ).pack(anchor="w", padx=SP2, pady=(SP2 // 2, 1))
            for field, lab in items:
                rowf = ctk.CTkFrame(box, fg_color="transparent", height=20)
                rowf.pack(fill="x", padx=4, pady=0)
                ctk.CTkLabel(
                    rowf,
                    text=lab[:12],
                    width=78,
                    anchor="w",
                    text_color=MUTED,
                    font=ctk.CTkFont(size=9),
                ).pack(side="left")
                var = ctk.StringVar(value="")
                self.field_vars[field] = var
                try:
                    var.trace_add("write", self._mark_dirty)
                except Exception:
                    pass
                ctk.CTkEntry(
                    rowf,
                    textvariable=var,
                    width=36,
                    height=20,
                    font=ctk.CTkFont(size=11),
                    fg_color=CARD,
                    border_color=BORDER,
                    text_color=TEXT,
                    border_width=1,
                    corner_radius=4,
                ).pack(side="right", padx=(0, 4))

        gk = next((g for g in player_schema.ATTR_GROUPS if g[0] == "Goalkeeping"), None)
        if gk:
            box = ctk.CTkFrame(edit_panel, fg_color=LIST_BG, corner_radius=6)
            box.pack(anchor="w", padx=SP3, pady=(0, SP2))
            ctk.CTkLabel(
                box,
                text=_attr_group_title("Goalkeeping"),
                text_color=ACCENT,
                font=ctk.CTkFont(size=10, weight="bold"),
            ).pack(anchor="w", padx=SP2, pady=(SP2 // 2, 1))
            rowg = ctk.CTkFrame(box, fg_color="transparent")
            rowg.pack(anchor="w", padx=4, pady=(0, SP2))
            for j, (field, lab) in enumerate(gk[1]):
                cell = ctk.CTkFrame(rowg, fg_color="transparent")
                cell.grid(row=0, column=j, padx=3, sticky="w")
                ctk.CTkLabel(
                    cell, text=lab.replace("GK ", ""), text_color=MUTED, font=ctk.CTkFont(size=9)
                ).pack(side="left")
                var = ctk.StringVar(value="")
                self.field_vars[field] = var
                try:
                    var.trace_add("write", self._mark_dirty)
                except Exception:
                    pass
                ctk.CTkEntry(
                    cell,
                    textvariable=var,
                    width=36,
                    height=20,
                    font=ctk.CTkFont(size=11),
                    fg_color=CARD,
                    border_color=BORDER,
                    text_color=TEXT,
                    border_width=1,
                    corner_radius=4,
                ).pack(side="left", padx=(2, 0))

        ps_panel = ctk.CTkFrame(edit_panel, fg_color=LIST_BG, corner_radius=6)
        ps_panel.pack(fill="x", padx=SP2, pady=(SP2, SP2))
        w.label(
            ps_panel,
            "PlayStyles / PlayStyle+  (tick to enable — loaded from FUT.GG when available)",
            bold=True,
            size=11,
        ).pack(anchor="w", padx=SP2, pady=(SP2, 2))
        self._build_playstyle_ui(ps_panel, cols=6, font_size=9)

    def _build_full(self, default_on: set) -> None:
        """Full editor: accordion sections; topic checks open/close + gate Apply writes."""
        self.section_frames: Dict[str, Any] = {}  # cid -> CollapsibleSection
        topics = w.panel(self.root)
        try:
            topics.configure(border_color=ACCENT, border_width=1)
        except Exception:
            pass
        topics.pack(fill="x", pady=(0, SP3))
        w.label(topics, "Write only checked topics", bold=True, size=14).pack(
            anchor="w", padx=SP3, pady=(SP3, 2)
        )
        w.label(
            topics,
            "Checked topics expand below and are written on Apply. Unchecked stay collapsed.",
            muted=True,
            size=11,
        ).pack(anchor="w", padx=SP3, pady=(0, SP2))

        tgrid = ctk.CTkFrame(topics, fg_color="transparent")
        tgrid.pack(fill="x", padx=SP2, pady=(0, SP2))
        for i, cat in enumerate(player_schema.EDIT_CATEGORIES):
            cid = str(cat["id"])
            var = ctk.BooleanVar(value=bool(cat.get("default")) or cid in default_on)
            self.cat_vars[cid] = var
            cb = ctk.CTkCheckBox(
                tgrid,
                text=cat["label"],
                variable=var,
                font=ctk.CTkFont(size=12),
                text_color=TEXT,
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                border_color=BORDER,
                checkmark_color="#042f2e",
                command=self._apply_topic_visibility,
            )
            r, c = divmod(i, 4)
            cb.grid(row=r, column=c, sticky="w", padx=SP2, pady=SP2 // 2)

        brow = ctk.CTkFrame(topics, fg_color="transparent")
        brow.pack(fill="x", padx=SP3, pady=(0, SP3))

        def _defaults() -> None:
            d = player_schema.default_enabled_categories()
            for cid, var in self.cat_vars.items():
                var.set(cid in d)
            self._apply_topic_visibility()

        def _all() -> None:
            for var in self.cat_vars.values():
                var.set(True)
            self._apply_topic_visibility()

        def _none() -> None:
            for var in self.cat_vars.values():
                var.set(False)
            self._apply_topic_visibility()

        def _expand_all() -> None:
            for sec in self.section_frames.values():
                try:
                    sec.set_open(True)
                except Exception:
                    pass

        def _collapse_all() -> None:
            for sec in self.section_frames.values():
                try:
                    sec.set_open(False)
                except Exception:
                    pass

        w.btn(brow, "Defaults", _defaults, kind="ghost", width=90, height=28).pack(
            side="left", padx=2
        )
        w.btn(brow, "All", _all, kind="ghost", width=70, height=28).pack(side="left", padx=2)
        w.btn(brow, "None", _none, kind="ghost", width=70, height=28).pack(side="left", padx=2)
        w.btn(brow, "Expand all", _expand_all, kind="ghost", width=100, height=28).pack(
            side="left", padx=SP2
        )
        w.btn(brow, "Collapse all", _collapse_all, kind="ghost", width=110, height=28).pack(
            side="left", padx=2
        )

        # Headers only first — field grids built lazily on first open (perf)
        for cat in player_schema.EDIT_CATEGORIES:
            self._build_category_section(self.root, cat)
        # CRITICAL: do NOT open all default topics here.
        # Opening attributes+ratings+skills+… builds hundreds of CTkEntry and freezes
        # first visit to Editor (and preload). Checkboxes still mark write-gate;
        # sections stay closed until user expands or we open one light section later.
        # Do NOT auto-open any section here — even "ratings" builds many CTkEntry
        # and freezes first Editor paint / preload. User expands when needed.

    def _open_primary_section_idle(self) -> None:
        """Optional: open a single light section (caller schedules if wanted)."""
        frames = getattr(self, "section_frames", None) or {}
        for cid in ("ratings", "attributes"):
            sec = frames.get(cid)
            if sec is None:
                continue
            try:
                sec.set_open(True)
            except Exception:
                pass
            break

    def _apply_topic_visibility(self) -> None:
        """Open accordion for checked topics; close for unchecked (write gate + UI)."""
        frames = getattr(self, "section_frames", None) or {}
        for cat in player_schema.EDIT_CATEGORIES:
            cid = str(cat["id"])
            sec = frames.get(cid)
            if sec is None:
                continue
            var = self.cat_vars.get(cid)
            want_open = True if var is None else bool(var.get())
            try:
                sec.set_open(want_open)
            except Exception:
                pass

    def _build_category_section(self, parent: Any, cat: Dict[str, Any]) -> None:
        """Create accordion header only; populate fields on first expand."""
        cid = str(cat["id"])
        if not hasattr(self, "section_frames"):
            self.section_frames = {}
        var = self.cat_vars.get(cid)
        open0 = True if var is None else bool(var.get())

        def _fill(body: Any, c: Dict[str, Any] = cat) -> None:
            if c.get("playstyle_ui"):
                self._build_playstyle_ui(body, cols=5, font_size=11)
                grid = ctk.CTkFrame(body, fg_color="transparent")
                grid.pack(fill="x", padx=SP2, pady=(SP2, SP2))
                for i, (field, label, hint) in enumerate(c["fields"]):
                    self._add_field(grid, field, label, hint, i, cols=3)
            elif c.get("groups"):
                for gname, items in c["groups"]:
                    ctk.CTkLabel(
                        body,
                        text=_attr_group_title(str(gname)),
                        text_color=ACCENT,
                        font=ctk.CTkFont(size=11, weight="bold"),
                        anchor="w",
                    ).pack(anchor="w", padx=SP2, pady=(SP2, 2))
                    grid = ctk.CTkFrame(body, fg_color="transparent")
                    grid.pack(fill="x", padx=SP2, pady=(0, SP2))
                    for i, (field, label) in enumerate(items):
                        self._add_field(grid, field, label, "1–99", i, cols=4)
            else:
                grid = ctk.CTkFrame(body, fg_color="transparent")
                grid.pack(fill="x", padx=SP2, pady=(0, SP2))
                for i, (field, label, hint) in enumerate(c["fields"]):
                    self._add_field(grid, field, label, hint, i, cols=3)
            # Re-apply working card into newly created fields (lazy open)
            self._push_working_into_fields()

        sec = CollapsibleSection(
            parent,
            title=str(cat["label"]),
            description=str(cat.get("description") or ""),
            open=False,  # always start closed; open via _apply_topic_visibility
            body_builder=_fill,
        )
        sec.pack(fill="x", pady=(0, SP2))
        self.section_frames[cid] = sec
        # open0 applied after all headers exist
        del open0

    def _add_field(
        self,
        grid: Any,
        field: str,
        label: str,
        hint: str,
        index: int,
        cols: int = 3,
    ) -> None:
        r, c = divmod(index, cols)
        cell = ctk.CTkFrame(
            grid, fg_color=CARD, corner_radius=8, border_width=1, border_color=BORDER
        )
        cell.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
        grid.columnconfigure(c, weight=1)
        ctk.CTkLabel(cell, text=label, text_color=MUTED, font=ctk.CTkFont(size=10)).pack(
            anchor="w", padx=8, pady=(6, 0)
        )
        var = ctk.StringVar(value="")
        self.field_vars[field] = var
        try:
            var.trace_add("write", self._mark_dirty)
        except Exception:
            pass
        ent = ctk.CTkEntry(
            cell,
            textvariable=var,
            height=30,
            corner_radius=6,
            fg_color=LIST_BG,
            border_color=BORDER,
            text_color=TEXT,
        )
        ent.pack(fill="x", padx=8, pady=(2, 6))
        if hint:
            try:
                ent.configure(placeholder_text=hint)
            except Exception:
                pass

    def _build_playstyle_ui(self, parent: Any, *, cols: int, font_size: int) -> None:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=6, pady=(0, 6))
        for title, bank, is_plus, styles in (
            ("PlayStyles", "ps1", False, player_schema.PLAYSTYLE1),
            ("PlayStyles · GK / misc", "ps2", False, player_schema.PLAYSTYLE2),
            ("PlayStyle+", "ps1", True, player_schema.PLAYSTYLE1),
            ("PlayStyle+ · GK / misc", "ps2", True, player_schema.PLAYSTYLE2),
        ):
            w.label(wrap, title, muted=True, size=10 if self.compact else 11).pack(
                anchor="w", padx=2, pady=(4, 0)
            )
            g = ctk.CTkFrame(wrap, fg_color="transparent")
            g.pack(fill="x")
            for i, (name, bit, _b) in enumerate(styles):
                key = f"{'plus' if is_plus else 'reg'}:{bank}:{bit}"
                var = ctk.BooleanVar(value=False)
                self.ps_vars[key] = (bank, bit, is_plus, var)
                cb = ctk.CTkCheckBox(
                    g,
                    text=name,
                    variable=var,
                    font=ctk.CTkFont(size=font_size),
                    text_color=TEXT,
                    width=118 if self.compact else 140,
                    height=18 if self.compact else 24,
                    checkbox_width=14 if self.compact else 18,
                    checkbox_height=14 if self.compact else 18,
                    fg_color=ACCENT,
                    hover_color=ACCENT_HOVER,
                    border_color=BORDER,
                    checkmark_color="#042f2e",
                    command=self.sync_ps_masks_from_checks,
                )
                rr, cc = divmod(i, cols)
                cb.grid(row=rr, column=cc, sticky="w", padx=2, pady=0)

    def sync_ps_masks_from_checks(self) -> None:
        if self._loading:
            return
        # Full Editor lazy-builds PlayStyles; empty ps_vars must NOT zero traits
        if not self.ps_vars:
            return
        self.dirty = True
        self._ps_dirty = True
        t1 = t2 = i1 = i2 = 0
        reg_names: List[str] = []
        plus_names: List[str] = []
        for _key, (bank, bit, is_plus, var) in self.ps_vars.items():
            if not var.get():
                continue
            styles = player_schema.PLAYSTYLE1 if bank == "ps1" else player_schema.PLAYSTYLE2
            nm = next((n for n, b, _ in styles if b == bit), None)
            if is_plus:
                if bank == "ps1":
                    i1 |= bit
                else:
                    i2 |= bit
                if nm:
                    plus_names.append(nm)
            else:
                if bank == "ps1":
                    t1 |= bit
                else:
                    t2 |= bit
                if nm:
                    reg_names.append(nm)
        self.working["trait1"] = t1
        self.working["trait2"] = t2
        self.working["icontrait1"] = i1
        self.working["icontrait2"] = i2
        self.working["playstyles"] = reg_names
        self.working["playstyles_plus"] = plus_names
        self.working["playstylesPlus"] = plus_names
        # keep mask fields in editor-style vars if present
        for field, val in (
            ("trait1", t1),
            ("trait2", t2),
            ("icontrait1", i1),
            ("icontrait2", i2),
        ):
            if field in self.field_vars:
                self.field_vars[field].set(str(val))

    def sync_ps_checks_from_masks(self, card: Optional[Dict[str, Any]] = None) -> None:
        c = card if card is not None else self.working
        # Detect whether source actually had PS data (vs blank defaults).
        # Blank must stay blank so Apply does not wipe Career playstyles.
        raw_traits = [c.get(k) for k in ("trait1", "trait2", "icontrait1", "icontrait2")]
        had_explicit_mask = any(v not in (None, "") for v in raw_traits)
        reg_src = c.get("playstyles") or c.get("play_styles") or []
        plus_src = c.get("playstyles_plus") or c.get("playstylesPlus") or []
        had_lists = bool(reg_src) or bool(plus_src)

        try:
            t1 = int(c.get("trait1") or 0) if c.get("trait1") not in (None, "") else 0
            t2 = int(c.get("trait2") or 0) if c.get("trait2") not in (None, "") else 0
            i1 = int(c.get("icontrait1") or 0) if c.get("icontrait1") not in (None, "") else 0
            i2 = int(c.get("icontrait2") or 0) if c.get("icontrait2") not in (None, "") else 0
        except (TypeError, ValueError):
            t1 = t2 = i1 = i2 = 0
        if t1 == 0 and t2 == 0:
            reg = reg_src
            if reg:
                t1, t2 = player_schema.playstyle_names_to_masks(
                    player_schema.playstyle_list_to_names(reg)
                )
                if t1 == 0 and t2 == 0:
                    t1, t2 = futgg_client.futgg_playstyle_ids_to_masks(reg)
        if i1 == 0 and i2 == 0:
            plus = plus_src
            if plus:
                i1, i2 = player_schema.playstyle_names_to_masks(
                    player_schema.playstyle_list_to_names(plus)
                )
                if i1 == 0 and i2 == 0:
                    i1, i2 = futgg_client.futgg_playstyle_ids_to_masks(plus)
        for _key, (bank, bit, is_plus, var) in self.ps_vars.items():
            mask = (i1 if bank == "ps1" else i2) if is_plus else (t1 if bank == "ps1" else t2)
            try:
                var.set(bool(mask & bit))
            except Exception:
                pass
        if had_explicit_mask or had_lists or t1 or t2 or i1 or i2:
            self.working["trait1"] = t1
            self.working["trait2"] = t2
            self.working["icontrait1"] = i1
            self.working["icontrait2"] = i2
        else:
            # No PS on source card — leave blank so only_nonempty skips them
            self.working["trait1"] = ""
            self.working["trait2"] = ""
            self.working["icontrait1"] = ""
            self.working["icontrait2"] = ""

    @staticmethod
    def meta_bits(card: Dict[str, Any]) -> List[str]:
        reg, plus = player_schema.card_playstyle_labels(card)
        ps = ", ".join(reg[:8]) + ("…" if len(reg) > 8 else "") if reg else "—"
        psp = ", ".join(plus[:6]) + ("…" if len(plus) > 6 else "") if plus else "—"
        return [
            f"Club {card.get('club') or '—'}",
            f"Nation {card.get('nation') or '—'}",
            f"League {card.get('league') or '—'}",
            f"Rarity {card.get('revision') or card.get('rarity') or '—'}",
            f"AcceleRATE {card.get('accelerateType') or '—'}",
            f"PS {ps}",
            f"PS+ {psp}",
        ]

    def _push_working_into_fields(self) -> None:
        """Copy self.working into any field_vars currently built (lazy sections)."""
        card = self.working or {}
        prev = self._loading
        self._loading = True
        try:
            for field, var in self.field_vars.items():
                v = card.get(field)
                try:
                    var.set("" if v in (None, "") else str(int(float(v))))
                except (TypeError, ValueError):
                    var.set("" if v in (None, "") else str(v))
            if self.ps_vars:
                self.sync_ps_checks_from_masks(card)
        finally:
            self._loading = prev

    def _refresh_meta_chips(self) -> None:
        """Update compact OVR/POT chips from working card (no-op if chips absent)."""
        card = self.working or {}

        def _fmt(key: str) -> str:
            v = card.get(key)
            if v in (None, ""):
                return "—"
            try:
                return str(int(float(v)))
            except (TypeError, ValueError):
                return str(v)

        if self._ovr_chip is not None:
            try:
                self._ovr_chip.configure(text=f"OVR {_fmt('overallrating')}")
            except Exception:
                pass
        if self._pot_chip is not None:
            try:
                self._pot_chip.configure(text=f"POT {_fmt('potential')}")
            except Exception:
                pass

    def load_card(self, card: Dict[str, Any], *, name_var: Optional[Any] = None) -> None:
        self._loading = True
        try:
            if self.compact:
                self.working = card_to_lua.card_to_editor_card(dict(card))
            else:
                self.working = player_schema.normalize_player_card(dict(card))
            if name_var is not None and self.working.get("name"):
                name_var.set(str(self.working.get("name")))
            if self.meta_line is not None:
                try:
                    self.meta_line.configure(text=" · ".join(self.meta_bits(self.working or card)))
                except Exception:
                    pass
            self._refresh_meta_chips()
            self._push_working_into_fields()
            self.dirty = False
            self._ps_dirty = False
        finally:
            self._loading = False

    def collect_card(self, *, name: str = "", base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Only recompute masks from checkboxes when the user toggled PS.
        # Always-sync would force trait*=0 for cards with no PS data and wipe CM.
        if self._ps_dirty:
            self.sync_ps_masks_from_checks()
        out = dict(base or self.working or {})
        if name:
            out["name"] = name
        for field, var in self.field_vars.items():
            raw = (var.get() or "").strip()
            if raw == "":
                continue
            try:
                out[field] = int(float(raw))
            except ValueError:
                out[field] = raw
        for k in (
            "trait1",
            "trait2",
            "icontrait1",
            "icontrait2",
            "playstyles",
            "playstyles_plus",
            "playstylesPlus",
        ):
            if k in self.working:
                out[k] = self.working[k]
        if self.compact:
            return card_to_lua.card_to_editor_card(out)
        return player_schema.normalize_player_card(out)

    def enabled_categories(self) -> List[str]:
        if not self.cat_vars:
            return list(player_schema.default_enabled_categories())
        return [cid for cid, var in self.cat_vars.items() if var.get()]
