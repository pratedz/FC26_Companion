import json
from pathlib import Path

import pytest

from companion.domain.job import JobValidationError
from companion.domain.profile_library import actions, build_job


ROOT = Path(__file__).parents[2]


def test_full_v1_profile_inventory_is_visible_with_truthful_status():
    raw = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
    items = actions(raw)
    assert len(items) == 51
    by_id = {item.id: item for item in items}
    assert by_id["full_fitness"].mode == "native"
    assert by_id["never_retire"].mode == "pending"
    assert by_id["unlock_boots"].mode == "pending"
    # Recurring event scripts map to one-shot typed career ops.
    assert by_id["full_fitness_event"].mode == "native"
    assert by_id["list_players"].mode == "native"


def test_native_profile_jobs_are_typed_and_whole_save_edits_are_withheld():
    career = build_job("full_fitness").to_wire()
    assert career["ops"][0]["op"] == "career.set"
    with pytest.raises(JobValidationError, match="Whole-save"):
        build_job("never_retire")


def test_pending_profile_never_generates_legacy_lua():
    with pytest.raises(JobValidationError, match="verified unlock"):
        build_job("unlock_boots")


def test_cli_exposes_profile_inventory_and_explicit_mass_confirmation():
    from companion.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["profiles"]).cmd == "profiles"
    args = parser.parse_args(["run-profile", "never_retire", "--confirm-all"])
    assert args.profile_id == "never_retire"
    assert args.confirm_all is True


def test_event_profile_aliases_build_typed_career_jobs():
    wire = build_job("full_fitness_event").to_wire()
    assert wire["ops"][0]["op"] == "career.set"
    assert wire["ops"][0]["fitness"] == 100


def test_list_players_exports_via_export_squad_op():
    wire = build_job("list_players").to_wire()
    assert wire["ops"][0]["op"] == "export_squad"
