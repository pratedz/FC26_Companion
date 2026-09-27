"""LE Lua snippet templates for one-shot profile actions.

Patterns match stock scripts under lua/scripts/auto_max_user_team_*.lua
and helpers in lua/libs/v2/imports/career_mode/helpers.lua.
Snippets apply immediately (no event handlers).
"""

from __future__ import annotations

from typing import Dict

# One-shot snippets: require career helpers then call UserTeam* helpers.

SNIPPETS: Dict[str, str] = {
    "full_fitness": """--- Full Fitness (one-shot) — user senior team
--- Matches UserTeamSetPlayersFitness from career_mode/helpers.lua
require 'imports/career_mode/helpers'

UserTeamSetPlayersFitness(95)
""",
    "full_sharpness": """--- Full Sharpness (one-shot) — user senior team
require 'imports/career_mode/helpers'

UserTeamSetPlayersSharpness(100)
""",
    "full_form": """--- Full Form (one-shot) — user senior team
require 'imports/career_mode/helpers'

UserTeamSetPlayersForm(100)
""",
    "full_morale": """--- Full Morale (one-shot) — user senior team
require 'imports/career_mode/helpers'

UserTeamSetPlayersMorale(100)
""",
    "full_form_morale_sharpness": """--- Full Form + Morale + Sharpness (one-shot) — user senior team
--- Stock combined helper uses form=100, sharpness=100, morale=120 (max complacent)
require 'imports/career_mode/helpers'

UserTeamSetPlayersFormSharpnessMorale(
    100,     -- Form
    100,     -- Sharpness
    120      -- Morale
)
""",
    "matchday_pack": """--- Match Day Pack — fitness + sharpness (one-shot)
require 'imports/career_mode/helpers'

UserTeamSetPlayersFitness(95)
UserTeamSetPlayersSharpness(100)
""",
    "squad_boost_pack": """--- Full Squad Boost — fitness + form/morale/sharpness (one-shot)
require 'imports/career_mode/helpers'

UserTeamSetPlayersFitness(95)
UserTeamSetPlayersFormSharpnessMorale(100, 100, 120)
""",
}


def get_snippet(snippet_key: str) -> str:
    """Return Lua source for a snippet_key. Raises KeyError if unknown."""
    if snippet_key not in SNIPPETS:
        known = ", ".join(sorted(SNIPPETS))
        raise KeyError(f"Unknown snippet_key {snippet_key!r}. Known: {known}")
    return SNIPPETS[snippet_key]


def list_snippet_keys() -> list[str]:
    return sorted(SNIPPETS.keys())
