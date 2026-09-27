"""Store — dispatch(Event) -> None, subscribe(fn), snapshot() -> AppState.

Thread-safe: commands dispatch from worker threads, the UI subscribes on the Tk
thread. Notification happens outside the lock so a subscriber can dispatch
without deadlocking, and subscribers are snapshotted so one unsubscribing
during notification cannot corrupt the iteration.
"""

from __future__ import annotations

import threading
from typing import Callable

from .events import Event
from .reducers import reduce
from .state import AppState

Subscriber = Callable[[AppState], None]


class Store:
    def __init__(self, initial: AppState | None = None) -> None:
        self._state = initial or AppState()
        self._lock = threading.RLock()
        self._subs: list[Subscriber] = []
        self._on_error: Callable[[BaseException], None] | None = None

    def snapshot(self) -> AppState:
        with self._lock:
            return self._state

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def set_error_handler(self, fn: Callable[[BaseException], None]) -> None:
        self._on_error = fn

    def dispatch(self, event: Event) -> AppState:
        with self._lock:
            before = self._state
            after = reduce(before, event)
            self._state = after
            subs = list(self._subs)
        if after is not before:
            for fn in subs:
                try:
                    fn(after)
                except BaseException as e:  # noqa: BLE001 — a bad view must not
                    if self._on_error is not None:  # kill the dispatch loop
                        self._on_error(e)
        return after

    def dispatch_all(self, *events: Event) -> AppState:
        state = self.snapshot()
        for e in events:
            state = self.dispatch(e)
        return state
