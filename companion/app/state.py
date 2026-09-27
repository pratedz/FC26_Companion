"""AppState — the frozen dataclass tree that replaces the god object.

v1's ``PremiumApp(ctk.CTk)`` carried **182 instance attributes** (36 set in
``__init__``, 151 injected from outside by tab modules), 262 distinct ``app.*``
names referenced across ``src/``, and 112 methods of which ~100 were one-line
forwarders. Fifteen attribute names were read but never assigned anywhere —
latent ``AttributeError``s with no declaration of the interface anywhere.

It is replaced by four things; this module is the first: all *domain* state,
frozen, so a stale reference cannot silently mutate under a background thread.
(The other three: ``Store`` for mutation, per-view ``ViewModel`` for
widget-local state, and ``Services`` for the injected ports.)

Notably, v1's five parallel result lists — ``_hits``, ``_all_hits``,
``_target_hits``, ``_add_team_hits``, ``_add_team_raw_hits``, each with its own
invalidation bug — collapse into ``search.results`` plus per-surface selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from ..core.transport.v3 import Liveness, Pill
from ..domain.outcome import ApplyOutcome, JobResult

# A catalog row. Kept structural rather than a hard import so the state tree
# does not depend on the catalog implementation.
Card = Mapping[str, Any]

# Phase 2/3 operations are the stable resident-core path.  Phase 4's
# add_to_team remains deliberately absent: it is experimental and must be
# opted into explicitly until the measured live gates pass.
DEFAULT_CORE_OPS = frozenset(
    {"export_squad", "snapshot", "budget", "set_fields", "transfer"}
)


@dataclass(frozen=True, slots=True)
class BridgeState:
    """What the transport says about the worker. Never a single bool."""

    liveness: Liveness = field(
        default_factory=lambda: Liveness(
            pill=Pill.OFF, message="Start LE — the companion arms itself.", armed=False
        )
    )
    force_drain_snippet: str = ""

    @property
    def pill(self) -> Pill:
        return self.liveness.pill

    @property
    def armed(self) -> bool:
        return self.liveness.armed


@dataclass(frozen=True, slots=True)
class TargetState:
    """The player every surface acts on. One lock, app-wide."""

    playerid: int | None = None
    name: str = ""
    source: str = ""  # "search" | "squad" | "manual" | "import"
    teamid: int | None = None

    @property
    def locked(self) -> bool:
        return self.playerid is not None


@dataclass(frozen=True, slots=True)
class SearchState:
    query: str = ""
    year: str = ""
    filters: Mapping[str, Any] = field(default_factory=dict)
    results: tuple[Card, ...] = ()
    selected: int = -1
    busy: bool = False
    generation: int = 0
    error: str = ""

    @property
    def selection(self) -> Card | None:
        if 0 <= self.selected < len(self.results):
            return self.results[self.selected]
        return None


@dataclass(frozen=True, slots=True)
class EditorState:
    """The working edit. ``dirty`` is what will actually be written."""

    base: Mapping[str, Any] = field(default_factory=dict)
    dirty: Mapping[str, Any] = field(default_factory=dict)
    categories: frozenset[str] = frozenset()
    copy_name: bool = False
    source: str = ""
    source_label: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(self.dirty)

    def merged(self) -> dict[str, Any]:
        return {**dict(self.base), **dict(self.dirty)}


@dataclass(frozen=True, slots=True)
class SquadState:
    """Live save state — a cache with a save-scoped key and a short TTL.

    v1's ``current_squad.json`` had no save key at all, so it would happily show
    save A's squad while you edited save B.
    """

    save_uid: str = ""
    session_id: str = ""
    teamid: int | None = None
    players: tuple[Mapping[str, Any], ...] = ()
    free_agents: tuple[Mapping[str, Any], ...] = ()
    taken: datetime | None = None
    stale: bool = True

    def age_seconds(self, now: datetime) -> float | None:
        if self.taken is None:
            return None
        return max(0.0, (now - self.taken).total_seconds())


@dataclass(frozen=True, slots=True)
class JobView:
    """One in-flight or finished job, as the UI sees it."""

    job_id: str
    label: str
    outcome: ApplyOutcome
    submitted: datetime | None = None
    result: JobResult | None = None

    @property
    def done(self) -> bool:
        return self.outcome.is_terminal


@dataclass(frozen=True, slots=True)
class JobsState:
    active: Mapping[str, JobView] = field(default_factory=dict)
    history: tuple[JobView, ...] = ()

    @property
    def in_flight(self) -> int:
        return sum(1 for j in self.active.values() if not j.done)


@dataclass(frozen=True, slots=True)
class CatalogState:
    rows: int = 0
    years: tuple[str, ...] = ()
    fts: bool = False
    healthy: bool = False
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class Prefs:
    """Was ``companion_config.json``. ``timeout_live``/``poll_sec`` are gone —
    they are no longer meaningful once a result file is definitive."""

    growth_mirror: bool = True
    verify_writes: bool = True
    theme: str = "dark"
    confirm_destructive: bool = True
    http_push: bool = False
    core_ops: frozenset[str] = DEFAULT_CORE_OPS
    values: Mapping[str, Any] = field(default_factory=dict)

    def core_op_enabled(self, op: str) -> bool:
        return op in self.core_ops

    @property
    def experimental_add_to_team(self) -> bool:
        return self.core_op_enabled("add_to_team")


@dataclass(frozen=True, slots=True)
class AppState:
    bridge: BridgeState = field(default_factory=BridgeState)
    target: TargetState = field(default_factory=TargetState)
    search: SearchState = field(default_factory=SearchState)
    editor: EditorState = field(default_factory=EditorState)
    squad: SquadState = field(default_factory=SquadState)
    jobs: JobsState = field(default_factory=JobsState)
    catalog: CatalogState = field(default_factory=CatalogState)
    prefs: Prefs = field(default_factory=Prefs)
    status: str = ""
    status_tone: str = "info"
    status_id: int = 0

    def with_(self, **kw: Any) -> "AppState":
        return replace(self, **kw)
