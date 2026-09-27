"""Club surface — read the squad, filter it, describe one player.

Backs ``docs/V2_UX_DESIGN.md`` §3.2 (Setup) and §3.3 (connected).

The one thing this module refuses to do is v1's failure mode: v1 polled the
game blind for 25 seconds and then showed a generic "failed". Here, *not
having a squad export is a first-class state*, known instantly and offline:
:attr:`SquadView.loaded` is False, :attr:`SquadView.reason` says exactly why,
and :attr:`SquadView.next_action` names the control that fixes it. Nothing in
this module waits on the game to find that out.

What ``current_squad.json`` actually contains, and therefore what is honest to
show: ``playerid``, ``name``, ``position``, ``overallrating``, ``potential``,
``jerseynumber``. Age and contract are **not** in it. They are resolved from
``base_players.csv`` (FC 26 launch data) and carried with an explicit
``source`` so the UI can caption them rather than pass them off as save reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ... import base_players
from ... import product
from ... import target_players
from . import MutationResult, Outcome, Progress, from_apply_result, tick

__all__ = [
    "SquadPlayer",
    "TeamInfo",
    "SquadView",
    "PlayerPage",
    "PlayerDetail",
    "FreeAgent",
    "SORT_KEYS",
    "SQUAD_VIEWS",
    "load_squad",
    "players",
    "player_detail",
    "export_squad",
    "free_agent_pool",
    "team_info",
]


#: The FC 26 season reference date, as gregorian days (see
#: ``add_player.to_gregorian_days``). Ages are computed against this fixed
#: point rather than "today" so that a squad rendered in July and the same
#: squad rendered in December agree, and so tests never depend on the clock.
FC26_SEASON_START_DAYS = 161698  # add_player.to_gregorian_days(2025, 7, 1)
FC26_SEASON_START_LABEL = "2025-07-01 (FC 26 season start)"

_DAYS_PER_YEAR = 365.2425

#: What ``players()`` will sort by. ``name`` ascending is the default.
SORT_KEYS: Tuple[str, ...] = (
    "name",
    "overall",
    "potential",
    "position",
    "age",
    "contract",
    "jersey",
    "playerid",
)

#: The ``Squad ▾`` view switcher in §3.3.
SQUAD_VIEWS: Tuple[str, ...] = ("squad", "free_agents")

#: Position group facets (the POS ▾ menu).
POSITION_GROUPS: Dict[str, Tuple[str, ...]] = {
    "GK": ("GK",),
    "DEF": ("SW", "RWB", "RB", "RCB", "CB", "LCB", "LB", "LWB"),
    "MID": (
        "RDM", "CDM", "LDM", "RM", "RCM", "CM", "LCM", "LM",
        "RAM", "CAM", "LAM",
    ),
    "ATT": ("RF", "CF", "LF", "RW", "RS", "ST", "LS", "LW"),
}


# ── shapes ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SquadPlayer:
    """One row of the Club grid. Everything here is either read or captioned."""

    playerid: int
    name: str
    position: str = ""
    overall: Optional[int] = None
    potential: Optional[int] = None
    jersey: Optional[int] = None
    #: Years old at :data:`FC26_SEASON_START_LABEL`, or None when unknown.
    age: Optional[int] = None
    age_source: str = ""
    #: Season the contract runs to, from FC 26 launch data — *not* your save.
    contract_until: Optional[int] = None
    contract_source: str = ""
    source: str = "squad"

    @property
    def position_group(self) -> str:
        pos = (self.position or "").upper()
        for group, members in POSITION_GROUPS.items():
            if pos in members:
                return group
        return ""

    @property
    def display(self) -> str:
        ovr = self.overall if self.overall is not None else "—"
        pos = self.position or "—"
        return f"{self.name} · {pos} · OVR {ovr} · id {self.playerid}"


@dataclass(frozen=True)
class FreeAgent:
    """A free-agent slot that a signing can overwrite (team 111592)."""

    playerid: int
    overall: int = 50
    teamid: int = 0
    source: str = "free_agent"


@dataclass(frozen=True)
class TeamInfo:
    """The Club header. ``loaded`` False means "we have never read a squad"."""

    teamid: int = 0
    name: str = ""
    mode: str = ""
    player_count: int = 0
    free_agent_count: int = 0
    loaded: bool = False
    reason: str = ""
    path: str = ""
    avg_overall: Optional[float] = None
    avg_age: Optional[float] = None

    @property
    def headline(self) -> str:
        if not self.loaded:
            return "No squad yet"
        name = self.name or f"team {self.teamid}"
        bits = [name, f"{self.mode or 'career'}", f"{self.player_count} players"]
        if self.avg_overall is not None:
            bits.append(f"avg OVR {self.avg_overall:.0f}")
        if self.avg_age is not None:
            bits.append(f"avg age {self.avg_age:.1f}")
        return " · ".join(bits)


@dataclass(frozen=True)
class SquadView:
    """Everything the Club surface needs in one instant, offline read."""

    team: TeamInfo
    rows: Tuple[SquadPlayer, ...] = ()
    free_agents: Tuple[FreeAgent, ...] = ()
    loaded: bool = False
    reason: str = ""
    #: The control the empty/error state should offer. Never prose alone (H5).
    next_action: str = ""
    caveats: Tuple[str, ...] = ()
    path: str = ""

    def by_id(self, playerid: Any) -> Optional[SquadPlayer]:
        try:
            pid = int(playerid)
        except (TypeError, ValueError):
            return None
        for row in self.rows:
            if row.playerid == pid:
                return row
        return None


@dataclass(frozen=True)
class PlayerPage:
    """A filtered, sorted slice of the squad, plus why it may be empty."""

    rows: Tuple[SquadPlayer, ...] = ()
    total: int = 0
    matched: int = 0
    reason: str = ""
    next_action: str = ""
    caveats: Tuple[str, ...] = ()
    sort: str = "name"
    descending: bool = False

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)


@dataclass(frozen=True)
class PlayerDetail:
    """One player as the Club row-click / Player header knows them."""

    playerid: int
    found: bool = False
    reason: str = ""
    row: Optional[SquadPlayer] = None
    #: FC 26 launch reference values (identity + appearance). Not your save.
    base: Mapping[str, Any] = field(default_factory=dict)
    face_real: bool = False
    face_reason: str = ""
    nationality_id: Optional[int] = None
    in_squad: bool = False
    sources: Tuple[str, ...] = ()


# ── helpers ──────────────────────────────────────────────────────────


def _int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def age_from_birthdate(birthdate_days: Any, *, at_days: int = FC26_SEASON_START_DAYS) -> Optional[int]:
    """FIFA ``players.birthdate`` (gregorian days) -> whole years at ``at_days``."""
    days = _int(birthdate_days)
    if days is None or days <= 0:
        return None
    years = (int(at_days) - days) / _DAYS_PER_YEAR
    if years < 10 or years > 60:
        return None
    return int(years)


def _base_row(playerid: int) -> Mapping[str, str]:
    try:
        return base_players.get_row(playerid) or {}
    except Exception:  # noqa: BLE001 - a missing CSV is a degrade, not a crash
        return {}


def _enrich(raw: Mapping[str, Any], *, with_base: bool) -> SquadPlayer:
    pid = _int(raw.get("playerid")) or 0
    age: Optional[int] = None
    age_source = ""
    contract: Optional[int] = None
    contract_source = ""
    if with_base and pid > 0:
        row = _base_row(pid)
        if row:
            age = age_from_birthdate(row.get("birthdate"))
            if age is not None:
                age_source = "base_players"
            contract = _int(row.get("contractvaliduntil"))
            if contract is not None:
                contract_source = "base_players"
    return SquadPlayer(
        playerid=pid,
        name=str(raw.get("name") or "").strip(),
        position=str(raw.get("position") or "").strip(),
        overall=_int(raw.get("overallrating")),
        potential=_int(raw.get("potential")),
        jersey=_int(raw.get("jerseynumber")),
        age=age,
        age_source=age_source,
        contract_until=contract,
        contract_source=contract_source,
        source=str(raw.get("source") or "squad"),
    )


_NO_SQUAD_REASON = (
    "No squad read yet. Read your squad from the game to see your players "
    "here — nothing has been tried and nothing failed."
)

_AGE_CAVEAT = (
    "Age is computed from FC 26 launch birthdates at "
    + FC26_SEASON_START_LABEL
    + " — the squad export does not carry it."
)
_CONTRACT_CAVEAT = (
    "Contract year comes from FC 26 launch data, not from your save. Your "
    "career has almost certainly changed it."
)


# ── reads ────────────────────────────────────────────────────────────


def load_squad(
    path: Optional[Union[str, Path]] = None,
    *,
    with_base: bool = True,
    progress: Progress = None,
) -> SquadView:
    """Read ``current_squad.json``. Instant, offline, never raises.

    Args:
        path: Override the squad export location (tests, alternate saves).
        with_base: Resolve age/contract from ``base_players.csv``. The first
            call builds a 22,348-row index (~1s); pass False, or pass
            ``progress``, when that matters.
        progress: ``progress(fraction, message)``.

    Returns:
        A :class:`SquadView`. When there is no export, ``loaded`` is False,
        ``reason`` explains it in one sentence and ``next_action`` is
        ``"export_squad"``.
    """
    tick(progress, 0.05, "Reading squad export…")
    raw = target_players.load_squad(path)
    squad_path = str(raw.get("path") or "")

    if not raw.get("loaded"):
        tick(progress, 1.0, "No squad export")
        return SquadView(
            team=TeamInfo(loaded=False, reason=_NO_SQUAD_REASON, path=squad_path),
            loaded=False,
            reason=_NO_SQUAD_REASON,
            next_action="export_squad",
            path=squad_path,
        )

    tick(progress, 0.4, "Resolving players…")
    rows = tuple(
        _enrich(p, with_base=with_base) for p in (raw.get("players") or [])
    )
    fa = tuple(
        FreeAgent(
            playerid=_int(f.get("playerid")) or 0,
            overall=_int(f.get("overallrating")) or 50,
            teamid=_int(f.get("teamid")) or 0,
            source=str(f.get("source") or "free_agent"),
        )
        for f in (raw.get("free_agents") or [])
    )

    ovrs = [r.overall for r in rows if r.overall is not None]
    ages = [r.age for r in rows if r.age is not None]
    caveats: List[str] = []
    if with_base and ages:
        caveats.append(_AGE_CAVEAT)
    if with_base and any(r.contract_until is not None for r in rows):
        caveats.append(_CONTRACT_CAVEAT)
    if not fa:
        caveats.append(
            "No free-agent pool in this export — signing into your club needs "
            "one. Re-export while live in Career Mode."
        )

    team = TeamInfo(
        teamid=_int(raw.get("teamid")) or 0,
        name=str(raw.get("teamname") or ""),
        mode=str(raw.get("mode") or "career"),
        player_count=len(rows),
        free_agent_count=len(fa),
        loaded=True,
        path=squad_path,
        avg_overall=(sum(ovrs) / len(ovrs)) if ovrs else None,
        avg_age=(sum(ages) / len(ages)) if ages else None,
    )
    tick(progress, 1.0, f"{len(rows)} players")
    return SquadView(
        team=team,
        rows=rows,
        free_agents=fa,
        loaded=True,
        reason="",
        next_action="",
        caveats=tuple(caveats),
        path=squad_path,
    )


def team_info(
    *,
    squad: Optional[SquadView] = None,
    path: Optional[Union[str, Path]] = None,
) -> TeamInfo:
    """The Club header. Cheap enough to call on every paint."""
    view = squad if squad is not None else load_squad(path)
    return view.team


def free_agent_pool(
    *,
    squad: Optional[SquadView] = None,
    path: Optional[Union[str, Path]] = None,
) -> Tuple[FreeAgent, ...]:
    """Free-agent slots a signing can overwrite. Empty is a real answer."""
    view = squad if squad is not None else load_squad(path, with_base=False)
    return view.free_agents


def _matches_position(row: SquadPlayer, wanted: str) -> bool:
    want = (wanted or "").strip().upper()
    if not want:
        return True
    if want in POSITION_GROUPS:
        return row.position_group == want
    return (row.position or "").upper() == want


def _sort_value(row: SquadPlayer, key: str) -> Tuple[int, Any]:
    """(is_unknown, value) so unknowns always sort last in either direction."""
    if key == "name":
        return (0, (row.name or "").casefold())
    if key == "position":
        return (0, (row.position or "~").upper())
    if key == "playerid":
        return (0, row.playerid)
    value = {
        "overall": row.overall,
        "potential": row.potential,
        "age": row.age,
        "contract": row.contract_until,
        "jersey": row.jersey,
    }.get(key)
    if value is None:
        return (1, 0)
    return (0, value)


def players(
    *,
    squad: Optional[SquadView] = None,
    path: Optional[Union[str, Path]] = None,
    view: str = "squad",
    name: str = "",
    position: str = "",
    min_ovr: Optional[int] = None,
    max_ovr: Optional[int] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    contract_before: Optional[int] = None,
    contract_after: Optional[int] = None,
    sort: str = "name",
    descending: bool = False,
    limit: Optional[int] = None,
) -> PlayerPage:
    """Filter and sort the squad grid.

    Every filter is applied against values this module can actually justify;
    a filter on ``age`` or ``contract`` over players whose base row is unknown
    excludes them and says so in :attr:`PlayerPage.caveats` rather than
    silently pretending they did not match.

    Args:
        name: Substring over name and player id (the §3.3 filter box).
        position: ``"CAM"`` for exact, or a group: ``GK`` / ``DEF`` / ``MID`` / ``ATT``.
        min_ovr, max_ovr: Inclusive overall band.
        min_age, max_age: Inclusive age band at :data:`FC26_SEASON_START_LABEL`.
        contract_before, contract_after: Inclusive contract-year bounds.
        sort: One of :data:`SORT_KEYS`.
        descending: Column-header second click.
        limit: Cap the returned rows (``total``/``matched`` still count all).

    Returns:
        A :class:`PlayerPage`. An empty page always carries a ``reason``.
    """
    sort_key = (sort or "name").strip().lower()
    if sort_key not in SORT_KEYS:
        sort_key = "name"
    wanted_view = (view or "squad").strip().lower()
    if wanted_view not in SQUAD_VIEWS:
        wanted_view = "squad"

    v = squad if squad is not None else load_squad(path)
    if not v.loaded:
        return PlayerPage(
            reason=v.reason,
            next_action=v.next_action,
            sort=sort_key,
            descending=descending,
        )

    if wanted_view == "free_agents":
        source_rows: Tuple[SquadPlayer, ...] = tuple(
            SquadPlayer(
                playerid=f.playerid,
                name=f"Free agent {f.playerid}",
                overall=f.overall,
                source="free_agent",
            )
            for f in v.free_agents
        )
    else:
        source_rows = v.rows

    total = len(source_rows)
    needle = (name or "").strip().casefold()
    caveats: List[str] = list(v.caveats)
    age_filtered = min_age is not None or max_age is not None
    contract_filtered = contract_before is not None or contract_after is not None
    dropped_unknown_age = 0
    dropped_unknown_contract = 0

    kept: List[SquadPlayer] = []
    for row in source_rows:
        if needle:
            haystack = f"{row.name} {row.position} {row.playerid}".casefold()
            if needle not in haystack:
                continue
        if not _matches_position(row, position):
            continue
        if min_ovr is not None and (row.overall is None or row.overall < min_ovr):
            continue
        if max_ovr is not None and (row.overall is None or row.overall > max_ovr):
            continue
        if age_filtered and row.age is None:
            dropped_unknown_age += 1
            continue
        if min_age is not None and row.age is not None and row.age < min_age:
            continue
        if max_age is not None and row.age is not None and row.age > max_age:
            continue
        if contract_filtered and row.contract_until is None:
            dropped_unknown_contract += 1
            continue
        if (
            contract_before is not None
            and row.contract_until is not None
            and row.contract_until > contract_before
        ):
            continue
        if (
            contract_after is not None
            and row.contract_until is not None
            and row.contract_until < contract_after
        ):
            continue
        kept.append(row)

    if dropped_unknown_age:
        caveats.append(
            f"{dropped_unknown_age} players hidden: their age is unknown, so "
            "an age filter cannot honestly include or exclude them."
        )
    if dropped_unknown_contract:
        caveats.append(
            f"{dropped_unknown_contract} players hidden: their contract year "
            "is unknown."
        )

    kept.sort(key=lambda r: _sort_value(r, sort_key), reverse=bool(descending))
    matched = len(kept)
    rows = tuple(kept[: int(limit)] if limit else kept)

    reason = ""
    next_action = ""
    if not rows:
        described = []
        if needle:
            described.append(f'"{name.strip()}"')
        if position:
            described.append(position.upper())
        if min_ovr is not None or max_ovr is not None:
            described.append(f"OVR {min_ovr or 1}–{max_ovr or 99}")
        if age_filtered:
            described.append(f"age {min_age or 0}–{max_age or 99}")
        if contract_filtered:
            described.append("contract window")
        if described and total:
            reason = "No players match " + ", ".join(described) + "."
            next_action = "clear_filters"
        elif not total:
            reason = "This view has no players in it."
            next_action = "export_squad"

    return PlayerPage(
        rows=rows,
        total=total,
        matched=matched,
        reason=reason,
        next_action=next_action,
        caveats=tuple(dict.fromkeys(caveats)),
        sort=sort_key,
        descending=bool(descending),
    )


def player_detail(
    playerid: Any,
    *,
    squad: Optional[SquadView] = None,
    path: Optional[Union[str, Path]] = None,
    progress: Progress = None,
) -> PlayerDetail:
    """Everything known about one player, with each source named.

    ``base`` holds FC 26 launch reference data (identity + appearance). It is
    deliberately *not* merged into :attr:`PlayerDetail.row`: the squad row is
    read from the save, the base row is not, and the Player surface renders
    them differently.
    """
    pid = _int(playerid)
    if pid is None or pid <= 0:
        return PlayerDetail(playerid=0, found=False, reason="Not a player id.")

    tick(progress, 0.1, "Looking up squad row…")
    view = squad if squad is not None else load_squad(path)
    row = view.by_id(pid)
    sources: List[str] = []
    if row is not None:
        sources.append("squad_export")

    tick(progress, 0.5, "Resolving FC 26 base data…")
    base = _base_row(pid)
    face: Mapping[str, Any] = {}
    if base:
        sources.append("base_players")
        try:
            face = base_players.face_profile(pid) or {}
        except Exception:  # noqa: BLE001
            face = {}

    if row is None and not base:
        return PlayerDetail(
            playerid=pid,
            found=False,
            reason=(
                f"Player {pid} is not in your squad export and not in FC 26's "
                "base data. Re-export your squad, or search the Library."
            ),
            in_squad=False,
        )

    if row is None:
        row = SquadPlayer(
            playerid=pid,
            name=(str(base.get("commonname") or base.get("surname") or "").strip()),
            overall=_int(base.get("overallrating")),
            potential=_int(base.get("potential")),
            age=age_from_birthdate(base.get("birthdate")),
            age_source="base_players" if base.get("birthdate") else "",
            contract_until=_int(base.get("contractvaliduntil")),
            contract_source="base_players" if base.get("contractvaliduntil") else "",
            source="base_players",
        )

    tick(progress, 1.0, "Ready")
    return PlayerDetail(
        playerid=pid,
        found=True,
        row=row,
        base=dict(base),
        face_real=bool(face.get("real")),
        face_reason=str(face.get("reason") or ""),
        nationality_id=_int(base.get("nationality")),
        in_squad=view.by_id(pid) is not None,
        sources=tuple(sources),
    )


# ── the one mutation ─────────────────────────────────────────────────


#: The profile that refreshes ``current_squad.json``. Named once, here.
EXPORT_PROFILE_ID = "export_user_squad"


def export_squad(*, wait: bool = False, progress: Progress = None) -> MutationResult:
    """Queue the squad export (``Refresh squad`` in §3.3).

    Defaults to ``wait=False`` so the UI thread is never held. The result is
    ``QUEUED`` until the worker reports back — which is the truth, and the
    reason v1's 25-second blind poll is gone.
    """
    tick(progress, 0.1, "Queueing squad export…")
    try:
        result = product.run_profile_turbo(EXPORT_PROFILE_ID, wait=bool(wait))
    except Exception as exc:  # noqa: BLE001 - profiles.json can be missing
        return MutationResult(
            outcome=Outcome.ERROR,
            reason=f"Could not build the squad export job: {exc}",
            meta={"profile_id": EXPORT_PROFILE_ID},
        )
    tick(progress, 1.0, "Queued")
    return from_apply_result(
        result,
        fallback_reason="Squad export queued.",
        undo_available=False,
        profile_id=EXPORT_PROFILE_ID,
        kind="export",
    )


def refresh(path: Optional[Union[str, Path]] = None) -> SquadView:
    """Re-read the export from disk after a queued job has landed."""
    return load_squad(path)


def with_rows(view: SquadView, rows: Sequence[SquadPlayer]) -> SquadView:
    """Test/UI helper: same view, different rows (no mutation anywhere)."""
    return replace(view, rows=tuple(rows))
