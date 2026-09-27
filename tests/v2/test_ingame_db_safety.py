from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "ingame" / "le_companion" / "db.lua"
OPS = ROOT / "ingame" / "le_companion" / "ops.lua"


def test_live_records_use_valid_record_iterator() -> None:
    source = DB.read_text(encoding="utf-8")
    records = source.split("function Handle:records()", 1)[1].split(
        "local function load_le_table", 1
    )[0]

    assert "GetFirstRecord" in records
    assert "GetNextValidRecord" in records
    assert "first_record + self.record_size" not in records


def test_squad_export_prefers_host_membership_and_defers_empty_db() -> None:
    source = OPS.read_text(encoding="utf-8")
    export = source.split('ops.handlers["export_squad"]', 1)[1].split(
        'ops.handlers["transfer"]', 1
    )[0]

    assert 'collect_ids("GetUserSeniorTeamPlayerIDs"' in export
    assert "for prec in ph:records()" in export
    assert 'reason = "db_not_ready"' in export
    assert "deferred = true" in export
    assert "attempts = 0" in export
    assert "squad_empty" not in export


def test_squad_export_prefers_edited_names_over_the_cached_host_name() -> None:
    source = OPS.read_text(encoding="utf-8")
    export = source.split('ops.handlers["export_squad"]', 1)[1].split(
        'ops.handlers["transfer"]', 1
    )[0]

    assert 'db.open("editedplayernames")' in export
    assert "local edited_names = {}" in export
    assert "if edited_names[pid] then" in export
    assert export.index("if edited_names[pid] then") < export.rindex("GetPlayerName")


def test_name_inserts_use_text_playerids_and_a_fresh_verified_lookup() -> None:
    add_source = (ROOT / "ingame" / "le_companion" / "ops" / "add_to_team.lua").read_text(encoding="utf-8")
    ops_source = OPS.read_text(encoding="utf-8")

    assert "playerid = pid_s" in add_source
    assert 'pcall(_G.GetDBTableRows, "editedplayernames")' in add_source
    assert 'h, rec = db.resolve("editedplayernames", "playerid", pid)' in add_source
    assert 'if key_field == "playerid" then insert_key = tostring(key_value) end' in ops_source
    # Blank-name regression: never treat a shell row as success.
    assert "durable_name_ok" in add_source
    assert "name_row_content_matches" in add_source
    assert "prev_nameids" in add_source
