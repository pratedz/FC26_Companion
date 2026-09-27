"""Player-page card import is selective, target-bound and review-only."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from companion.app import events as E
from companion.app.commands.card_import import prepare_card_proposal, stage_card
from companion.app.commands.player import (
    editor_has_live_values,
    ensure_live_player,
    lock_player,
    reload_player,
)
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.card_import import (
    BasePlayerProfile,
    CardImportTopics,
    build_card_proposal,
    card_source_playerid,
    changed_fields_by_category,
    forced_birthdate_for_age,
)
from companion.domain.catalog import ImportPlan
from companion.domain.player import PlayerValidationError
from companion.platform import base_players as base_adapter
from companion.ui.surfaces import library, player


class CatalogStub:
    def prepare_cross_year_import(self, card, *, target_playerid):
        return ImportPlan(
            target_playerid=target_playerid,
            fields={"overallrating": 93, "acceleration": 96},
            source={"name": card.get("name", "Card"), "year": "26", "variant": "Gold"},
        )


@pytest.fixture
def svc(tmp_path):
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=FakeClock(),
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=Store(),
        catalog=CatalogStub(),
    )


def _full_target(svc, playerid=10):
    row = {"playerid": playerid, "name": f"Target {playerid}"}
    row.update({f"attribute{i}": 50 for i in range(1, 11)})
    # Use real editor fields; the count makes the row truthfully "fully read".
    row.update(
        {
            "overallrating": 70,
            "potential": 75,
            "acceleration": 71,
            "sprintspeed": 72,
            "finishing": 50,
            "shortpassing": 60,
            "strength": 65,
            "height": 180,
            "weight": 75,
            "preferredfoot": 1,
        }
    )
    lock_player(svc, playerid, record=row, source="squad")


def test_topic_defaults_are_stats_only():
    topics = CardImportTopics()
    assert topics.stats is True
    assert topics.kit is False
    assert topics.name is topics.age is topics.face is False
    assert topics.labels() == ("Stats",)
    assert CardImportTopics(stats=True, kit=True).labels() == ("Stats", "Kit")


def test_card_source_lookup_rejects_variant_like_or_fractional_ids():
    assert card_source_playerid({"person_id": "190871", "variant_id": 50_522_519}) == 190871
    assert card_source_playerid({"person_id": "190871.5", "variant_id": 50_522_519}) is None
    assert card_source_playerid({"person_id": True, "variant_id": 50_522_519}) is None


def test_selected_topics_copy_only_verified_safe_fields():
    profile = BasePlayerProfile(
        source_playerid=999,
        identity={
            "firstnameid": 101,
            "lastnameid": 202,
            "commonnameid": 303,
            "playerjerseynameid": 404,
            "birthdate": 733_000,
            "nationality": 52,
            "playerid": 999,
        },
        appearance={
            "headassetid": 999,
            "headclasscode": 0,
            "headtypecode": 33,
            "hairtypecode": 4,
            "height": 199,
            "teamid": 777,
        },
        face_verified=True,
    )
    result = build_card_proposal(
        card={"name": "Source", "person_id": 999, "variant_id": 888},
        target_playerid=10,
        topics=CardImportTopics(stats=True, name=True, age=True, face=True),
        stats_fields={
            "overallrating": 94,
            "playerid": 999,
            "teamid": 777,
            "contractvaliduntil": 2035,
        },
        source={"name": "Source", "year": "26", "variant": "Gold"},
        base_profile=profile,
    )
    assert result.fields == {
        "overallrating": 94,
        "firstnameid": 101,
        "lastnameid": 202,
        "commonnameid": 303,
        "playerjerseynameid": 404,
        "birthdate": 733_000,
        "headassetid": 999,
        "headclasscode": 0,
        "headtypecode": 33,
        "hairtypecode": 4,
    }
    assert not ({"playerid", "person_id", "variant_id", "teamid", "nationality"} & result.fields.keys())


def test_missing_base_data_skips_optional_topics_with_warnings():
    result = build_card_proposal(
        card={"name": "Unknown"},
        target_playerid=10,
        topics=CardImportTopics(stats=True, name=True, age=True, face=True),
        stats_fields={"overallrating": 88},
        source={"name": "Unknown", "year": "20"},
        base_profile=None,
    )
    assert result.fields == {"overallrating": 88}
    assert len(result.warnings) == 3
    with pytest.raises(PlayerValidationError, match="no verified editable fields"):
        build_card_proposal(
            card={"name": "Unknown"},
            target_playerid=10,
            topics=CardImportTopics(stats=False, name=True),
            stats_fields={},
            source={"name": "Unknown"},
            base_profile=None,
        )


def test_forced_age_overrides_card_birthdate_without_base_player_data():
    result = build_card_proposal(
        card={"name": "Legend"},
        target_playerid=10,
        topics=CardImportTopics(stats=False, forced_age=25),
        stats_fields={},
        source={"name": "Legend", "year": "26"},
        base_profile=None,
    )
    assert result.fields == {"birthdate": forced_birthdate_for_age(25)}
    assert result.fields["birthdate"] == 152_567  # 2000-07-01 in EA Gregorian days


@pytest.mark.parametrize("age", (15, 41))
def test_forced_age_uses_safe_career_bounds(age):
    with pytest.raises(PlayerValidationError, match="between 16 and 40"):
        forced_birthdate_for_age(age)


def test_base_adapter_reads_exact_appearance_category_and_head_fields(monkeypatch):
    from src import base_players

    row = {
        "playerid": "999",
        "firstnameid": "101",
        "lastnameid": "202",
        "birthdate": "733000",
        "headassetid": "999",
        "headclasscode": "0",
        "headtypecode": "33",
        "hashighqualityhead": "0",
        "hairtypecode": "4",
        "height": "199",
        "teamid": "777",
    }
    monkeypatch.setattr(base_players, "get_row", lambda _pid: row)
    monkeypatch.setattr(
        base_players,
        "identity",
        lambda _pid: {"firstnameid": 101, "lastnameid": 202, "birthdate": 733000},
    )
    found = base_adapter.resolve_card_base_profile({"person_id": 999})
    assert found is not None
    assert found.identity == {
        "firstnameid": 101,
        "lastnameid": 202,
        "birthdate": 733000,
    }
    assert found.appearance["headassetid"] == 999
    assert found.appearance["headclasscode"] == 0
    assert found.appearance["hairtypecode"] == 4
    assert "height" not in found.appearance
    assert "teamid" not in found.appearance
    assert found.face_verified is True


def test_generic_face_zero_name_ids_and_zero_birthdate_are_never_staged():
    profile = BasePlayerProfile(
        source_playerid=999,
        identity={
            "firstnameid": 0,
            "lastnameid": 0,
            "commonnameid": 0,
            "playerjerseynameid": 0,
            "birthdate": 0,
        },
        appearance={"headassetid": 0, "headclasscode": 1, "hairtypecode": 4},
        face_verified=False,
    )
    with pytest.raises(PlayerValidationError, match="no verified editable fields"):
        build_card_proposal(
            card={"name": "Generic"},
            target_playerid=10,
            topics=CardImportTopics(stats=False, name=True, age=True, face=True),
            stats_fields={},
            source={"name": "Generic"},
            base_profile=profile,
        )


def test_stage_is_review_only_and_never_uses_source_id(svc):
    _full_target(svc, 10)
    result = stage_card(svc, {"name": "Source", "person_id": 999})
    state = svc.store.snapshot()
    assert result.target_playerid == 10
    assert state.target.playerid == 10
    assert state.editor.dirty == {"overallrating": 93, "acceleration": 96}
    assert state.editor.source == "card"
    assert svc.transport.submitted == []


def test_card_stage_calls_review_hook_after_it_has_staged(svc):
    _full_target(svc, 10)
    reviewed = []

    result = stage_card(
        svc,
        {"name": "Source", "person_id": 999},
        on_staged=reviewed.append,
    )

    assert result.field_count == 2
    assert reviewed == [result]
    assert svc.store.snapshot().editor.has_changes


def test_sparse_target_with_existing_draft_can_replace_it_with_card(svc):
    lock_player(svc, 10, record={"playerid": 10, "overallrating": 70}, source="squad")
    svc.store.dispatch(E.FieldEdited("acceleration", 91))

    result = stage_card(svc, {"name": "Source", "person_id": 999})

    assert result.reload_job_id == ""
    assert svc.store.snapshot().editor.dirty == {"overallrating": 93, "acceleration": 96}
    assert any("not fully loaded" in warning for warning in svc.store.snapshot().editor.warnings)


def test_sparse_target_reads_live_values_before_staging_card(svc):
    lock_player(svc, 10, record={"playerid": 10, "name": "First"}, source="manual")
    started = stage_card(svc, {"name": "Source", "person_id": 999})
    assert started.reload_job_id
    assert started.field_count == 0
    assert svc.store.snapshot().target.playerid == 10
    assert svc.store.snapshot().editor.dirty == {}
    assert svc.transport.submitted[0].label == "Read player 10"


def test_stage_card_reuses_an_in_flight_live_read(svc):
    lock_player(svc, 10, record={"playerid": 10, "name": "First"}, source="manual")
    first = reload_player(svc, follow=False)
    started = stage_card(svc, {"name": "Source", "person_id": 999})
    assert started.reload_job_id == first
    assert started.field_count == 0
    assert len(svc.transport.submitted) == 1
    assert svc.store.snapshot().editor.dirty == {}


def test_ensure_live_player_is_noop_when_editor_already_full(svc):
    _full_target(svc, 10)
    seen = []
    job_id = ensure_live_player(svc, on_loaded=seen.append)
    assert job_id == ""
    assert svc.transport.submitted == []
    assert seen and "overallrating" in seen[0]
    assert editor_has_live_values(svc.store.snapshot())


def test_sparse_target_can_stage_card_immediately_without_live_read(svc):
    lock_player(svc, 10, record={"playerid": 10, "name": "First", "overallrating": 70}, source="squad")

    started = stage_card(
        svc,
        {"name": "Source", "person_id": 999},
        read_live_first=False,
    )

    assert started.reload_job_id == ""
    assert started.field_count == 2
    assert svc.transport.submitted == []
    assert svc.store.snapshot().editor.dirty == {
        "overallrating": 93,
        "acceleration": 96,
    }
    assert any("not fully loaded" in warning for warning in started.warnings)


def test_card_stages_after_the_live_read_returns(svc):
    lock_player(svc, 10, record={"playerid": 10, "name": "First"}, source="manual")
    staged = []

    def complete(job):
        svc.transport.complete(
            job.job_id,
            {
                "state": "done",
                "ok": True,
                "counts": {"ops_total": 1, "ops_ok": 1},
                "ops": [{
                    "id": "player.read",
                    "op": "snapshot",
                    "ok": True,
                    "data": {"rows": [{
                        "playerid": 10,
                        "overallrating": 70,
                        "acceleration": 71,
                        "potential": 75,
                    }]},
                }],
            },
        )

    svc.transport.on_submit = complete
    result = stage_card(
        svc,
        {"name": "Source", "person_id": 999},
        on_staged=staged.append,
    )

    assert result.field_count == 2
    assert staged == [result]
    assert svc.store.snapshot().editor.base["overallrating"] == 70
    assert svc.store.snapshot().editor.dirty == {
        "overallrating": 93,
        "acceleration": 96,
    }


def test_prepare_requires_locked_live_destination(svc):
    with pytest.raises(PlayerValidationError, match="verified live squad"):
        prepare_card_proposal(svc, {"name": "Source"}, CardImportTopics())


def test_library_stage_uses_shared_card_build_command(monkeypatch):
    calls = []
    navigated = []

    def fake_stage(svc, card, topics, *, on_staged=None, **_kw):
        calls.append((card, topics))
        if on_staged is not None:
            on_staged(SimpleNamespace())

    monkeypatch.setattr("companion.app.commands.card_import.stage_card", fake_stage)
    fake_svc = SimpleNamespace(
        ui=SimpleNamespace(navigate=navigated.append),
        store=SimpleNamespace(dispatch=lambda _event: None),
    )
    raw = {"name": "Card", "person_id": 999}
    library._stage_card(fake_svc, {"_raw": raw})
    assert calls[0][0] is raw
    assert calls[0][1] == CardImportTopics(stats=True)
    assert calls[0][1].kit is False
    assert navigated == ["player"]


def test_player_surface_contains_embedded_selective_card_workflow():
    source = inspect.getsource(player._build_card_picker)
    for marker in (
        "Search a player or card variant",
        "Card build",
        "Name",
        "Age",
        "Force age",
        "Face",
        "Stage card & review now",
        "ensure_live_player",
        "themed_scroll",
        "read_live_first=False",
        "show_card_preview",
        "Review -> Apply",
        "Select a card variant to preview",
        "← Back",
        "clear_card_selection",
    ):
        assert marker in source
    assert '"stats": ctk.BooleanVar(value=True)' in source
    assert '"kit": ctk.BooleanVar(value=False)' in source
    assert '("kit", "Kit")' in source
    assert "shown[:6]" not in source
    assert "[:6]" not in source
    assert "CTkScrollableFrame" not in source
    assert "themed_scroll" in source
    assert "changed_fields_by_category" in source
    preview_pack = source.split("themed_scroll(preview_host", 1)[1].split(
        "body = scroll.inner", 1
    )[0]
    assert "expand=True" not in preview_pack
    assert 'fill="x"' in preview_pack
    host_pack = source.split("host = panel(root, level=1)", 1)[1].split("heading =", 1)[0]
    assert 'fill="both"' not in host_pack
    assert "expand=True" not in host_pack
    preview_show = source.split("def show_card_preview()", 1)[1].split(
        "def _prefetch_live_values", 1
    )[0]
    assert "expand=True" not in preview_show
    assert 'fill="x"' in preview_show
    # Card Library is an explicit copy workflow: its five selected details
    # should be complete by default, including face and Force age. Kit stays off.
    for topic in ("name", "age", "face", "force_age"):
        assert f'"{topic}": ctk.BooleanVar(value=True)' in source


def test_review_diff_uses_field_spec_labels_and_groups():
    from companion.ui import shell as shell_mod

    source = inspect.getsource(shell_mod.Shell._diff_model)
    assert "FIELD_SPECS" in source
    assert "labels=" in source
    assert "groups=" in source
    assert "spec.category" in source


def test_card_build_copies_body_but_kit_is_opt_in():
    stats_fields = {
        "overallrating": 94,
        "height": 185,
        "weight": 80,
        "bodytypecode": 187,
        "runstylecode": 120,
        "jerseyfit": 2,
        "shoetypecode": 150,
    }
    stats_only = build_card_proposal(
        card={"name": "CR7"},
        target_playerid=10,
        topics=CardImportTopics(stats=True),
        stats_fields=stats_fields,
        source={"name": "CR7", "year": "09", "variant": "Gold"},
        base_profile=None,
    )
    assert stats_only.fields["bodytypecode"] == 187
    assert stats_only.fields["runstylecode"] == 120
    assert stats_only.fields["height"] == 185
    assert "jerseyfit" not in stats_only.fields
    assert "shoetypecode" not in stats_only.fields
    assert "Kit" not in stats_only.summary

    with_kit = build_card_proposal(
        card={"name": "CR7"},
        target_playerid=10,
        topics=CardImportTopics(stats=True, kit=True),
        stats_fields=stats_fields,
        source={"name": "CR7", "year": "09", "variant": "Gold"},
        base_profile=None,
    )
    assert with_kit.fields["jerseyfit"] == 2
    assert with_kit.fields["shoetypecode"] == 150
    assert "Kit" in with_kit.summary

    missing_kit = build_card_proposal(
        card={"name": "Thin"},
        target_playerid=10,
        topics=CardImportTopics(stats=True, kit=True),
        stats_fields={"overallrating": 88, "height": 180},
        source={"name": "Thin", "year": "09"},
        base_profile=None,
    )
    assert missing_kit.fields == {"overallrating": 88, "height": 180}
    assert any("Kit was skipped" in warning for warning in missing_kit.warnings)


def test_kit_only_with_no_kit_fields_stages_nothing():
    with pytest.raises(PlayerValidationError, match="no verified editable fields"):
        build_card_proposal(
            card={"name": "Thin"},
            target_playerid=10,
            topics=CardImportTopics(stats=False, kit=True),
            stats_fields={"overallrating": 88},
            source={"name": "Thin"},
            base_profile=None,
        )


def test_changed_fields_group_by_category_and_hide_unchanged():
    groups = dict(
        changed_fields_by_category(
            {
                "overallrating": 94,
                "acceleration": 96,
                "height": 185,
                "bodytypecode": 187,
                "runstylecode": 12,
                "jerseyfit": 2,
                "firstnameid": 101,
            },
            {
                "overallrating": 91,
                "acceleration": 96,
                "height": 187,
                "bodytypecode": 1,
                "runstylecode": 12,
            },
        )
    )
    assert "Pace" not in groups
    assert groups["Ratings"][0][0] == "overallrating"
    body_fields = {item[0] for item in groups["Body"]}
    assert body_fields == {"height", "bodytypecode"}
    assert "runstylecode" not in {item[0] for item in groups.get("Movement & animation", ())}
    assert groups["Kit & accessories"][0][0] == "jerseyfit"
    assert groups["Career & identity"][0] == ("firstnameid", "First-name ID", None, 101)
    height_row = next(item for item in groups["Body"] if item[0] == "height")
    assert height_row[2:] == (187, 185)
