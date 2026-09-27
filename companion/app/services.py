"""Services — the frozen bundle of ports, constructed once in the composition root.

This is the ONLY thing passed around, and unlike v1's ``app: Any`` (181
occurrences across 12 modules) it is fully typed, so ``svc.transport.submot(...)``
is a type error rather than a 3 a.m. ``AttributeError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.clock import Clock
from ..core.paths import AppPaths
from .commands.liveness_hold import LivenessHold
from .commands.squad_sync_memory import SquadSyncMemory
from .store import Store
from .ui_actions import UiActions


@dataclass(frozen=True, slots=True)
class Services:
    paths: AppPaths
    clock: Clock
    executor: Any       # core.ports.Executor
    transport: Any      # core.ports.BridgeTransport
    store: Store
    log: Any = None     # logging.Logger
    db: Any = None      # core.db connection/handle, when present
    catalog: Any = None  # domain.catalog universe handle, when present
    ui: UiActions = field(default_factory=UiActions)
    # Poll memory. Mutable objects inside the frozen bundle; not AppState.
    liveness_hold: LivenessHold = field(default_factory=LivenessHold)
    squad_sync: SquadSyncMemory = field(default_factory=SquadSyncMemory)
