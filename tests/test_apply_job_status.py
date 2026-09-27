"""Job status parsing must fail add_to_team ok=false even if LE archived the job."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from src import apply_service
from src.apply_service import ApplyResult, parse_job_status


class TestParseJobStatus:
    def test_found_false_is_failed(self) -> None:
        p = parse_job_status("job=edit id=1 found=false written=0")
        assert p["found"] is False
        assert p["failed"] is True
        assert "NOT FOUND" in p["summary"] or "found" in p["summary"].lower()

    def test_add_to_team_ok_false_is_failed(self) -> None:
        p = parse_job_status(
            "job=add_to_team ok=false path=none id=0 team=243 transfer=false err=no_playerid"
        )
        assert p["ok"] is False
        assert p["failed"] is True
        assert p["job"] == "add_to_team"
        assert "FAILED" in p["summary"] or p["failed"]

    def test_add_to_team_ok_true_surfaces_path_and_id(self) -> None:
        p = parse_job_status(
            "job=add_to_team ok=true path=create id=460123 team=243 transfer=true err=ok name=X"
        )
        assert p["ok"] is True
        assert p["failed"] is False
        assert p["path"] == "create"
        assert p["player_id"] == 460123
        assert p["team_id"] == 243
        assert "path=create" in p["summary"]
        assert "id=460123" in p["summary"]

    def test_dummy_path_summary(self) -> None:
        p = parse_job_status(
            "job=add_to_team ok=true path=dummy id=99 team=10 transfer=true err=ok"
        )
        assert p["path"] == "dummy"
        assert p["player_id"] == 99
        assert not p["failed"]


def test_apply_lua_honors_ok_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shipped apply_lua: worker 'applied' + ok=false → outcome error, not applied."""
    q = tmp_path / "queue"
    q.mkdir()
    (q / "done").mkdir()
    job = q / "add_team_243.lua"
    job.write_text("-- job\n", encoding="utf-8")

    monkeypatch.setattr(apply_service.le_apply, "queue_dir", lambda: q)
    monkeypatch.setattr(apply_service.le_apply, "bridge_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(apply_service.le_apply, "clear_stale_jobs", lambda **_k: 0)

    def _write_lua(*_a, **_k):
        return {"queue_file": str(job)}

    monkeypatch.setattr(apply_service.actions, "write_lua", _write_lua)

    def _wait(*_a, **_k):
        # Simulate LE moving job (worker ran) while side-file says failure
        (q / "_job_status.txt").write_text(
            "job=add_to_team ok=false path=none id=0 team=243 transfer=false err=create_failed\n",
            encoding="utf-8",
        )
        return {
            "applied": True,
            "bridge_alive": True,
            "reason": "job file processed",
            "last_result": "OK processed=1",
        }

    monkeypatch.setattr(apply_service.le_apply, "wait_until_applied", _wait)
    monkeypatch.setattr(
        apply_service.le_apply,
        "job_status_text",
        lambda: (q / "_job_status.txt").read_text(encoding="utf-8").strip(),
    )

    r = apply_service.apply_lua("--x", stem="add_team_243", detail="test", wait=True)
    assert r.applied is False
    assert r.outcome == "error"
    assert r.meta.get("job_status_parsed", {}).get("failed") is True
    assert "path=" in r.reason or "FAILED" in r.reason or "create" in r.reason.lower()


def test_apply_lua_success_includes_path_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = tmp_path / "queue"
    q.mkdir()
    job = q / "add_ok.lua"
    job.write_text("-- job\n", encoding="utf-8")
    monkeypatch.setattr(apply_service.le_apply, "queue_dir", lambda: q)
    monkeypatch.setattr(apply_service.le_apply, "bridge_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(apply_service.le_apply, "clear_stale_jobs", lambda **_k: 0)
    monkeypatch.setattr(
        apply_service.actions, "write_lua", lambda *_a, **_k: {"queue_file": str(job)}
    )

    def _wait(*_a, **_k):
        (q / "_job_status.txt").write_text(
            "job=add_to_team ok=true path=create id=460555 team=243 transfer=true err=ok\n",
            encoding="utf-8",
        )
        return {
            "applied": True,
            "bridge_alive": True,
            "reason": "applied",
            "last_result": "OK",
        }

    monkeypatch.setattr(apply_service.le_apply, "wait_until_applied", _wait)
    monkeypatch.setattr(
        apply_service.le_apply,
        "job_status_text",
        lambda: (q / "_job_status.txt").read_text(encoding="utf-8").strip(),
    )
    r = apply_service.apply_lua("--x", stem="add_ok", wait=True)
    assert r.applied is True
    assert r.outcome == "applied"
    assert r.meta["job_status_parsed"]["path"] == "create"
    assert r.meta["job_status_parsed"]["player_id"] == 460555
    summary = apply_service.format_result_with_job_status(r)
    assert "path=create" in summary
    assert "id=460555" in summary


def test_present_apply_shows_path_id() -> None:
    from src.ui import apply_flow

    class App:
        def __init__(self) -> None:
            self.msgs = []

        def _set_apply_btn_text(self, t: str) -> None:
            pass

        def _set_apply_status(self, msg: str, **_k) -> None:
            self.msgs.append(msg)

        def _refresh_sync_chrome(self) -> None:
            pass

    r = ApplyResult(
        applied=True,
        queued=True,
        live=True,
        reason="applied · job=add_to_team · path=dummy · id=99 · team=10 · ok",
        last_result="OK",
        outcome="applied",
        detail="Add to team",
        meta={
            "job_status": "job=add_to_team ok=true path=dummy id=99 team=10",
            "job_status_parsed": parse_job_status(
                "job=add_to_team ok=true path=dummy id=99 team=10"
            ),
        },
    )
    app = App()
    apply_flow.present_apply_result(app, r, detail="Add to Real (243)", detailed=True)
    assert app.msgs
    assert "path=dummy" in app.msgs[-1] or "id=99" in app.msgs[-1]
