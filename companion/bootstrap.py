"""Composition root: build the ``Services`` bundle exactly once.

Everything above this line is injected; nothing below constructs its own
dependencies. This is the whole reason the app is testable without a game —
swap ``FileTransport`` for ``FakeTransport`` and ``ThreadExecutor`` for
``InlineExecutor`` and the entire pipeline runs in-process.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Any

from .app.services import Services
from .app.state import (
    DEFAULT_CORE_OPS,
    AppState,
    JobsState,
    JobView,
    Prefs,
    SquadState,
)
from .app.store import Store
from .core.clock import SystemClock
from .core.executor import ThreadExecutor
from .core.paths import RealAppPaths
from .core.transport.v3 import FileTransport


def build_services(
    root: Path | None = None,
    *,
    inline: bool = False,
    logger: Any = None,
) -> Services:
    paths = RealAppPaths(root)
    paths.ensure_layout()
    clock = SystemClock()
    db = _try_db(paths)
    initial = _hydrate_state(db)
    store = Store(initial)

    if inline:
        from .core.executor import InlineExecutor

        executor: Any = InlineExecutor()
    else:
        executor = ThreadExecutor()

    if logger is None:
        logger = _try_logger(paths)

    transport = FileTransport(paths, clock=clock)
    svc = Services(
        paths=paths,
        clock=clock,
        executor=executor,
        transport=transport,
        store=store,
        log=logger,
        db=db,
        catalog=_try_catalog(paths, db),
    )
    from .app.commands.apply import reconcile_recorded_worker_results

    try:
        reconcile_recorded_worker_results(svc)
    except Exception as exc:
        logger.warning("Could not reconcile stored worker results: %s", exc)
    _persist_preferences(svc)
    return svc


def _try_logger(paths: Any) -> Any:
    """Use core.log if it is present; fall back to a plain logger.

    ``core/log.py`` is built separately; the app must start either way rather
    than crashing on an import during bootstrap.
    """
    try:
        from .core.log import setup_logging  # type: ignore[attr-defined]

        return setup_logging(paths)
    except Exception:
        import logging

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        return logging.getLogger("companion")


def _try_db(paths: Any) -> Any:
    """Open state.sqlite when possible; app still starts if it fails."""
    try:
        from .core.db import open_state_db

        return open_state_db(paths)
    except Exception:
        return None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _hydrate_state(db: Any) -> AppState:
    """Build the initial state from durable data before any view subscribes."""
    if db is None:
        return AppState()
    try:
        pref_values = db.all_prefs()
        raw_core_ops = pref_values.get("core_ops", DEFAULT_CORE_OPS)
        if not isinstance(raw_core_ops, (list, tuple, set, frozenset)):
            raw_core_ops = DEFAULT_CORE_OPS
        prefs = Prefs(
            growth_mirror=bool(pref_values.get("growth_mirror", True)),
            verify_writes=bool(pref_values.get("verify_writes", True)),
            theme=str(pref_values.get("theme", "dark")),
            confirm_destructive=bool(pref_values.get("confirm_destructive", True)),
            http_push=bool(pref_values.get("http_push", False)),
            core_ops=frozenset(str(x) for x in raw_core_ops),
            values=pref_values,
        )

        active: dict[str, JobView] = {}
        history: list[JobView] = []
        from .domain.outcome import JobResult

        for rec in db.job_records(limit=200):
            raw = dict(rec.result_json or {})
            raw.setdefault("job_id", rec.job_id)
            raw.setdefault("label", rec.label)
            raw.setdefault("outcome", rec.outcome.name)
            result = JobResult.from_wire(raw)
            view = JobView(
                job_id=rec.job_id,
                label=rec.label,
                outcome=rec.outcome,
                submitted=_parse_utc(rec.submitted_utc or rec.created_utc),
                result=result if rec.outcome.is_terminal else None,
            )
            if rec.outcome.is_terminal:
                history.append(view)
            else:
                active[rec.job_id] = view

        # Do not hydrate an actionable squad before the worker proves which
        # Career session is active. A previous save's cache must never become
        # today's player picker merely because it is the newest row on disk.
        squad = SquadState()
        return AppState(
            prefs=prefs,
            jobs=JobsState(active=active, history=tuple(history)),
            squad=squad,
        )
    except Exception:
        return AppState()


def _persist_preferences(svc: Services) -> None:
    """Persist preference changes without coupling reducers to SQLite."""
    if svc.db is None:
        return
    previous = dict(svc.store.snapshot().prefs.values)

    def save(state: AppState) -> None:
        nonlocal previous
        current = {
            key: value
            for key, value in dict(state.prefs.values).items()
            if "api_key" not in str(key).lower()
            and str(key).lower() not in {"openai_key", "openai_api_key"}
        }
        if current == previous:
            return
        svc.db.set_prefs(current)
        previous = current

    svc.store.subscribe(save)


def _try_catalog(paths: Any, db: Any = None) -> Any:
    """Local card catalog for Library search — never raises past bootstrap."""
    try:
        from .domain.catalog import LocalCatalog

        universe = Path(paths.root) / "card_db" / "universe.sqlite"
        cat = LocalCatalog(
            catalog_path=paths.universe_db,
            universe_path=universe if universe.is_file() else None,
            state_db=db,
        )
        return cat if cat.available else cat  # always attach; search errors honestly
    except Exception:
        return None
