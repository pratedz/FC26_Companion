"""A capped or interrupted source must never publish a partial catalog."""
import json
from unittest.mock import patch

import pytest

from src import futgg_client as f


def definition(slug, overall=80):
    return {
        "game": "27", "slug": slug, "eaId": int(slug.split('-')[1]),
        "basePlayerEaId": 123, "firstName": "Test", "lastName": "Player",
        "overall": overall, "position": 25, "skillMoves": 4,
        "rarity": {"name": "Future Stars"},
        **{key: 70 for key in f.ATTR_MAP},
    }


def stub(slug, overall=80):
    return {"slug": slug, "game": "27", "overall": overall}


def test_capped_query_is_split_and_complete(tmp_path):
    def listing(year, page=1, count=50, *, overall_min=None, overall_max=None):
        if (overall_min, overall_max) == (0, 99):
            return {"total": 10000, "data": [], "next": 2}
        if overall_min == 0:
            return {"total": 1, "data": [stub("27-1", 40)], "next": None}
        if page == 1:
            return {"total": 2, "data": [stub("27-2")], "next": 2}
        return {"total": 2, "data": [stub("27-3")], "next": None}

    with patch.object(f, "futgg_cache_dir", return_value=tmp_path), \
         patch.object(f, "list_page", side_effect=listing), \
         patch.object(f, "fetch_definitions", side_effect=lambda slugs: [definition(s) for s in slugs]):
        stats = f.sync_year(27, sleep_s=0)
    rows = [json.loads(line) for line in (tmp_path / 'futgg_27.jsonl').read_text().splitlines()]
    assert {r['slug'] for r in rows} == {'27-1', '27-2', '27-3'}
    assert stats['complete'] and stats['total_reported'] == stats['written'] == 3


@pytest.mark.parametrize('failure', ['page', 'count', 'duplicate', 'definitions', 'attributes', 'cap', 'limit'])
def test_bad_download_preserves_previous_cache(tmp_path, failure):
    target = tmp_path / 'futgg_27.jsonl'
    target.write_text('previous good data\n')

    def listing(year, page=1, count=50, *, overall_min=None, overall_max=None):
        if failure == 'cap':
            return {'total': 10000, 'data': [], 'next': None}
        if page == 2:
            if failure == 'page':
                raise f.FutGGError('source unavailable')
            return {'total': 2, 'data': [stub('27-1')], 'next': None}
        return {'total': 2 if failure in ['page', 'count', 'duplicate', 'limit'] else 1,
                'data': [stub('27-1')],
                'next': 2 if failure in ['page', 'duplicate', 'limit'] else None}

    def defs(slugs):
        if failure == 'definitions':
            return []
        result = [definition(s) for s in slugs]
        if failure == 'attributes':
            result[0].pop('attributeAcceleration')
        return result

    with patch.object(f, 'futgg_cache_dir', return_value=tmp_path), \
         patch.object(f, 'list_page', side_effect=listing), \
         patch.object(f, 'fetch_definitions', side_effect=defs), \
         patch.object(f.time, 'sleep'):
        with pytest.raises(f.FutGGError):
            f.sync_year(27, sleep_s=0, max_pages=1 if failure == 'limit' else None)
    assert target.read_text() == 'previous good data\n'


def test_list_page_sends_bounded_rating_filters():
    with patch.object(f, '_http_get_json', return_value={}) as get:
        f.list_page(27, overall_min=50, overall_max=62)
    assert 'overall__gte=50' in get.call_args.args[0]
    assert 'overall__lte=62' in get.call_args.args[0]
    assert 'show_holographics=true' in get.call_args.args[0]


def test_holographic_variant_keeps_separate_id_and_label():
    item = definition('27-67146440')
    item.update(holographicType='pristine', standardItemEaId=37576)
    row = f.definition_to_le_row(item)
    assert row['slug'] == '27-67146440'
    assert row['standardItemEaId'] == 37576
    assert row['revision'] == 'Future Stars (Pristine Holographic)'
    assert row['base_rarity'] == 'Future Stars'


def test_backup_releases_windows_file_handles(tmp_path):
    import sqlite3
    from tools import sync_futgg_database as sync
    import tempfile
    with tempfile.TemporaryDirectory(dir=tmp_path) as temp:
        source = __import__('pathlib').Path(temp) / 'source.sqlite'
        target = source.with_name('target.sqlite')
        conn = sqlite3.connect(source)
        conn.execute('CREATE TABLE sample (value TEXT)')
        conn.execute("INSERT INTO sample VALUES ('preserved')")
        conn.commit()
        conn.close()
        sync.backup(source, target)
    assert not source.exists()


def test_universe_update_keeps_history_and_item_identity(tmp_path, monkeypatch):
    import sqlite3
    from tools import sync_futgg_database as sync
    from tools import build_universe as ub

    old_row = f.definition_to_le_row(definition('27-1'))
    rows = [old_row, f.definition_to_le_row(definition('27-2'))]
    data = tmp_path / 'futgg_27.jsonl'
    data.write_text(json.dumps(old_row) + '\n', encoding='utf-8')
    db = tmp_path / 'universe.sqlite'
    conn = sqlite3.connect(db)
    conn.executescript(ub.SCHEMA)
    builder = ub.Builder(conn, tmp_path, tmp_path, False, verbose=False)
    builder._load_futgg_file(data)
    builder.build_persons()
    builder.build_fts()
    # Seed a historical observation which must remain byte-for-byte identical.
    obs = list(conn.execute('SELECT * FROM observation').fetchone())
    columns = [r[1] for r in conn.execute('PRAGMA table_info(observation)')]
    obs[0] = 2
    obs[columns.index('year')] = 26
    conn.execute(f"INSERT INTO observation VALUES ({','.join('?' for _ in columns)})", obs)
    conn.commit()
    before = sync.historical(conn, 'universe')
    conn.close()
    data.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    monkeypatch.setattr(sync, 'DATA', data)
    sync.update_universe(db)
    with sqlite3.connect(db) as conn:
        assert sync.historical(conn, 'universe') == before
        assert conn.execute("SELECT obs_id FROM observation WHERE year=27 AND variant_id='1'").fetchone() == (1,)
        assert conn.execute("SELECT skillmoves FROM observation WHERE year=27 ORDER BY obs_id").fetchall() == [(4,), (4,)]
        assert conn.execute('SELECT count(*) FROM observation WHERE year=27').fetchone() == (2,)
