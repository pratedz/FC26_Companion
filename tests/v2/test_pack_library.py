"""Product pack migration + CLI surface (v2.6.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.domain.job import JobValidationError
from companion.domain.pack_library import actions, build_job, load_v1_packs
from companion.domain.teams import search_clubs


ROOT = Path(__file__).parents[2]


def test_all_v1_packs_are_inventoried():
    packs = load_v1_packs()
    assert len(packs) == 19
    items = actions(packs)
    # 19 product packs + companion extras + workflow aliases (e.g. pre_match)
    assert len(items) >= 19
    by_id = {p.id: p for p in items}
    assert by_id["matchday"].mode == "native"
    assert by_id["never_retire"].mode == "pending"
    assert by_id["unlocks_all"].mode == "pending"
    assert by_id["match_ready"].mode == "native"
    assert by_id["pre_match"].mode == "native"
    assert by_id["growth_sync"].mode == "native"
    assert by_id["ping"].mode == "native"
    assert by_id["recurring_fitness"].mode == "pending"


def test_pre_match_alias_builds_same_career_shape_as_pre_match_full():
    alias = build_job("pre_match").to_wire()
    full = build_job("pre_match_full").to_wire()
    assert [o["op"] for o in alias["ops"]] == [o["op"] for o in full["ops"]]
    assert alias["ops"][0]["op"] == "career.set"


def test_native_pack_jobs_are_typed_and_whole_save_packs_are_withheld():
    career = build_job("matchday").to_wire()
    assert career["ops"][0]["op"] == "career.set"
    with pytest.raises(JobValidationError, match="Whole-save"):
        build_job("never_retire")
    workflow = build_job("match_ready").to_wire()
    ops = [o["op"] for o in workflow["ops"]]
    assert "export_squad" in ops
    assert "career.set" in ops


def test_pending_pack_never_builds_a_job():
    with pytest.raises(JobValidationError, match="unlock|verified"):
        build_job("unlocks_all")


def test_cli_exposes_packs_search_budget_and_clubs():
    from companion.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["packs"]).cmd == "packs"
    args = parser.parse_args(["run-pack", "never_retire", "--confirm-all"])
    assert args.confirm_all is True
    assert parser.parse_args(["search-cards", "Neymar"]).query == "Neymar"
    assert parser.parse_args(["best-match", "Messi"]).name == "Messi"
    assert parser.parse_args(["search-clubs", "Barcelona"]).query == "Barcelona"
    assert parser.parse_args(["budget", "--amount", "50000000"]).amount == 50_000_000


def test_team_search_finds_real_clubs_when_db_present():
    db = ROOT / "card_db" / "teams.sqlite"
    if not db.is_file():
        return
    hits = search_clubs(db, "Barcelona", limit=5)
    assert hits
    assert any("barcel" in str(h.get("name", "")).lower() for h in hits)
    by_id = search_clubs(db, str(hits[0]["team_id"]), limit=1)
    assert by_id and by_id[0]["team_id"] == hits[0]["team_id"]
