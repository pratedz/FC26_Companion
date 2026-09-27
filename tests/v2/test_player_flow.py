"""Phase 2 player targeting, live read, schema and safe apply."""

from __future__ import annotations

import inspect

import pytest

from companion.app import events as E
from companion.app.commands.apply import ApplyError, apply_editor
from companion.app.commands.player import (
    editor_has_live_values,
    ensure_live_player,
    lock_player,
    player_read_pending,
    reload_player,
)
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.player import (
    EDITABLE_FIELDS,
    FIELD_SPECS,
    PlayerValidationError,
    normalize_patch,
)


@pytest.fixture
def svc(tmp_path):
    store = Store()
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=store,
    )


def test_schema_covers_full_player_editor_surface():
    assert len(EDITABLE_FIELDS) >= 100
    for required in (
        "overallrating", "acceleration", "gkreflexes", "skillmoves",
        "preferredposition7", "trait1", "height", "headassetid",
        "shoetypecode", "tattooleftarm", "contractvaliduntil", "pacdiv",
    ):
        assert required in FIELD_SPECS


def test_patch_aliases_and_bounds_are_explicit():
    assert normalize_patch({"ovr": "94", "skill_moves": 4}) == {
        "overallrating": 94,
        "skillmoves": 4,
    }
    with pytest.raises(PlayerValidationError, match="at most 4"):
        normalize_patch({"skillmoves": 5})
    with pytest.raises(PlayerValidationError, match="not an editable"):
        normalize_patch({"totally_fake": 1})


def test_lock_player_uses_cached_values_not_placeholders(svc):
    svc.store.dispatch(
        E.SquadSynced(
            save_uid="SAVE",
            teamid=241,
            players=(
                {
                    "playerid": 158023,
                    "name": "Lionel Messi",
                    "overallrating": 86,
                    "potential": 86,
                },
            ),
            taken=svc.clock.now(),
        )
    )
    player = lock_player(svc, 158023, source="manual")
    state = svc.store.snapshot()
    assert player.name == "Lionel Messi"
    assert state.target.playerid == 158023
    assert state.editor.base["overallrating"] == 86
    assert state.editor.dirty == {}


def test_target_change_discards_previous_players_patch(svc):
    lock_player(svc, 1, record={"playerid": 1, "overallrating": 70})
    svc.store.dispatch(E.FieldEdited("overallrating", 99))
    assert svc.store.snapshot().editor.has_changes
    lock_player(svc, 2, record={"playerid": 2, "overallrating": 80})
    assert svc.store.snapshot().editor.base["playerid"] == 2
    assert svc.store.snapshot().editor.dirty == {}


def test_choose_another_player_clears_target_and_target_bound_draft(svc):
    """The Player UI must provide a safe route back to the squad picker."""
    from companion.ui.surfaces.player import _choose_another_player

    lock_player(svc, 1, record={"playerid": 1, "name": "First", "overallrating": 70})
    svc.store.dispatch(E.FieldEdited("overallrating", 91))

    _choose_another_player(svc)

    state = svc.store.snapshot()
    assert state.target.playerid is None
    assert state.editor.base == {}
    assert state.editor.dirty == {}
    assert "discarded" in state.status.lower()


def test_player_page_offers_card_library_and_ai_only():
    from companion.ui.surfaces import player

    page = inspect.getsource(player.build)
    session = inspect.getsource(player._build_session_panel)
    assert "_build_card_picker" in page
    assert "_build_quick_editor" not in page
    assert "CTkScrollableFrame" not in page
    assert "segmented_rail" not in page
    assert '_build_target_summary' not in page
    assert 'center.pack(fill="both", expand=True)' in page
    assert "child.destroy()" in page
    assert 'view.setdefault("player_source", "cards")' in page
    assert player.PLAYER_SOURCES == (
        ("cards", "Card library"),
        ("presets", "AI"),
    )
    strip = inspect.getsource(player._build_workstation_strip)
    assert "PLAYER_SOURCES" in strip
    assert "Manual Edit" not in strip
    assert "Use preset" not in session
    assert "Ask AI" in session


def test_player_target_picker_has_a_live_squad_search_bar():
    from companion.ui.surfaces import player

    picker = inspect.getsource(player._build_target_picker)
    assert "Search your current squad by name, position, or ID" in picker
    assert "def filter_squad" in picker
    assert 'query.trace_add("write", filter_squad)' in picker


def test_manual_target_has_no_invented_ratings(svc):
    lock_player(svc, "12345")
    assert svc.store.snapshot().editor.base == {"playerid": 12345, "name": ""}


def test_reload_submits_explicit_snapshot_and_loads_result(svc):
    lock_player(svc, 158023, record={"playerid": 158023, "overallrating": 80})

    def complete(job):
        svc.transport.complete(
            job.job_id,
            {
                "state": "done",
                "ok": True,
                "counts": {"ops_total": 1, "ops_ok": 1},
                "ops": [
                    {
                        "id": "player.read",
                        "op": "snapshot",
                        "ok": True,
                        "counts": {"targets": 1, "found": 1},
                        "data": {
                            "rows": [
                                {
                                    "playerid": 158023,
                                    "overallrating": 91,
                                    "potential": 92,
                                }
                            ]
                        },
                    }
                ],
            },
        )

    svc.transport.on_submit = complete
    jid = reload_player(svc)
    wire = svc.transport.job(jid).to_wire()
    assert wire["ops"][0]["op"] == "snapshot"
    assert wire["ops"][0]["playerids"] == [158023]
    assert wire["ops"][0]["fields"] == list(EDITABLE_FIELDS)
    assert svc.store.snapshot().editor.base["overallrating"] == 91
    assert svc.store.snapshot().editor.dirty == {}


def test_reload_deduplicates_an_in_flight_read_for_the_same_player(svc):
    lock_player(svc, 158023, record={"playerid": 158023, "overallrating": 80})

    first = reload_player(svc, follow=False)

    assert player_read_pending(svc.store.snapshot(), 158023) is True
    with pytest.raises(PlayerValidationError, match="already queued"):
        reload_player(svc, follow=False)
    assert len(svc.transport.submitted) == 1
    joined = reload_player(svc, follow=False, on_loaded=lambda _row: None)
    assert joined == first
    assert len(svc.transport.submitted) == 1


def test_ensure_live_player_joins_an_in_flight_read(svc):
    lock_player(svc, 158023, record={"playerid": 158023, "overallrating": 80})
    first = reload_player(svc, follow=False)
    second = ensure_live_player(svc)
    assert second == first
    assert len(svc.transport.submitted) == 1
    assert editor_has_live_values(svc.store.snapshot()) is False


def test_reload_requires_armed_worker(svc):
    lock_player(svc, 1)
    svc.transport.set_liveness(
        Liveness(pill=Pill.OFF, message="Start Live Editor.", armed=False)
    )
    svc.store.dispatch(E.LivenessChanged(svc.transport.liveness()))
    with pytest.raises(PlayerValidationError, match="Start Live Editor"):
        reload_player(svc)


def test_reload_refuses_to_discard_staged_changes(svc):
    lock_player(svc, 1, record={"playerid": 1, "overallrating": 70})
    svc.store.dispatch(E.FieldEdited("overallrating", 71))
    with pytest.raises(PlayerValidationError, match="staged changes"):
        reload_player(svc)
    assert svc.transport.submitted == []


def test_apply_validates_dirty_fields_before_transport(svc):
    lock_player(svc, 1, record={"playerid": 1, "overallrating": 70})
    svc.store.dispatch(E.FieldEdited("skillmoves", 5))
    with pytest.raises(ApplyError, match="at most 4"):
        apply_editor(svc)
    assert svc.transport.submitted == []


def test_apply_rejects_invalid_player_id_before_transport(svc):
    from companion.app.commands.apply import apply_fields

    with pytest.raises(ApplyError, match="greater than zero"):
        apply_fields(svc, 0, {"overallrating": 90})
    assert svc.transport.submitted == []

    with pytest.raises(ApplyError, match="499999"):
        apply_fields(svc, 500_000, {"overallrating": 90})
    assert svc.transport.submitted == []
