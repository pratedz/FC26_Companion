"""Card collections, Best/Base/All picks and collection filters in Sign and Suggestions."""
from datetime import datetime, timezone
import itertools
import json
import sqlite3

import pytest

from companion.app.services import Services
from companion.app.state import AppState, BridgeState, Prefs, SquadState
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain import card_collections as cc
from companion.domain.add_player import CardRecommendation, resolve_recommendation_report
from companion.domain.catalog import LocalCatalog
from companion.ui.surfaces.sign.card_filters import (
    ANY_COLLECTION, apply_menu_value, menu_value, repick_cards, set_view_pick, view_pick,
)
from companion.ui.surfaces.sign.search import _card_row, shop_cards, shop_feedback

# (obs_id, person_id, name, year, overall, label, club, source_kind)
CARDS = (
    (1, 10, "Paolo Maldini", 27, 93, "Base Icon", "ICON", "fut"),
    (2, 10, "Paolo Maldini", 26, 92, "Icon", "ICON", "fut"),
    (3, 10, "Paolo Maldini", 26, 96, "Team of the Year ICON", "ICON", "fut"),
    (4, 10, "Paolo Maldini", 27, 93, "Base Icon (Pristine Holographic)", "ICON", "fut"),
    (5, 20, "Joe Cole", 26, 87, "Heroes", "HERO", "fut"),
    (6, 20, "Joe Cole", 26, 90, "Prime Hero", "HERO", "fut"),
    (7, 20, "Joe Cole", 26, 92, "FUT Birthday Hero", "Chelsea", "fut"),
    (8, 30, "Bukayo Saka", 27, 88, "Rare", "Arsenal", "fut"),
    (9, 30, "Bukayo Saka", 27, 90, "Team of the week", "Arsenal", "fut"),
    (10, 30, "Bukayo Saka", 26, 87, "Rare", "Arsenal", "fut"),
    (11, 30, "Bukayo Saka", 26, 94, "Ultimate Rewind Winter Wildcards", "Arsenal", "fut"),
    (12, 30, "Bukayo Saka", 26, 87, "base", "Arsenal", "career"),
    (13, 40, "Jamie Silva", 26, 70, "Common", "Benfica", "fut"),
    (14, 50, "Low Bronze", 26, 60, "Common", "Club", "fut"),
    (15, 60, "Kylian Mbappe", 26, 97, "Pre-Season FUTTIES", "Real Madrid", "fut"),
)


def _write_universe(path):
    with sqlite3.connect(path) as con:
        con.executescript("""
        CREATE TABLE person(person_id INTEGER PRIMARY KEY,display_name TEXT,name_norm TEXT,exists_in_fc26 INT);
        CREATE TABLE observation(obs_id INTEGER PRIMARY KEY,person_id INT,year INT,overall INT,
          potential INT,positions_text TEXT,preferredposition1 INT,preferredposition2 INT,
          preferredposition3 INT,preferredposition4 INT,source_kind TEXT,source TEXT,
          variant TEXT,variant_id TEXT,club_name TEXT,nationality_name TEXT,attrs TEXT);
        """)
        for pid, name in {(c[1], c[2]) for c in CARDS}:
            con.execute("INSERT INTO person VALUES (?,?,?,1)", (pid, name, name.casefold()))
        for oid, pid, _name, year, ovr, label, club, kind in CARDS:
            con.execute(
                "INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, pid, year, ovr, None, "ST", 25, None, None, None, kind,
                 "futgg" if kind == "fut" else kind, label, str(oid), club, None,
                 json.dumps({"_face_verified": True})))


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "universe.sqlite"
    _write_universe(path)
    return LocalCatalog(universe_path=path)


def _ids(rows):
    return sorted(int(r["obs_id"]) for r in rows)


@pytest.mark.parametrize("label,collection", [
    ("Rare", "Base Gold"), ("Common", "Base Gold"), ("Gold Rare", "Base Gold"),
    ("Silver Non-rare", "Base Silver"), ("Bronze Rare", "Base Bronze"),
    ("Icon", "Base Icons"), ("Base Icon", "Base Icons"), ("All Icons", "Base Icons"),
    ("Heroes", "Base & Prime Heroes"), ("Base Hero", "Base & Prime Heroes"), ("Prime Hero", "Base & Prime Heroes"),
    ("Team of the Week", "Team of the Week"), ("Team of the week (Holographic)", "Team of the Week"),
    ("Team of the Year ICON", "Team of the Year"), ("TOTY HM Heroes", "Team of the Year"),
    ("TOTS Honourable Mentions", "Team of the Season"),
    ("UEFA Champions League Primetime", "UEFA Primetime"), ("UWCL Primetime Hero", "UEFA Primetime"),
    ("UEFA Europa League Road to the Final", "Road to the Final"), ("UCL Road to the Finals", "Road to the Final"),
    ("FoF: Answer the Call", "FOF: Answer the Call"), ("Answer The Call", "FOF: Answer the Call"),
    ("Festival of Football: Path to Glory", "FOF: Path to Glory"),
    ("Festival of Football: Greats of the Game ICON", "FOF: Greats of the Game"),
    ("Festival of Football: Glory Hunters", "FOF: Glory Hunters"),
    ("Festival of Football: Phenoms", "FOF: Phenoms"), ("Summer Stars Tournament Winners", "FOF: Summer Stars"),
    ("Festival of Football: Captains", "FUT Captains"), ("Thunderstruck ICON", "Black Friday / Thunderstruck"),
    ("FC Pro Leagues Live", "FC Pro Live"), ("FC Pro World Champion ICON", "FC Pro Champions"),
    ("Winter Wildcards Hero Red", "Winter Wildcards"), ("Fantasy FC Live", "Fantasy FC"),
    ("FUT Birthday ICON", "FUT Birthday"), ("Trophy Titans Hero", "Trophy Titans"),
    ("FUTTIES Hero", "FUTTIES"), ("Shapeshifters", "Shapeshifters"),
    ("Ultimate Rewind Joga Bonito", "Ultimate Rewind"), ("Pre-Season FUTTIES ICON", "Pre-Season"),
    ("CONMEBOL Sudamericana", "Libertadores & Sudamericana"),
    ("End Of An Era", "End of an Era"), ("End of Era", "End of an Era"),
    ("POTM LALIGA EA SPORTS", "Player of the Month"), ("Squad Foundations (Holographic)", "Squad Foundations"),
    ("Showdown Upgrade", "Showdown"), ("Festival of Football Showdown", "Showdown"),
    ("Base Hall of FUT", "Hall of FUT"), ("Debut International Icon", "Debut Icons"),
])
def test_labels_map_to_named_collections(label, collection):
    card = {"variant": label, "overall": 80, "club": "ICON" if "icon" in label.casefold() else ""}
    assert cc.label_class(label).mapped
    assert cc.classify(card).collection == collection


def test_kind_comes_from_label_or_club():
    assert cc.classify({"variant": "Fantasy FC Hero", "club": "Arsenal"}).kind == cc.KIND_HERO
    assert cc.classify({"variant": "Rare", "club": "ICON", "overall": 90}).collection == cc.BASE_ICONS
    assert cc.classify({"variant": "Base Hall of FUT", "club": "HALL OF FUT"}).kind == cc.KIND_HERO
    assert cc.classify({"variant": "Rare", "club": "Arsenal", "overall": 88}).kind == cc.KIND_PLAYER


def test_campaign_label_names_fc26_rewind_and_pre_season():
    rewind = {"year": "26", "revision": "Winter Wildcards", "createdAt": "2026-08-21T17:00:00Z", "raritySquadId": 572}
    rewind_sbc = {"year": "26", "revision": "Joga Bonito", "createdAt": "2026-08-22T17:00:00Z"}
    pre_season = {"year": "26", "revision": "FUTTIES", "createdAt": "2026-08-28T17:00:00Z"}
    futties = {"year": "26", "revision": "FUTTIES", "createdAt": "2026-08-21T17:00:00Z"}
    original = {"year": "26", "revision": "Winter Wildcards", "createdAt": "2025-12-12T17:00:00Z"}
    fc27 = {"year": "27", "revision": "Team of the week", "createdAt": "2026-10-09T17:00:00Z"}
    assert cc.campaign_label(rewind) == "Ultimate Rewind Winter Wildcards"
    assert cc.campaign_label(rewind_sbc) == "Ultimate Rewind Joga Bonito"
    assert cc.campaign_label(pre_season) == "Pre-Season FUTTIES"
    assert cc.campaign_label(futties) == "FUTTIES"
    assert cc.campaign_label(original) == "Winter Wildcards"
    assert cc.campaign_label(fc27) == "Team of the week"


def test_base_pick_returns_the_plain_card_for_icons_heroes_and_players(catalog):
    for name, expected in (("paolo maldini", 1), ("joe cole", 5), ("bukayo saka", 8)):
        rows = catalog.search(name, limit=200)
        chosen = cc.choose_cards(rows, cc.PICK_BASE)
        assert [int(c["obs_id"]) for c in chosen] == [expected], name
        # The Library can do the same narrowing in SQL, so a row limit never hides it.
        sql_base = catalog.search(name, limit=200, base_only=True)
        assert expected in _ids(sql_base) and all(cc.classify(r).base for r in sql_base)


def test_best_and_all_picks_are_unchanged(catalog):
    rows = catalog.search("bukayo saka", limit=200)
    assert [int(c["obs_id"]) for c in cc.choose_cards(rows, cc.PICK_BEST)] == [11]
    assert len(cc.choose_cards(rows, cc.PICK_ALL)) == 5


def test_library_filters_by_collection_and_type(catalog):
    assert _ids(catalog.search("", collection="Team of the Year")) == [3]
    assert _ids(catalog.search("", collection=cc.BASE_ICONS)) == [1, 2, 4]
    assert _ids(catalog.search("", collection=cc.BASE_ICONS, base_only=True)) == [1, 2]
    assert _ids(catalog.search("", collection=cc.BASE_HEROES)) == [5, 6]
    assert _ids(catalog.search("", year="26", collection=cc.BASE_GOLD)) == [10, 12]
    assert _ids(catalog.search("", collection=cc.BASE_SILVER)) == [13]
    assert _ids(catalog.search("", collection=cc.BASE_BRONZE)) == [14]
    assert _ids(catalog.search("", collection="Ultimate Rewind")) == [11]
    assert _ids(catalog.search("", collection="Pre-Season")) == [15]
    assert _ids(catalog.search("", kind=cc.KIND_HERO)) == [5, 6, 7]
    assert _ids(catalog.search("", kind=cc.KIND_ICON)) == [1, 2, 3, 4]
    assert _ids(catalog.search("", year="26", kind=cc.KIND_PLAYER)) == [10, 11, 12, 13, 14, 15]
    assert _ids(catalog.search("maldini", collection="Team of the Year")) == [3]
    names = catalog.collections(year="26")
    assert names.index("Team of the Year") < names.index("Ultimate Rewind") < names.index("Pre-Season")
    assert names.index(cc.BASE_ICONS) < names.index("Team of the Year")


def test_sql_filter_agrees_with_the_python_classifier():
    labels = ["Rare", "Common", "base", "Gold Rare", "Silver Non-rare", "Bronze Rare", "Icon", "Base Icon",
              "Base Icon (Pristine Holographic)", "Heroes", "Prime Hero", "Base Hall of FUT",
              "Team of the Year ICON", "TOTY HM Heroes", "Fantasy FC Hero", "Team of the week (Holographic)",
              "Ultimate Rewind Winter Wildcards", "Pre-Season FUTTIES", "Cornerstones", "Something New"]
    clubs = ["ICON", "HERO", "HALL OF FUT", "Arsenal", None]
    overalls = [55, 64, 65, 74, 75, 90]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, label TEXT, club TEXT, ovr INT)")
    combos = list(itertools.product(labels, clubs, overalls))
    con.executemany("INSERT INTO t(label,club,ovr) VALUES (?,?,?)", combos)
    filters = [dict(collection=c) for c in cc.ordered_collections(
        cc.collections_for_labels(labels) + ("Something New",))]
    filters += [dict(kind=k) for k in cc.KINDS] + [dict(base_only=True)]
    filters += [dict(collection=cc.BASE_ICONS, base_only=True), dict(collection="Team of the Year", kind=cc.KIND_ICON)]
    for spec in filters:
        sql, args = cc.sql_filter(labels, label_col="label", ovr_col="ovr", club_col="club", **spec)
        got = {row[0] for row in con.execute(f"SELECT id FROM t WHERE {sql}", args)}
        want = {index + 1 for index, (label, club, ovr) in enumerate(combos)
                if cc.matches({"variant": label, "club": club, "overall": ovr}, **spec)}
        assert got == want, spec


def test_shop_cards_and_feedback_follow_the_pick(catalog):
    rows = catalog.search("bukayo saka", limit=200)
    assert [int(r["obs_id"]) for r in shop_cards(rows, verified_only=False, pick="base")] == [8]
    assert [int(r["obs_id"]) for r in shop_cards(rows, verified_only=False, pick="best")] == [11]
    assert len(shop_cards(rows, verified_only=False, pick="all")) == 5
    assert "1 base card from 5 matches" in shop_feedback(5, 1, best_only=False, capped=False, pick="base")
    assert "Choose Best card or All versions" in shop_feedback(5, 0, best_only=False, capped=False, pick="base")


def test_result_rows_show_the_collection(catalog):
    toty = next(r for r in catalog.search("maldini", limit=200) if int(r["obs_id"]) == 3)
    assert _card_row(toty)["collection"] == "Team of the Year · ICON"
    base = next(r for r in catalog.search("bukayo saka", limit=200) if int(r["obs_id"]) == 8)
    assert _card_row(base)["collection"] == "Base Gold"
    assert _card_row({"name": "Signed", "_history": True})["collection"] == ""


def test_one_menu_holds_collections_and_card_types():
    view = {}
    apply_menu_value(view, "All Icons")
    assert (view["kind"], view["collection"], menu_value(view)) == (cc.KIND_ICON, "", "All Icons")
    apply_menu_value(view, "Team of the Year")
    assert (view["kind"], view["collection"], menu_value(view)) == ("", "Team of the Year", "Team of the Year")
    apply_menu_value(view, ANY_COLLECTION)
    assert (view["kind"], view["collection"]) == ("", "")
    legacy = {"best_only": False}
    assert view_pick(legacy) == cc.PICK_ALL
    set_view_pick(legacy, cc.PICK_BASE)
    assert legacy == {"best_only": False, "card_pick": cc.PICK_BASE}


# --- Suggestions -----------------------------------------------------------

def _names(*names):
    return tuple(CardRecommendation(name, "fits the squad") for name in names)


def test_suggestions_resolve_the_card_the_pick_asks_for(catalog):
    picks = {}
    for pick in (cc.PICK_BEST, cc.PICK_BASE):
        report = resolve_recommendation_report(
            catalog, _names("Paolo Maldini", "Joe Cole", "Bukayo Saka"), limit=3, pick=pick)
        picks[pick] = [int(item.card["obs_id"]) for item in report.resolved]
    assert picks[cc.PICK_BASE] == [1, 5, 8]
    assert picks[cc.PICK_BEST] == [3, 7, 11]


def test_suggestions_respect_the_collection_filter(catalog):
    report = resolve_recommendation_report(
        catalog, _names("Paolo Maldini", "Bukayo Saka", "Invented Ghost"), limit=3,
        collection="Team of the Year")
    assert [int(item.card["obs_id"]) for item in report.resolved] == [3]
    assert report.rejected_out_of_band == ("Bukayo Saka has no Team of the Year card in the Library.",)
    assert report.missing == ("Invented Ghost",)


def test_suggestions_repick_swaps_cards_for_the_same_players(catalog):
    shown = [dict(next(r for r in catalog.search("maldini", limit=200) if int(r["obs_id"]) == 3),
                  _ai_reason="Leader at the back"),
             dict(next(r for r in catalog.search("bukayo saka", limit=200) if int(r["obs_id"]) == 11))]
    kept, dropped = repick_cards(catalog, shown, pick=cc.PICK_BASE)
    assert [int(r["obs_id"]) for r in kept] == [1, 8] and dropped == ()
    assert kept[0]["_ai_reason"] == "Leader at the back"
    kept, dropped = repick_cards(catalog, shown, pick=cc.PICK_BEST, collection="Team of the Year")
    assert [int(r["obs_id"]) for r in kept] == [3] and dropped == ("Bukayo Saka",)
    kept, _dropped = repick_cards(catalog, shown, pick=cc.PICK_BASE, year=26)
    assert [int(r["obs_id"]) for r in kept] == [2, 10]


def test_suggestions_worker_passes_sign_filters(catalog, monkeypatch):
    from companion.integrations import grok
    from companion.ui.surfaces.sign.assistant import grok_library_worker, suggestion_filters

    monkeypatch.setattr(grok, "recommend_library_players",
                        lambda **_kw: ("Ideas", _names("Joe Cole")))
    view = {"card_pick": cc.PICK_BASE, "collection": "", "kind": ""}
    draft = grok_library_worker(request="a hero", count=1, catalog=catalog, **suggestion_filters(view))
    assert [int(item.card["obs_id"]) for item in draft.resolved] == [5]


# --- Sign surface at the shipped window sizes ---------------------------------

@pytest.fixture
def svc(tmp_path, catalog):
    paths = TempAppPaths(tmp_path)
    db = Database(paths.state_db)
    catalog.state_db = db
    state = AppState(bridge=BridgeState(Liveness(pill=Pill.ARMED, armed=True, message="Ready",
                                                 core_version="2.6.26", capabilities={"add_to_team": True})),
                     squad=SquadState(teamid=243, save_uid="save-a", session_id="session-a",
                                      players=({"playerid": 10, "name": "Test squad player", "overallrating": 80},),
                                      free_agents=(), taken=datetime.now(timezone.utc), stale=False),
                     prefs=Prefs(core_ops=frozenset({"add_to_team"})))
    services = Services(paths=paths, clock=FakeClock(), executor=InlineExecutor(),
                        transport=FakeTransport(), store=Store(state), db=db, catalog=catalog)
    yield services
    db.close()


@pytest.mark.gui
@pytest.mark.parametrize("size", [(1040, 720), (1280, 820)])
def test_sign_shows_collections_and_filters_in_the_results_toolbar(svc, size):
    import customtkinter as ctk
    from companion.ui.surfaces.sign import build

    root = ctk.CTk()
    root.geometry(f"{size[0]}x{size[1]}")
    sidebar = ctk.CTkFrame(root, width=200)
    sidebar.pack(side="left", fill="y")
    host = ctk.CTkFrame(root)
    host.pack(side="left", fill="both", expand=True, padx=16, pady=16)
    vm = {}
    surface = build(host, svc, vm)
    surface.pack(fill="both", expand=True)
    search = surface.sign_search
    try:
        root.update()
        # Before any search the filters sit in their own row so a collection can be browsed.
        assert search.filters.visible()
        search.filters._menu_changed("Ultimate Rewind")
        root.update()
        assert [int(r["_raw"]["obs_id"]) for r in search.model.rows] == [11]
        search.filters._menu_changed(ANY_COLLECTION)
        root.update()
        assert not search.model.rows and search.filters.visible()
        search.query.insert(0, "bukayo saka")
        search.search()
        root.update()
        toolbar = search.grid.toolbar
        assert search.filters.frame.pack_info()["in"] == toolbar
        filters_right = search.filters.frame.winfo_rootx() + search.filters.frame.winfo_width()
        assert filters_right <= search.grid.sort.winfo_rootx()
        assert search.grid.sort.winfo_rootx() + search.grid.sort.winfo_width() <= toolbar.winfo_rootx() + toolbar.winfo_width()
        best = search.model.rows
        assert [int(r["_raw"]["obs_id"]) for r in best] == [11]
        subtitle = search.grid._rows[0]["subtitle"].cget("text")
        assert "Ultimate Rewind · Winter Wildcards" in subtitle
        search.filters._pick_changed(cc.PICK_BASE)
        root.update()
        assert [int(r["_raw"]["obs_id"]) for r in search.model.rows] == [8]
        assert "Base Gold" in search.grid._rows[0]["subtitle"].cget("text")
        assert search.grid.widget.winfo_height() >= 120
        surface.sign_discovery.select("Favourites")
        root.update()
        assert not search.filters.visible()
        surface.sign_discovery.select("Recommendations")
        root.update()
        assert search.filters.visible()
    finally:
        root.destroy()
