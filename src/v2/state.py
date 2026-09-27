"""Typed application state for v2.

This replaces v1's `PremiumApp`, which grew into a 260-attribute untyped bag
passed around as `app: Any` at 181 call sites. Because nothing was typed, no
static checker could see a typo — which is exactly how a misspelled attribute
shipped as a permanently broken tab that failed silently for weeks.

Everything here is a plain dataclass with no tkinter import, so the whole state
model is testable without a display.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class Surface(str, Enum):
    """The five v2 surfaces, organised by career-manager job."""

    CLUB = "club"
    PLAYER = "player"
    TRANSFERS = "transfers"
    AUTOMATIONS = "automations"
    LIBRARY = "library"


class Provenance(str, Enum):
    """Where a displayed field value came from.

    UNKNOWN is the important one: it means the app never read this field, so it
    renders as an em dash and is structurally excluded from the write set. v1
    had no such concept and wrote whatever happened to be in the form.
    """

    READ = "read"
    EDITED = "edited"
    UNKNOWN = "unknown"


@dataclass
class FieldValue:
    """One editable field plus where its value came from."""

    name: str
    value: Optional[Any] = None
    provenance: Provenance = Provenance.UNKNOWN
    original: Optional[Any] = None

    @property
    def is_writable(self) -> bool:
        return self.provenance is Provenance.EDITED

    @property
    def changed(self) -> bool:
        return self.provenance is Provenance.EDITED and self.value != self.original

    def edit(self, new_value: Any) -> None:
        if self.provenance is Provenance.READ and self.original is None:
            self.original = self.value
        self.value = new_value
        self.provenance = Provenance.EDITED

    def display(self, unknown_glyph: str = "—") -> str:
        if self.provenance is Provenance.UNKNOWN or self.value is None:
            return unknown_glyph
        return str(self.value)


@dataclass
class Change:
    """A single staged change, as the user would describe it."""

    field: str
    before: Any
    after: Any
    label: str = ""

    def line(self) -> str:
        if self.label:
            return self.label
        return f"{self.field}: {self.before} → {self.after}"


@dataclass
class ChangeSet:
    """Everything staged for one target, applied by the single Apply control.

    v1 had six different controls on one screen that all wrote to the save, with
    three different meanings of "apply". Funnelling every mutation through one
    change set is what makes a mandatory diff preview possible.
    """

    target_id: Optional[int] = None
    target_label: str = ""
    changes: List[Change] = field(default_factory=list)
    copy_name: bool = False
    source_note: str = ""

    def __len__(self) -> int:
        return len(self.changes)

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def add(self, change: Change) -> None:
        for i, existing in enumerate(self.changes):
            if existing.field == change.field:
                # Keep the original "before" so the diff stays truthful across
                # repeated edits of the same field.
                self.changes[i] = Change(
                    change.field, existing.before, change.after, change.label
                )
                return
        self.changes.append(change)

    def clear(self) -> None:
        self.changes.clear()
        self.copy_name = False
        self.source_note = ""

    def summary(self) -> str:
        if not self.changes:
            return "No changes staged"
        n = len(self.changes)
        return f"{n} change{'s' if n != 1 else ''} on {self.target_label or self.target_id}"

    def diff_lines(self) -> List[str]:
        return [c.line() for c in self.changes]


@dataclass
class ConnectionState:
    """Liveness, with the three facts v1 collapsed into one kept separate."""

    armed: bool = False
    game_running: bool = False
    career_loaded: bool = False
    last_drain_age: Optional[float] = None
    queued: int = 0
    worker_installed: bool = False
    detail: str = ""

    @property
    def state(self) -> str:
        if self.detail.startswith("error"):
            return "error"
        if not self.worker_installed:
            return "setup"
        if self.armed and self.career_loaded:
            return "live"
        if self.armed:
            return "armed"
        return "waiting"

    def human(self) -> str:
        """An honest one-liner that always names the next action."""
        s = self.state
        if s == "setup":
            return "Worker not installed — click Connect"
        if s == "waiting":
            return "Waiting for FC 26 — start the game with the LE Launcher"
        if s == "armed":
            return (
                "Armed — queued work runs the next time a Career Mode event fires "
                "(advance a day, or open your squad screen)"
            )
        if s == "error":
            return self.detail or "Something went wrong — open Activity (Ctrl+J)"
        age = self.last_drain_age
        if age is None:
            return "Live"
        return f"Live · last activity {int(age)}s ago"


@dataclass
class Toast:
    message: str
    tone: str = "info"
    ts: float = field(default_factory=time.time)


@dataclass
class ActivityEntry:
    """One row in the Activity drawer — the replacement for five modals."""

    ts: float
    kind: str
    detail: str
    outcome: str = ""
    job_id: str = ""
    undoable: bool = False


@dataclass
class AppState:
    """The whole application state. No widgets, no Tcl, no globals."""

    surface: Surface = Surface.CLUB
    connection: ConnectionState = field(default_factory=ConnectionState)
    change_set: ChangeSet = field(default_factory=ChangeSet)
    selected_player: Optional[int] = None
    squad_team_id: Optional[int] = None
    squad_team_name: str = ""
    activity: List[ActivityEntry] = field(default_factory=list)
    toasts: List[Toast] = field(default_factory=list)
    busy: Dict[str, bool] = field(default_factory=dict)
    drawer_open: bool = False

    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _listeners: List[Callable[[str], None]] = field(default_factory=list, repr=False)

    # ── observation ──────────────────────────────────────────────────
    def subscribe(self, fn: Callable[[str], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return unsubscribe

    def notify(self, topic: str = "*") -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(topic)
            except Exception:  # noqa: BLE001
                # A broken listener must never take down a state update; the
                # shell logs these centrally.
                continue

    # ── mutations ────────────────────────────────────────────────────
    def go(self, surface: Surface) -> None:
        if self.surface != surface:
            self.surface = surface
            self.notify("surface")

    def set_busy(self, key: str, value: bool) -> None:
        with self._lock:
            if value:
                self.busy[key] = True
            else:
                self.busy.pop(key, None)
        self.notify("busy")

    def is_busy(self, key: Optional[str] = None) -> bool:
        with self._lock:
            return bool(self.busy) if key is None else bool(self.busy.get(key))

    def toast(self, message: str, tone: str = "info") -> None:
        with self._lock:
            self.toasts.append(Toast(message, tone))
            del self.toasts[:-5]
        self.notify("toast")

    def log_activity(self, entry: ActivityEntry) -> None:
        with self._lock:
            self.activity.insert(0, entry)
            del self.activity[200:]
        self.notify("activity")

    def stage(self, change: Change) -> None:
        self.change_set.add(change)
        self.notify("change_set")

    def clear_changes(self) -> None:
        self.change_set.clear()
        self.notify("change_set")

    def select_player(self, playerid: Optional[int], label: str = "") -> None:
        self.selected_player = playerid
        if playerid is not None and self.change_set.target_id != playerid:
            # Staged edits belong to the player they were made against.
            self.change_set = ChangeSet(target_id=playerid, target_label=label)
        self.notify("player")

    def update_connection(self, **kwargs: Any) -> None:
        changed = False
        for key, value in kwargs.items():
            if hasattr(self.connection, key) and getattr(self.connection, key) != value:
                setattr(self.connection, key, value)
                changed = True
        if changed:
            self.notify("connection")
