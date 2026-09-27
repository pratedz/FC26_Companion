"""Phase 3/4 result-boundary regressions."""

from companion.app.commands.apply import _status_line
from companion.domain.outcome import ApplyOutcome, Failure, JobResult, OpResult
from companion.app import events as E
from companion.app.reducers import reduce
from companion.app.state import AppState


def test_ok_true_never_overrides_failed_field_counts():
    result = JobResult.from_wire(
        {
            "state": "done",
            "ok": True,
            "counts": {
                "fields_requested": 91,
                "fields_written": 88,
                "fields_failed": 3,
            },
        }
    )
    assert result.outcome is ApplyOutcome.PARTIAL
    assert result.ok is False


def test_ok_true_with_only_failures_is_failed_not_no_op():
    result = JobResult.from_wire(
        {
            "state": "done",
            "ok": True,
            "counts": {"fields_written": 0, "fields_failed": 1},
        }
    )
    assert result.outcome is ApplyOutcome.FAILED


def test_explicit_applied_cannot_override_failure_evidence():
    result = JobResult.from_wire(
        {
            "state": "done",
            "outcome": "APPLIED",
            "ok": True,
            "counts": {"fields_written": 1},
            "failures": [
                {
                    "op": "move",
                    "reason": "name_not_applied",
                    "detail": "read-back retained the old name",
                }
            ],
        }
    )
    assert result.outcome is ApplyOutcome.PARTIAL


def test_add_player_name_failure_never_claims_the_transfer_was_not_applied():
    result = JobResult(
        job_id="name-check",
        outcome=ApplyOutcome.PARTIAL,
        label="Add Player: Iniesta",
        failures=(Failure(op_id="add", reason="name_not_visible"),),
    )
    line = _status_line(result)
    assert "incomplete" in line.lower()
    assert "may already be present" in line.lower()
    assert "not transferred" not in line.lower()


def test_verified_set_fields_op_is_not_downgraded_to_no_op():
    op = OpResult.from_wire(
        {
            "id": "stats",
            "op": "set_fields",
            "ok": True,
            "counts": {
                "fields_requested": 2,
                "fields_written": 2,
                "fields_failed": 0,
            },
        }
    )
    assert op.outcome is ApplyOutcome.APPLIED


def test_verified_transfer_side_effect_is_applied():
    op = OpResult.from_wire(
        {
            "id": "move",
            "op": "transfer",
            "ok": True,
            "counts": {"targets": 1, "side_effects": 1},
        }
    )
    assert op.outcome is ApplyOutcome.APPLIED


def test_phase4_add_to_team_is_opt_in_by_default():
    prefs = AppState().prefs
    assert prefs.core_op_enabled("set_fields")
    assert prefs.core_op_enabled("transfer")
    assert prefs.experimental_add_to_team is False


def test_phase4_add_to_team_toggle_round_trips_through_prefs():
    state = reduce(
        AppState(),
        E.PrefsLoaded(
            {
                "core_ops": [
                    "export_squad",
                    "snapshot",
                    "set_fields",
                    "transfer",
                    "budget",
                    "add_to_team",
                ]
            }
        ),
    )
    assert state.prefs.experimental_add_to_team is True
    assert "add_to_team" in state.prefs.values["core_ops"]
