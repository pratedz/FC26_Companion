"""Retry memory for automatic squad reads. Not a one-shot session flag."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SquadSyncMemory:
    """How the coordinator decides the next automatic export_squad.

    ``counted_jobs`` records job ids whose terminal result already moved the
    failure counter, so a follow thread and the poll collector cannot
    double-count one result.
    """

    failures: int = 0
    retry_after_ts: float | None = None
    last_error: str = ""
    counted_jobs: set[str] = field(default_factory=set)
