"""Core 2.6.40: releasing players from the club actually completes.

Two regressions, both seen in one save on 2026-10-17 where a 17-player
release released exactly one player per run and reported that one as failed:

* ``GetTeamIdFromPlayerId`` still answers with the old club in the same frame
  as ``ReleasePlayerFromTeam``, so the read-back turned a working release into
  ``verify_mismatch``; and
* the release raises a nested Career event on the worker's own thread, which
  revoked hub readiness and made the runner abandon every remaining op with
  ``career_event_interrupted``.

A loaned-out player is also handled now: Live Editor's own docs warn that
releasing one can damage the save, because the release moves him out of the
loan club rather than the parent club.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

from tests.v2.test_native_tick import DAY_PASSED, HUB, ULID_A, core  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "ingame" / "le_companion"
TEAM = 18
LOAN_CLUB = 1800


def _runtime():
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


HOST = r"""
SQUAD = {}            -- [pid] = true, what GetUserSeniorTeamPlayerIDs returns
CLUB_OF = {}          -- [pid] = teamid, what GetTeamIdFromPlayerId returns
LOANED = {}           -- [pid] = true
RELEASED = {}         -- [pid] = true, calls that reached the host
TERMINATED = {}       -- [pid] = true
STALE_GETTER = true   -- the club getter lags one Career event behind
HAS_SENIOR_HELPER = true
USER_TEAM = 18

GetUserTeamID = function() return USER_TEAM end
GetTeamIdFromPlayerId = function(pid) return CLUB_OF[pid] or -1 end
IsPlayerPresigned = function() return false end
IsPlayerLoanedOut = function(pid) return LOANED[pid] == true end

TerminateLoan = function(pid)
    TERMINATED[pid] = true
    LOANED[pid] = nil
    CLUB_OF[pid] = USER_TEAM
end

-- FC's behaviour: the squad helper drops the player at once, the club getter
-- keeps answering with the old club until the next Career event.
ReleasePlayerFromTeam = function(pid)
    RELEASED[pid] = true
    if LOANED[pid] then return end        -- LE's documented danger case
    SQUAD[pid] = nil
    if not STALE_GETTER then CLUB_OF[pid] = 111592 end
end

function set_senior_helper(on)
    HAS_SENIOR_HELPER = on
    if on then
        GetUserSeniorTeamPlayerIDs = function()
            local out = {}
            for pid in pairs(SQUAD) do out[pid] = true end
            return out
        end
    else
        GetUserSeniorTeamPlayerIDs = nil
    end
end
set_senior_helper(true)

function add_squad_player(pid, teamid)
    SQUAD[pid] = true
    CLUB_OF[pid] = teamid or USER_TEAM
end

function release(pid, extra)
    local op = { op = "transfer", id = "release-1", action = "release", playerid = pid }
    for k, v in pairs(extra or {}) do op[k] = v end
    return require("ops").handlers["transfer"](op, {}, {})
end
"""


@pytest.fixture
def host(tmp_path: Path):
    lua = _runtime()
    g = lua.globals()
    g.core_dir = _posix(CORE)
    lua.execute("package.path = core_dir .. '/?.lua;' .. package.path")
    lua.execute(HOST)
    # Teammates: an empty squad helper is "cannot tell", not "he left".
    lua.execute("for i = 1, 10 do add_squad_player(5000 + i, USER_TEAM) end")
    return lua


def _result(lua, pid: int, **extra) -> dict:
    lua.globals()._tmp = lua.globals().release(
        pid, lua.eval("require('json').decode([==[%s]==])" % json.dumps(extra)) if extra else None
    )
    return json.loads(lua.eval("require('json').encode(_tmp)"))


# ---------------------------------------------------------------------------
# read-back
# ---------------------------------------------------------------------------


def test_release_is_verified_when_only_the_club_getter_is_stale(host) -> None:
    host.globals().add_squad_player(4001, TEAM)
    res = _result(host, 4001)
    assert res["ok"] is True
    assert res["data"]["verified"] is True
    # The squad helper decided it, not the getter that still says 18.
    assert res["data"]["verify_source"] == "senior_squad"
    assert res["data"]["teamid"] == TEAM
    assert host.eval("RELEASED[4001]") is True


def test_release_is_verified_the_cheap_way_when_the_getter_keeps_up(host) -> None:
    host.globals().add_squad_player(4002, TEAM)
    host.execute("STALE_GETTER = false")
    res = _result(host, 4002)
    assert res["ok"] is True
    assert res["data"]["verified"] is True
    assert res["data"]["verify_source"] == "host"


def test_a_release_that_changed_nothing_is_still_a_failure(host) -> None:
    host.globals().add_squad_player(4003, TEAM)
    # The host call happens but neither source moves.
    host.execute("ReleasePlayerFromTeam = function(pid) RELEASED[pid] = true end")
    res = _result(host, 4003)
    assert res["ok"] is False
    assert res["reason"] == "verify_mismatch"
    assert "still in squad" in res["detail"]


def test_without_a_squad_helper_a_stale_getter_is_unverified_not_applied(host) -> None:
    host.globals().add_squad_player(4004, TEAM)
    host.globals().set_senior_helper(False)
    res = _result(host, 4004)
    assert res["ok"] is False
    # Honest "cannot tell", never a silent pass.
    assert res["reason"] == "unverified_side_effect"


def test_release_from_another_club_still_uses_the_club_getter(host) -> None:
    """A player who is not ours cannot be checked against our squad."""
    host.globals().add_squad_player(4005, 241)
    host.execute("SQUAD[4005] = nil")
    host.execute("STALE_GETTER = false")
    res = _result(host, 4005)
    assert res["ok"] is True
    assert res["data"]["verify_source"] == "host"


# ---------------------------------------------------------------------------
# loaned-out players
# ---------------------------------------------------------------------------


def test_a_loaned_out_player_has_his_loan_ended_before_the_release(host) -> None:
    host.globals().add_squad_player(4006, LOAN_CLUB)
    host.execute("LOANED[4006] = true")
    res = _result(host, 4006)
    assert host.eval("TERMINATED[4006]") is True
    assert res["ok"] is True
    assert res["data"]["cleared"]["loan"] is True
    # The club to beat is the parent club he returned to, not the loan club.
    assert res["data"]["before_teamid"] == TEAM
    assert res["data"]["verified"] is True


def test_clear_loan_false_refuses_to_pretend_a_loaned_release_worked(host) -> None:
    host.globals().add_squad_player(4007, LOAN_CLUB)
    host.execute("LOANED[4007] = true")
    res = _result(host, 4007, clear_loan=False)
    assert host.eval("TERMINATED[4007]") is None
    assert res["ok"] is False
    assert res["reason"] in ("verify_mismatch", "unverified_side_effect")


# ---------------------------------------------------------------------------
# the runner: a job's own nested Career event must not end the job
# ---------------------------------------------------------------------------


NESTED = """
    fired = 0
    local real = LEC.ops.handlers["diag.ping"]
    LEC.ops.handlers["diag.ping"] = function(op, job, ctx)
        -- Exactly what ReleasePlayerFromTeam does: a Career event lands on
        -- this thread, inside our own host call.
        fired = fired + 1
        handlers["post__CareerModeEvent"](nil, %d)
        return real(op, job, ctx)
    end
"""


def _ping_job(core, job_id: str, n: int) -> None:  # noqa: F811
    body = {
        "schema": 3,
        "job_id": job_id,
        "label": "nested",
        "budget_ms": 2000,
        "ops": [{"op": "diag.ping", "id": f"p{i}"} for i in range(1, n + 1)],
    }
    (core.queue / "jobs" / f"{job_id}.json").write_text(json.dumps(body), encoding="utf-8")


def _result_file(core, job_id: str) -> dict:  # noqa: F811
    return json.loads((core.queue / "results" / f"{job_id}.json").read_text(encoding="utf-8"))


def test_a_nested_career_event_does_not_abandon_the_rest_of_the_job(core) -> None:  # noqa: F811
    core.run(NESTED % DAY_PASSED)
    _ping_job(core, ULID_A, 3)
    core.hub(200.0)
    result = _result_file(core, ULID_A)
    assert core.eval("fired") == 3
    assert len(result["ops"]) == 3
    assert result["ok"] is True
    assert result["state"] == "done"
    assert "career_event_interrupted" not in json.dumps(result["failures"])


def test_a_nested_event_still_marks_caches_and_the_mirror_stale(core) -> None:  # noqa: F811
    core.run(NESTED % DAY_PASSED)
    _ping_job(core, ULID_A, 1)
    core.hub(200.0)
    assert core.eval("LEC.runner._cache_dirty") is True
    assert core.eval("LEC.runner._nested_events") == 1
    # Readiness is untouched: nothing but our own call can reach this thread.
    assert core.eval("LEC.runner._hub_ready") is True
    assert core.eval("LEC.runner._simulating") is False


def test_a_real_simulation_event_outside_a_job_still_pauses_the_worker(core) -> None:  # noqa: F811
    core.hub(200.0)
    assert core.eval("LEC.runner._hub_ready") is True
    core.pre(DAY_PASSED)
    assert core.eval("LEC.runner._hub_ready") is False
    assert core.eval("LEC.runner._simulating") is True
    assert core.eval("LEC.runner.pump('force')") == "sim_paused"


def test_a_nested_hub_event_during_a_job_is_still_refused(core) -> None:  # noqa: F811
    core.run(NESTED % HUB)
    _ping_job(core, ULID_A, 2)
    core.hub(200.0)
    result = _result_file(core, ULID_A)
    assert len(result["ops"]) == 2
    assert result["ok"] is True
