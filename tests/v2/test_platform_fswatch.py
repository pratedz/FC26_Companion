"""companion/platform/fswatch.py — directory watching, native and polling.

Three things are worth testing and hard to get right:

* the ``FILE_NOTIFY_INFORMATION`` parser (pure, so it is tested with bytes);
* that an *atomic write* (write tmp + ``os.replace``, which is how every job
  and result file lands) is reported once, not three times;
* that the watcher stops promptly and leaves no thread behind.

Timeouts are generous so the suite does not flake on a loaded machine; the
happy paths all complete in milliseconds.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

import pytest

from companion.platform import fswatch
from companion.platform.fswatch import ChangeKind, FileEvent

_TIMEOUT = 8.0


class Collector:
    """Callback + an event that fires whenever a batch arrives."""

    def __init__(self) -> None:
        self.batches: list[tuple[FileEvent, ...]] = []
        self.errors: list[BaseException] = []
        self.arrived = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, events: tuple[FileEvent, ...]) -> None:
        with self._lock:
            self.batches.append(events)
        self.arrived.set()

    def on_error(self, exc: BaseException) -> None:
        self.errors.append(exc)

    @property
    def events(self) -> list[FileEvent]:
        with self._lock:
            return [e for batch in self.batches for e in batch]

    def wait_for(self, name: str, timeout: float = _TIMEOUT) -> list[FileEvent]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hits = [e for e in self.events if e.path.name == name]
            if hits:
                return hits
            self.arrived.wait(0.1)
            self.arrived.clear()
        return []


# --- the parser (no filesystem at all) --------------------------------------


def _record(action: int, name: str, *, last: bool) -> bytes:
    raw = name.encode("utf-16-le")
    pad = (-(12 + len(raw))) % 4          # NextEntryOffset must stay DWORD-aligned
    body = struct.pack("<II", action, len(raw)) + raw + b"\0" * pad
    next_offset = 0 if last else 4 + len(body)
    return struct.pack("<I", next_offset) + body


def test_parse_single_notification(tmp_path: Path) -> None:
    buf = _record(1, "result.json", last=True)
    events = fswatch.parse_notifications(buf, tmp_path)
    assert events == [FileEvent(tmp_path / "result.json", ChangeKind.CREATED)]


def test_parse_chain_in_order(tmp_path: Path) -> None:
    buf = (
        _record(1, "a.json", last=False)
        + _record(3, "b.json", last=False)
        + _record(2, "c.json", last=False)
        + _record(4, "d.tmp", last=False)
        + _record(5, "d.json", last=True)
    )
    events = fswatch.parse_notifications(buf, tmp_path)
    assert [(e.name, e.kind) for e in events] == [
        ("a.json", ChangeKind.CREATED),
        ("b.json", ChangeKind.MODIFIED),
        ("c.json", ChangeKind.DELETED),
        ("d.tmp", ChangeKind.RENAMED_FROM),
        ("d.json", ChangeKind.RENAMED_TO),
    ]


def test_parse_handles_unicode_names(tmp_path: Path) -> None:
    events = fswatch.parse_notifications(_record(1, "Müller_ÅÄÖ.json", last=True), tmp_path)
    assert events[0].name == "Müller_ÅÄÖ.json"


def test_parse_rejects_malformed_buffers(tmp_path: Path) -> None:
    assert fswatch.parse_notifications(b"", tmp_path) == []
    assert fswatch.parse_notifications(b"\x01\x02\x03", tmp_path) == []
    # Name length runs past the end of the buffer: stop, do not read garbage.
    truncated = struct.pack("<III", 0, 1, 999) + "x".encode("utf-16-le")
    assert fswatch.parse_notifications(truncated, tmp_path) == []
    # A NextEntryOffset that cannot advance must not loop forever.
    looping = struct.pack("<III", 4, 1, 2) + "a".encode("utf-16-le")
    assert len(fswatch.parse_notifications(looping, tmp_path)) <= 1


def test_parse_ignores_unknown_actions(tmp_path: Path) -> None:
    assert fswatch.parse_notifications(_record(99, "x.json", last=True), tmp_path) == []


def test_appeared_covers_the_atomic_write_kinds() -> None:
    assert ChangeKind.CREATED.appeared
    assert ChangeKind.RENAMED_TO.appeared
    assert ChangeKind.MODIFIED.appeared
    assert not ChangeKind.DELETED.appeared
    assert not ChangeKind.RESCAN.appeared


# --- the polling backend (works on any filesystem) --------------------------


def test_polling_reports_create_modify_delete(tmp_path: Path) -> None:
    sink = Collector()
    with fswatch.watch_directory(
        tmp_path, sink, backend="polling", poll_interval=0.05, debounce=0
    ) as watcher:
        assert watcher.backend == "polling"
        target = tmp_path / "a.json"

        target.write_text("1", encoding="utf-8")
        assert _eventually(lambda: _has(sink, "a.json", ChangeKind.CREATED))

        target.write_text("22", encoding="utf-8")   # size changes => detected
        assert _eventually(lambda: _has(sink, "a.json", ChangeKind.MODIFIED))

        target.unlink()
        assert _eventually(lambda: _has(sink, "a.json", ChangeKind.DELETED))


def _has(sink: Collector, name: str, kind: ChangeKind) -> bool:
    return any(e.name == name and e.kind is kind for e in sink.events)


def _eventually(predicate, timeout: float = _TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_snapshot_and_diff_are_pure(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("x", encoding="utf-8")
    before = fswatch._snapshot(tmp_path, False)
    (tmp_path / "b.json").write_text("y", encoding="utf-8")
    (tmp_path / "a.json").unlink()
    after = fswatch._snapshot(tmp_path, False)

    kinds = {e.name: e.kind for e in fswatch._diff(before, after)}
    assert kinds == {"b.json": ChangeKind.CREATED, "a.json": ChangeKind.DELETED}


def test_snapshot_of_missing_directory_is_empty(tmp_path: Path) -> None:
    assert fswatch._snapshot(tmp_path / "nope", False) == {}


# --- the native backend -----------------------------------------------------

_native_only = pytest.mark.skipif(
    os.name != "nt", reason="ReadDirectoryChangesW is Windows-only"
)


@_native_only
def test_native_backend_is_chosen_on_windows(tmp_path: Path) -> None:
    sink = Collector()
    with fswatch.watch_directory(tmp_path, sink, debounce=0.05) as watcher:
        assert watcher.backend == "native"
        assert watcher.running
        (tmp_path / "hello.json").write_text("{}", encoding="utf-8")
        assert sink.wait_for("hello.json")


@_native_only
def test_atomic_write_is_one_callback(tmp_path: Path) -> None:
    """write tmp + os.replace is several kernel notifications for one new file.

    Debouncing must coalesce them, or every result file wakes the UI 3-4 times.
    """
    sink = Collector()
    with fswatch.watch_directory(tmp_path, sink, debounce=0.15):
        time.sleep(0.2)
        tmp = tmp_path / "r.json.tmp"
        tmp.write_text("{}", encoding="utf-8")
        os.replace(tmp, tmp_path / "r.json")
        assert sink.wait_for("r.json")
        time.sleep(0.4)
    assert len(sink.batches) == 1
    kinds = {e.kind for e in sink.events}
    assert ChangeKind.RENAMED_TO in kinds


@_native_only
def test_stop_is_prompt_and_leaves_no_thread(tmp_path: Path) -> None:
    before = threading.active_count()
    watcher = fswatch.watch_directory(tmp_path, Collector(), debounce=0.05)
    assert watcher.running
    started = time.monotonic()
    watcher.stop()
    assert time.monotonic() - started < 1.5      # signalled, not polled
    assert not watcher.running
    watcher.stop()                                # idempotent
    assert _eventually(lambda: threading.active_count() <= before)


@_native_only
def test_watch_for_new_files_fires_once_per_file(tmp_path: Path) -> None:
    seen: list[Path] = []
    got = threading.Event()

    def on_new(path: Path) -> None:
        seen.append(path)
        got.set()

    with fswatch.watch_for_new_files(tmp_path, on_new, suffix=".json", debounce=0.1):
        time.sleep(0.2)
        tmp = tmp_path / "01JQ.json.tmp"
        tmp.write_text("{}", encoding="utf-8")
        os.replace(tmp, tmp_path / "01JQ.json")
        (tmp_path / "ignored.txt").write_text("x", encoding="utf-8")
        assert got.wait(_TIMEOUT)
        time.sleep(0.4)

    assert [p.name for p in seen] == ["01JQ.json"]   # once, and the .txt filtered out


def test_watch_for_new_files_can_report_existing(tmp_path: Path) -> None:
    (tmp_path / "old.json").write_text("{}", encoding="utf-8")
    seen: list[Path] = []
    watcher = fswatch.watch_for_new_files(
        tmp_path, seen.append, suffix=".json", include_existing=True, backend="polling"
    )
    try:
        assert [p.name for p in seen] == ["old.json"]
    finally:
        watcher.stop()


# --- degradation and error handling -----------------------------------------


def test_auto_backend_falls_back_when_native_cannot_start(tmp_path: Path) -> None:
    """A directory that cannot be opened for notifications still gets watched."""
    missing = tmp_path / "not-there"
    watcher = fswatch.watch_directory(missing, Collector(), backend="auto", poll_interval=0.05)
    try:
        assert watcher.backend == "polling"
        assert watcher.running
        missing.mkdir()
        sink_path = missing / "late.json"
        sink_path.write_text("{}", encoding="utf-8")
    finally:
        watcher.stop()


@_native_only
def test_explicit_native_backend_raises_instead_of_degrading(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        fswatch.watch_directory(tmp_path / "nope", Collector(), backend="native")


@_native_only
def test_native_failure_mid_flight_degrades_to_polling(tmp_path: Path) -> None:
    """The kernel stops notifying (a share drops, the filesystem refuses).

    The watcher must keep working: report the error, announce RESCAN so the
    caller re-lists, and carry on by polling — not die quietly and leave the
    app waiting for results that never arrive.
    """
    sink = Collector()

    class Flaky(fswatch.NativeDirectoryWatcher):
        def _watch_loop(self) -> BaseException | None:
            return OSError(1, "notifications stopped")

    watcher = Flaky(tmp_path, sink, poll_interval=0.05, on_error=sink.on_error)
    watcher.start()
    try:
        assert _eventually(lambda: "degraded" in watcher.backend)
        assert _eventually(lambda: any(e.kind is ChangeKind.RESCAN for e in sink.events))
        (tmp_path / "after.json").write_text("{}", encoding="utf-8")
        assert _eventually(lambda: _has(sink, "after.json", ChangeKind.CREATED))
        assert sink.errors and isinstance(sink.errors[0], OSError)
    finally:
        watcher.stop()
    assert not watcher.running


def test_unknown_backend_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        fswatch.watch_directory(tmp_path, Collector(), backend="inotify")


def test_a_raising_callback_does_not_kill_the_watcher(tmp_path: Path) -> None:
    errors: list[BaseException] = []
    calls: list[int] = []

    def bad(events: tuple[FileEvent, ...]) -> None:
        calls.append(len(events))
        raise RuntimeError("subscriber exploded")

    with fswatch.watch_directory(
        tmp_path, bad, backend="polling", poll_interval=0.05, on_error=errors.append
    ) as watcher:
        (tmp_path / "a.json").write_text("1", encoding="utf-8")
        assert _eventually(lambda: len(calls) >= 1)
        (tmp_path / "b.json").write_text("2", encoding="utf-8")
        assert _eventually(lambda: len(calls) >= 2)    # still alive after raising
        assert watcher.running
    assert errors and isinstance(errors[0], RuntimeError)


def test_watcher_can_be_created_without_starting(tmp_path: Path) -> None:
    watcher = fswatch.watch_directory(tmp_path, Collector(), backend="polling", start=False)
    assert not watcher.running
    watcher.start()
    assert watcher.running
    watcher.stop()


def test_idle_watcher_does_not_burn_cpu(tmp_path: Path) -> None:
    """A blocking wait, not a spin loop: an idle second must cost ~no CPU."""
    watcher = fswatch.watch_directory(tmp_path, Collector(), debounce=0.05)
    try:
        start = time.process_time()
        time.sleep(1.0)
        assert time.process_time() - start < 0.15
    finally:
        watcher.stop()
