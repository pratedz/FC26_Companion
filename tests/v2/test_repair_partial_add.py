"""Contract checks for the narrowly-scoped partial Add Player recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.domain.job import Grants, Job, JobValidationError, op_repair_partial_add


ROOT = Path(__file__).resolve().parents[2]


def _op():
    return op_repair_partial_add(
        "repair",
        playerid=66371,
        teamid=9,
        expected_overallrating=90,
        expected_potential=41,
        potential=90,
        names={
            "firstname": "Steven",
            "surname": "Gerrard",
            "commonname": "Steven Gerrard",
            "playerjerseyname": "Gerrard",
        },
    )


def test_repair_requires_same_save_and_explicit_add_player_grant() -> None:
    with pytest.raises(JobValidationError, match="allow_add_to_team"):
        Job(ops=(_op(),), requires={"save_uid": "career-a"}).to_wire()
    with pytest.raises(JobValidationError, match="save_uid"):
        Job(ops=(_op(),), grants=Grants(allow_add_to_team=True)).to_wire()

    wire = Job(
        ops=(_op(),),
        grants=Grants(allow_add_to_team=True),
        requires={"save_uid": "career-a"},
    ).to_wire()
    op = wire["ops"][0]
    assert op["op"] == "repair_partial_add"
    assert op["expected"] == {"overallrating": 90, "potential": 41}
    assert op["potential"] == 90
    assert wire["grants"]["allow_add_to_team"] is True


def test_native_repair_keeps_a_complete_fc_dictionary_identity() -> None:
    op = op_repair_partial_add(
        "repair-native",
        playerid=66371,
        teamid=241,
        expected_overallrating=92,
        expected_potential=92,
        potential=92,
        names={"commonname": "Iniesta"},
        native_name_ids={
            "firstnameid": 2162,
            "lastnameid": 16352,
            "commonnameid": 16351,
            # Zero is the verified FC source value for this optional field.
            "playerjerseynameid": 0,
            "usercaneditname": 0,
        },
    )
    assert op.v == 2
    assert op.body["native_name_ids"] == {
        "firstnameid": 2162,
        "lastnameid": 16352,
        "commonnameid": 16351,
        "playerjerseynameid": 0,
        "usercaneditname": 0,
    }


def test_repair_rejects_invalid_identity_or_lower_potential() -> None:
    with pytest.raises(JobValidationError, match="positive playerid"):
        op_repair_partial_add(
            "repair", playerid=0, teamid=9, expected_overallrating=90,
            expected_potential=41, potential=90, names={"commonname": "Steven Gerrard"},
        )
    with pytest.raises(JobValidationError, match="at least the expected OVR"):
        op_repair_partial_add(
            "repair", playerid=66371, teamid=9, expected_overallrating=90,
            expected_potential=41, potential=89, names={"commonname": "Steven Gerrard"},
        )
    with pytest.raises(JobValidationError, match="at least one name"):
        op_repair_partial_add(
            "repair", playerid=66371, teamid=9, expected_overallrating=90,
            expected_potential=41, potential=90, names={},
        )
    with pytest.raises(JobValidationError, match="all four FC name IDs"):
        op_repair_partial_add(
            "repair", playerid=66371, teamid=9, expected_overallrating=90,
            expected_potential=41, potential=90, names={"commonname": "Iniesta"},
            native_name_ids={
                "firstnameid": 1, "lastnameid": 2, "commonnameid": 3,
                "usercaneditname": 0,
            },
        )
    with pytest.raises(JobValidationError, match="at least one non-zero"):
        op_repair_partial_add(
            "repair", playerid=66371, teamid=9, expected_overallrating=90,
            expected_potential=41, potential=90, names={"commonname": "Iniesta"},
            native_name_ids={
                "firstnameid": 0, "lastnameid": 0, "commonnameid": 0,
                "playerjerseynameid": 0, "usercaneditname": 0,
            },
        )


def test_worker_rechecks_save_team_and_exact_player_values_before_writes() -> None:
    source = (ROOT / "ingame/le_companion/ops/repair_partial_add.lua").read_text(encoding="utf-8")
    assert 'required_save ~= tostring((ctx and ctx.save_uid) or "")' in source
    assert "verify_membership(teamid, playerid)" in source
    assert "GetPlayerIDSForTeam" in source
    assert "teamplayerlinks" in source
    assert "actual ~= 111592" in source
    assert "actual_ovr ~= expected_ovr or actual_pot ~= expected_pot" in source
    assert 'db.resolve("editedplayernames", "playerid", playerid)' in source
    assert "detach_name_dictionary" in source
    assert "firstnameid = 0" in source
    assert "usercaneditname = 1" in source
    assert 'rawget(_G, "GetPlayerName")' in source
    assert "visible_name_matches(visible, names.commonname)" in source
    assert "contains_sequence" in source
    assert "got:find(want" not in source
    assert 'fail("name_not_visible"' in source
    assert "native_name_ids_from(op)" in source
    assert "native_name_write_failed" in source
    assert "if not native_name_ids then" in source
    assert "native_name_ids, {" in source
    assert "potential = target_pot" in source
    assert "TransferPlayer" not in source
    assert "headasset" not in source
    assert "birthdate" not in source


def test_repair_is_single_op_on_both_sides_of_the_contract() -> None:
    runner = (ROOT / "ingame/le_companion/runner.lua").read_text(encoding="utf-8")
    assert 'op.op == "repair_partial_add"' in runner
    install = (ROOT / "companion/platform/le_install.py").read_text(encoding="utf-8")
    assert '"ops/repair_partial_add.lua"' in install
    assert _op().v == 2
