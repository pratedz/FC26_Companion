"""Apply rebases a reviewed plan onto the live squad instead of forcing a rebuild."""
import pytest

from companion.app.commands.apply import ApplyError
from companion.app.commands.squad_plan import apply_squad_plan, squad_plan_receipt

from tests.v2.test_squad_durable_execution import Backend, _plan, _refresh, _service


def test_career_growth_on_an_untouched_attribute_does_not_block_apply(tmp_path):
    backend = Backend(3)
    for row in backend.players.values():
        row["aggression"] = 22
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    backend.players[2]["aggression"] = 23  # weekly development in Career Mode
    _refresh(svc, backend)

    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")

    assert squad_plan_receipt(svc, plan_id)["verification"] == "verified"
    assert backend.players[2]["aggression"] == 23
    assert backend.players[2]["skillmoves"] == 4


def test_drift_on_a_planned_field_applies_the_reviewed_value_and_reports_it(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)  # plans overallrating 90 for everyone
    backend.players[1]["overallrating"] = 81
    _refresh(svc, backend)

    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")

    report = squad_plan_receipt(svc, plan_id)
    assert report["verification"] == "verified"
    assert backend.players[1]["overallrating"] == 90
    assert report["live_changes"] == ["Player 1: Overall 80 → 81 in Career, plan sets 90"]


def test_a_player_who_left_the_club_is_skipped_and_the_rest_still_apply(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc)  # also rotates shirts 1→2→3→1
    sold = backend.players.pop(3)
    backend.shirts.pop(3)
    _refresh(svc, backend)

    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")

    report = squad_plan_receipt(svc, plan_id)
    assert report["verification"] == "verified"
    assert [backend.players[pid]["skillmoves"] for pid in (1, 2)] == [4, 4]
    assert sold["skillmoves"] == 1
    assert report["live_changes"] == ["Player 3 left the club; skipped"]
    assert backend.shirts == {1: 2, 2: 3}


def test_apply_stops_when_every_planned_player_left_the_club(tmp_path):
    backend = Backend(2)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    backend.players.clear()
    backend.shirts.clear()
    _refresh(svc, backend)

    with pytest.raises(ApplyError, match="No planned player is still at the club"):
        apply_squad_plan(svc, plan, confirm_token="APPLY")
    assert not svc.transport.submitted


def test_newcomer_wearing_a_planned_shirt_is_moved_to_a_free_number(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc)  # 1→#2, 2→#3, 3→#1
    backend.shirts[2] = 12
    backend.players[9] = {"playerid": 9, "name": "Player 9", "position": "ST",
                          "overallrating": 70, "potential": 80, "skillmoves": 1}
    backend.shirts[9] = 2
    _refresh(svc, backend)

    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")

    report = squad_plan_receipt(svc, plan_id)
    assert report["verification"] == "verified"
    assert backend.shirts == {1: 2, 2: 3, 3: 1, 9: 13}
    assert "Player 9 moves from #2 to #13 to free #2" in report["live_changes"]
    assert backend.players[9]["skillmoves"] == 1


def test_stale_squad_asks_for_a_refresh_with_a_dedicated_error(tmp_path):
    from companion.app import events as E
    from companion.app.commands.squad_plan_recovery import SquadRefreshNeeded

    backend = Backend(2)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    svc.store.dispatch(E.SquadInvalidated())

    with pytest.raises(SquadRefreshNeeded):
        apply_squad_plan(svc, plan, confirm_token="APPLY")
    assert not svc.transport.submitted
