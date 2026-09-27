"""Futbin client facade — public API (compat with pre-split imports).

Implementation split:
  futbin_models — types/constants
  futbin_parse  — HTML/JSON → LE card rows
  futbin_http   — network client + cache helpers
  futbin_cli    — optional CLI
"""

from __future__ import annotations

from .futbin_models import (  # noqa: F401
    CDN_HOST,
    FUTBIN_HOST,
    POSITION_TO_CODE,
    FutbinBlockedError,
    FutbinError,
    FutbinParseError,
    FutbinPlayer,
    map_stat_name,
    parse_futbin_url,
    player_image_url,
    position_to_code,
    year_to_cdn_folder,
)
from .futbin_parse import (  # noqa: F401
    import_html_file,
    import_json,
    parse_player_html,
    to_le_base_player_patch,
    to_le_card_row,
)
from .futbin_http import (  # noqa: F401
    FutbinClient,
    fetch_player_url,
    import_from_html,
    import_from_json,
    probe_futbin,
    save_cookie_instructions,
)

try:
    from .futbin_cli import _cli
except ImportError:  # pragma: no cover
    _cli = None  # type: ignore

__all__ = [
    "CDN_HOST",
    "FUTBIN_HOST",
    "POSITION_TO_CODE",
    "FutbinBlockedError",
    "FutbinClient",
    "FutbinError",
    "FutbinParseError",
    "FutbinPlayer",
    "fetch_player_url",
    "import_from_html",
    "import_from_json",
    "import_html_file",
    "import_json",
    "map_stat_name",
    "parse_futbin_url",
    "parse_player_html",
    "player_image_url",
    "position_to_code",
    "probe_futbin",
    "save_cookie_instructions",
    "to_le_base_player_patch",
    "to_le_card_row",
    "year_to_cdn_folder",
]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli() if _cli else 1)
