"""Events — the only things a reducer consumes. Past tense, always facts.

An Intent is a request ("apply this"); an Event is something that already
happened ("the job was submitted"). Commands turn Intents into I/O and dispatch
Events back. Reducers are pure ``(AppState, Event) -> AppState``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..core.transport.v3 import Liveness
from ..domain.outcome import JobResult
from .state import Card


@dataclass(frozen=True, slots=True)
class LivenessChanged:
    liveness: Liveness


@dataclass(frozen=True, slots=True)
class StatusSet:
    text: str
    tone: str = "info"


@dataclass(frozen=True, slots=True)
class TargetLocked:
    playerid: int
    name: str = ""
    source: str = ""
    teamid: int | None = None


@dataclass(frozen=True, slots=True)
class TargetCleared:
    pass


@dataclass(frozen=True, slots=True)
class SearchStarted:
    query: str
    year: str = ""
    filters: Mapping[str, Any] = field(default_factory=dict)
    generation: int = 0


@dataclass(frozen=True, slots=True)
class SearchSucceeded:
    results: Sequence[Card]
    generation: int


@dataclass(frozen=True, slots=True)
class SearchFailed:
    error: str
    generation: int


@dataclass(frozen=True, slots=True)
class ResultSelected:
    index: int


@dataclass(frozen=True, slots=True)
class EditorLoaded:
    base: Mapping[str, Any]
    categories: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class FieldEdited:
    field_name: str
    value: Any


@dataclass(frozen=True, slots=True)
class DraftStaged:
    """One validated proposal replaces the current draft atomically."""

    fields: Mapping[str, Any]
    source: str
    source_label: str = ""
    warnings: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class EditorReset:
    pass


@dataclass(frozen=True, slots=True)
class JobSubmitted:
    job_id: str
    label: str
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class JobProgressed:
    job_id: str
    result: JobResult


@dataclass(frozen=True, slots=True)
class JobFinished:
    job_id: str
    result: JobResult


@dataclass(frozen=True, slots=True)
class JobHistoryCleared:
    """Clear only the visible session history; the durable audit stays intact."""


@dataclass(frozen=True, slots=True)
class JobQueueCleared:
    """Remove all waiting/in-progress jobs while retaining finished history."""


@dataclass(frozen=True, slots=True)
class SquadSynced:
    save_uid: str
    teamid: int | None
    players: Sequence[Mapping[str, Any]]
    taken: datetime
    session_id: str = ""
    free_agents: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True, slots=True)
class SquadInvalidated:
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CatalogProbed:
    rows: int
    years: Sequence[str]
    fts: bool
    healthy: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class PrefsLoaded:
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PrefChanged:
    key: str
    value: Any


Event = (
    LivenessChanged
    | StatusSet
    | TargetLocked
    | TargetCleared
    | SearchStarted
    | SearchSucceeded
    | SearchFailed
    | ResultSelected
    | EditorLoaded
    | FieldEdited
    | DraftStaged
    | EditorReset
    | JobSubmitted
    | JobProgressed
    | JobFinished
    | JobHistoryCleared
    | JobQueueCleared
    | SquadSynced
    | SquadInvalidated
    | CatalogProbed
    | PrefsLoaded
    | PrefChanged
)
