"""Career growth XP mirroring — curve integrity, star indexing, Lua safety."""

from __future__ import annotations

import re

import pytest

from src import growth_xp as gx


# ── curve data ───────────────────────────────────────────────────────


def test_curve_tables_match_cheat_table_shape() -> None:
    assert len(gx.XP_TO_ATTRIBUTE) == 99
    assert len(gx.XP_TO_STAR) == 5
    assert gx.XP_TO_ATTRIBUTE[0] == 1000
    assert gx.XP_TO_ATTRIBUTE[-1] == 189650
    assert gx.XP_TO_STAR == (100, 2500, 5000, 7500, 10000)
    assert len(gx.GROWTH_FIELDS_ORDERED) == 36
    assert len(gx.GROWABLE_ATTRIBUTE_FIELDS) == 34
    # No duplicates in the ordered array (a dupe would silently drop a field).
    assert len(set(gx.GROWTH_FIELDS_ORDERED)) == 36


def test_curves_are_strictly_increasing() -> None:
    for a, b in zip(gx.XP_TO_ATTRIBUTE, gx.XP_TO_ATTRIBUTE[1:]):
        assert b > a
    for a, b in zip(gx.XP_TO_STAR, gx.XP_TO_STAR[1:]):
        assert b > a


# ── attribute <-> xp ─────────────────────────────────────────────────


def test_attribute_xp_round_trip_1_to_99() -> None:
    for value in range(1, 100):
        xp = gx.attribute_to_xp(value)
        assert gx.xp_to_attribute(xp) == value, f"round trip broke at {value}"


def test_attribute_to_xp_is_monotonic_with_no_gaps() -> None:
    seen = [gx.attribute_to_xp(v) for v in range(1, 100)]
    assert seen == sorted(seen)
    assert len(set(seen)) == 99
    # Every value 1..99 is reachable — no attribute is unrepresentable.
    assert {gx.xp_to_attribute(x) for x in seen} == set(range(1, 100))


def test_xp_between_thresholds_floors_to_lower_attribute() -> None:
    xp_50 = gx.attribute_to_xp(50)
    xp_51 = gx.attribute_to_xp(51)
    assert gx.xp_to_attribute(xp_50 + 1) == 50
    assert gx.xp_to_attribute(xp_51 - 1) == 50


def test_attribute_clamping_outside_range() -> None:
    lo = gx.attribute_to_xp(1)
    hi = gx.attribute_to_xp(99)
    assert gx.attribute_to_xp(0) == lo
    assert gx.attribute_to_xp(-40) == lo
    assert gx.attribute_to_xp(100) == hi
    assert gx.attribute_to_xp(999) == hi


def test_xp_to_attribute_clamping_outside_range() -> None:
    assert gx.xp_to_attribute(0) == 1
    assert gx.xp_to_attribute(-1) == 1
    assert gx.xp_to_attribute(999) == 1  # below the first threshold
    assert gx.xp_to_attribute(gx.XP_TO_ATTRIBUTE[-1]) == 99
    assert gx.xp_to_attribute(10_000_000) == 99


def test_attribute_to_xp_accepts_stringy_values() -> None:
    assert gx.attribute_to_xp("85") == gx.attribute_to_xp(85)
    assert gx.attribute_to_xp(85.0) == gx.attribute_to_xp(85)


# ── stars ────────────────────────────────────────────────────────────


def test_stars_to_xp_and_back() -> None:
    for stars in range(1, 6):
        assert gx.xp_to_stars(gx.stars_to_xp(stars)) == stars
    assert gx.stars_to_xp(0) == gx.XP_TO_STAR[0]
    assert gx.stars_to_xp(9) == gx.XP_TO_STAR[-1]
    assert gx.xp_to_stars(0) == 1
    assert gx.xp_to_stars(10_000_000) == 5


def test_skillmoves_is_zero_indexed() -> None:
    # players table stores 0..4 => 1..5 stars (verified in base_players.csv).
    assert [gx.field_value_to_stars("skillmoves", v) for v in range(0, 5)] == [
        1, 2, 3, 4, 5
    ]
    assert gx.field_value_to_xp("skillmoves", 4) == gx.XP_TO_STAR[4]
    assert gx.field_value_to_xp("skillmoves", 0) == gx.XP_TO_STAR[0]


def test_weakfoot_is_one_indexed() -> None:
    # players table stores 1..5 => 1..5 stars.
    assert [
        gx.field_value_to_stars("weakfootabilitytypecode", v) for v in range(1, 6)
    ] == [1, 2, 3, 4, 5]
    assert gx.field_value_to_xp("weakfootabilitytypecode", 5) == gx.XP_TO_STAR[4]
    assert gx.field_value_to_xp("weakfootabilitytypecode", 1) == gx.XP_TO_STAR[0]


def test_star_fields_do_not_share_an_offset() -> None:
    # 5-star skill moves (stored 4) and 5-star weak foot (stored 5) must land on
    # the same XP; treating skillmoves as 1-indexed would cost a whole star.
    assert gx.field_value_to_xp("skillmoves", 4) == gx.field_value_to_xp(
        "weakfootabilitytypecode", 5
    )
    assert gx.field_value_to_xp("skillmoves", 3) != gx.field_value_to_xp(
        "weakfootabilitytypecode", 3
    )


def test_star_field_clamping() -> None:
    assert gx.field_value_to_stars("skillmoves", -3) == 1
    assert gx.field_value_to_stars("skillmoves", 12) == 5
    assert gx.field_value_to_stars("weakfootabilitytypecode", 0) == 1
    assert gx.field_value_to_stars("weakfootabilitytypecode", 99) == 5


def test_star_round_trip_through_field_values() -> None:
    for value in range(0, 5):
        xp = gx.field_value_to_xp("skillmoves", value)
        assert gx.xp_to_field_value("skillmoves", xp) == value
    for value in range(1, 6):
        xp = gx.field_value_to_xp("weakfootabilitytypecode", value)
        assert gx.xp_to_field_value("weakfootabilitytypecode", xp) == value


def test_field_value_to_stars_rejects_non_star_fields() -> None:
    with pytest.raises(ValueError):
        gx.field_value_to_stars("finishing", 3)


# ── growable membership ──────────────────────────────────────────────


def test_is_growable() -> None:
    for field in gx.GROWTH_FIELDS_ORDERED:
        assert gx.is_growable(field)
    assert gx.is_growable("SkillMoves")  # case/space tolerant
    assert gx.is_growable(" finishing ")
    for field in (
        "overallrating",
        "potential",
        "height",
        "weight",
        "preferredfoot",
        "preferredposition1",
        "nationality",
        "trait1",
        "modifier",
        "internationalrep",
        "usercaneditname",
        "",
        None,
    ):
        assert not gx.is_growable(field)


def test_is_star_field() -> None:
    assert gx.is_star_field("skillmoves")
    assert gx.is_star_field("weakfootabilitytypecode")
    assert not gx.is_star_field("acceleration")


def test_field_value_to_xp_rejects_non_growable() -> None:
    with pytest.raises(ValueError):
        gx.field_value_to_xp("overallrating", 90)
    with pytest.raises(ValueError):
        gx.xp_to_field_value("height", 1000)


def test_default_mode_is_raw() -> None:
    """Raw is the shipped default; XP is opt-in until probed on a live save.

    Writing XP when the API wants a raw value maps 99 -> attribute 1, and the
    development plan outranks the players table, so the mistake would visibly
    collapse a player. Raw is the behaviour v1 has always used in the field.
    """
    assert gx.DEFAULT_VALUE_MODE == "raw"
    assert gx.growth_updates([("finishing", 90)]) == [("finishing", 90)]


def test_raw_and_xp_modes_differ() -> None:
    raw = dict(gx.growth_updates([("finishing", 90)], mode="raw"))
    xp = dict(gx.growth_updates([("finishing", 90)], mode="xp"))
    assert raw["finishing"] == 90
    assert xp["finishing"] == gx.attribute_to_xp(90)
    assert raw["finishing"] != xp["finishing"]


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError):
        gx.growth_updates([("finishing", 90)], mode="bogus")


def test_raw_mode_still_filters_non_growable() -> None:
    updates = dict(gx.growth_updates([("finishing", 90), ("headtypecode", 12)], mode="raw"))
    assert "finishing" in updates
    assert "headtypecode" not in updates


def test_growth_updates_filters_and_converts() -> None:
    updates = gx.growth_updates(
        mode="xp",
        fields=[
            ("finishing", 99),
            ("overallrating", 99),  # not growth-tracked
            ("height", 185),  # not growth-tracked
            ("skillmoves", 4),
            ("weakfootabilitytypecode", 5),
            ("preferredfoot", 1),
        ]
    )
    assert [f for f, _ in updates] == [
        "finishing",
        "skillmoves",
        "weakfootabilitytypecode",
    ]
    assert dict(updates)["finishing"] == gx.XP_TO_ATTRIBUTE[98]
    assert dict(updates)["skillmoves"] == gx.XP_TO_STAR[4]


def test_growth_updates_accepts_mapping_and_skips_blanks() -> None:
    updates = dict(
        gx.growth_updates({"finishing": 80, "curve": "", "vision": None}, mode="xp")
    )
    assert updates == {"finishing": gx.attribute_to_xp(80)}


def test_growth_updates_dedupes_keeping_last_value() -> None:
    updates = gx.growth_updates([("finishing", 50), ("finishing", 90)], mode="xp")
    assert updates == [("finishing", gx.attribute_to_xp(90))]


def test_growth_updates_rejects_bare_strings() -> None:
    with pytest.raises(TypeError):
        gx.growth_updates(["finishing", "curve"])


# ── generated Lua ────────────────────────────────────────────────────

LE_GLOBALS = (
    "IsInCM",
    "PlayerHasDevelopementPlan",
    "PlayerSetValueInDevelopementPlan",
    "Log",
)

SAMPLE_FIELDS = [
    ("acceleration", 95),
    ("finishing", 99),
    ("skillmoves", 4),
    ("weakfootabilitytypecode", 5),
    ("overallrating", 99),
    ("height", 180),
]


def _block(status_path: str = "C:/q/_growth_status.txt", mode: str = "xp") -> str:
    return gx.growth_sync_block(158023, SAMPLE_FIELDS, status_path=status_path, mode=mode)


def test_block_raw_mode_writes_raw_values() -> None:
    lua = gx.growth_sync_block(158023, [("finishing", 90)], mode="raw")
    assert '{"finishing", 90},' in lua
    assert f'{{"finishing", {gx.attribute_to_xp(90)}}},' not in lua


def test_block_contains_the_required_guards() -> None:
    lua = _block()
    assert 'type(IsInCM) ~= "function"' in lua
    assert 'type(PlayerHasDevelopementPlan) ~= "function"' in lua
    assert 'type(PlayerSetValueInDevelopementPlan) ~= "function"' in lua
    assert 'type(Log) == "function"' in lua
    assert "pcall(PlayerHasDevelopementPlan, g_pid)" in lua
    assert "pcall(PlayerSetValueInDevelopementPlan, g_pid, fname, fxp)" in lua
    assert "pcall(IsInCM)" in lua


def test_block_never_calls_an_le_global_directly() -> None:
    """Every LE global is passed to pcall as a value, never invoked as `Name(`."""
    lua = _block()
    for name in LE_GLOBALS:
        direct = re.search(rf"(?<![.:\w]){re.escape(name)}\s*\(", lua)
        assert direct is None, f"{name} is called directly at {direct}"


def test_block_guards_every_global_it_references() -> None:
    lua = _block()
    for name in LE_GLOBALS:
        assert f"type({name})" in lua, f"{name} referenced without a type() guard"


def test_block_reports_no_dev_plan_and_not_in_career_cleanly() -> None:
    lua = _block()
    assert 'g_report(true, 0, 0, false, "no_dev_plan")' in lua
    assert 'g_report(true, 0, 0, false, "not_in_career")' in lua
    # A missing growth API is a real failure, not a clean no-op.
    assert 'g_report(false, 0, 0, false, "no_growth_api")' in lua
    assert 'reason=%s' in lua


def test_block_status_line_matches_project_job_format() -> None:
    lua = _block()
    assert (
        '"job=growth id=%d ok=%s written=%d failed=%d total=%d plan=%s reason=%s"'
        in lua
    )
    assert "g_written = g_written + 1" in lua
    assert "g_failed = g_failed + 1" in lua


def test_block_wraps_everything_in_pcall_and_reports_lua_errors() -> None:
    lua = _block()
    assert "local g_ok, g_err = pcall(function()" in lua
    assert 'g_report(false, 0, 0, false, "lua_error", g_msg)' in lua
    # io.open is wrapped too — a locked status file must not kill the job.
    assert "pcall(function()\n    local f = io.open(g_status_path" in lua


def test_block_only_writes_growable_fields_with_xp_values() -> None:
    lua = _block()
    assert f'{{"finishing", {gx.XP_TO_ATTRIBUTE[98]}}},' in lua
    assert f'{{"acceleration", {gx.attribute_to_xp(95)}}},' in lua
    assert f'{{"skillmoves", {gx.XP_TO_STAR[4]}}},' in lua
    assert f'{{"weakfootabilitytypecode", {gx.XP_TO_STAR[4]}}},' in lua
    assert "overallrating" not in lua
    assert "height" not in lua
    assert "local g_total = 4" in lua
    # Raw attribute values must never reach the setter (the pre-existing bug:
    # 99 XP is attribute 1, not 99).
    assert '{"finishing", 99}' not in lua


def test_block_is_scoped_and_splices_into_a_host_job() -> None:
    lua = _block()
    assert lua.startswith("do\n")
    assert lua.rstrip().endswith("end")
    assert lua.count("\r") == 0
    # Locals are namespaced so they cannot collide with the apply job's own
    # `fields` / `written` / `write_status` / `target_playerid`.
    for host_local in (
        "local fields",
        "local written",
        "local write_status",
        "local target_playerid",
    ):
        assert host_local not in lua


def test_block_default_status_path_is_separate_from_job_status() -> None:
    lua = gx.growth_sync_block(1, [("finishing", 90)])
    assert gx.GROWTH_STATUS_NAME in lua
    assert "_job_status.txt" not in lua


def test_empty_field_set_still_produces_a_safe_block() -> None:
    lua = gx.growth_sync_block(158023, [])
    assert "local g_total = 0" in lua
    assert 'g_report(true, 0, 0, false, "no_growable_fields")' in lua
    assert lua.startswith("do\n")


def test_only_non_growable_fields_yields_an_empty_block() -> None:
    lua = gx.growth_sync_block(7, [("overallrating", 99), ("height", 190)])
    assert "local g_total = 0" in lua


def test_generate_growth_sync_lua_is_a_standalone_job() -> None:
    lua = gx.generate_growth_sync_lua(158023, SAMPLE_FIELDS, status_path="C:/q/s.txt")
    assert lua.startswith("--[[ LE Companion growth sync | id=158023 ]]")
    assert "do\n" in lua
    assert 'g_status_path = "C:/q/s.txt"' in lua
    assert "job=growth id=%d" in lua


def test_generate_growth_sync_lua_defaults_to_job_status_file() -> None:
    lua = gx.generate_growth_sync_lua(158023, [("finishing", 90)])
    assert "_job_status.txt" in lua


def test_windows_paths_are_escaped_for_lua() -> None:
    lua = gx.growth_sync_block(1, [("finishing", 90)], status_path="C:\\q\\s.txt")
    assert 'g_status_path = "C:\\\\q\\\\s.txt"' in lua


def test_player_id_is_numeric_in_lua() -> None:
    lua = gx.growth_sync_block("158023", [("finishing", 90)])
    assert "local g_pid = 158023" in lua


def test_status_line_is_readable_by_the_project_status_parser() -> None:
    """The growth line must survive apply_service.parse_job_status unchanged.

    Imported lazily/skippably: apply_service is owned elsewhere and this test
    only asserts the contract the two modules share.
    """
    apply_service = pytest.importorskip("src.apply_service")
    parse = apply_service.parse_job_status

    ok_line = (
        "job=growth id=158023 ok=true written=36 failed=0 "
        "total=36 plan=true reason=ok"
    )
    parsed = parse(ok_line)
    assert parsed["job"] == "growth"
    assert parsed["player_id"] == 158023
    assert parsed["ok"] is True
    assert parsed["written"] == 36
    assert parsed["failed_writes"] == 0
    assert parsed["failed"] is False

    # A player without a development plan is a clean no-op, not a failure.
    no_plan = parse(
        "job=growth id=158023 ok=true written=0 failed=0 "
        "total=36 plan=false reason=no_dev_plan"
    )
    assert no_plan["failed"] is False
    assert no_plan["reason"] == "no_dev_plan"

    # A missing LE API is a failure the user must see.
    missing = parse(
        "job=growth id=158023 ok=false written=0 failed=0 "
        "total=36 plan=false reason=no_growth_api"
    )
    assert missing["failed"] is True


def test_lua_has_balanced_do_end_and_no_format_leftovers() -> None:
    lua = _block()
    assert "{pid}" not in lua and "{rows}" not in lua and "{status_path}" not in lua
    # Naive brace balance: the Lua table literal must be closed.
    assert lua.count("{") == lua.count("}")
