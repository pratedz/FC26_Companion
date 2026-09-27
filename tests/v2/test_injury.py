"""Injuries page — job wire shape and scan-result parser (no game required)."""

from __future__ import annotations

from companion.app.commands.injury import parse_scan_names, parse_scan_players, return_date_label
from companion.domain.job import Job, op_injury_cure, op_injury_scan


def test_injury_scan_job_wire_shape():
    job = Job(
        ops=(op_injury_scan("scan"),),
        label="Scan squad injuries",
        origin="ui.injury.scan",
        require_cm=True,
    )
    wire = job.to_wire(now=1)
    assert wire["require_cm"] is True
    assert wire["ops"] == [{"op": "injury.scan", "id": "scan"}]


def test_injury_cure_job_wire_shape_empty_means_all():
    job = Job(
        ops=(op_injury_cure("cure", playerids=()),),
        label="Cure injured players",
        origin="ui.injury.cure",
        require_cm=True,
    )
    wire = job.to_wire(now=1)
    assert wire["require_cm"] is True
    assert wire["ops"] == [
        {"op": "injury.cure", "id": "cure", "playerids": []},
    ]


def test_injury_cure_job_wire_shape_with_ids():
    job = Job(
        ops=(op_injury_cure("cure", playerids=(158023, 20801)),),
        label="Cure 2 injured",
        origin="ui.injury.cure",
        require_cm=True,
    )
    wire = job.to_wire(now=1)
    assert wire["ops"][0]["playerids"] == [158023, 20801]


def test_parse_scan_names_from_fake_result():
    data = {
        "players": [
            {"playerid": 158023, "name": "L. Messi", "injury": "Hamstring"},
            {"playerid": 20801, "name": "Ronaldo", "injury": "Ankle"},
            {"playerid": 999, "name": "", "injury": "Knee"},
        ]
    }
    assert parse_scan_names(data) == ["L. Messi", "Ronaldo", "999"]
    rows = parse_scan_players(data)
    assert rows[0] == {
        "playerid": 158023,
        "name": "L. Messi",
        "injury": "Hamstring",
    }
    assert parse_scan_names({"players": []}) == []
    assert parse_scan_names(None) == []


def test_injury_recovery_date_is_preserved_and_invalid_dates_are_hidden():
    rows = parse_scan_players({"players": [{"playerid": 73669, "name": "Carlos Alberto", "return_date": 20270401}]})
    assert rows[0]["return_date"] == 20270401
    assert return_date_label(rows[0]["return_date"]) == "1 Apr 2027"
    for value in (None, 0, "20270231", "broken", 20270401.5):
        assert return_date_label(value) == ""
