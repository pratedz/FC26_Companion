"""Offline v3 path: Python builders + FileTransport + offline drain twin.

Proves claim → result for diag.ping (and set_fields / export_squad envelopes)
without a live FC 26 process. Also structurally asserts the shipped Lua core.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from companion.core.clock import FakeClock
from companion.core.offline_core import arm_session, drain_once
from companion.core.paths import TempAppPaths
from companion.core.transport.jobfile import list_job_ids, read_json
from companion.core.transport.v3 import FileTransport
from companion.domain.job import (
    KNOWN_OPS,
    Job,
    job_apply_player,
    job_ping,
    op_bulk_edit,
    op_career_set,
    op_export_squad,
    op_set_fields,
)
from companion.domain.outcome import ApplyOutcome, JobResult
from companion.platform import le_install

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "ingame" / "le_companion"


def test_shipped_core_modules_present():
    required = [
        "init.lua",
        "version.lua",
        "util.lua",
        "json.lua",
        "log.lua",
        "events.lua",
        "status.lua",
        "db.lua",
        "ops.lua",
        "runner.lua",
    ]
    for name in required:
        path = CORE / name
        assert path.is_file(), f"missing core module {name}"
    init = (CORE / "init.lua").read_text(encoding="utf-8")
    assert "function LEC.arm" in init or "function LEC.arm()" in init or "LEC.arm" in init
    assert "configure" in init
    # SI-6: arm must not call pump/drain
    arm_block = init.split("function LEC.arm")[1].split("function LEC.force_drain")[0]
    assert "runner.pump" not in arm_block
    assert "force_drain" not in arm_block or "ForceDrain" in arm_block  # export ok
    runner = (CORE / "runner.lua").read_text(encoding="utf-8")
    assert "util.claim" in runner
    assert "write_result" in runner or "status.write_result" in runner
    ops = (CORE / "ops.lua").read_text(encoding="utf-8")
    for op in ("diag.ping", "set_fields", "export_squad"):
        assert op in ops
    assert "unsupported_op" in ops
    version = (CORE / "version.lua").read_text(encoding="utf-8")
    for op in KNOWN_OPS:
        assert f'["{op}"]' in version or f'["{op}"]' in version.replace(" ", "")


def test_autorun_stub_never_patches_live_editor_or_drains():
    stub = le_install._autorun_stub(
        scripts_dir=Path("C:/le/lua/scripts"),
        core_dir=Path("C:/le/lua/scripts/le_companion"),
        queue_dir=Path("C:/app/queue"),
    )
    assert "le_companion.init" in stub
    assert "SAFE_ARM" in stub or "__LE_COMPANION_SAFE_ARM" in stub
    assert "force_drain" not in stub.lower() or "ForceDrain" not in stub
    assert "runner.pump" not in stub
    assert 'cfg_text:find(\'"add_to_team"\'' in stub
    assert "core_ops = CORE_OPS" in stub
    # Never load/patch live_editor.lua
    assert 'require("live_editor' not in stub.replace("'", '"')
    assert "dofile" not in stub or "live_editor" not in stub


def test_submit_writes_valid_wire_and_index(tmp_path):
    paths = TempAppPaths(tmp_path)
    clock = FakeClock(start=1_700_000_000.0)
    transport = FileTransport(paths, clock=clock)

    jid = transport.submit(job_ping(label="wire ping", origin="test"))
    wire = read_json(paths.jobs / f"{jid}.json")
    assert wire is not None
    assert wire["schema"] == 3
    assert wire["job_id"] == jid
    assert len(jid) == 26
    assert wire["ops"][0]["op"] == "diag.ping"

    index = json.loads((paths.jobs / "index.json").read_text(encoding="utf-8"))
    assert f"{jid}.json" in index

    # set_fields + export_squad envelopes
    j2 = transport.submit(
        Job(
            ops=(
                op_set_fields("stats", key_value=158023, fields={"overallrating": 94}),
                op_export_squad("squad"),
            ),
            label="apply+export",
            origin="test",
        )
    )
    w2 = read_json(paths.jobs / f"{j2}.json")
    assert w2["ops"][0]["op"] == "set_fields"
    assert w2["ops"][1]["op"] == "export_squad"
    assert set(list_job_ids(paths.jobs)) == {jid, j2}


def test_concurrent_submits_publish_a_complete_job_index(tmp_path):
    """A late index writer must never hide another process's queued job."""
    paths = TempAppPaths(tmp_path)

    def submit_one(number: int) -> str:
        return FileTransport(paths).submit(job_ping(label=f"race {number}", origin="test"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit_one, range(24)))

    index = json.loads((paths.jobs / "index.json").read_text(encoding="utf-8"))
    assert set(index) == {f"{job_id}.json" for job_id in ids}
    assert set(list_job_ids(paths.jobs)) == set(ids)


def test_offline_drain_diag_ping_terminal_result(tmp_path):
    paths = TempAppPaths(tmp_path)
    clock = FakeClock(start=1_700_000_000.0)
    transport = FileTransport(paths, clock=clock, pid_probe=lambda _pid: True)

    sess = arm_session(paths.queue, session_id="testsession", le_pid=12345)
    assert sess["core_version"]
    assert (paths.session_file).is_file()

    jid = transport.submit(job_ping(label="offline ping", origin="test.offline"))
    summary = drain_once(paths.queue, session_id="testsession")
    assert summary["jobs_seen"] == 1
    assert summary["jobs_done"] == 1

    raw = read_json(paths.results / f"{jid}.json")
    assert raw is not None
    assert raw["schema"] == 3
    assert raw["job_id"] == jid
    assert raw["ok"] is True
    assert raw["state"] == "done"
    assert raw["ops"][0]["op"] == "diag.ping"
    assert raw["ops"][0]["ok"] is True
    assert raw["ops"][0]["data"]["core_version"]

    result = JobResult.from_wire(raw)
    # Ping is a successful zero-side-effect op. OpResult may map ok+no-writes
    # to NO_OP; both APPLIED and NO_OP are success terminals.
    assert result.outcome.is_success
    assert result.ok
    assert result.job_id == jid

    # Job left jobs/ (claimed cleaned)
    assert jid not in list_job_ids(paths.jobs)
    assert (paths.drain_file).is_file()

    lv = transport.liveness()
    assert lv.armed is True


def test_offline_set_fields_and_export_honest(tmp_path):
    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="s2", le_pid=1)

    jid = transport.submit(
        job_apply_player(158023, {"overallrating": 94}, label="messi ovr")
    )
    jid2 = transport.submit(
        Job(ops=(op_export_squad("squad"),), label="export", origin="test")
    )
    summary = drain_once(paths.queue, session_id="s2")
    assert summary["jobs_seen"] == 2

    r1 = JobResult.from_wire(read_json(paths.results / f"{jid}.json"))
    # Offline without DB → failed/no_op path, not a silent success lie
    assert r1.outcome.is_terminal
    assert r1.outcome is not ApplyOutcome.APPLIED
    assert r1.outcome in (
        ApplyOutcome.REJECTED,
        ApplyOutcome.FAILED,
        ApplyOutcome.NO_OP,
        ApplyOutcome.PARTIAL,
    )

    r2 = JobResult.from_wire(read_json(paths.results / f"{jid2}.json"))
    assert r2.outcome.is_terminal
    assert r2.outcome is not ApplyOutcome.APPLIED


def test_install_includes_full_core(tmp_path):
    data = tmp_path / "le_data"
    (data / "lua" / "autorun").mkdir(parents=True)
    (data / "lua" / "scripts").mkdir(parents=True)
    queue = tmp_path / "queue"
    queue.mkdir()
    report = le_install.install(queue_dir=queue, dry_run=False, data_dir=data)
    assert report["ok"] is True
    dest = data / "lua" / "scripts" / "le_companion"
    assert (dest / "init.lua").is_file()
    assert (dest / "runner.lua").is_file()
    assert (dest / "ops.lua").is_file()
    assert (dest / "config.json").is_file()


# ---------------------------------------------------------------------------
# Skeptic-panel regressions: false success / whole-DB bulk / ops_ok double-count
# ---------------------------------------------------------------------------


def test_runner_lua_never_remerges_ops_ok_counts():
    """Shipped Lua must skip ops_ok/ops_failed/ops_total when merging op counts.

    A bare re-add of res.counts made diag.ping report ops_ok=2 for a 1-op job.
    """
    runner = (CORE / "runner.lua").read_text(encoding="utf-8")
    assert 'k ~= "ops_ok"' in runner or "k ~= 'ops_ok'" in runner
    assert "ops_failed" in runner
    assert "ops_total" in runner
    # The merge loop must exclude them, not just mention them elsewhere.
    assert "Never re-merge ops_ok" in runner or 'k ~= "ops_ok"' in runner
    # diag.ping handler must not put ops_ok in its counts table.
    ops = (CORE / "ops.lua").read_text(encoding="utf-8")
    ping_block = ops.split('ops.handlers["diag.ping"]')[1].split("ops.handlers[")[0]
    assert "ops_ok = 1" not in ping_block
    assert "{ ops_ok" not in ping_block
    assert "ops_ok=" not in ping_block.replace(" ", "")


def test_offline_ping_ops_ok_equals_ops_total_not_double(tmp_path):
    """1-op diag.ping → counts.ops_ok == 1 == ops_total (never 2)."""
    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="ops_ok", le_pid=1)
    jid = transport.submit(job_ping(label="count check"))
    drain_once(paths.queue, session_id="ops_ok")
    raw = read_json(paths.results / f"{jid}.json")
    assert raw is not None
    assert raw["ok"] is True
    counts = raw["counts"]
    assert counts["ops_total"] == 1
    assert counts["ops_ok"] == 1
    assert counts["ops_failed"] == 0
    assert len(raw["ops"]) == 1


def test_offline_core_enforces_manual_deadline_and_force(tmp_path):
    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="manual", le_pid=1)
    jid = transport.submit(Job(
        ops=(op_set_fields(
            "dry", key_value=1, fields={"overallrating": 1},
        ),),
        require_cm=False,
        dry_run=True,
        deadline_kind="manual",
    ))

    first = drain_once(paths.queue, session_id="manual")
    assert first["jobs_deferred"] == 1
    assert jid in list_job_ids(paths.jobs)
    assert not (paths.results / f"{jid}.json").exists()

    second = drain_once(paths.queue, session_id="manual", force=True)
    assert second["jobs_seen"] == 1
    assert (paths.results / f"{jid}.json").exists()


def test_offline_core_rejects_expired_and_save_locked_jobs(tmp_path):
    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="gates", le_pid=1)

    expired = transport.submit(Job(
        ops=(op_set_fields(
            "dry", key_value=1, fields={"overallrating": 1},
        ),),
        require_cm=False,
        dry_run=True,
        expires_at=1,
    ))
    save_locked = transport.submit(Job(
        ops=(op_set_fields(
            "dry", key_value=1, fields={"overallrating": 1},
        ),),
        require_cm=False,
        dry_run=True,
        requires={"save_uid": "SAVE-A"},
    ))
    drain_once(paths.queue, session_id="gates")
    expired_raw = read_json(paths.results / f"{expired}.json")
    locked_raw = read_json(paths.results / f"{save_locked}.json")
    assert expired_raw["failures"][0]["reason"] == "expired"
    assert locked_raw["failures"][0]["reason"] == "save_uid_unavailable"


def test_bulk_edit_empty_where_refused_offline_and_in_lua_source(tmp_path):
    """Empty where on players must not match the whole DB — refuse need_where."""
    ops = (CORE / "ops.lua").read_text(encoding="utf-8")
    assert "need_where" in ops
    assert "user_team" in ops
    assert "never the whole players table" in ops or "need_where" in ops

    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="bulk1", le_pid=1)

    # Wire with empty where, no scope — the shape automations used to emit.
    bad = Job(
        ops=(
            op_bulk_edit(
                "boost",
                set_fields={"acceleration": 85, "sprintspeed": 85},
            ),
        ),
        label="unsafe bulk",
        origin="test.bulk_empty",
        require_cm=False,
    )
    # op_bulk_edit with no where/scope still builds; core must refuse at execute.
    wire = bad.to_wire(now=1)
    assert "where" not in wire["ops"][0] or not wire["ops"][0].get("where")
    assert wire["ops"][0].get("scope") is None

    jid = transport.submit(bad)
    drain_once(paths.queue, session_id="bulk1")
    raw = read_json(paths.results / f"{jid}.json")
    assert raw is not None
    assert raw["ok"] is False
    reasons = [f.get("reason") for f in (raw.get("failures") or [])]
    op_reasons = [o.get("reason") for o in (raw.get("ops") or []) if not o.get("ok")]
    assert "need_where" in reasons or "need_where" in op_reasons or any(
        "need_where" in str(x) for x in (reasons + op_reasons + [raw.get("error")])
    )


def test_bulk_edit_user_team_scope_on_wire_and_honest_offline(tmp_path):
    """User-team bulk_edit is scoped; offline fails no_db not silent success."""
    boost = Job(
        ops=(
            op_bulk_edit(
                "boost",
                scope="user_team",
                set_fields={
                    "acceleration": 85,
                    "sprintspeed": 85,
                    "stamina": 90,
                    "strength": 80,
                },
            ),
        ),
        label="Full Squad Boost",
        origin="ui.automations.squad_boost",
        require_cm=False,
    )
    wire = boost.to_wire(now=1)
    assert wire["ops"][0]["scope"] == "user_team"

    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="bulk2", le_pid=1)
    jid = transport.submit(boost)
    drain_once(paths.queue, session_id="bulk2")
    raw = read_json(paths.results / f"{jid}.json")
    assert raw["ok"] is False  # offline: no_db / no_team — never silent success
    assert raw["state"] in ("failed", "rejected", "partial")


def test_phase5_bulk_and_growth_use_v2_contracts():
    from companion.domain.job import op_growth_sync

    bulk = op_bulk_edit(
        "bulk",
        scope="user_team",
        set_fields={"acceleration": 90},
        growth_mirror="auto",
    ).to_wire()
    growth = op_growth_sync("growth", scope="user_team").to_wire()
    assert bulk["v"] == 2
    assert bulk["growth_mirror"] == "auto"
    assert growth["v"] == 2
    assert growth["scope"] == "user_team"

    source = (CORE / "ops.lua").read_text(encoding="utf-8")
    assert "growth_sync full team pass requires live index" not in source
    assert "growth_sync is limited to 60 players per job" in source
    assert "growth_no_plan" in source


def test_growth_mirror_covers_canonical_defensive_awareness_field():
    source = (CORE / "ops.lua").read_text(encoding="utf-8")
    assert "defensiveawareness=true" in source


def test_whole_save_bulk_edit_is_explicitly_refused_in_core_source():
    source = (CORE / "ops.lua").read_text(encoding="utf-8")
    assert 'scope == "all_players"' in source
    assert "unsafe_scope" in source


def test_career_set_team_scope_no_false_success(tmp_path):
    """Match Ready style career.set without playerid must not return ok offline.

    Lua source must refuse no_playerid / no_team when scope needs a team and
    no playerid is present — never ok with empty applied.
    """
    ops = (CORE / "ops.lua").read_text(encoding="utf-8")
    career_block = ops.split('ops.handlers["career.set"]')[1].split("ops.handlers[")[0]
    assert "no_playerid" in career_block or "no_team" in career_block
    assert "user_senior_team" in career_block
    # Must not return ok_result with empty applied when targets unresolved.
    assert "empty_team" in career_block or "no_team" in career_block

    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    arm_session(paths.queue, session_id="career1", le_pid=1)

    job = Job(
        ops=(
            op_career_set(
                "pack",
                scope="user_senior_team",
                fitness=100,
                form=100,
                morale=100,
                sharpness=100,
            ),
        ),
        label="Match Ready",
        origin="ui.automations.match_ready",
        require_cm=False,
    )
    wire = job.to_wire(now=1)
    assert wire["ops"][0]["op"] == "career.set"
    assert wire["ops"][0]["scope"] == "user_senior_team"
    assert "playerid" not in wire["ops"][0]

    jid = transport.submit(job)
    drain_once(paths.queue, session_id="career1")
    raw = read_json(paths.results / f"{jid}.json")
    assert raw is not None
    assert raw["ok"] is False, f"false success: {raw}"
    assert raw["state"] != "done"
    # Explicit reason — not a silent empty applied
    blob = json.dumps(raw)
    assert "no_team" in blob or "no_playerid" in blob or "unsupported_op" in blob


def test_automations_squad_boost_emits_scope_not_empty_where():
    """Unified pack rail: no empty-where bulk; squad_boost is product career pack."""
    from companion.domain.pack_library import build_job, quick_rail

    src = (ROOT / "companion" / "ui" / "surfaces" / "automations.py").read_text(
        encoding="utf-8"
    )
    assert "where={}" not in src
    assert "core scopes to user team when empty" not in src
    assert "quick_rail" in src
    rail_ids = {p.id for p in quick_rail()}
    assert "squad_boost" in rail_ids and "ping" in rail_ids
    wire = build_job("squad_boost").to_wire()
    # Product pack semantics: career fitness pack, not whole-DB bulk.
    assert wire["ops"][0]["op"] == "career.set"
    assert wire["ops"][0].get("scope") == "user_senior_team"


def test_automations_build_source_no_undefined_k():
    """Regression: no bare loop variable in kind=; rail uses pack_library only."""
    import inspect

    from companion.domain.pack_library import QUICK_RAIL_IDS
    from companion.ui.surfaces import automations

    src = inspect.getsource(automations.build)
    assert "or k in" not in src
    assert "_PACKS" not in src
    assert "quick_rail" in src
    assert "ping" in QUICK_RAIL_IDS and "export_squad" in QUICK_RAIL_IDS
