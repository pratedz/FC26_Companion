"""Squad Planner multi-edit + Grok template (proposal-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from companion.app.commands.squad_plan import confirm_squad_apply
from companion.domain.builds import preset
from companion.domain.job import JobValidationError
from companion.domain.squad_plan import (
    MAX_PLAYERS,
    build_apply_job,
    plan_from_rows,
    selection_revision,
    template_from_fields,
)
from companion.integrations import grok


ROOT = Path(__file__).parents[2]


def _rows(*ids_names: tuple[int, str, str]) -> list[dict]:
    return [
        {
            "id": pid,
            "name": name,
            "pos": pos,
            "_raw": {
                "playerid": pid,
                "name": name,
                "position": pos,
                "overallrating": 80,
                "acceleration": 70,
                "sprintspeed": 72,
                "defensiveawareness": 75,
            },
        }
        for pid, name, pos in ids_names
    ]


def test_plan_from_rows_and_selection_revision():
    plan = plan_from_rows(_rows((1, "A", "CB"), (2, "B", "CB"), (1, "A", "CB")))
    assert plan.player_count == 2
    assert plan.playerids == (1, 2)
    assert plan.selection_revision == selection_revision(plan.members)
    assert plan.field_count == 0


def test_template_preset_expands_matrix_and_build_job():
    plan = plan_from_rows(_rows((10, "Van Dijk", "CB"), (11, "Saliba", "CB")))
    plan = plan.with_template(preset("cb_wall"))
    assert plan.field_count >= 5
    assert plan.source == "preset"
    rows = plan.matrix_rows()
    assert any(r["playerid"] == 10 and r["field"] == "defensiveawareness" for r in rows)
    job = build_apply_job(plan)
    wire = job.to_wire()
    assert len(wire["ops"]) == 2
    assert all(o["op"] == "set_fields" for o in wire["ops"])
    assert wire["ops"][0]["key"]["value"] == 10
    assert "defensiveawareness" in wire["ops"][0]["fields"]
    assert wire["ops"][0]["growth_mirror"] == "auto"


def test_manual_template_and_blast_summary():
    plan = plan_from_rows(_rows((5, "X", "CM"),))
    prop = template_from_fields(
        {"acceleration": 90, "stamina": 88},
        source="manual",
        label="manual",
    )
    plan = plan.with_template(prop)
    assert plan.template["acceleration"] == 90
    assert "1 player" in plan.blast_summary()


def test_empty_selection_and_empty_template_refused():
    with pytest.raises(JobValidationError):
        plan_from_rows([])
    plan = plan_from_rows(_rows((1, "A", "ST"),))
    with pytest.raises(JobValidationError):
        build_apply_job(plan)


def test_max_players_cap():
    many = _rows(*[(i, f"P{i}", "CM") for i in range(1, MAX_PLAYERS + 5)])
    plan = plan_from_rows(many)
    assert plan.player_count == MAX_PLAYERS


def test_confirm_squad_apply_gate():
    assert confirm_squad_apply("APPLY") is True
    assert confirm_squad_apply("apply") is False
    assert confirm_squad_apply("") is False


def test_grok_squad_template_revision_bound(monkeypatch):
    plan = plan_from_rows(_rows((42, "Test", "CB"), (43, "Other", "CB")))
    rev = plan.selection_revision
    content = {
        "selection_revision": rev,
        "summary": "High line CBs",
        "changes": [
            {"field": "acceleration", "value": 88, "reason": "pace"},
            {"field": "defensiveawareness", "value": 92, "reason": "line"},
        ],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{"message": {"content": json.dumps(content)}}]
        },
    )
    from companion.domain.squad_plan import selection_summary_for_ai

    prop = grok.propose_squad_template(
        selection=selection_summary_for_ai(plan),
        selection_revision=rev,
        request="high line defenders",
    )
    assert prop.source == "grok"
    assert prop.fields["acceleration"] == 88
    assert prop.fields["defensiveawareness"] == 92
    staged = plan.with_template(prop)
    assert staged.player_count == 2
    assert build_apply_job(staged).to_wire()["ops"][0]["fields"]["acceleration"] == 88


def test_grok_squad_template_rejects_stale_revision(monkeypatch):
    plan = plan_from_rows(_rows((1, "A", "ST"),))
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "selection_revision": "deadbeefdeadbeef",
                        "summary": "x",
                        "changes": [{"field": "acceleration", "value": 99}],
                    })
                }
            }]
        },
    )
    with pytest.raises(RuntimeError, match="older selection"):
        grok.propose_squad_template(
            selection=[{"playerid": 1}],
            selection_revision=plan.selection_revision,
            request="go",
        )


def test_club_and_planner_sources_exist():
    club = (ROOT / "companion" / "ui" / "surfaces" / "club.py").read_text(encoding="utf-8")
    assert "Open selected players" not in club or "Plan squad" in club
    assert "_open_squad_planner" in club
    assert "_ask_squad_grok" in club
    assert "Only checked" not in club
    assert "Players this prompt will edit" not in club
    assert "Use grid" not in club
    assert "multi_select=True" in club
    planner = (ROOT / "companion" / "ui" / "surfaces" / "planner.py").read_text(
        encoding="utf-8"
    )
    assert "messi jersey 10" in planner
    assert "propose_squad_template" in planner
    assert "apply_squad_plan" in planner
    assert "mode=\"per_player\"" in planner or "mode: str = \"template\"" in planner
    assert "Same numbers on every selected player" in planner or "Same patch for all" in planner
    # Regression: ThreadExecutor.submit calls fn(token); hang if worker is 0-arg.
    assert "def grok_worker(" in planner
    assert "def work(token" in planner
    assert 'submit("squad.grok.template", work)' in planner


def test_open_squad_planner_invokes_open_planner_once():
    club = (ROOT / "companion" / "ui" / "surfaces" / "club.py").read_text(encoding="utf-8")
    start = club.index("def _open_squad_planner")
    end = club.index("def _select_player")
    body = club[start:end]
    assert body.count("open_planner(") == 1
    assert "on_row_click" in club
    assert "Plan these {n}" in club
    assert 'mode="per_player"' in club
    assert "cap_prompt_rows(rows)" in club


def test_grok_worker_accepts_token_and_reports_empty_prompt():
    """Executor contract: worker is fn(token). Empty prompt must call on_error."""
    from companion.ui.surfaces.planner import grok_worker

    plan = plan_from_rows(_rows((1, "A", "CB"),))
    errors: list[BaseException] = []
    success: list[object] = []

    class _Tok:
        cancelled = False

    grok_worker(
        _Tok(),
        plan=plan,
        rev=plan.selection_revision,
        prompt="   ",
        on_success=success.append,
        on_error=errors.append,
    )
    assert success == []
    assert len(errors) == 1
    assert "empty prompt" in str(errors[0]).lower() or "describe" in str(errors[0]).lower()


def test_grok_worker_respects_cancelled_token(monkeypatch):
    from companion.ui.surfaces import planner as planner_mod
    from companion.ui.surfaces.planner import grok_worker

    plan = plan_from_rows(_rows((1, "A", "CB"),))
    called = {"n": 0}

    def boom(**_kw):
        called["n"] += 1
        raise AssertionError("should not call Grok when cancelled")

    monkeypatch.setattr(
        "companion.integrations.grok.propose_squad_template",
        boom,
    )
    # Also patch lazy import path if needed via integrations
    monkeypatch.setattr(
        planner_mod,
        "grok_worker",
        grok_worker,  # keep
    )

    class _Tok:
        cancelled = True

    errors: list = []
    success: list = []
    grok_worker(
        _Tok(),
        plan=plan,
        rev=plan.selection_revision,
        prompt="high line",
        on_success=success.append,
        on_error=errors.append,
    )
    assert called["n"] == 0
    assert success == []
    assert errors == []


def test_grok_worker_success_path(monkeypatch):
    from companion.domain.builds import BuildProposal
    from companion.ui.surfaces.planner import grok_worker

    plan = plan_from_rows(_rows((7, "X", "ST"),))
    prop = BuildProposal(
        fields={"acceleration": 91},
        source="grok",
        label="test",
        summary="pace up",
    )

    def fake_propose(**_kw):
        return prop

    monkeypatch.setattr(
        "companion.integrations.grok.propose_squad_template",
        fake_propose,
    )

    class _Tok:
        cancelled = False

    out: list = []
    grok_worker(
        _Tok(),
        plan=plan,
        rev=plan.selection_revision,
        prompt="pace",
        on_success=out.append,
        on_error=lambda e: (_ for _ in ()).throw(e),
    )
    assert out == [prop]
