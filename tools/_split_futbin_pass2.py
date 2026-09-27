"""Split futbin_client.py into models / parse / http + thin facade."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "src" / "futbin_client.py").read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)

# 0-based indices for exclusive end
# models: docstring through map_stat_name helpers end (before parse_player_html)
# parse: parse_player_html through to_le_base_player_patch (before FutbinClient)
# http: FutbinClient through fetch_player_url
# cli: _cli to end
# facade: public re-exports

# Find markers (1-based from earlier map)
def find_line(prefix: str) -> int:
    for i, L in enumerate(lines):
        if L.startswith(prefix):
            return i
    raise SystemExit(f"missing {prefix}")


i_player = find_line("class FutbinPlayer")
i_parse = find_line("def parse_player_html")
i_client = find_line("class FutbinClient")
i_save = find_line("def save_cookie_instructions")
i_cli = find_line("def _cli")

# Shared header for submodules (constants live in models)
models_header = '''"""Futbin domain types, constants, and small helpers."""

from __future__ import annotations

'''

# Original file starts with docstring + imports + constants through helpers before parse
# models = lines[0:i_parse] but need to keep imports

models_body = "".join(lines[:i_parse])
parse_body = (
    '"""Futbin HTML/JSON parse and LE card row mapping."""\n\n'
    "from __future__ import annotations\n\n"
    "import json\n"
    "import re\n"
    "from pathlib import Path\n"
    "from typing import Any, Mapping, Optional, Union\n\n"
    "from .futbin_models import (\n"
    "    CDN_HOST,\n"
    "    FUTBIN_HOST,\n"
    "    FutbinParseError,\n"
    "    FutbinPlayer,\n"
    "    _as_int,\n"
    "    _norm_key,\n"
    "    map_stat_name,\n"
    "    parse_futbin_url,\n"
    "    player_image_url,\n"
    "    position_to_code,\n"
    "    year_to_cdn_folder,\n"
    ")\n\n"
    + "".join(lines[i_parse:i_client])
)

http_body = (
    '"""Futbin HTTP client, cache, and high-level import helpers."""\n\n'
    "from __future__ import annotations\n\n"
    "import json\n"
    "import time\n"
    "from pathlib import Path\n"
    "from typing import Any, Mapping, MutableMapping, Optional, Sequence, Union\n"
    "from urllib.parse import urlparse\n\n"
    "from .futbin_models import (\n"
    "    FUTBIN_HOST,\n"
    "    FutbinBlockedError,\n"
    "    FutbinError,\n"
    "    FutbinParseError,\n"
    "    FutbinPlayer,\n"
    "    _is_cloudflare_block,\n"
    "    parse_futbin_url,\n"
    "    year_to_cdn_folder,\n"
    ")\n"
    "from .futbin_parse import (\n"
    "    import_html_file,\n"
    "    import_json,\n"
    "    parse_player_html,\n"
    "    to_le_card_row,\n"
    ")\n\n"
    + "".join(lines[i_client:i_cli])
)

cli_body = (
    '"""Optional CLI for futbin_client module."""\n\n'
    "from __future__ import annotations\n\n"
    "import json\n"
    "import sys\n"
    "from pathlib import Path\n"
    "from typing import Optional, Sequence\n\n"
    "from .futbin_http import (\n"
    "    fetch_player_url,\n"
    "    import_from_html,\n"
    "    import_from_json,\n"
    "    probe_futbin,\n"
    "    save_cookie_instructions,\n"
    ")\n\n"
    + "".join(lines[i_cli:])
)

# Write models: original top through parse_player_html exclusive
(ROOT / "src" / "futbin_models.py").write_text(models_body, encoding="utf-8")
(ROOT / "src" / "futbin_parse.py").write_text(parse_body, encoding="utf-8")
(ROOT / "src" / "futbin_http.py").write_text(http_body, encoding="utf-8")
(ROOT / "src" / "futbin_cli.py").write_text(cli_body, encoding="utf-8")

facade = '''"""Futbin client facade — public API (compat with pre-split imports).

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
'''
(ROOT / "src" / "futbin_client.py").write_text(facade, encoding="utf-8")
print("models", len(models_body.splitlines()))
print("parse", len(parse_body.splitlines()))
print("http", len(http_body.splitlines()))
print("cli", len(cli_body.splitlines()))
print("facade", len(facade.splitlines()))
print("OK")
