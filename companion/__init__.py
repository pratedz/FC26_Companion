"""FC 26 Career Companion — v2.

A rebuilt companion for EA SPORTS FC 26 Career Mode that works alongside
xAranaktu's Live Editor. v1 lives in ``src/`` and keeps working; this package
is the v2 architecture described in ``docs/V2_ARCHITECTURE.md`` and
``docs/V2_INGAME_CORE.md`` (reconciled in ``companion/DECISIONS.md``).

Layering (strict downward dependency):

    ui/        -> app/ (types only)
    app/       -> domain/, core/
    domain/    -> core/ (types + ports only), stdlib
    core/      -> stdlib only
    platform/  -> stdlib + Windows APIs; implements core ports
"""

from __future__ import annotations

__version__ = "2.10.40"
PROTOCOL_V = 3
# Must match ingame/le_companion/version.lua. A Companion build older than the
# installed worker refuses the live session, so the app looks disconnected
# even after a script copy or an FC restart.
CORE_VERSION = "2.6.26"  # the resident Lua core semver this build ships/expects
# add_to_team v4 is unchanged from this core. Injury-only bumps of CORE_VERSION
# must not refuse Checkout while that worker is still the one Live Editor loaded.
SIGN_CORE_MIN = "2.6.25"


def core_at_least(value: str, minimum: str) -> bool:
    """True when both values are x.y.z and ``value`` is at least ``minimum``."""

    def _parse(text: str) -> tuple[int, int, int] | None:
        parts = str(text or "").strip().split(".")
        if len(parts) != 3:
            return None
        try:
            parsed = tuple(int(part) for part in parts)
        except ValueError:
            return None
        if any(part < 0 for part in parsed):
            return None
        return parsed  # type: ignore[return-value]

    have = _parse(value)
    want = _parse(minimum)
    return have is not None and want is not None and have >= want
