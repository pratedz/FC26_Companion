"""Durable offline retest of FC26_Companion shipped entry points.

Drives real ``app_entry_v2`` / ``companion.cli`` and domain builders — not mocks
of the thing under test. Live game apply is out of scope (see plan non-goals).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from companion.domain.job import KNOWN_OPS, JobValidationError, job_ping
from companion.domain.pack_library import (
    QUICK_RAIL_IDS,
    actions as pack_actions,
    build_job as build_pack_job,
    load_v1_packs,
    quick_rail,
)
from companion.domain.profile_library import actions as profile_actions, build_job as build_profile_job
from companion.domain.teams import search_clubs

ROOT = Path(__file__).resolve().parents[2]


def _test_root(tmp_path: Path) -> Path:
    """Give subprocess CLI tests an isolated queue and state database."""
    shutil.copy2(ROOT / "profiles.json", tmp_path / "profiles.json")
    return tmp_path


def _run_entry(*args: str, timeout: float = 60.0, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    root_args = ("--root", str(root)) if root is not None else ()
    return subprocess.run(
        [sys.executable, "app_entry_v2.py", *root_args, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _run_module(*args: str, timeout: float = 60.0, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    root_args = ("--root", str(root)) if root is not None else ()
    return subprocess.run(
        [sys.executable, "-m", "companion.cli", *root_args, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _json_stdout(proc: subprocess.CompletedProcess[str]) -> dict:
    assert "Traceback" not in (proc.stdout + proc.stderr), proc.stderr or proc.stdout
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
    return data


def test_app_entry_version_reports_app_protocol_core():
    proc = _run_entry("version")
    assert proc.returncode == 0
    data = _json_stdout(proc)
    assert data.get("app")
    assert data.get("protocol") == 3
    assert data.get("core_expected")


def test_module_cli_version_matches_entry():
    a = _json_stdout(_run_entry("version"))
    b = _json_stdout(_run_module("version"))
    assert a["app"] == b["app"]
    assert a["protocol"] == b["protocol"]
    assert a["core_expected"] == b["core_expected"]


def test_doctor_returns_structured_health_without_traceback():
    proc = _run_entry("doctor")
    # Exit 1 is OK when the in-game worker is not armed (no FC26 session).
    assert proc.returncode in (0, 1)
    data = _json_stdout(proc)
    assert "healthy" in data
    assert isinstance(data.get("checks"), list) and data["checks"]
    assert "bridge" in data
    names = {c["name"] for c in data["checks"]}
    assert "Queue directory" in names
    assert "Card catalog" in names


def test_status_returns_liveness_fields_not_crash():
    proc = _run_entry("status")
    assert proc.returncode in (0, 1)
    data = _json_stdout(proc)
    assert "pill" in data
    assert "armed" in data
    assert "message" in data
    assert "queue" in data


def test_profiles_and_packs_list_sane_json():
    profiles = _json_stdout(_run_entry("profiles"))
    assert profiles["count"] >= 1
    assert isinstance(profiles["profiles"], list)
    assert all("id" in p and "mode" in p for p in profiles["profiles"])

    packs = _json_stdout(_run_entry("packs"))
    assert packs["count"] >= 1
    assert isinstance(packs["packs"], list)
    assert all("id" in p and "mode" in p for p in packs["packs"])


def test_search_cards_and_clubs_offline():
    cards = _json_stdout(_run_entry("search-cards", "messi", "--limit", "3"))
    assert cards["count"] >= 1
    assert cards["results"]

    clubs = _json_stdout(_run_entry("search-clubs", "barcelona", "--limit", "3"))
    assert clubs["count"] >= 1
    assert clubs["clubs"]

    best = _json_stdout(_run_entry("best-match", "Messi", "--limit", "3"))
    assert best["count"] >= 1


def test_queue_sweep_force_drain_offline(tmp_path):
    root = _test_root(tmp_path)
    queue = _json_stdout(_run_entry("queue", root=root))
    assert "pending" in queue and "claimed" in queue

    sweep = _json_stdout(_run_entry("sweep", root=root))
    assert "swept" in sweep and "count" in sweep

    drain = _run_entry("force-drain", root=root)
    assert drain.returncode == 0
    assert "Traceback" not in drain.stdout + drain.stderr
    assert len(drain.stdout.strip()) > 10


def test_pending_profile_refuses_without_building_fake_job(tmp_path):
    proc = _run_entry("run-profile", "extend_contracts", "--no-wait", root=_test_root(tmp_path))
    assert proc.returncode == 69
    assert "not available" in proc.stderr.lower() or "not available" in proc.stdout.lower()


def test_whole_save_profile_is_safely_withheld(tmp_path):
    refused = _run_entry("run-profile", "never_retire", "--no-wait", root=_test_root(tmp_path))
    assert refused.returncode == 69
    assert "whole-save" in (refused.stderr + refused.stdout).lower()


def test_every_non_pending_profile_builds_known_ops():
    raw = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
    for item in profile_actions(raw):
        if item.mode == "pending":
            with pytest.raises(JobValidationError):
                build_profile_job(item.id)
            continue
        job = build_profile_job(item.id)
        assert job.ops, f"{item.id} built zero ops"
        for op in job.ops:
            assert op.op in KNOWN_OPS, f"{item.id} unknown op {op.op}"
        job.validate()


def test_every_non_pending_listed_pack_builds_known_ops():
    for item in pack_actions(load_v1_packs()):
        if item.mode == "pending":
            with pytest.raises(JobValidationError):
                build_pack_job(item.id)
            continue
        job = build_pack_job(item.id)
        assert job.ops, f"{item.id} built zero ops"
        for op in job.ops:
            assert op.op in KNOWN_OPS, f"{item.id} unknown op {op.op}"
        job.validate()


def test_quick_rail_ids_resolve_and_pre_match_builds():
    """UI rail and CLI share the same inventory for rail aliases like pre_match."""
    rail = {p.id: p for p in quick_rail()}
    for pid in QUICK_RAIL_IDS:
        assert pid in rail, f"quick rail missing {pid}"
    job = build_pack_job("pre_match")
    assert job.ops
    assert all(op.op in KNOWN_OPS for op in job.ops)


def test_cli_packs_and_run_pack_include_pre_match_alias(tmp_path):
    """Workflow-only rail aliases must appear in packs list and accept run-pack.

    pre_match lives in _WORKFLOW (not product.PACKS); actions() merges it so CLI
    and Automations quick rail stay on one inventory.
    """
    listed = {p.id: p for p in pack_actions(load_v1_packs())}
    assert "pre_match" in listed
    assert listed["pre_match"].mode == "native"
    assert "pre_match_full" in listed
    packs = _json_stdout(_run_entry("packs"))
    assert any(p["id"] == "pre_match" for p in packs["packs"])
    proc = _run_entry("run-pack", "pre_match", "--no-wait", root=_test_root(tmp_path))
    assert proc.returncode == 0, proc.stderr or proc.stdout
    data = _json_stdout(proc)
    assert data.get("state") == "queued"
    assert data.get("pack") == "pre_match"


def test_job_ping_wire_contract():
    job = job_ping(label="retest", origin="test.retest")
    wire = job.to_wire()
    assert wire["schema"] == 3
    assert wire["ops"][0]["op"] == "diag.ping"
    assert wire["job_id"]


def test_team_search_function_matches_cli():
    db = ROOT / "card_db" / "teams.sqlite"
    if not db.is_file():
        pytest.skip("teams.sqlite missing")
    hits = search_clubs(db, "arsenal", limit=2)
    assert hits
    cli = _json_stdout(_run_entry("search-clubs", "arsenal", "--limit", "2"))
    assert cli["count"] >= 1


def test_python_known_ops_match_lua_handlers_and_versions():
    import re

    ver = (ROOT / "ingame" / "le_companion" / "version.lua").read_text(encoding="utf-8")
    ops_lua = (ROOT / "ingame" / "le_companion" / "ops.lua").read_text(encoding="utf-8")
    block = re.search(r"version\.OP_VERSIONS\s*=\s*\{([^}]+)\}", ver, re.S)
    assert block, "OP_VERSIONS missing from version.lua"
    lua_ops = set(re.findall(r'\["([^"]+)"\]', block.group(1)))
    handlers = set(re.findall(r'ops\.handlers\["([^"]+)"\]', ops_lua))
    assert lua_ops == set(KNOWN_OPS)
    assert handlers == set(KNOWN_OPS)
