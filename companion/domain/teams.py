"""Read-only club lookup used by the destination-aware Transfers surface.

The v1 app already ships ``card_db/teams.sqlite``. Keeping this tiny query
layer inside v2 lets Transfers use that asset without accepting a raw EA team
id from the user.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path
from typing import Any


def _normalise(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _like_literal(value: str) -> str:
    """Escape user text before placing it in a SQLite LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_clubs(database: Path, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Return real clubs matching a name or known numeric EA team id.

    The directory is optional and read-only. A missing or corrupt database is
    an empty result, never a reason to make a transfer guess.
    """
    text = str(query or "").strip()
    if not text or not database.is_file():
        return []
    try:
        cap = max(1, min(int(limit), 20))
        uri = "file:{}?mode=ro".format(database.resolve().as_posix())
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if text.isdecimal():
                rows = conn.execute(
                    """SELECT t.team_id, t.name, l.name AS league_name
                       FROM team t LEFT JOIN league l ON l.league_id = t.league_id
                       WHERE t.team_id = ? AND t.is_club = 1""",
                    (int(text),),
                ).fetchall()
            else:
                needle = _normalise(text)
                pattern = "%" + _like_literal(needle) + "%"
                rows = conn.execute(
                    """SELECT t.team_id, t.name, l.name AS league_name
                       FROM team t LEFT JOIN league l ON l.league_id = t.league_id
                       WHERE t.is_club = 1 AND t.name_norm LIKE ? ESCAPE '\\'
                       ORDER BY CASE WHEN t.name_norm = ? THEN 0 ELSE 1 END,
                                t.player_count DESC, t.name
                       LIMIT ?""",
                    (pattern, needle, cap),
                ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    return [dict(row) for row in rows]
