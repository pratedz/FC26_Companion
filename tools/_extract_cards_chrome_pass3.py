"""Pass 3: move Cards handlers + chrome out of gui.py into modules."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
gui_path = ROOT / "src" / "gui.py"
lines = gui_path.read_text(encoding="utf-8").splitlines(keepends=True)


def find_def(name: str) -> int:
    for i, L in enumerate(lines):
        if re.match(rf"    def {re.escape(name)}\(", L):
            if i > 0 and lines[i - 1].strip().startswith("@"):
                return i - 1
            return i
    raise SystemExit(f"missing {name}")


def method_end(start: int) -> int:
    i = start + 1
    if lines[start].strip().startswith("@"):
        i = start + 2
    while i < len(lines):
        L = lines[i]
        if L.startswith("    def ") or L.startswith("class ") or re.match(r"^def ", L):
            return i
        if L.strip().startswith("@") and i + 1 < len(lines) and lines[i + 1].startswith(
            "    def "
        ):
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
        def_i = 0
        while def_i < len(body) and body[def_i].strip().startswith("@"):
            def_i += 1
        # Join multi-line signatures until line ending with ':'
        sig_parts = [body[def_i].rstrip("\n")]
        j = def_i
        while j < len(body) - 1 and not sig_parts[-1].rstrip().endswith(":"):
            j += 1
            sig_parts.append(body[j].rstrip("\n").strip())
        first = " ".join(p.strip() for p in sig_parts)
        first = re.sub(r"\s+", " ", first)
        body_start = j + 1
        m = re.match(r"def (\w+)\(self(?:,\s*)?(.*)\)(.*):", first)
        m_static = re.match(r"def (\w+)\((.*)\)(.*):", first) if not m else None
        if m:
            fname, rest_args, ret = m.group(1), m.group(2), m.group(3)
            sig = (
                f"def {fname}(app: Any, {rest_args}){ret}:\n"
                if rest_args
                else f"def {fname}(app: Any){ret}:\n"
            )
        elif m_static:
            fname, rest_args, ret = m_static.group(1), m_static.group(2), m_static.group(3)
            sig = f"def {fname}({rest_args}){ret}:\n"
        else:
            raise SystemExit(f"parse fail: {first!r}")
        out = [sig]
        for L in body[body_start:]:
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
            L2 = L2.replace(
                "from .ui.tabs import cards as cards_tab", "from . import cards as cards_tab"
            )
            L2 = L2.replace(
                "from ...ui.tabs import cards as cards_tab",
                "from . import cards as cards_tab",
            )
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


# ── Cards handlers (logic, not build which already lives in cards.py) ──
card_names = [
    "_search_async",
    "_set_search_progress",
    "_show_hits",
    "_apply_card_filters",
    "_show_favorites_list",
    "_toggle_favorite",
    "_on_variant_select",
    "_select_variant_index",
    "_on_variant_enriched",
    "_ensure_card_surface",
    "_load_card_into_editor_boxes",
    "_mark_card_editor_dirty",
    "_collect_card_from_editor_boxes",
    "_card_ai_chat_send",
    "_card_chat_append",
    "_card_ai_chat_done",
    "_card_ai_chat_fail",
    "_target_as_before_card",
    "_card_enabled_categories",
    "_update_card_detail",
    "_show_compare",
    "_enrich_selected_card",
    "_enrich_done",
    "_card_to_editor",
    "_ai_from_card",
    "_ai_from_card_done",
    "_ai_from_card_err",
    "_search_err",
    "_apply_card",
]

# ── Chrome / shell ops ──
chrome_names = [
    "_build_header",
    "_build_ops_bar",
    "_build_footer",
    "_turbo_install_arm",
    "_focus_card_search",
    "_maybe_first_run",
    "_prebuild_card_index_bg",
    "_force_drain",
    "_show_health",
    "_show_job_history",
    "_show_queue_inspector",
    "_clear_queue_all",
    "_undo_last_apply",
    "_refresh_sync_chrome",
    "_require_live_worker",
    "_tick_bridge_status",
    "_copy_bridge_script",
    "_copy_last_apply_lua",
    "_enable_auto_apply",
    "_set_apply_steps",
    "_set_apply_status",
    "_set_apply_btn_text",
    "_show_arm_guide",
    "_queue_and_wait_bridge",
    "_grok_connect_popup",
]

cards_header_extra = '''
import threading
from typing import Any, Dict, List, Optional

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
from ...card_types import CardDict, CardRow

'''

chrome_header = '''"""App chrome — header, ops bar, footer, sync/arm, Grok connect."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    messagebox = None  # type: ignore

from .. import actions
from .. import apply_service
from .. import health_check
from .. import job_history
from .. import le_apply
from .. import paths
from .. import undo_apply
from .. import __version__
from ..ui_theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    CARD,
    DANGER,
    FONT_MONO,
    FONT_UI,
    LIST_BG,
    MUTED,
    MUTED_DIM,
    PANEL,
    RADIUS,
    RADIUS_SM,
    SUCCESS,
    TEXT,
    WARNING,
)
from . import apply_chrome
from . import widgets as w

# Pill tokens with fallbacks (match gui.py)
from .. import ui_theme as _ui_theme

PILL_LIVE_BG = getattr(_ui_theme, "PILL_LIVE_BG", "#052e1c")
PILL_OFF_BG = getattr(_ui_theme, "PILL_OFF_BG", "#422006")
PILL_BUSY_BG = getattr(_ui_theme, "PILL_BUSY_BG", "#083344")

'''

# Fix chrome imports: methods use `from ... import product` after rewrite
# and `from .. import apply_flow` etc.

card_chunks, card_ranges = to_module_funcs(card_names)
chrome_chunks, chrome_ranges = to_module_funcs(chrome_names)

# Append card handlers to existing cards.py
cards_path = ROOT / "src" / "ui" / "tabs" / "cards.py"
existing_cards = cards_path.read_text(encoding="utf-8")
# ensure imports present
if "from ... import card_catalog" not in existing_cards:
    # inject after player_edit import block
    inject_at = existing_cards.find("from ..player_edit import PlayerEditSurface")
    if inject_at < 0:
        inject_at = existing_cards.find("from __future__")
    # better: append imports after existing imports section
    marker = "from ..player_edit import PlayerEditSurface\n"
    if marker in existing_cards:
        existing_cards = existing_cards.replace(
            marker,
            marker
            + """
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
from ...card_types import CardDict, CardRow
""",
        )
# Fix relative imports inside card chunks for tabs package
fixed_card_chunks = []
for ch in card_chunks:
    ch = ch.replace("from ...ui.tabs import cards as cards_tab\n", "")
    ch = ch.replace("from . import cards as cards_tab\n", "")
    # ensure_card_surface calls cards_tab._ensure_edit_surface -> local
    ch = ch.replace("cards_tab._ensure_edit_surface", "_ensure_edit_surface")
    ch = ch.replace("from ...ui import apply_flow", "from .. import apply_flow")
    ch = ch.replace("from .. import apply_flow", "from .. import apply_flow")
    # product path
    ch = ch.replace("from ... import product as product_mod", "from ... import product as product_mod")
    fixed_card_chunks.append(ch)

cards_path.write_text(
    existing_cards.rstrip() + "\n\n# ── Handlers (extracted from PremiumApp) ──\n\n"
    + "\n\n".join(fixed_card_chunks)
    + "\n",
    encoding="utf-8",
)
print("cards.py lines", len(cards_path.read_text(encoding="utf-8").splitlines()))

# chrome.py needs different import rewrite: from .. not ...
chrome_fixed = []
for ch in chrome_chunks:
    ch = ch.replace("from ... import ", "from .. import ")
    ch = ch.replace("from ...ui_theme", "from ..ui_theme")
    ch = ch.replace("from .. import apply_flow", "from . import apply_flow")
    ch = ch.replace("from ...ui import apply_flow", "from . import apply_flow")
    # product for undo
    ch = ch.replace("from .. import product as product_mod", "from .. import product as product_mod")
    chrome_fixed.append(ch)

chrome_path = ROOT / "src" / "ui" / "chrome.py"
chrome_path.write_text(chrome_header + "\n\n".join(chrome_fixed) + "\n", encoding="utf-8")
print("chrome.py lines", len(chrome_path.read_text(encoding="utf-8").splitlines()))

# Replace methods in gui with thin wrappers
all_ranges = card_ranges + chrome_ranges
all_ranges.sort(key=lambda x: x[0], reverse=True)
method_mod = {n: "cards" for n in card_names}
method_mod.update({n: "chrome" for n in chrome_names})

for s, e, name in all_ranges:
    mod = method_mod[name]
    alias = "cards_tab" if mod == "cards" else "chrome"
    # reconstruct multi-line signature from original range
    def_i = s
    while def_i < e and lines[def_i].strip().startswith("@"):
        def_i += 1
    sig_parts = [lines[def_i].rstrip("\n")]
    j = def_i
    while j < e - 1 and not sig_parts[-1].rstrip().endswith(":"):
        j += 1
        sig_parts.append(lines[j].rstrip("\n").strip())
    first = re.sub(r"\s+", " ", " ".join(p.strip() for p in sig_parts))
    m = re.match(r"def (\w+)\(self(?:,\s*)?(.*)\)(.*):", first)
    m_static = re.match(r"def (\w+)\((.*)\)(.*):", first) if not m else None
    if m:
        rest, ret = m.group(2), m.group(3)
        if rest:
            call = forward_call(rest)
            # keep multi-line friendly single-line wrapper
            wrapper = (
                f"    def {name}(self, {rest}){ret}:\n"
                f"        return {alias}.{name}(self, {call})\n\n"
            )
        else:
            wrapper = (
                f"    def {name}(self){ret}:\n"
                f"        return {alias}.{name}(self)\n\n"
            )
    elif m_static:
        rest, ret = m_static.group(2), m_static.group(3)
        call = forward_call(rest) if rest else ""
        wrapper = (
            f"    @staticmethod\n"
            f"    def {name}({rest}){ret}:\n"
            f"        return {alias}.{name}({call})\n\n"
        )
    else:
        raise SystemExit(f"wrapper parse fail {first}")
    lines[s:e] = [wrapper]

text = "".join(lines)
if "from .ui import chrome" not in text:
    text = text.replace(
        "from .ui import apply_chrome",
        "from .ui import apply_chrome\nfrom .ui import chrome",
    )

gui_path.write_text(text, encoding="utf-8")
print("gui lines", len(text.splitlines()))
print("OK")
