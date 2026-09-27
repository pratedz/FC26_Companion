r"""Watch a directory for changes — ``ReadDirectoryChangesW``, or polling.

Why this exists: ``queue/results/`` is how the game answers. v1 discovered
results by re-listing the queue on a timer forever, which is both laggy (a
result written 20 ms after a poll waits a whole interval) and wasteful (the
directory is stat-ed hundreds of times a minute while nothing happens). A
blocking kernel notification costs nothing while idle and fires in single-digit
milliseconds.

Design constraints, in the order they mattered:

* **Cancellable.** The watcher thread blocks in ``WaitForMultipleObjects`` on
  *two* handles: the overlapped I/O event and a stop event. ``stop()`` signals
  the second one, so shutdown is immediate — no "wake up every 200 ms to check
  a flag", no thread that survives the window closing. The pending
  ``ReadDirectoryChangesW`` is torn down properly (``CancelIoEx`` +
  ``GetOverlappedResult(wait=True)``) before the directory handle is closed;
  skipping that is how you get the kernel writing into a freed buffer.
* **Must not spin.** Both backends sleep in a wait call. Neither ever busy-loops,
  including while coalescing bursts.
* **Degrades to polling cleanly.** ``ReadDirectoryChangesW`` does not work on
  every filesystem the queue can land on (some network redirectors, some
  virtualised shares — R5 already warns that a synced queue breaks
  claim-by-rename). If the native watcher cannot start, ``watch_directory``
  returns a polling watcher with the *same* API; if it dies mid-flight the
  thread switches to the polling loop itself, emits ``RESCAN`` so the caller
  re-lists, and keeps going. Callers never learn which backend they got, except
  via :attr:`DirectoryWatcher.backend` for the doctor screen.
* **Simple callback API.** One function, called with a tuple of events on a
  background thread. Bursts inside ``debounce`` seconds arrive as one call — an
  atomic write (write tmp, ``os.replace``) is three raw notifications for what
  is logically one new file.

A callback that raises is reported to ``on_error`` and swallowed: a bad
subscriber must not kill the watch loop.

Threading contract: the callback runs on the watcher thread, never the caller's.
Marshal to Tk with ``after(0, ...)`` — do not touch widgets from it.
"""

from __future__ import annotations

import logging
import os
import struct
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Sequence

log = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"

# --- Win32 constants --------------------------------------------------------

_FILE_LIST_DIRECTORY = 0x0001
_FILE_SHARE_READ = 0x0001
_FILE_SHARE_WRITE = 0x0002
_FILE_SHARE_DELETE = 0x0004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000   # required to open a *directory*
_FILE_FLAG_OVERLAPPED = 0x40000000

_FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
_FILE_NOTIFY_CHANGE_DIR_NAME = 0x00000002
_FILE_NOTIFY_CHANGE_SIZE = 0x00000008
_FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
_DEFAULT_FILTER = (
    _FILE_NOTIFY_CHANGE_FILE_NAME
    | _FILE_NOTIFY_CHANGE_DIR_NAME
    | _FILE_NOTIFY_CHANGE_SIZE
    | _FILE_NOTIFY_CHANGE_LAST_WRITE
)

_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_ERROR_IO_PENDING = 997
_ERROR_OPERATION_ABORTED = 995

#: 32 KB of notifications is thousands of filenames. Anything above 64 KB is
#: rejected outright on network shares, so this is also the safe ceiling.
_BUFFER_BYTES = 32 * 1024

_ACTION_ADDED = 1
_ACTION_REMOVED = 2
_ACTION_MODIFIED = 3
_ACTION_RENAMED_OLD_NAME = 4
_ACTION_RENAMED_NEW_NAME = 5


class ChangeKind(str, Enum):
    """What happened to a path."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED_FROM = "renamed_from"   # the old name of a rename
    RENAMED_TO = "renamed_to"       # the new name — an atomic write lands here
    RESCAN = "rescan"               # notifications were lost; re-list the dir

    @property
    def appeared(self) -> bool:
        """True for the kinds that mean "a file is (now) there"."""
        return self in (ChangeKind.CREATED, ChangeKind.RENAMED_TO, ChangeKind.MODIFIED)


@dataclass(frozen=True, slots=True)
class FileEvent:
    """A single change. ``path`` is absolute; for ``RESCAN`` it is the directory."""

    path: Path
    kind: ChangeKind

    @property
    def name(self) -> str:
        return self.path.name


Callback = Callable[[tuple[FileEvent, ...]], None]
ErrorHandler = Callable[[BaseException], None]

_ACTION_MAP: dict[int, ChangeKind] = {
    _ACTION_ADDED: ChangeKind.CREATED,
    _ACTION_REMOVED: ChangeKind.DELETED,
    _ACTION_MODIFIED: ChangeKind.MODIFIED,
    _ACTION_RENAMED_OLD_NAME: ChangeKind.RENAMED_FROM,
    _ACTION_RENAMED_NEW_NAME: ChangeKind.RENAMED_TO,
}


# --- shared machinery -------------------------------------------------------


class DirectoryWatcher:
    """Base class: lifecycle, dispatch, error handling. Backends fill in ``_run``."""

    backend = "none"

    def __init__(
        self,
        directory: Path,
        callback: Callback,
        *,
        recursive: bool = False,
        debounce: float = 0.15,
        poll_interval: float = 1.0,
        on_error: ErrorHandler | None = None,
    ) -> None:
        self.directory = Path(directory)
        self._callback = callback
        self._recursive = bool(recursive)
        self._debounce = max(0.0, float(debounce))
        self._poll_interval = max(0.05, float(poll_interval))
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    # -- lifecycle --

    def start(self) -> None:
        """Begin watching. Idempotent."""
        if self._thread is not None:
            return
        self._prepare()
        self._thread = threading.Thread(
            target=self._safe_run,
            name=f"fswatch-{self.backend}-{self.directory.name}",
            daemon=True,   # never keep a closing app alive
        )
        self._thread.start()
        self._started.wait(timeout=2.0)

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the thread and wait for it. Idempotent, safe from any thread.

        Resources are released only once the thread is provably gone. Freeing a
        watch buffer or closing a directory handle while the watcher thread may
        still be inside a Win32 call is how you corrupt memory in a process
        whose whole job is editing a live game.
        """
        self._stop.set()
        self._wake()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                log.warning("fswatch %s: thread did not stop in %.1fs", self.directory, timeout)
                return   # leak the handles rather than free them underneath it
        self._thread = None
        self._cleanup()

    close = stop

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def __enter__(self) -> "DirectoryWatcher":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- hooks --

    def _prepare(self) -> None:
        """Acquire resources on the *calling* thread so failures are visible."""

    def _wake(self) -> None:
        """Nudge the backend out of its wait."""

    def _cleanup(self) -> None:
        """Release resources. Must tolerate being called twice."""

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- dispatch --

    def _safe_run(self) -> None:
        self._started.set()
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            self._report(exc)
        finally:
            self._cleanup()

    def _emit(self, events: Sequence[FileEvent]) -> None:
        if not events:
            return
        try:
            self._callback(tuple(events))
        except Exception as exc:  # noqa: BLE001 - a bad subscriber must not kill us
            self._report(exc)

    def _report(self, exc: BaseException) -> None:
        if self._on_error is not None:
            try:
                self._on_error(exc)
                return
            except Exception:  # noqa: BLE001
                pass
        log.warning("fswatch %s (%s): %s", self.directory, self.backend, exc)

    # -- the polling loop, shared with the native backend's fallback --

    def _poll_loop(self, *, announce_rescan: bool = False) -> None:
        if announce_rescan:
            self._emit([FileEvent(self.directory, ChangeKind.RESCAN)])
        previous = _snapshot(self.directory, self._recursive)
        while not self._stop.wait(self._poll_interval):   # sleeps; never spins
            current = _snapshot(self.directory, self._recursive)
            events = _diff(previous, current)
            previous = current
            self._emit(events)


def _snapshot(directory: Path, recursive: bool) -> dict[str, tuple[int, int]]:
    """path -> (mtime_ns, size). Missing directory yields an empty snapshot."""
    out: dict[str, tuple[int, int]] = {}
    try:
        walker: Iterable[tuple[str, list[str], list[str]]]
        if recursive:
            walker = os.walk(directory)
        else:
            walker = [(str(directory), [], [e.name for e in os.scandir(directory) if e.is_file()])]
        for root, _dirs, files in walker:
            for name in files:
                full = os.path.join(root, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue   # vanished between listing and stat — normal
                out[full] = (st.st_mtime_ns, st.st_size)
    except OSError:
        return out
    return out


def _diff(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> list[FileEvent]:
    events: list[FileEvent] = []
    for path, stamp in after.items():
        old = before.get(path)
        if old is None:
            events.append(FileEvent(Path(path), ChangeKind.CREATED))
        elif old != stamp:
            events.append(FileEvent(Path(path), ChangeKind.MODIFIED))
    for path in before:
        if path not in after:
            events.append(FileEvent(Path(path), ChangeKind.DELETED))
    return events


# --- polling backend --------------------------------------------------------


class PollingDirectoryWatcher(DirectoryWatcher):
    """``os.scandir`` + ``stat`` on an interval. Works everywhere, always.

    Resolution is ``poll_interval``; a file created and deleted between two
    scans is invisible. That is acceptable for the queue (results are written
    and left) and it is the price of a filesystem that cannot notify.
    """

    backend = "polling"

    def _run(self) -> None:
        self._poll_loop()


# --- native backend ---------------------------------------------------------


def _win32():
    """kernel32 with the directory-watching signatures declared."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE

    k32.CreateEventW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    ]
    k32.CreateEventW.restype = wintypes.HANDLE

    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.SetEvent.restype = wintypes.BOOL
    k32.ResetEvent.argtypes = [wintypes.HANDLE]
    k32.ResetEvent.restype = wintypes.BOOL

    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k32.CancelIoEx.restype = wintypes.BOOL

    k32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    ]
    k32.GetOverlappedResult.restype = wintypes.BOOL

    k32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
    ]
    k32.WaitForMultipleObjects.restype = wintypes.DWORD

    k32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p,
    ]
    k32.ReadDirectoryChangesW.restype = wintypes.BOOL
    return k32


def _overlapped_type():
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ulong_ptr),
            ("InternalHigh", ulong_ptr),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    return OVERLAPPED


def parse_notifications(raw: bytes, directory: Path) -> list[FileEvent]:
    """Decode a ``FILE_NOTIFY_INFORMATION`` chain into events.

    Layout per record: ``DWORD NextEntryOffset, DWORD Action,
    DWORD FileNameLength`` (in **bytes**, not characters) then the UTF-16LE
    name, unterminated. ``NextEntryOffset == 0`` ends the chain. Pure and
    exported so the parser is testable without a filesystem at all.
    """
    events: list[FileEvent] = []
    offset = 0
    size = len(raw)
    while offset + 12 <= size:
        next_offset, action, name_bytes = struct.unpack_from("<III", raw, offset)
        start = offset + 12
        end = start + name_bytes
        if end > size:
            break
        try:
            name = raw[start:end].decode("utf-16-le")
        except UnicodeDecodeError:
            name = ""
        if name:
            kind = _ACTION_MAP.get(action)
            if kind is not None:
                events.append(FileEvent(directory / name, kind))
        if next_offset == 0:
            break
        if next_offset < 12:
            break   # malformed; refuse to loop forever
        offset += next_offset
    return events


class NativeDirectoryWatcher(DirectoryWatcher):
    """``ReadDirectoryChangesW`` with overlapped I/O and a stop event.

    ``_prepare`` opens the directory on the caller's thread so a bad path or an
    unsupported filesystem raises *before* :func:`watch_directory` commits to
    this backend and can still fall back to polling.
    """

    backend = "native"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._k32 = None
        self._dir_handle: int | None = None
        self._io_event: int | None = None
        self._stop_handle: int | None = None
        self._lock = threading.Lock()

    # -- resources --

    def _prepare(self) -> None:
        import ctypes

        if not _IS_WINDOWS:
            raise OSError("ReadDirectoryChangesW is Windows-only")
        if not self.directory.is_dir():
            raise FileNotFoundError(f"not a directory: {self.directory}")

        k32 = _win32()
        handle = k32.CreateFileW(
            str(self.directory),
            _FILE_LIST_DIRECTORY,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OVERLAPPED,
            None,
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise OSError(
                ctypes.get_last_error(),
                f"CreateFileW failed for {self.directory}",
            )
        io_event = k32.CreateEventW(None, True, False, None)     # manual reset
        stop_event = k32.CreateEventW(None, True, False, None)
        if not io_event or not stop_event:
            k32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "CreateEventW failed")

        self._k32 = k32
        self._dir_handle = handle
        self._io_event = io_event
        self._stop_handle = stop_event

    def _wake(self) -> None:
        # Same lock as _cleanup: signalling a handle another thread is closing
        # can land on whatever object Windows recycled that value into.
        with self._lock:
            k32, stop_handle = self._k32, self._stop_handle
            if k32 is not None and stop_handle:
                k32.SetEvent(stop_handle)

    def _cleanup(self) -> None:
        with self._lock:
            k32 = self._k32
            if k32 is None:
                return
            for attr in ("_dir_handle", "_io_event", "_stop_handle"):
                handle = getattr(self, attr)
                if handle:
                    k32.CloseHandle(handle)
                setattr(self, attr, None)
            self._k32 = None

    # -- loop --

    def _run(self) -> None:
        # _watch_loop guarantees no I/O is outstanding when it returns, so the
        # buffer it owns can be freed safely. Degrading happens out here, after
        # that guarantee holds — never from inside the loop.
        reason = self._watch_loop()
        if reason is not None:
            self._degrade(reason)

    def _watch_loop(self) -> BaseException | None:
        """Watch until stopped. Returns an exception if we should degrade.

        **Invariant: no ``ReadDirectoryChangesW`` is pending on return.** The
        kernel writes into ``buffer`` when the I/O completes; if the frame that
        owns it dies first, that write lands in freed memory. The symptom is an
        access violation somewhere else entirely, minutes later. The ``finally``
        below (``CancelIoEx`` + ``GetOverlappedResult(wait=True)``) is the only
        thing standing between this module and that bug — the loop is *not*
        allowed to ``return`` around it.
        """
        import ctypes
        from ctypes import wintypes

        k32 = self._k32
        if k32 is None or not (self._dir_handle and self._io_event and self._stop_handle):
            return OSError("watcher was cleaned up before it started")

        overlapped = _overlapped_type()()
        overlapped.hEvent = self._io_event
        # DWORD array => guaranteed 4-byte alignment, which the API requires.
        buffer = (wintypes.DWORD * (_BUFFER_BYTES // 4))()
        handles = (wintypes.HANDLE * 2)(self._io_event, self._stop_handle)
        returned = wintypes.DWORD(0)
        pending: list[FileEvent] = []
        armed = False

        def issue_read() -> bool:
            nonlocal armed
            k32.ResetEvent(self._io_event)
            ok = k32.ReadDirectoryChangesW(
                self._dir_handle,
                ctypes.byref(buffer),
                _BUFFER_BYTES,
                self._recursive,
                _DEFAULT_FILTER,
                ctypes.byref(returned),
                ctypes.byref(overlapped),
                None,
            )
            armed = bool(ok) or ctypes.get_last_error() == _ERROR_IO_PENDING
            return armed

        def drain() -> None:
            """Cancel and *wait out* any outstanding read. Idempotent."""
            nonlocal armed
            if not armed:
                return
            k32.CancelIoEx(self._dir_handle, ctypes.byref(overlapped))
            got = wintypes.DWORD(0)
            k32.GetOverlappedResult(
                self._dir_handle, ctypes.byref(overlapped), ctypes.byref(got), True
            )
            armed = False

        try:
            if not issue_read():
                # Some filesystems accept the directory handle and then refuse
                # to watch it (ERROR_INVALID_FUNCTION on a few network
                # redirectors). That is a degrade, not a crash.
                return OSError(ctypes.get_last_error(), "ReadDirectoryChangesW failed")

            while not self._stop.is_set():
                timeout = _INFINITE if not pending else max(1, int(self._debounce * 1000))
                waited = k32.WaitForMultipleObjects(2, ctypes.byref(handles), False, timeout)

                if waited == _WAIT_TIMEOUT:
                    self._emit(pending)      # debounce window closed
                    pending = []
                    continue

                if waited == _WAIT_OBJECT_0 + 1 or self._stop.is_set():
                    self._emit(pending)      # do not swallow what we already saw
                    return None

                if waited == _WAIT_FAILED:
                    return OSError(ctypes.get_last_error(), "WaitForMultipleObjects failed")

                got = wintypes.DWORD(0)
                ok = k32.GetOverlappedResult(
                    self._dir_handle, ctypes.byref(overlapped), ctypes.byref(got), False
                )
                if not ok:
                    err = ctypes.get_last_error()
                    armed = False if err == _ERROR_OPERATION_ABORTED else armed
                    if err == _ERROR_OPERATION_ABORTED:
                        return None          # someone cancelled us; that is a stop
                    return OSError(err, "GetOverlappedResult failed")
                armed = False                # this read completed

                count = int(got.value)
                if count <= 0:
                    # Buffer overflowed: the kernel dropped notifications and
                    # will not say which. The only honest answer is "re-list".
                    events = [FileEvent(self.directory, ChangeKind.RESCAN)]
                else:
                    events = parse_notifications(
                        ctypes.string_at(ctypes.byref(buffer), count), self.directory
                    )

                # Re-arm *after* copying out of the buffer and *before*
                # dispatching, so changes during the callback are not lost.
                if not issue_read():
                    self._emit(pending + events)
                    return OSError(ctypes.get_last_error(), "re-arm failed")

                if self._debounce <= 0:
                    self._emit(events)
                else:
                    pending.extend(events)
            self._emit(pending)
            return None
        finally:
            drain()

    def _degrade(self, exc: BaseException) -> None:
        """Native watching died. Say so, then keep working by polling."""
        self._report(exc)
        self._cleanup()
        if self._stop.is_set():
            return
        log.info("fswatch %s: falling back to polling", self.directory)
        self.backend = "polling (degraded)"   # instance attribute shadows the class one
        self._poll_loop(announce_rescan=True)


# --- factory ----------------------------------------------------------------


def watch_directory(
    directory: Path | str,
    callback: Callback,
    *,
    recursive: bool = False,
    debounce: float = 0.15,
    poll_interval: float = 1.0,
    backend: str = "auto",
    on_error: ErrorHandler | None = None,
    start: bool = True,
) -> DirectoryWatcher:
    """Watch ``directory``; call ``callback(events)`` on a background thread.

    ``backend`` is ``"auto"`` (native, silently falling back to polling),
    ``"native"`` (raise if unavailable) or ``"polling"`` (force). ``debounce``
    coalesces bursts — an atomic ``write tmp + os.replace`` is several raw
    notifications for one logical file. Stop with ``watcher.stop()`` or use it
    as a context manager.
    """
    path = Path(directory)
    kwargs = {
        "recursive": recursive,
        "debounce": debounce,
        "poll_interval": poll_interval,
        "on_error": on_error,
    }

    if backend not in ("auto", "native", "polling"):
        raise ValueError(f"unknown backend: {backend!r}")

    if backend == "polling" or (backend == "auto" and not _IS_WINDOWS):
        watcher: DirectoryWatcher = PollingDirectoryWatcher(path, callback, **kwargs)  # type: ignore[arg-type]
        if start:
            watcher.start()
        return watcher

    native = NativeDirectoryWatcher(path, callback, **kwargs)  # type: ignore[arg-type]
    if not start:
        return native
    try:
        native.start()
        return native
    except Exception as exc:  # noqa: BLE001
        native.stop(timeout=0.5)
        if backend == "native":
            raise
        log.info("fswatch %s: native unavailable (%s); polling instead", path, exc)
        fallback = PollingDirectoryWatcher(path, callback, **kwargs)  # type: ignore[arg-type]
        fallback.start()
        return fallback


def watch_for_new_files(
    directory: Path | str,
    on_new: Callable[[Path], None],
    *,
    suffix: str = "",
    include_existing: bool = False,
    **kwargs: object,
) -> DirectoryWatcher:
    """Convenience wrapper: call ``on_new(path)`` once per file that appears.

    This is the ``queue/results/`` case. Rules that matter there:

    * an atomic write shows up as CREATED and/or RENAMED_TO and/or MODIFIED —
      all three mean "there is a file now", and each path fires **once**;
    * on ``RESCAN`` the directory is re-listed, because the kernel dropped
      notifications and will not tell us which;
    * a path is only reported if it still exists when we look.
    """
    root = Path(directory)
    seen: set[str] = set()
    lock = threading.Lock()

    def consider(path: Path) -> list[Path]:
        if suffix and path.suffix.lower() != suffix.lower():
            return []
        key = str(path).lower()
        with lock:
            if key in seen:
                return []
            if not path.is_file():
                return []
            seen.add(key)
        return [path]

    def relist() -> list[Path]:
        out: list[Path] = []
        try:
            for entry in sorted(root.iterdir()):
                if entry.is_file():
                    out.extend(consider(entry))
        except OSError:
            pass
        return out

    if include_existing:
        # Report what is already there *before* starting the watcher, so a file
        # present at start cannot be delivered twice.
        for existing in relist():
            on_new(existing)

    def dispatch(events: tuple[FileEvent, ...]) -> None:
        fresh: list[Path] = []
        for event in events:
            if event.kind is ChangeKind.RESCAN:
                fresh.extend(relist())
            elif event.kind is ChangeKind.DELETED:
                with lock:
                    seen.discard(str(event.path).lower())
            elif event.kind.appeared:
                fresh.extend(consider(event.path))
        for path in fresh:
            on_new(path)

    return watch_directory(root, dispatch, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "Callback",
    "ChangeKind",
    "DirectoryWatcher",
    "ErrorHandler",
    "FileEvent",
    "NativeDirectoryWatcher",
    "PollingDirectoryWatcher",
    "parse_notifications",
    "watch_directory",
    "watch_for_new_files",
]
