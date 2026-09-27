"""Bounded, real-player candidates for the Add Player dummy flow.

These are *not* universally safe IDs.  They are low-rated FC26 players that
are free agents in the supplied/base roster.  A Career save can sign, loan, or
transfer any of them, so the Lua worker must prove that a candidate is still a
free agent immediately before it is overwritten.

The first five were confirmed in the user's Live Editor Free Agents view; the
remaining entries are present as non-retiring FC26 base free agents locally.
Keeping this list small lets a squad refresh make at most 30 free-agent probes
instead of scanning or guessing across the whole Career database.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FreeAgentCandidate:
    """A known low-rated base-roster free-agent *candidate*, never a guarantee."""

    playerid: int
    name: str
    overall: int


# Do not add club players to this pool.  Live Editor still re-checks every row
# (existence, free-agent team id, no loan/presign, low OVR, and per-save use)
# just before a player is changed.
REAL_FREE_AGENT_CANDIDATES: tuple[FreeAgentCandidate, ...] = (
    FreeAgentCandidate(66371, "Moisés Rodríguez", 41),
    FreeAgentCandidate(66388, "Andy Domínguez", 41),
    FreeAgentCandidate(66393, "Daniel Cardoza", 41),
    FreeAgentCandidate(66449, "Ntandoyenkosi Nkosi", 41),
    FreeAgentCandidate(66040, "Joshua Trigueño", 41),
    FreeAgentCandidate(76241, "M. Gouda", 59),
    FreeAgentCandidate(75401, "A. Al Rawi", 60),
    FreeAgentCandidate(279193, "T. Galvez", 60),
    FreeAgentCandidate(268986, "A. Suhail", 61),
    FreeAgentCandidate(76229, "M. Badreldin", 62),
    FreeAgentCandidate(75403, "A. Al Yazidi", 63),
    FreeAgentCandidate(76234, "A. Yousef", 63),
    FreeAgentCandidate(268982, "J. Gaber", 63),
    FreeAgentCandidate(233865, "R. Hale", 64),
    FreeAgentCandidate(243906, "U. Nissilä", 64),
    FreeAgentCandidate(272472, "S. Zakaria", 64),
    FreeAgentCandidate(279194, "M. Mashaal", 64),
    FreeAgentCandidate(239878, "A. Madibo", 65),
    FreeAgentCandidate(268877, "Y. Abdurisag", 65),
    FreeAgentCandidate(208597, "H. Hermannsson", 66),
    FreeAgentCandidate(268780, "M. Muntari", 66),
    FreeAgentCandidate(74236, "A. Al Ganehi", 67),
    FreeAgentCandidate(190640, "V. Pálsson", 67),
    FreeAgentCandidate(234298, "N. Alho", 67),
    FreeAgentCandidate(258599, "R. Ivanov", 67),
    FreeAgentCandidate(268099, "P. Szappanos", 67),
    FreeAgentCandidate(276232, "D. Lukács", 67),
    FreeAgentCandidate(278819, "R. McCausland", 67),
    FreeAgentCandidate(207593, "J. Uronen", 68),
    FreeAgentCandidate(231254, "A. Osváth", 68),
)

REAL_FREE_AGENT_CANDIDATE_IDS: tuple[int, ...] = tuple(
    candidate.playerid for candidate in REAL_FREE_AGENT_CANDIDATES
)


__all__ = [
    "FreeAgentCandidate",
    "REAL_FREE_AGENT_CANDIDATES",
    "REAL_FREE_AGENT_CANDIDATE_IDS",
]
