"""Signing uses safe generic heads without pretending they are real faces."""
import csv
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import base_players, paths
from companion.app.commands import team
from companion.domain.catalog import ImportPlan, LocalCatalog
from companion.domain.card_import import card_source_playerid
from companion.platform.base_players import resolve_card_base_profile
from companion.ui.surfaces.sign.search import _card_row
from companion.ui.surfaces.sign.results import can_add
from companion.ui.surfaces.sign.review import face_label, face_summary


@pytest.fixture
def base_data(tmp_path, monkeypatch):
    path = tmp_path / 'base_players.csv'
    fields = ['playerid', 'gender', 'headassetid', 'headclasscode',
              'headtypecode', 'headvariation', 'hashighqualityhead',
              'hairtypecode', 'haircolorcode', 'firstnameid', 'lastnameid',
              'commonnameid', 'playerjerseynameid', 'nationality', 'birthdate']
    values = [
        (69957, 0, 69957, 1, 1503, 0, 0, 10, 2, 1, 2, 0, 0, 52, 150000),
        (110000, 1, 110000, 1, 1200, 0, 0, 12, 3, 3, 4, 0, 0, 14, 152000),
        (37576, 0, 37576, 0, 527, 0, 0, 11, 1, 5, 6, 7, 0, 54, 144000),
    ]
    with path.open('w', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(fields)
        writer.writerows(values)
    monkeypatch.setattr(paths, 'base_players_csv_path', lambda: path)
    base_players.clear_cache()
    yield path
    base_players.clear_cache()


def service(metadata=None):
    return SimpleNamespace(catalog=SimpleNamespace(
        prepare_cross_year_import=lambda card, **kwargs: ImportPlan(
            target_playerid=1, fields={'overallrating': 88}, source={}),
        signing_metadata=lambda card: metadata or {}))


def test_fc26_native_generic_appearance_can_be_added(base_data):
    row = _card_row({'name': 'Juan Manuel Elordi', 'person_id': 69957,
                     'overallrating': 69, 'year': '27', 'revision': 'Rare'})
    assert row['face'] == 'Generic'
    assert can_add(row), 'A known native generic head must not block signing'


def test_native_generic_uses_own_cosmetics_and_clears_real_asset(base_data):
    plan = team.prepare_card_for_team(service(), {'name': 'Elordi', 'person_id': 69957})
    assert plan.face_status == 'Generic'
    assert plan.face['headassetid'] == 0
    assert plan.face['headclasscode'] == 1
    assert plan.face['hashighqualityhead'] == 0
    assert plan.face['headtypecode'] == 1503
    assert plan.face['hairtypecode'] == 10
    assert plan.fields['gender'] == 0
    assert plan.name_strategy == 'native_ids'
    entry = team.TeamAddEntry({'name': 'Elordi'}, plan, 91234)
    assert not entry.face_verified
    assert face_label(entry) == 'Native generic face'


def test_unknown_card_uses_same_gender_generic_template(base_data):
    metadata = {'gender': 1, 'nationality': 14, 'dateOfBirth': '2007-02-12'}
    plan = team.prepare_card_for_team(service(metadata), {'name': 'New player', 'person_id': 300001})
    assert plan.face_status == 'Fallback'
    assert plan.fields['gender'] == 1 and plan.fields['nationality'] == 14
    assert plan.face['headtypecode'] == 1200 and plan.face['hairtypecode'] == 12
    assert plan.face['headassetid'] == 0 and plan.face['headclasscode'] == 1
    assert plan.name_strategy == 'custom'
    assert can_add(_card_row({'name': 'New player', 'person_id': 300001}))
    entry = team.TeamAddEntry({'name': 'New player'}, plan, 91234)
    preview = team.TeamAddPreview((entry,), 1)
    assert preview.face_verified_count == 0
    assert face_label(entry) == 'Generic fallback'
    assert "do not reproduce the player's real likeness" in face_summary(preview)


def test_real_face_keeps_exact_native_head_quartet(base_data):
    plan = team.prepare_card_for_team(service(), {'name': 'Ronaldo', 'person_id': 37576})
    assert plan.face_status == 'Verified'
    assert plan.face['headassetid'] == 37576
    assert plan.face['headclasscode'] == 0
    assert plan.face['hashighqualityhead'] == 0
    assert plan.face['headtypecode'] == 527
    assert team.TeamAddEntry({'name': 'Ronaldo'}, plan, 91234).face_verified


def test_missing_source_data_stays_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'base_players_csv_path', lambda: tmp_path / 'missing.csv')
    base_players.clear_cache()
    try:
        assert not can_add(_card_row({'name': 'New player', 'person_id': 300001}))
        plan = team.prepare_card_for_team(service(), {'name': 'New player', 'person_id': 300001})
        assert plan.face_status == 'Missing' and not plan.face
    finally:
        base_players.clear_cache()


def test_base_id_alias_never_uses_holographic_item_id():
    assert card_source_playerid({'basePlayerEaId': 37576, 'eaId': 67146440}) == 37576
    assert card_source_playerid({'eaId': 67146440, 'variant_id': 67146440}) is None


def test_registered_install_wins_over_unrelated_parent_lua(tmp_path, monkeypatch):
    from companion.platform import registry
    desktop = tmp_path / 'Desktop'
    source = desktop / 'Companion_src'
    source.mkdir(parents=True)
    (desktop / 'lua/scripts').mkdir(parents=True)
    registered = desktop / 'Live Editor'
    registered.mkdir()
    (registered / 'FCLiveEditor.DLL').touch()
    monkeypatch.setattr(paths, 'app_root', lambda: source)
    monkeypatch.setattr(registry, 'resolve_install_root', lambda hint: registry.Resolution(registered, registry.Source.ENV))
    assert paths.le_root() == registered


def test_metadata_matches_item_and_base_id_and_caches_year(tmp_path):
    db = tmp_path / 'catalog.sqlite'
    payload = {'playerid': 300001, 'gender': 1, 'dateOfBirth': '2007-02-12'}
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE cards(year TEXT,slug TEXT,playerid TEXT,payload TEXT)')
        con.execute('INSERT INTO cards VALUES (?,?,?,?)', ('27', '27-50331649', '300001', json.dumps(payload)))
    cat = LocalCatalog(catalog_path=db)
    card = {'person_id': 300001, 'year': 27, 'variant_id': '50331649'}
    assert cat.signing_metadata(card)['gender'] == 1
    assert cat.signing_metadata(dict(card, person_id=37576)) == {}
    assert cat.signing_metadata(dict(card, year=26)) == {}
    assert cat.signing_metadata(card)['dateOfBirth'] == '2007-02-12'
    assert len(cat._signing_metadata_cache[27][1]) == 1


def test_review_refuses_changed_appearance_with_same_status():
    plan = team.TeamCardPlan({'overallrating': 80}, {'commonname': 'One'},
                             {'headassetid': 0, 'headclasscode': 1, 'hairtypecode': 10}, face_status='Generic')
    entry = team.TeamAddEntry({'name': 'One'}, plan, 91234)
    from dataclasses import replace
    changed = replace(entry, plan=replace(plan, face=dict(plan.face, hairtypecode=12)))
    assert team._preview_signature(team.TeamAddPreview((entry,), 1)) != team._preview_signature(team.TeamAddPreview((changed,), 1))


def test_missing_face_plan_cannot_be_queued():
    from companion.domain.job import JobValidationError
    plan = team.TeamCardPlan({}, {}, {}, face_status="Missing")
    with pytest.raises(JobValidationError, match="No safe FC26 face template"):
        team._player_payload(plan)
