"""Snapshot last apply fields for undo Lua generation."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths

# Field names emitted into Lua string literals — reject injection / traversal.
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _path() -> Path:
    return paths.app_root() / "last_apply_snapshot.json"


def _safe_field_name(name: str) -> Optional[str]:
    f = str(name or "").strip()
    if not f or not _FIELD_NAME_RE.match(f):
        return None
    # Prefer known LE editor fields when schema is available
    try:
        from . import player_schema

        if f not in set(player_schema.ALL_EDIT_FIELDS):
            return None
    except Exception:
        pass
    return f


def _lua_comment_safe(s: str, *, max_len: int = 80) -> str:
    t = str(s or "").replace("\r", " ").replace("\n", " ")
    t = t.replace("]", "").replace("[", "").replace('"', "'")
    t = " ".join(t.split())
    return t[:max_len] or "undo"


def save_snapshot(
    *,
    target_id: int,
    fields: List[Tuple[str, int]],
    label: str = "",
) -> None:
    safe_fields = []
    for f, v in fields:
        sf = _safe_field_name(str(f))
        if sf is None:
            continue
        try:
            safe_fields.append({"f": sf, "v": int(v)})
        except (TypeError, ValueError):
            continue
    data = {
        "ts": time.time(),
        "target_id": int(target_id),
        "label": _lua_comment_safe(label),
        "fields": safe_fields,
    }
    try:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_snapshot() -> Optional[Dict[str, Any]]:
    p = _path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("fields"):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def generate_undo_lua(snap: Optional[Dict[str, Any]] = None) -> str:
    """Generate silent Lua that restores snapshot fields onto target id.

    Note: we only store the *applied* card fields (new values), not pre-apply
    DB values — true undo needs a pre-read. This 'undo' re-applies the last
    known good snapshot if user saved before overwrite, or re-sends last card.
    For true pre-state undo, call save_snapshot with previous values before apply.
    """
    s = snap or load_snapshot()
    if not s:
        raise ValueError("No snapshot to undo")
    target = int(s["target_id"])
    rows = []
    for item in s.get("fields") or []:
        if not isinstance(item, dict):
            continue
        f = _safe_field_name(str(item.get("f") or ""))
        if f is None:
            continue
        try:
            v = int(item.get("v"))
        except (TypeError, ValueError):
            continue
        rows.append(f'  {{"{f}", {v}}},')
    if not rows:
        raise ValueError("Snapshot empty")
    label = _lua_comment_safe(s.get("label") or "undo")
    return f"""--[[ LE Companion undo/reapply | id={target} | {label} ]]
pcall(function() require("imports/career_mode/helpers") end)
local target_playerid = {target}
local fields = {{
{chr(10).join(rows)}
}}
local players_table = LE.db:GetTable("players")
if players_table == nil then return end
local current_record = players_table:GetFirstRecord()
local scanned = 0
local MAX_SCAN = 25000
while current_record > 0 and scanned < MAX_SCAN do
  scanned = scanned + 1
  local playerid = tonumber(players_table:GetRecordFieldValue(current_record, "playerid"))
  if playerid ~= nil and playerid == target_playerid then
    for i = 1, #fields do
      pcall(function()
        players_table:SetRecordFieldValue(current_record, fields[i][1], fields[i][2])
      end)
    end
    break
  end
  current_record = players_table:GetNextValidRecord()
end
-- NEVER ReloadPlayersManager — freezes FC26 Career
if Log then Log("[LE Companion] undo/reapply id=" .. tostring(target_playerid)) end
"""


def save_pre_apply_from_card(card: Dict[str, Any], target_id: int, label: str = "") -> None:
    """Store card field updates as snapshot (re-apply last card / soft undo)."""
    from . import field_map

    updates = field_map.extract_attr_updates(card)
    if "overallrating" in card and not any(f == "overallrating" for f, _ in updates):
        try:
            updates.append(("overallrating", int(card["overallrating"])))
        except (TypeError, ValueError):
            pass
    if not updates:
        return
    save_snapshot(target_id=target_id, fields=updates, label=label or str(card.get("name") or ""))
