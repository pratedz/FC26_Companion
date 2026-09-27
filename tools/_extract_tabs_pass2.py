"""One-shot: extract Boost/Squad/Editor/Catalog/About from gui.py into ui/tabs/."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
gui_path = ROOT / "src" / "gui.py"
lines = gui_path.read_text(encoding="utf-8").splitlines(keepends=True)


def find_def(name: str) -> int:
    for i, L in enumerate(lines):
        if re.match(rf"    def {re.escape(name)}\(", L):
            # include @staticmethod / @classmethod decorator if present
            if i > 0 and lines[i - 1].strip().startswith("@"):
                return i - 1
            return i
    raise SystemExit(f"missing {name}")


def method_end(start: int) -> int:
    i = start + 1
    # skip decorator line if start is decorator
    if lines[start].strip().startswith("@"):
        i = start + 2  # after decorator + def
    while i < len(lines):
        L = lines[i]
        if L.startswith("    def ") or L.startswith("class ") or re.match(r"^def ", L):
            return i
        if L.strip().startswith("@") and i + 1 < len(lines) and lines[i + 1].startswith("    def "):
            return i
        i += 1
    return len(lines)


def to_module_funcs(names: list[str]) -> tuple[list[str], list[tuple[int, int, str]]]:
    chunks: list[str] = []
    ranges: list[tuple[int, int, str]] = []
    for name in names:
        s = find_def(name)
        e = method_end(s)
        ranges.append((s, e, name))
        body = lines[s:e]
        # skip leading decorators
        def_i = 0
        while def_i < len(body) and body[def_i].strip().startswith("@"):
            def_i += 1
        first = body[def_i].rstrip("\n")
        # Instance method
        m = re.match(r"\s*def (\w+)\(self(?:,\s*)?(.*)\)(.*):", first)
        # Staticmethod without self (e.g. _boost_category_icon)
        m_static = re.match(r"\s*def (\w+)\((.*)\)(.*):", first) if not m else None
        if m:
            fname = m.group(1)
            rest_args = m.group(2)
            ret = m.group(3)
            if rest_args:
                sig = f"def {fname}(app: Any, {rest_args}){ret}:\n"
            else:
                sig = f"def {fname}(app: Any){ret}:\n"
        elif m_static:
            fname = m_static.group(1)
            rest_args = m_static.group(2)
            ret = m_static.group(3)
            sig = f"def {fname}({rest_args}){ret}:\n"
        else:
            raise SystemExit(f"parse fail: {first!r}")
        out = [sig]
        for L in body[def_i + 1 :]:
            L2 = L[4:] if L.startswith("    ") else L
            L2 = re.sub(r"\bself\b", "app", L2)
            L2 = L2.replace("_btn(", "w.btn(")
            L2 = L2.replace("_label(", "w.label(")
            L2 = L2.replace("_entry(", "w.entry(")
            L2 = L2.replace("_panel(", "w.panel(")
            L2 = L2.replace("_listbox(", "w.listbox(")
            L2 = L2.replace("from . import ", "from ... import ")
            L2 = L2.replace("from .ui import ", "from .. import ")
            L2 = L2.replace("from .ui_theme", "from ...ui_theme")
            L2 = L2.replace("from .ui.player_edit", "from ..player_edit")
            out.append(L2)
        chunks.append("".join(out))
    return chunks, ranges


def forward_call(rest: str) -> str:
    if not rest.strip():
        return ""
    args: list[str] = []
    depth = 0
    cur = ""
    for ch in rest:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    forward: list[str] = []
    for a in args:
        a0 = a.split("=")[0].strip()
        aname = a0.split(":")[0].strip()
        if aname.startswith("*"):
            forward.append(aname)
        elif "=" in a:
            forward.append(f"{aname}={aname}")
        else:
            forward.append(aname)
    return ", ".join(forward)


boost_names = [
    "_boost_category_icon",
    "_build_profiles_tab",
    "_boost_fill_cards",
    "_run_profile",
    "_run_pack",
    "_best_match_for_target",
    "_batch_apply_squad",
    "_restore_last_snapshot",
]
squad_names = [
    "_build_squad_tab",
    "_refresh_squad_board",
    "_on_squad_board_select",
    "_squad_go_cards",
]
editor_names = [
    "_build_editor_tab",
    "_finish_editor_surface",
    "_ensure_editor_surface",
    "_editor_enabled_categories",
    "_editor_cats_defaults",
    "_editor_cats_all",
    "_editor_cats_none",
    "_editor_collect_card",
    "_editor_fill_form",
    "_editor_clear",
    "_editor_apply_preset",
    "_editor_load_from_target",
    "_editor_apply",
    "_ai_fill_async",
    "_ai_fill_done",
    "_ai_fill_err",
]
catalog_names = [
    "_build_catalog_tab",
    "_probe",
    "_sync_futgg",
    "_append_fut",
    "_sync_player_dialog",
    "_show_json",
    "_imp_html",
    "_imp_json",
]
about_names = ["_build_about_tab"]

headers = {
    "boost": '''"""Boost tab — packs, profile cards, batch/match helpers."""

from __future__ import annotations

import threading
from typing import Any, Optional

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    messagebox = None  # type: ignore

from ... import profiles
from ... import target_players
from ...ui_theme import (
    ACCENT,
    ACCENT_DIM,
    BG,
    FONT_UI,
    MUTED,
    PANEL,
    RADIUS,
    TEXT,
)
from .. import widgets as w


def category_icon(category: str) -> str:
    """Map profile category → assets/icons stem for Boost cards."""
    cat = (category or "").strip().lower()
    if cat == "user_team" or "fitness" in cat:
        return "fitness"
    if cat == "user_team_event":
        return "refresh"
    if cat == "contracts":
        return "contract"
    if cat == "mass_edit":
        return "unlock"
    if cat in ("unlocks", "unlock"):
        return "unlock"
    if cat == "squad":
        return "export"
    return "medal"

''',
    "squad": '''"""Squad board tab."""

from __future__ import annotations

from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import target_players
from ...ui_theme import (
    ACCENT,
    BG,
    CARD,
    FONT_MONO,
    LIST_BG,
    MUTED,
    TEXT,
)
from .. import widgets as w

''',
    "editor": '''"""Editor tab — full PlayerEditSurface + AI fill."""

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

''',
    "catalog": '''"""Catalog / Futbin-FUT.GG import tab."""

from __future__ import annotations

import threading
from typing import Any

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    filedialog = None  # type: ignore
    messagebox = None  # type: ignore

from ... import futgg_client
from ...futbin_client import import_from_html, import_from_json, probe_futbin
from ...ui_theme import BG, MUTED, TEXT
from .. import widgets as w

''',
    "about": '''"""About tab."""

from __future__ import annotations

from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ... import __version__
from ...ui_theme import BG, MUTED, TEXT
from .. import widgets as w

''',
}

groups = [
    ("boost", boost_names),
    ("squad", squad_names),
    ("editor", editor_names),
    ("catalog", catalog_names),
    ("about", about_names),
]

method_mod: dict[str, str] = {}
all_ranges: list[tuple[int, int, str]] = []

for mod, names in groups:
    chunks, ranges = to_module_funcs(names)
    # skip writing full _boost_category_icon body; use category_icon only
    if mod == "boost":
        filtered = []
        for name, chunk in zip(names, chunks):
            if name == "_boost_category_icon":
                continue
            filtered.append(chunk)
        body = headers[mod] + "\n\n".join(filtered)
    else:
        body = headers[mod] + "\n\n".join(chunks)
    (ROOT / "src" / "ui" / "tabs" / f"{mod}.py").write_text(body, encoding="utf-8")
    print(f"wrote {mod}.py ({len(body.splitlines())} lines)")
    for r in ranges:
        all_ranges.append(r)
        method_mod[r[2]] = mod

all_ranges.sort(key=lambda x: x[0], reverse=True)

for s, e, name in all_ranges:
    mod = method_mod[name]
    first = lines[s].rstrip("\n")
    m = re.match(r"\s*def (\w+)\(self(?:,\s*)?(.*)\)(.*):", first)
    m_static = re.match(r"\s*def (\w+)\((.*)\)(.*):", first) if not m else None
    if name == "_boost_category_icon":
        wrapper = (
            "    @staticmethod\n"
            "    def _boost_category_icon(category: str) -> str:\n"
            "        return boost_tab.category_icon(category)\n\n"
        )
    elif m:
        rest = m.group(2)
        ret = m.group(3)
        if rest:
            call_args = forward_call(rest)
            wrapper = (
                f"    def {name}(self, {rest}){ret}:\n"
                f"        return {mod}_tab.{name}(self, {call_args})\n\n"
            )
        else:
            wrapper = (
                f"    def {name}(self){ret}:\n"
                f"        return {mod}_tab.{name}(self)\n\n"
            )
    elif m_static:
        rest = m_static.group(2)
        ret = m_static.group(3)
        call_args = forward_call(rest) if rest else ""
        wrapper = (
            f"    @staticmethod\n"
            f"    def {name}({rest}){ret}:\n"
            f"        return {mod}_tab.{name}({call_args})\n\n"
        )
    else:
        raise SystemExit(f"wrapper parse fail: {first}")
    lines[s:e] = [wrapper]

text = "".join(lines)
needle = "from .ui.tabs import cards as cards_tab"
if "from .ui.tabs import boost as boost_tab" not in text:
    text = text.replace(
        needle,
        "from .ui.tabs import about as about_tab\n"
        "from .ui.tabs import boost as boost_tab\n"
        "from .ui.tabs import cards as cards_tab\n"
        "from .ui.tabs import catalog as catalog_tab\n"
        "from .ui.tabs import editor as editor_tab\n"
        "from .ui.tabs import squad as squad_tab",
    )

gui_path.write_text(text, encoding="utf-8")
print("gui lines", len(text.splitlines()))
print("OK")
