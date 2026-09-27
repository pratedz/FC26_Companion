"""The apply pipeline must never report success for a job that wrote nothing.

Every test here pins one confirmed lie the pipeline used to tell:

  * web_api answered ok=True for a job apply_service had marked outcome="error"
    (the expression was `r.applied or ... or r.queued`, and the failure branch
    returns queued=True).
  * The editor Lua returned cleanly when the players table was missing, so the
    bridge counted ok=1, no `_job_status.txt` was written, and the empty parse
    came back failed=False → "APPLIED".
  * `written` was incremented next to a discarded pcall, so written=91 could
    mean 91 silent failures.
  * Lua error text was logged in-game and dropped on the floor.
  * `parse_job_status` had no `msg=` branch and could not tell "no status file"
    from "job succeeded".
  * apply_service only consulted the side-file when the wait already said
    applied, so a timed-out job with found=false was parsed and ignored.
  * boost_queue called a job "applied" because the file left queue/ — including
    when clear_stale_jobs had archived it as skipped_*.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src import apply_service
from src import boost_queue
from src import player_apply
from src import web_api
from src.apply_service import ApplyResult, parse_job_status


# ── parse_job_status: absence, diagnosis, counters ──────────────────


class TestParseJobStatusTruthfulness:
    def test_missing_status_file_is_not_success(self) -> None:
        p = parse_job_status("")
        assert p["present"] is False
        assert p["ok"] is None
        assert p["found"] is None
        assert p["summary"] == ""

    def test_whitespace_only_status_file_is_not_success(self) -> None:
        p = parse_job_status("   \n \t ")
        assert p["present"] is False
        assert p["ok"] is None

    def test_present_flag_is_set_for_a_real_line(self) -> None:
        p = parse_job_status("job=edit id=7 ok=true found=true written=3 failed=0 reason=ok")
        assert p["present"] is True
        assert p["failed"] is False
        assert p["written"] == 3
        assert p["failed_writes"] == 0

    def test_found_false_is_failed_and_never_ok(self) -> None:
        p = parse_job_status(
            "job=edit id=48940 ok=false found=false written=0 failed=0 "
            "scanned=25000 reason=not_found name=Petr Cech"
        )
        assert p["present"] is True
        assert p["failed"] is True
        assert p["ok"] is False
        assert p["found"] is False
        assert p["scanned"] == 25000
        assert "NOT FOUND" in p["summary"]

    def test_no_players_table_reason_is_failed(self) -> None:
        p = parse_job_status(
            "job=edit id=1 ok=false found=false written=0 failed=0 scanned=0 "
            "reason=no_players_table name=X"
        )
        assert p["failed"] is True
        assert p["reason"] == "no_players_table"
        assert "no_players_table" in p["summary"]

    def test_all_writes_failed_is_failed_even_without_ok_token(self) -> None:
        p = parse_job_status("job=edit id=1 found=true written=0 failed=91 scanned=120")
        assert p["written"] == 0
        assert p["failed_writes"] == 91
        assert p["failed"] is True

    def test_partial_write_failures_are_surfaced_without_failing_the_job(self) -> None:
        p = parse_job_status(
            "job=edit id=1 ok=true found=true written=89 failed=2 reason=partial_write_failures"
        )
        assert p["failed"] is False
        assert p["failed_writes"] == 2
        assert "failed=2" in p["summary"]
        assert "written=89" in p["summary"]

    def test_msg_survives_spaces(self) -> None:
        """The token regex truncated at the first space; msg= runs to EOL."""
        raw = (
            "job=bridge ok=false found=false written=0 failed=1 file=apply_1.lua "
            "reason=lua_error msg=apply_1.lua:42: attempt to index a nil value (global 'LE')"
        )
        p = parse_job_status(raw)
        assert p["failed"] is True
        assert p["msg"] is not None
        assert "attempt to index a nil value" in p["msg"]
        assert "attempt to index a nil value" in p["summary"]

    def test_msg_from_add_team_lua_error_is_parsed(self) -> None:
        p = parse_job_status("job=add_to_team ok=false err=lua_error msg=bad_argument_#1")
        assert p["failed"] is True
        assert p["msg"] == "bad_argument_#1"

    def test_format_result_with_job_status_says_nothing_when_absent(self) -> None:
        r = ApplyResult(
            applied=True,
            queued=True,
            live=True,
            reason="ok",
            outcome="applied",
            meta={"job_status": "", "job_status_parsed": parse_job_status("")},
        )
        assert apply_service.format_result_with_job_status(r) == ""


# ── apply_lua: the side-file is authoritative ───────────────────────


def _stub_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str):
    """Wire apply_lua at a throwaway queue dir; returns the job path."""
    q = tmp_path / "queue"
    q.mkdir(exist_ok=True)
    (q / "done").mkdir(exist_ok=True)
    job = q / "apply_1.lua"
    job.write_text("-- job\n", encoding="utf-8")
    monkeypatch.setattr(apply_service.le_apply, "queue_dir", lambda: q)
    monkeypatch.setattr(apply_service.le_apply, "bridge_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(apply_service.le_apply, "clear_stale_jobs", lambda **_k: 0)
    monkeypatch.setattr(
        apply_service.actions, "write_lua", lambda *_a, **_k: {"queue_file": str(job)}
    )
    monkeypatch.setattr(apply_service.le_apply, "job_status_text", lambda: status)
    return job


_TIMEOUT_WAIT = {
    "applied": False,
    "reason": "Timed out — pulse is LIVE but Career events did not drain the job",
    "done_file": None,
    "bridge_alive": True,
    "pending": "queue/apply_1.lua",
    "last_result": "OK idle queue_empty",
    "run_now_exists": False,
    "job_mtime0": 1.0,
    "bridge_busy": False,
}


def test_timeout_with_found_false_side_file_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The side-file used to be parsed and then dropped unless applied was True."""
    _stub_queue(
        tmp_path,
        monkeypatch,
        "job=edit id=48940 ok=false found=false written=0 failed=0 "
        "scanned=25000 reason=not_found name=X",
    )
    monkeypatch.setattr(
        apply_service.le_apply, "wait_until_applied", lambda *_a, **_k: dict(_TIMEOUT_WAIT)
    )
    r = apply_service.apply_lua("--x", stem="apply_1", wait=True)
    assert r.applied is False
    assert r.outcome == "error"
    assert r.meta["job_status_parsed"]["failed"] is True
    assert "NOT FOUND" in r.reason


def test_timeout_without_status_file_is_a_timeout_not_queued_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 90s hang used to be indistinguishable from a healthy queued job."""
    _stub_queue(tmp_path, monkeypatch, "")
    monkeypatch.setattr(
        apply_service.le_apply, "wait_until_applied", lambda *_a, **_k: dict(_TIMEOUT_WAIT)
    )
    r = apply_service.apply_lua("--x", stem="apply_1", wait=True)
    assert r.applied is False
    assert r.outcome == apply_service.OUTCOME_TIMEOUT
    assert r.outcome in apply_service.FAILED_OUTCOMES
    assert r.meta["job_status_parsed"]["present"] is False


def test_bridge_lua_error_reaches_the_parsed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bridge-written status carries the real Lua error out of the game."""
    _stub_queue(
        tmp_path,
        monkeypatch,
        "job=bridge ok=false found=false written=0 failed=1 file=apply_1.lua "
        "reason=lua_error msg=apply_1.lua:12:_attempt_to_call_a_nil_value",
    )
    monkeypatch.setattr(
        apply_service.le_apply,
        "wait_until_applied",
        lambda *_a, **_k: {
            "applied": True,
            "bridge_alive": True,
            "reason": "queue file processed by LE worker",
            "last_result": "FAIL processed=1 ok=0 last=apply_1.lua "
            "err=run:apply_1.lua:12:_attempt_to_call_a_nil_value",
        },
    )
    r = apply_service.apply_lua("--x", stem="apply_1", wait=True)
    assert r.applied is False
    assert r.outcome == "error"
    assert "attempt_to_call_a_nil_value" in r.reason
    assert "attempt_to_call_a_nil_value" in str(r.meta["job_status_parsed"]["msg"])


def test_bridge_fail_result_is_an_error_not_queued_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_queue(tmp_path, monkeypatch, "")
    monkeypatch.setattr(
        apply_service.le_apply,
        "wait_until_applied",
        lambda *_a, **_k: {
            "applied": False,
            "reason": "bridge reported FAIL: FAIL processed=1 ok=0 last=apply_1.lua",
            "done_file": None,
            "bridge_alive": True,
            "last_result": "FAIL processed=1 ok=0 last=apply_1.lua",
        },
    )
    r = apply_service.apply_lua("--x", stem="apply_1", wait=True)
    assert r.applied is False
    assert r.outcome == "error"


def test_outcome_from_wait_still_reports_healthy_states() -> None:
    applied = apply_service._outcome_from_wait({"applied": True}, was_live=True)
    queued = apply_service._outcome_from_wait(
        {"applied": False, "bridge_alive": True, "reason": "still waiting"},
        was_live=True,
    )
    blocked = apply_service._outcome_from_wait(
        {"applied": False, "bridge_alive": False, "reason": "still waiting"},
        was_live=False,
    )
    assert applied == "applied"
    assert queued == "queued_live"
    assert blocked == "blocked"


# ── web_api: ok must not be true for hard failures ──────────────────


_FAILED = ApplyResult(
    applied=False,
    queued=True,  # the failure branch of apply_lua really does set this
    live=True,
    reason="Worker ran the script but player id was NOT FOUND in the DB.",
    queue_file="queue/apply_1.lua",
    outcome="error",
)


@pytest.mark.parametrize("outcome", ["error", "blocked", "timeout"])
def test_ok_is_false_for_every_failed_outcome(outcome: str) -> None:
    r = ApplyResult(
        applied=False,
        queued=True,
        live=True,
        reason="failed",
        outcome=outcome,
    )
    assert web_api._ok_from_result(r) is False


def test_ok_stays_true_for_applied_and_queued_live() -> None:
    applied = ApplyResult(True, True, True, "ok", outcome="applied")
    queued = ApplyResult(False, True, True, "queued", outcome="queued_live")
    assert web_api._ok_from_result(applied) is True
    assert web_api._ok_from_result(queued) is True


def test_apply_card_reports_not_ok_for_failed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_api.product, "apply_card_to_target", lambda *_a, **_k: _FAILED
    )
    out = web_api.apply_card({"name": "X"}, target_playerid=1, wait=True)
    assert out["ok"] is False
    assert out["outcome"] == "error"
    assert out["applied"] is False
    assert "NOT FOUND" in out["reason"]


def test_run_profile_reports_not_ok_for_failed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_api.product, "run_profile_turbo", lambda *_a, **_k: _FAILED)
    out = web_api.run_profile("full_fitness", wait=True)
    assert out["ok"] is False
    assert out["outcome"] == "error"


def test_add_to_team_reports_not_ok_for_failed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_api.product, "add_card_to_user_team", lambda *_a, **_k: _FAILED
    )
    out = web_api.add_to_team({"name": "X"}, teamid=243, wait=True)
    assert out["ok"] is False
    assert out["outcome"] == "error"


def test_web_api_reports_not_ok_for_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    timed_out = ApplyResult(
        applied=False,
        queued=True,
        live=True,
        reason="Timed out — worker never drained the job",
        outcome="timeout",
    )
    monkeypatch.setattr(web_api.product, "run_profile_turbo", lambda *_a, **_k: timed_out)
    out = web_api.run_profile("full_fitness", wait=True)
    assert out["ok"] is False


# ── generated editor Lua: always reports, counts honestly ───────────


def _editor_lua() -> str:
    return player_apply.generate_apply_player_lua(
        {"name": "Test Card", "overallrating": 90, "acceleration": 88},
        158023,
        enabled_categories=["attributes"],
    )


class TestEditorLuaAlwaysReports:
    def test_missing_players_table_writes_a_status_line(self) -> None:
        lua = _editor_lua()
        head, _, tail = lua.partition("if players_table == nil then")
        assert tail, "players-table guard missing"
        block = tail.split("\nend\n", 1)[0]
        # The early return must not be reachable without a status write
        assert "write_status(" in block
        assert "reason=no_players_table" in block
        assert block.index("write_status(") < block.index("return")

    def test_every_return_path_is_preceded_by_a_status_write(self) -> None:
        lua = _editor_lua()
        for i, line in enumerate(lua.splitlines()):
            if line.strip() != "return":
                continue
            before = "\n".join(lua.splitlines()[:i])
            assert "write_status(" in before, f"return at line {i} reports nothing"

    def test_final_status_carries_reason_and_counters(self) -> None:
        lua = _editor_lua()
        assert "written=%d failed=%d" in lua
        assert "reason=%s" in lua
        assert "ok=%s" in lua

    def test_written_only_increments_on_a_successful_write(self) -> None:
        lua = _editor_lua()
        assert "local okw = pcall(function() players_table:SetRecordFieldValue" in lua
        idx = lua.index("written = written + 1")
        guard = lua[:idx].rsplit("\n", 3)[-3:]
        assert any("if okw then" in g for g in guard), lua[idx - 200 : idx]
        assert "failed = failed + 1" in lua

    def test_status_is_written_through_one_helper(self) -> None:
        lua = _editor_lua()
        # def + missing-table path + final path
        assert lua.count("write_status(") >= 3
        assert 'io.open(status_path, "wb")' in lua

    def test_generated_status_line_parses_as_a_failure_when_not_found(self) -> None:
        """Shape check: what the Lua formats must round-trip through the parser."""
        rendered = (
            "job=edit id=158023 ok=false found=false written=0 failed=0 devfailed=0 "
            "dev=false scanned=25000 names=false names_ok=true reason=not_found "
            "name=Test Card"
        )
        p = parse_job_status(rendered)
        assert p["failed"] is True
        assert p["written"] == 0

    def test_generated_status_line_parses_as_success_when_written(self) -> None:
        rendered = (
            "job=edit id=158023 ok=true found=true written=2 failed=0 devfailed=0 "
            "dev=true scanned=120 names=false names_ok=true reason=ok name=Test Card"
        )
        p = parse_job_status(rendered)
        assert p["failed"] is False
        assert p["written"] == 2


# ── bridge Lua: error text must escape the game ─────────────────────


def _bridge_text() -> str:
    from src import paths

    src = paths.bridge_source_path()
    if not src.is_file():
        pytest.skip("bridge source not found")
    return src.read_text(encoding="utf-8", errors="replace")


class TestBridgeCarriesErrorText:
    def test_run_source_returns_the_error_text(self) -> None:
        text = _bridge_text()
        assert "local function run_source(src, label)" in text
        assert 'return false, "run:" .. msg' in text
        assert 'return false, "load:" .. msg' in text
        assert "local ok, job_err = run_source(" in text

    def test_error_text_is_collapsed_and_truncated(self) -> None:
        text = _bridge_text()
        assert "local function collapse_err(e)" in text
        assert "local ERR_MAX = 200" in text
        assert 'gsub("%s+", "_")' in text
        assert "string.sub(s, 1, ERR_MAX)" in text

    def test_fail_result_line_carries_the_error(self) -> None:
        text = _bridge_text()
        assert "FAIL processed=%d ok=%d last=%s err=%s" in text

    def test_bridge_writes_a_job_status_when_the_job_died(self) -> None:
        text = _bridge_text()
        assert "local function write_job_error_status(q, name, err_text)" in text
        assert "reason=lua_error msg=%s" in text
        # Must never clobber a status the job itself wrote
        assert "if file_exists(status) then return false end" in text

    def test_bridge_error_paths_stay_defensive(self) -> None:
        text = _bridge_text()
        assert "pcall(function() write_job_error_status(q, name, last_err) end)" in text
        assert "err_note = collapse_err(err_outer)" in text


# ── boost_queue: absence from queue/ is not success ─────────────────


@pytest.fixture()
def boost_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    q = tmp_path / "queue"
    done = q / "done"
    done.mkdir(parents=True)
    monkeypatch.setattr(boost_queue.paths, "queue_dir", lambda: q)
    monkeypatch.setattr(boost_queue.le_apply, "queue_dir", lambda: q)
    monkeypatch.setattr(boost_queue.le_apply, "done_dir", lambda: done)
    monkeypatch.setattr(boost_queue.le_apply, "bridge_alive", lambda *_a, **_k: True)
    mq = boost_queue.BoostQueueManager()
    mq._stop = True  # no background writer/watcher racing the assertions
    time.sleep(0.05)
    return mq, q, done


def _queued_job(mq, q: Path, name: str = "boost_1.lua", *, gone_for: float = 60.0):
    job = boost_queue.BoostJob(
        id="j1",
        profile_id="full_fitness",
        label="Full fitness",
        status="queued",
        queue_file=str(q / name),
        ts=time.time() - 5.0,
    )
    # File already gone; grace window for the done/ artifact has expired
    job.gone_ts = time.time() - gone_for
    with mq._lock:
        mq._jobs = [job]
    return job


def test_skipped_artifact_is_not_applied(boost_env) -> None:
    """clear_stale_jobs archives as skipped_<ts>_<name> — the job never ran."""
    mq, q, done = boost_env
    job = _queued_job(mq, q)
    (done / f"skipped_{int(time.time())}_boost_1.lua").write_text("-- x", encoding="utf-8")
    mq._watch_once()
    assert job.status == "error"
    assert job.status != "applied"
    assert "without an LE artifact" in job.reason


def test_orphan_and_poison_artifacts_are_not_applied(boost_env) -> None:
    mq, q, done = boost_env
    for prefix in ("orphan_", "poison_"):
        job = _queued_job(mq, q)
        (done / f"{prefix}{int(time.time())}_boost_1.lua").write_text("-- x", encoding="utf-8")
        mq._watch_once()
        assert job.status == "error", prefix


def test_no_artifact_at_all_is_not_applied(boost_env) -> None:
    mq, q, _done = boost_env
    job = _queued_job(mq, q)
    mq._watch_once()
    assert job.status == "error"


def test_missing_artifact_gets_a_grace_window_before_failing(boost_env) -> None:
    mq, q, done = boost_env
    job = _queued_job(mq, q, gone_for=0.0)
    job.gone_ts = 0.0
    mq._watch_once()
    assert job.status == "queued"  # first sighting only records the time
    assert job.gone_ts > 0
    mq._watch_once()
    assert job.status == "queued"  # still inside the grace window
    # Artifact shows up late — success, not failure
    (done / "boost_1.lua").write_text("-- x", encoding="utf-8")
    mq._watch_once()
    assert job.status == "applied"


def test_real_bridge_artifact_is_applied(boost_env) -> None:
    mq, q, done = boost_env
    job = _queued_job(mq, q)
    (done / "boost_1.lua").write_text("-- x", encoding="utf-8")
    mq._watch_once()
    assert job.status == "applied"


def test_timestamped_bridge_artifact_is_applied(boost_env) -> None:
    """finish_job falls back to done/<os.time()>_<name> on collision."""
    mq, q, done = boost_env
    job = _queued_job(mq, q)
    (done / f"{int(time.time())}_boost_1.lua").write_text("-- x", encoding="utf-8")
    mq._watch_once()
    assert job.status == "applied"


def test_last_result_fail_vetoes_the_artifact(boost_env) -> None:
    mq, q, done = boost_env
    job = _queued_job(mq, q)
    (done / "boost_1.lua").write_text("-- x", encoding="utf-8")
    (q / "_last_result.txt").write_text(
        "FAIL processed=1 ok=0 last=boost_1.lua err=run:boost_1.lua:3:_bad\n",
        encoding="utf-8",
    )
    mq._watch_once()
    assert job.status == "error"
    assert "boost_1.lua" in job.reason


def test_last_result_ok_zero_vetoes_the_artifact(boost_env) -> None:
    mq, q, done = boost_env
    job = _queued_job(mq, q)
    (done / "boost_1.lua").write_text("-- x", encoding="utf-8")
    (q / "_last_result.txt").write_text(
        "OK processed=1 ok=0 last=boost_1.lua\n", encoding="utf-8"
    )
    mq._watch_once()
    assert job.status == "error"


def test_stale_fail_for_another_job_does_not_veto(boost_env) -> None:
    mq, q, done = boost_env
    job = _queued_job(mq, q)
    (done / "boost_1.lua").write_text("-- x", encoding="utf-8")
    (q / "_last_result.txt").write_text(
        "FAIL processed=1 ok=0 last=some_other.lua\n", encoding="utf-8"
    )
    mq._watch_once()
    assert job.status == "applied"


def test_bridge_artifact_helper_rejects_non_bridge_prefixes(tmp_path: Path) -> None:
    done = tmp_path / "done"
    done.mkdir()
    for name in ("skipped_1_boost_1.lua", "orphan_1_boost_1.lua", "poison_1_boost_1.lua"):
        (done / name).write_text("-- x", encoding="utf-8")
    assert boost_queue._bridge_artifact_for("boost_1.lua", done) is None
    (done / "boost_1.lua").write_text("-- x", encoding="utf-8")
    assert boost_queue._bridge_artifact_for("boost_1.lua", done) is not None


def test_write_marks_error_for_failed_and_timeout_outcomes(
    boost_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    mq, _q, _done = boost_env
    for outcome in ("error", "timeout"):
        job = boost_queue.BoostJob(
            id=f"j-{outcome}", profile_id="p", label="P", status="pending"
        )
        with mq._lock:
            mq._jobs = [job]
        monkeypatch.setattr(
            boost_queue.product,
            "run_profile_turbo",
            lambda *_a, **_k: ApplyResult(
                applied=False,
                queued=True,
                live=True,
                reason="job reported failure",
                queue_file="queue/p.lua",
                outcome=outcome,
            ),
        )
        mq._write_one(job)
        assert job.status == "error", outcome
