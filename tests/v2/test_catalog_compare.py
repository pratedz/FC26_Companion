"""Library multi-card compare drives the real LocalCatalog.compare API."""

from __future__ import annotations

from companion.domain.catalog import LocalCatalog


def test_compare_two_cards_reports_changed_fields_via_shipped_api():
    before = {
        "overallrating": 86,
        "acceleration": 80,
        "sprintspeed": 82,
        "finishing": 75,
        "name": "Player A",
    }
    after = {
        "overallrating": 91,
        "acceleration": 90,
        "sprintspeed": 82,
        "finishing": 88,
        "name": "Player B",
    }
    rows = LocalCatalog.compare(before, after)
    assert rows
    by_field = {r["field"]: r for r in rows}
    assert by_field["overallrating"]["changed"] is True
    assert by_field["overallrating"]["delta"] == 5
    assert by_field["sprintspeed"]["changed"] is False
    assert by_field["finishing"]["delta"] == 13


def test_compare_ignores_identity_noise_outside_import_fields():
    rows = LocalCatalog.compare(
        {"playerid": 1, "teamid": 10, "overallrating": 80},
        {"playerid": 2, "teamid": 99, "overallrating": 85},
    )
    fields = {r["field"] for r in rows}
    assert "overallrating" in fields
    # Identity keys are not in the import/attr field set.
    assert "playerid" not in fields
    assert "teamid" not in fields


def test_compare_reports_height_and_bodytype_deltas():
    rows = LocalCatalog.compare(
        {"height": 187, "bodytypecode": 1, "overallrating": 91},
        {"height": 185, "bodytypecode": 187, "overallrating": 94},
    )
    by_field = {r["field"]: r for r in rows}
    assert by_field["height"]["changed"] is True
    assert by_field["height"]["delta"] == -2
    assert by_field["bodytypecode"]["changed"] is True
    assert by_field["bodytypecode"]["after"] == 187
    assert by_field["overallrating"]["delta"] == 3
