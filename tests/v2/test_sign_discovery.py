"""Real catalog/favourites, successful signing history and windowed Sign UI."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import sqlite3

import pytest

from companion.app.services import Services
from companion.app.state import AppState, BridgeState, SquadState, Prefs, JobsState, JobView
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database, JobRecord
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.catalog import LocalCatalog
from companion.domain.outcome import ApplyOutcome
from companion.domain.add_player import CardRecommendation
from companion.ui.surfaces.sign import build, grok_library_worker
from companion.ui.surfaces.sign.details import detail_fields
from companion.ui.surfaces.sign.discovery import signing_history, squad_context
from companion.ui.surfaces.sign.results import can_add
from companion.ui.surfaces.sign.search import _card_row, shop_cards


@pytest.fixture
def svc(tmp_path):
    paths = TempAppPaths(tmp_path)
    db = Database(paths.state_db)
    path = tmp_path / "universe.sqlite"
    with sqlite3.connect(path) as con:
        con.executescript("""
        CREATE TABLE person(person_id INTEGER PRIMARY KEY,display_name TEXT,name_norm TEXT,exists_in_fc26 INT);
        CREATE TABLE observation(obs_id INTEGER PRIMARY KEY,person_id INT,year INT,overall INT,
          potential INT,positions_text TEXT,preferredposition1 INT,preferredposition2 INT,
          preferredposition3 INT,preferredposition4 INT,source_kind TEXT,source TEXT,
          variant TEXT,variant_id TEXT,club_name TEXT,nationality_name TEXT,attrs TEXT);
        """)
        for pid, name in ((1,"Lionel Messi"),(2,"Junior Messias"),(3,"Missing Face")):
            con.execute("INSERT INTO person VALUES (?,?,?,1)",(pid,name,name.casefold()))
        for oid,pid,year,ovr,pos,code,kind,verified in (
            (1,1,26,98,"RW, CAM",23,"fut",True),
            (2,1,25,93,"RW",23,"career",True),
            (3,2,26,82,"RW",23,"career",True),
            (4,3,26,90,"ST",25,"career",False)):
            con.execute("INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (oid,pid,year,ovr,None,pos,code,None,None,None,kind,"fixture",
                         "Base",str(oid),None,None,json.dumps({"_face_verified":verified})))
    now = datetime.now(timezone.utc)
    state = AppState(bridge=BridgeState(Liveness(pill=Pill.ARMED,armed=True,message="Ready",
                      core_version="2.6.26",capabilities={"add_to_team":True})),
                     squad=SquadState(teamid=243,save_uid="save-a",session_id="session-a",
                       players=({"playerid":10,"name":"Test squad player","overallrating":80},),
                       free_agents=({"playerid":91234,"overallrating":48,"source":"free_agent_template"},),
                       taken=now,stale=False),prefs=Prefs(core_ops=frozenset({"add_to_team"})))
    services = Services(paths=paths,clock=FakeClock(),executor=InlineExecutor(),
                        transport=FakeTransport(),store=Store(state),db=db,
                        catalog=LocalCatalog(universe_path=path,state_db=db))
    yield services
    db.close()


def test_catalog_filters_best_and_shared_favourites(svc):
    rows = svc.catalog.search("messi", limit=200)
    assert {r["name"] for r in rows} == {"Lionel Messi", "Junior Messias"}
    best = shop_cards(rows)
    assert [(r["name"],r["overallrating"]) for r in best] == [("Lionel Messi",98),("Junior Messias",82)]
    assert len(shop_cards(rows,best_only=False)) == 3
    assert len(svc.catalog.search("messi",year="25",ovr_min=90,ovr_max=95)) == 1
    assert svc.catalog.search("messi",source="fut")[0]["overallrating"] == 98
    assert shop_cards(rows,position="ST") == ()
    unverified = svc.catalog.search("missing")[0]
    assert not shop_cards((unverified,))
    assert not can_add(_card_row(unverified))
    card = rows[0]
    assert svc.catalog.toggle_favorite(card)
    library = LocalCatalog(universe_path=svc.catalog.universe_path,state_db=svc.db)
    assert library.is_favorite(card)
    assert library.favorites()[0]["name"] == "Lionel Messi"
    assert not library.toggle_favorite(card)
    assert svc.catalog.favorites() == ()


def test_details_hide_missing_and_history_cannot_enter_bag():
    assert detail_fields({"name":"Player","club":None,"nation":"null","potential":0}) == ()
    assert ("Club","Inter Miami") in detail_fields({"club":"Inter Miami"})
    assert not can_add({"face":"Verified","_raw":{"_history":True}})


def _record(jobid,when,*,teamid=243,outcome=ApplyOutcome.APPLIED,verified=True,dry_run=False,save="save-a"):
    return JobRecord(jobid,when,outcome,save_uid=save,finished_utc=when,
                     job_json={"dry_run":dry_run,"ops":[{"id":"add","op":"add_to_team","teamid":teamid,
                       "batch":[{"player":{"names":{"commonname":jobid},"fields":{"overallrating":90}}}]}]},
                     result_json={"ops":[{"id":"add","ok":True,"data":{"verified":verified,"playerids":[91234]}}]})


def test_recently_added_reads_successful_verified_history_for_current_save(svc):
    for record in (_record("older","2026-01-01"), _record("newer","2026-03-01"),
                   _record("other-team","2026-04-01",teamid=99),
                   _record("failed","2026-05-01",outcome=ApplyOutcome.FAILED),
                   _record("unverified","2026-06-01",verified=False),
                   _record("preview","2026-07-01",dry_run=True),
                   _record("other-save","2026-08-01",save="save-b")):
        svc.db.upsert_job_record(record)
    rows = signing_history(svc)
    assert [r["name"] for r in rows] == ["newer","older"]
    assert all(r["_history"] and "year" not in r and "variant" not in r for r in rows)
    assert "Test squad player" in squad_context(svc)
    assert "free_agent_template" not in squad_context(svc)


def test_ai_only_resolves_local_cards_and_reports_missing(svc,monkeypatch):
    from companion.integrations import grok
    monkeypatch.setattr(grok,"recommend_library_players",lambda **_kw:
                        ("Ideas",(CardRecommendation("Lionel Messi","Creative option"),CardRecommendation("Invented Ghost"))))
    draft = grok_library_worker(request="two ideas",count=2,catalog=svc.catalog)
    assert [r.card["name"] for r in draft.resolved] == ["Lionel Messi"]
    assert draft.missing == ("Invented Ghost",)
    monkeypatch.setattr(grok,"recommend_library_players",lambda **_kw:
                        ("Ideas",(CardRecommendation("Invented Ghost"),)))
    empty = grok_library_worker(request="one idea",count=1,catalog=svc.catalog)
    assert empty.resolved == () and empty.missing == ("Invented Ghost",)


@pytest.mark.gui
@pytest.mark.parametrize("size",[(1040,720),(1180,820),(1600,900)])
def test_sign_workspace_windowed_flow(svc,monkeypatch,size):
    import customtkinter as ctk
    from companion.ui.surfaces import sign
    root=ctk.CTk()
    root.geometry(f"{size[0]}x{size[1]}")
    root.grid_columnconfigure(1,weight=1)
    root.grid_rowconfigure(1,weight=1)
    chrome=ctk.CTkFrame(root,height=56)
    chrome.grid(row=0,column=0,columnspan=2,sticky="ew")
    chrome.grid_propagate(False)
    dock=ctk.CTkFrame(root,height=58)
    dock.grid(row=2,column=0,columnspan=2,sticky="ew")
    dock.grid_propagate(False)
    sidebar=ctk.CTkFrame(root,width=200)
    sidebar.grid(row=1,column=0,sticky="ns")
    sidebar.grid_propagate(False)
    host=ctk.CTkFrame(root)
    host.grid(row=1,column=1,sticky="nsew",padx=16,pady=16)
    vm={}
    surface=build(host,svc,vm)
    surface.pack(fill="both",expand=True)
    search=surface.sign_search
    try:
        search.query.insert(0,"messi")
        search.search()
        root.update()
        assert len(search.model.rows) == 2
        assert search.grid.widget.winfo_height() >= 120
        search.grid._rows[0]["add"].invoke()
        assert len(surface.sign_bag.items()) == 1
        assert surface.sign_details._signature and "Lionel Messi" in surface.sign_details._signature
        card=surface.sign_bag.items()[0]
        assert svc.catalog.toggle_favorite(card)
        surface.sign_discovery.select("Favourites")
        root.update()
        assert len(search.model.rows) == 1
        assert search.model.rows[0]["name"] == "Lionel Messi"
        surface.sign_discovery.select("Recommendations")
        search.replace_rows([card],mode="Recommendations")
        surface.sign_discovery.select("Search")
        assert search.query.get() == "messi"
        assert len(search.model.rows) == 2
        search.toggle_best()
        assert len(search.model.rows) == 3
        search.select(search.model.rows[0])
        selected=vm["selected_key"]
        surface.refresh_signing_progress()
        assert vm["selected_key"] == selected and search.query.get() == "messi"
        # Reconstruct like a non-job Shell refresh: modes, filters, selection and bag survive.
        surface.destroy()
        surface=build(host,svc,vm)
        surface.pack(fill="both",expand=True)
        root.update()
        search=surface.sign_search
        assert search.query.get() == "messi" and not vm["best_only"]
        assert vm["selected_key"] == selected and len(surface.sign_bag.items()) == 1
        checkout=surface.sign_bag.checkout_button
        assert checkout.winfo_ismapped()
        assert checkout.winfo_rooty()+checkout.winfo_height() <= root.winfo_rooty()+root.winfo_height()
        assert search.grid.widget.winfo_rootx()+search.grid.widget.winfo_width() <= surface.sign_bag.host.winfo_rootx()
        # Route to the actual existing Checkout preflight, without queuing any game write.
        from companion.app.commands import team
        opened=[]
        monkeypatch.setattr(team,"preview_cards_for_team",lambda _s,cards,**_kw:
                            SimpleNamespace(face_verified_count=len(cards),selected_count=len(cards),entries=()))
        monkeypatch.setattr(sign,"_open_review_window",lambda *args,**kw: opened.append((args,kw)))
        checkout.invoke()
        assert len(opened) == 1 and opened[0][1]["view"] is vm
        surface.sign_bag.remove(str(card["person_id"]))
        assert not surface.sign_bag.items()
        search.replace_rows(svc.catalog.search("missing"))
        search._activate_row(search.model.rows[0])
        assert not surface.sign_bag.items()
        surface.sign_discovery.select("Recommendations")
        from companion.integrations import grok, ai_provider
        monkeypatch.setattr(ai_provider,"block_reason",lambda: "")
        monkeypatch.setattr(grok,"status",lambda: SimpleNamespace(connected=True))
        requests=[]
        def recommend(**kwargs):
            requests.append(kwargs["request"])
            return "Creative options", (CardRecommendation("Lionel Messi","Creative option"),CardRecommendation("Invented Ghost"))
        monkeypatch.setattr(grok,"recommend_library_players",recommend)
        assistant=surface.sign_discovery.assistant
        assistant.grok_prompt.insert(0,"Recommend two realistic signings")
        assistant.ask_grok()
        root.update()
        assert len(search.model.rows) == 1
        assert "Invented Ghost" in vm["recommendation_feedback"]
        assert "Test squad player" in requests[0]
        assert search.grid.widget.winfo_height() >= 100
        assert assistant.ask_button.winfo_rootx()+assistant.ask_button.winfo_width() <= surface.sign_bag.host.winfo_rootx()
        search._activate_row(search.model.rows[0])
        assert len(surface.sign_bag.items()) == 1
        surface.sign_bag.clear()
        assert surface.sign_bag.items() == ()
    finally:
        root.destroy()
