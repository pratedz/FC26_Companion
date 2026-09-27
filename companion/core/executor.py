"""ThreadExecutor / InlineExecutor — the Executor port, real and fake.

v1 spawned raw ``threading.Thread(daemon=True)`` and hand-rolled cancellation
with seven generation counters on the god object. Here, same-key submit
supersedes the in-flight work; the token tells the old callable it lost.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class _Token:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


class ThreadExecutor:
    """Real executor: a small pool + a UI-thread pump the shell drains."""

    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="companion")
        self._tokens: dict[str, _Token] = {}
        self._lock = threading.Lock()
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

    def submit(self, key: str, fn: Callable[[Any], T]) -> Future:
        token = _Token()
        with self._lock:
            old = self._tokens.get(key)
            if old is not None:
                old.cancel()
            self._tokens[key] = token
        return self._pool.submit(fn, token)

    def cancel(self, key: str) -> None:
        with self._lock:
            token = self._tokens.pop(key, None)
        if token is not None:
            token.cancel()

    def on_ui_thread(self, fn: Callable[[], None]) -> None:
        self._ui_queue.put(fn)

    def drain_ui(self, budget: int = 100) -> int:
        """Called by the Tk shell on its own thread (via ``after``)."""
        n = 0
        while n < budget:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                from .log import get_logger

                get_logger("ui.executor").exception("ui callback failed")
            n += 1
        return n

    def shutdown(self) -> None:
        with self._lock:
            for t in self._tokens.values():
                t.cancel()
            self._tokens.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)


class _DoneFuture:
    __slots__ = ("_value", "_exc")

    def __init__(self, value: Any = None, exc: BaseException | None = None) -> None:
        self._value = value
        self._exc = exc

    def done(self) -> bool:
        return True

    def result(self, timeout: float | None = None) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._value


class InlineExecutor:
    """Test executor: runs synchronously, UI callbacks run immediately."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit(self, key: str, fn: Callable[[Any], T]) -> _DoneFuture:
        self.calls.append(key)
        token = _Token()
        try:
            return _DoneFuture(value=fn(token))
        except BaseException as e:  # noqa: BLE001 — surfaced via .result()
            return _DoneFuture(exc=e)

    def cancel(self, key: str) -> None:
        pass

    def on_ui_thread(self, fn: Callable[[], None]) -> None:
        fn()

    def shutdown(self) -> None:
        pass
