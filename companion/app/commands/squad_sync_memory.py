"""Retry memory for automatic squad reads. Not a one-shot session flag."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MirrorMemory:
    """What the poller knows about ``queue/mirror/squad.json``.

    ``file_sig`` is the (mtime_ns, size) seen last, so an unchanged file costs
    one ``stat`` per poll. ``session_id``/``seq``/``hash`` identify the last
    mirror that was examined (ingested or rejected), so it is parsed once.
    ``updated_ts`` is when the game generated the last *ingested* mirror;
    the Club "Live" indicator reads it. ``pending_writes`` are write jobs whose
    effect the squad view is still waiting to see in a mirror.
    """

    file_sig: tuple[int, int] | None = None
    session_id: str = ""
    save_uid: str = ""
    seq: int = 0
    hash: str = ""
    ingested: bool = False
    updated_ts: float | None = None
    build_seconds: float = 0.0
    last_skip: str = ""
    pending_writes: set[str] = field(default_factory=set)
    waiting_since_ts: float | None = None


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
    mirror: MirrorMemory = field(default_factory=MirrorMemory)
