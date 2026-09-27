"""Shared card row typing for catalog → UI → apply boundaries.

Cards remain plain dicts at runtime (JSON/CSV-friendly). TypedDict documents
the canonical LE-unified shape so call sites stop inventing ad-hoc keys.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict, Union


class CardRow(TypedDict, total=False):
    """Unified card row (catalog / FUT.GG / Futbin / editor working set)."""

    # Identity / meta
    year: str
    source: str
    name: str
    playerid: int
    overallrating: int
    potential: int
    revision: str
    origin: str
    uid: str
    club: str
    nation: str
    league: str
    rarity: str
    accelerateType: str
    slug: str
    eaId: Union[int, str]

    # Skills / positions
    skillmoves: int
    weakfootabilitytypecode: int
    preferredposition1: int
    preferredposition2: int
    preferredposition3: int
    preferredposition4: int

    # Playstyles (masks and/or labels)
    trait1: int
    trait2: int
    icontrait1: int
    icontrait2: int
    playstyles: List[Any]
    playstyles_plus: List[Any]
    playstylesPlus: List[Any]

    # Internal flags
    _enriched: bool
    _match_score: float
    _source_path: str


# Runtime alias used widely today — prefer CardRow at typed boundaries
CardDict = Dict[str, Any]


def as_card_row(data: Optional[Dict[str, Any]]) -> CardRow:
    """Shallow copy into a card-shaped dict (no validation)."""
    return dict(data or {})  # type: ignore[return-value]
