"""core/log.py — the context binder, the sinks, and idempotent setup.

The binder is the only part with real logic: if a record emitted from a module
that knows nothing about jobs does not carry the job_id, "grep the log for one
apply" does not work and the whole §7.1 design is decorative.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import pytest

from companion.core import log as logmod
from companion.core.paths import TempAppPaths


@pytest.fixture(autouse=True)
def _clean_logging():
    logmod.reset_for_tests()
    yield
    logmod.reset_for_tests()


@pytest.fixture()
def paths(tmp_path: Path) -> TempAppPaths:
    return TempAppPaths(tmp_path)


def _jsonl(paths: TempAppPaths) -> list[dict]:
    p = paths.logs / "companion.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- setup -----------------------------------------------------------------


def test_setup_creates_both_sinks(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    log.info("hello")
    for h in log.handlers:
        h.flush()
    assert (paths.logs / "companion.jsonl").is_file()
    assert (paths.logs / "companion.log").is_file()


def test_setup_is_idempotent(paths: TempAppPaths) -> None:
    a = logmod.setup_logging(paths)
    n = len(a.handlers)
    b = logmod.setup_logging(paths)
    assert a is b
    assert len(b.handlers) == n, "a second setup must not double every log line"


def test_setup_force_rebuilds_without_duplicating(paths: TempAppPaths) -> None:
    a = logmod.setup_logging(paths)
    n = len(a.handlers)
    b = logmod.setup_logging(paths, force=True)
    assert len(b.handlers) == n


def test_setup_survives_unwritable_log_dir(tmp_path: Path) -> None:
    """A read-only install must still start. The logger is not a launch blocker."""

    class Blocked:
        logs = tmp_path / "nope"

    orig = Path.mkdir

    def boom(self, *a, **k):  # noqa: ANN001
        if "nope" in str(self):
            raise OSError("read-only")
        return orig(self, *a, **k)

    Path.mkdir = boom  # type: ignore[method-assign]
    try:
        log = logmod.setup_logging(Blocked())
        log.info("still alive")
    finally:
        Path.mkdir = orig  # type: ignore[method-assign]
    assert log.name == "companion"


def test_logger_does_not_propagate_to_root(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    assert log.propagate is False


# --- context binding -------------------------------------------------------


def test_bind_tags_records_from_unrelated_loggers(paths: TempAppPaths) -> None:
    """A line logged from domain/ inside a bound apply carries the job id."""
    logmod.setup_logging(paths)
    domain_log = logging.getLogger("companion.domain.roster.face")
    with logmod.bind(job_id="01JQ8FTESTTESTTESTTESTTEST", command="apply"):
        domain_log.info("resolved head for %d", 48940)
    for h in logging.getLogger("companion").handlers:
        h.flush()

    rows = [r for r in _jsonl(paths) if "resolved head" in r["msg"]]
    assert len(rows) == 1
    assert rows[0]["job_id"] == "01JQ8FTESTTESTTESTTESTTEST"
    assert rows[0]["command"] == "apply"
    assert rows[0]["logger"] == "companion.domain.roster.face"


def test_bind_restores_previous_frame_on_exception(paths: TempAppPaths) -> None:
    logmod.setup_logging(paths)
    with logmod.bind(job_id="OUTER"):
        with pytest.raises(RuntimeError):
            with logmod.bind(job_id="INNER"):
                assert logmod.current_context()["job_id"] == "INNER"
                raise RuntimeError("boom")
        assert logmod.current_context()["job_id"] == "OUTER"
    assert logmod.current_context() == {}


def test_nested_bind_keeps_outer_fields(paths: TempAppPaths) -> None:
    logmod.setup_logging(paths)
    with logmod.bind(job_id="J1", save_uid="SAVE"):
        with logmod.bind(surface="ui.cards"):
            ctx = logmod.current_context()
    assert ctx == {"job_id": "J1", "save_uid": "SAVE", "surface": "ui.cards"}


def test_set_context_none_removes_the_key(paths: TempAppPaths) -> None:
    logmod.setup_logging(paths)
    with logmod.bind(job_id="J1"):
        logmod.set_context(job_id=None)
        assert "job_id" not in logmod.current_context()


def test_unbound_records_have_empty_context_fields_not_keyerror(paths: TempAppPaths) -> None:
    """%(job_id)s in the human format must never raise on an unbound record."""
    log = logmod.setup_logging(paths)
    log.info("no job here")
    for h in log.handlers:
        h.flush()
    text = (paths.logs / "companion.log").read_text(encoding="utf-8")
    assert "no job here" in text
    assert "[" not in text.split("no job here")[0].split("\n")[-1]


def test_context_propagates_into_a_worker_thread(paths: TempAppPaths) -> None:
    """ThreadExecutor runs commands off-thread; contextvars must travel with them."""
    import contextvars

    logmod.setup_logging(paths)
    seen: dict[str, str] = {}

    with logmod.bind(job_id="THREADED"):
        ctx = contextvars.copy_context()

        def work() -> None:
            seen.update(logmod.current_context())
            logging.getLogger("companion.worker").info("in thread")

        t = threading.Thread(target=lambda: ctx.run(work))
        t.start()
        t.join()

    assert seen.get("job_id") == "THREADED"
    for h in logging.getLogger("companion").handlers:
        h.flush()
    assert any(r.get("job_id") == "THREADED" for r in _jsonl(paths) if r["msg"] == "in thread")


def test_threads_do_not_leak_context_into_each_other(paths: TempAppPaths) -> None:
    logmod.setup_logging(paths)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def worker(job: str) -> None:
        with logmod.bind(job_id=job):
            barrier.wait(timeout=5)
            results.append(logmod.current_context())

    ts = [threading.Thread(target=worker, args=(j,)) for j in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(r["job_id"] for r in results) == ["A", "B"]


# --- formatters ------------------------------------------------------------


def test_jsonl_carries_extra_fields(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    log.info("wrote fields", extra={"playerid": 48940, "fields_written": 12})
    for h in log.handlers:
        h.flush()
    row = next(r for r in _jsonl(paths) if r["msg"] == "wrote fields")
    assert row["playerid"] == 48940
    assert row["fields_written"] == 12
    assert row["src"].startswith("test_core_log:")


def test_jsonl_never_raises_on_unserialisable_extra(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)

    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    log.info("odd payload", extra={"thing": Weird()})
    for h in log.handlers:
        h.flush()
    row = next(r for r in _jsonl(paths) if r["msg"] == "odd payload")
    assert row["thing"] == "<weird>"


def test_exception_is_serialised(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    try:
        raise ValueError("the real reason")
    except ValueError:
        log.exception("command boundary")
    for h in log.handlers:
        h.flush()
    row = next(r for r in _jsonl(paths) if r["msg"] == "command boundary")
    assert "ValueError: the real reason" in row["exc"]


def test_human_line_brackets_the_job_id(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    with logmod.bind(job_id="01JQ8FAAAAAAAAAAAAAAAAAAAA"):
        log.info("submitting 3 ops")
    for h in log.handlers:
        h.flush()
    text = (paths.logs / "companion.log").read_text(encoding="utf-8")
    assert "[01JQ8FAAAAAAAAAAAAAAAAAAAA] submitting 3 ops" in text


# --- ring buffer -----------------------------------------------------------


def test_ring_buffer_collects_and_caps(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    for i in range(520):
        log.info("line %d", i)
    ring = logmod.ring_buffer()
    assert ring is not None
    rows = ring.records()
    assert len(rows) == 500, "capacity is 500 (§7.1)"
    assert rows[-1]["msg"] == "line 519"


def test_ring_buffer_ignores_debug(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    ring = logmod.ring_buffer()
    assert ring is not None
    ring.clear()
    log.debug("noise")
    log.warning("signal")
    assert [r["msg"] for r in ring.records()] == ["signal"]


def test_ring_buffer_filters_by_level(paths: TempAppPaths) -> None:
    log = logmod.setup_logging(paths)
    ring = logmod.ring_buffer()
    assert ring is not None
    ring.clear()
    log.info("ok")
    log.error("bad")
    assert [r["msg"] for r in ring.records(level="ERROR")] == ["bad"]


# --- get_logger ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", "companion"),
        ("companion", "companion"),
        ("apply", "companion.apply"),
        ("companion.app.commands.apply", "companion.app.commands.apply"),
    ],
)
def test_get_logger_names(name: str, expected: str) -> None:
    assert logmod.get_logger(name).name == expected


def test_bootstrap_uses_setup_logging(tmp_path: Path) -> None:
    """bootstrap._try_logger imports setup_logging(paths) — keep that contract."""
    from companion.bootstrap import build_services

    svc = build_services(tmp_path, inline=True)
    assert svc.log.name == "companion"
    assert (tmp_path / "logs").is_dir()
