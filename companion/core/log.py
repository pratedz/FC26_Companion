"""One logging configuration for the whole app, called once from bootstrap.

v1 has **zero uses of the ``logging`` module** across 55 modules: eleven
``print()`` calls and an ``app.log`` written by ``app_entry.py`` through its own
hand-rolled path derivation are the entire diagnostic surface, next to 699
``except`` clauses of which 361 end in a bare ``pass``. When an apply silently
did nothing there was no line anywhere saying which player id was resolved, or
whether the job was even claimed. That is the debt that makes every other bug
expensive, so this module exists to make logging *cheaper to call than to skip*.

Design (``docs/V2_ARCHITECTURE.md §7.1``):

============================  ==========================  =======  ============
sink                          format                      level    rotation
============================  ==========================  =======  ============
``logs/companion.jsonl``      one JSON object per line    DEBUG    10 MB x 5
``logs/companion.log``        human, with ``[job_id]``    INFO     10 MB x 3
console (dev only, unfrozen)  human                       DEBUG    --
in-app ring buffer            ``LogRecord`` dicts         INFO     last 500
============================  ==========================  =======  ============

The load-bearing part is the **context binder**. ``job_id``/``save_uid``/
``session_id``/``command``/``surface`` live in ``contextvars``, are stamped onto
every record by a ``logging.Filter``, and are set once at the Command boundary::

    with bind(job_id=job.job_id, command="apply", surface="ui.cards"):
        log.info("submitting %d ops", len(job.ops))

Every line emitted inside that block -- including from ``domain/`` modules that
know nothing about jobs -- carries the id. "Show me everything that happened for
job 01JQ8F..." becomes one ``grep`` over the JSONL, which is the whole point.

``contextvars`` (not thread-locals) because ``core/executor.py`` runs work on a
``ThreadExecutor``; ``contextvars.copy_context()`` propagates the binding into
the worker thread, and each submit gets its own snapshot rather than racing over
one shared dict.

Idempotence matters more than it looks: ``build_services`` may be called by the
CLI, the GUI and a test in the same process. ``setup_logging`` installs handlers
exactly once per root directory and returns the same logger afterwards, so a
second call cannot double every line.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import sys
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

__all__ = [
    "LOGGER_NAME",
    "CONTEXT_FIELDS",
    "setup_logging",
    "get_logger",
    "bind",
    "set_context",
    "current_context",
    "clear_context",
    "ring_buffer",
    "RingBufferHandler",
    "JsonlFormatter",
    "ContextFilter",
    "reset_for_tests",
]

LOGGER_NAME = "companion"

#: The five fields every record carries (§7.1). Stamped by :class:`ContextFilter`
#: so a ``%(job_id)s`` in any format string can never raise ``KeyError``.
CONTEXT_FIELDS: tuple[str, ...] = ("job_id", "save_uid", "session_id", "command", "surface")

_MAX_BYTES = 10 * 1024 * 1024
_RING_CAPACITY = 500

#: One contextvar holding an immutable mapping, rather than five vars: rebinding
#: is then a single atomic ``set`` and ``reset`` restores the whole frame.
_context: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar(
    "companion_log_context", default={}
)

_lock = threading.Lock()
_configured_root: Path | None = None
_ring: "RingBufferHandler | None" = None


class _PathsLike(Protocol):
    """Structural: only ``logs`` is needed, so ``TempAppPaths`` works unchanged."""

    logs: Path


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def current_context() -> dict[str, str]:
    """The context that would be stamped onto a record emitted right now."""
    return dict(_context.get())


def set_context(**fields: Any) -> contextvars.Token[Mapping[str, str]]:
    """Merge ``fields`` into the ambient context; returns a reset token.

    ``None`` values *remove* a key, so ``set_context(job_id=None)`` un-tags
    without leaving the string ``"None"`` in the logs.
    """
    merged = dict(_context.get())
    for key, value in fields.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = str(value)
    return _context.set(merged)


def clear_context() -> None:
    _context.set({})


@contextmanager
def bind(**fields: Any) -> Iterator[dict[str, str]]:
    """Scope a context frame. Restores the previous frame even on exception.

    This is what a Command wraps itself in. Nesting is fine -- an inner
    ``bind(surface=...)`` keeps the outer ``job_id``.
    """
    token = set_context(**fields)
    try:
        yield current_context()
    finally:
        _context.reset(token)


class ContextFilter(logging.Filter):
    """Copy the ambient context onto each record, defaulting every field to ``""``.

    A ``Filter`` rather than a ``LoggerAdapter`` because adapters only tag calls
    made through the adapter; records from ``logging.getLogger(__name__)`` deep
    in ``domain/`` would be missed, and those are exactly the ones worth having.

    Installed on every **handler**, not on the ``companion`` logger. Logger-level
    filters run in ``Logger.handle`` for the logger the call was made on, and are
    *not* re-applied to records propagating up from children -- so a filter on
    ``companion`` would silently skip every line from ``companion.domain.*``,
    which is the majority of them. Handler filters see all of it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _context.get()
        for name in CONTEXT_FIELDS:
            if not hasattr(record, name):
                setattr(record, name, ctx.get(name, ""))
        # Anything bound that is not one of the five still reaches the JSONL.
        extra = {k: v for k, v in ctx.items() if k not in CONTEXT_FIELDS}
        if extra and not hasattr(record, "ctx_extra"):
            record.ctx_extra = extra  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Formatters and handlers
# ---------------------------------------------------------------------------

#: Attributes ``logging`` puts on every record; anything else came from ``extra=``
#: and is worth serialising.
_STOCK = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName ctx_extra""".split()
)


class JsonlFormatter(logging.Formatter):
    """One JSON object per line: greppable, and parseable by the diagnostics pane.

    Never raises. A formatter that throws on an un-serialisable ``extra`` would
    take down the very apply it was supposed to explain, so unknown objects fall
    back to ``repr`` via ``default=str``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, "")
            if value:
                payload[name] = value
        for key, value in record.__dict__.items():
            if key not in _STOCK and key not in CONTEXT_FIELDS and not key.startswith("_"):
                payload[key] = value
        extra = getattr(record, "ctx_extra", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        payload["src"] = f"{record.module}:{record.lineno}"
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps(
                {"ts": payload["ts"], "level": "ERROR", "logger": record.name,
                 "msg": "unserialisable log record", "src": payload["src"]}
            )


class _HumanFormatter(logging.Formatter):
    """``… INFO  companion.apply [01JQ8F…] submitting 3 ops``.

    The job id is bracketed only when present, so idle lines are not padded with
    empty brackets -- v1's log was unreadable for exactly that kind of noise.
    """

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-5s %(name)s %(message)s", "%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        job_id = getattr(record, "job_id", "")
        base = super().format(record)
        if job_id:
            head, sep, tail = base.partition(record.getMessage())
            return f"{head}[{job_id}] {record.getMessage()}{tail}" if sep else f"{base} [{job_id}]"
        return base


class RingBufferHandler(logging.Handler):
    """Last N records, for **About > Diagnostics** with no file read.

    Stores formatted dicts rather than ``LogRecord`` objects: a record holds a
    reference to its ``args``, which for a card apply is the whole card dict, so
    keeping 500 of them would pin megabytes of payloads alive.
    """

    def __init__(self, capacity: int = _RING_CAPACITY) -> None:
        super().__init__(level=logging.INFO)
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            for name in CONTEXT_FIELDS:
                value = getattr(record, name, "")
                if value:
                    entry[name] = value
            if record.exc_info:
                entry["exc"] = logging.Formatter().formatException(record.exc_info)
            with self._guard:
                self._buf.append(entry)
        except Exception:  # noqa: BLE001 -- a broken handler must never kill the caller
            self.handleError(record)

    def records(self, *, level: str = "", limit: int = 0) -> list[dict[str, Any]]:
        with self._guard:
            rows = list(self._buf)
        if level:
            want = level.upper()
            rows = [r for r in rows if r["level"] == want]
        return rows[-limit:] if limit > 0 else rows

    def clear(self) -> None:
        with self._guard:
            self._buf.clear()


def ring_buffer() -> RingBufferHandler | None:
    """The installed ring buffer, or ``None`` before ``setup_logging``."""
    return _ring


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _level_from_env(default: int = logging.DEBUG) -> int:
    raw = (os.environ.get("COMPANION_LOG_LEVEL") or "").strip().upper()
    if not raw:
        return default
    return getattr(logging, raw, default) if isinstance(getattr(logging, raw, None), int) else default


def setup_logging(paths: _PathsLike | Any, *, force: bool = False) -> logging.Logger:
    """Configure the ``companion`` logger tree and return it.

    ``companion/bootstrap.py`` already calls exactly ``setup_logging(paths)``, so
    this signature is fixed. Idempotent per root: calling it again for the same
    ``paths.logs`` is a no-op that returns the existing logger.

    Never raises. If ``logs/`` cannot be created (read-only install, a locked
    directory) the console handler is still installed and the app starts -- the
    logger failing must not be the reason the user cannot launch.
    """
    global _configured_root, _ring

    log_dir = Path(getattr(paths, "logs", Path("logs")))
    logger = logging.getLogger(LOGGER_NAME)

    with _lock:
        if _configured_root == log_dir and not force and logger.handlers:
            return logger
        if force or logger.handlers:
            _teardown(logger)

        logger.setLevel(logging.DEBUG)
        # The app owns its own sinks; a library-style basicConfig at the root
        # would otherwise duplicate every line onto stderr under pytest.
        logger.propagate = False
        ctx_filter = ContextFilter()

        writable = True
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            writable = False

        if writable:
            for filename, level, backups, formatter in (
                ("companion.jsonl", _level_from_env(), 5, JsonlFormatter()),
                ("companion.log", logging.INFO, 3, _HumanFormatter()),
            ):
                try:
                    handler = logging.handlers.RotatingFileHandler(
                        log_dir / filename,
                        maxBytes=_MAX_BYTES,
                        backupCount=backups,
                        encoding="utf-8",
                        delay=True,
                    )
                except OSError:
                    continue
                handler.setLevel(level)
                handler.setFormatter(formatter)
                logger.addHandler(handler)

        if not _is_frozen() and not os.environ.get("COMPANION_NO_CONSOLE"):
            console = logging.StreamHandler(sys.stderr)
            console.setLevel(_level_from_env(logging.INFO))
            console.setFormatter(_HumanFormatter())
            logger.addHandler(console)

        _ring = RingBufferHandler()
        logger.addHandler(_ring)

        for handler in logger.handlers:
            handler.addFilter(ctx_filter)

        if not logger.handlers:  # pragma: no cover -- belt and braces
            logger.addHandler(logging.NullHandler())

        _configured_root = log_dir

    logger.debug(
        "logging configured",
        extra={"log_dir": str(log_dir), "writable": writable, "frozen": _is_frozen()},
    )
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """``get_logger("apply")`` -> the ``companion.apply`` child logger.

    Children inherit the handlers and the :class:`ContextFilter` from the parent,
    so no module ever configures anything itself.
    """
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    # ``get_logger(__name__)`` from companion.app.commands.apply -> companion.app…
    if name.startswith("companion."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def _teardown(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass
    for filt in list(logger.filters):
        logger.removeFilter(filt)


def reset_for_tests() -> None:
    """Drop all handlers so the next ``setup_logging`` starts clean."""
    global _configured_root, _ring
    with _lock:
        _teardown(logging.getLogger(LOGGER_NAME))
        _configured_root = None
        _ring = None
    clear_context()
