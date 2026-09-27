"""V2 Add Player tab: local-card truth, safe dummy job, Grok boundary."""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import CORE_VERSION, SIGN_CORE_MIN
from companion.app.commands import team
from companion.app.state import AppState, BridgeState, JobView, JobsState, Prefs, SquadState
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.job import JobValidationError
from companion.domain.outcome import ApplyOutcome, JobResult, OpResult
from companion.domain.add_player import (
    INDIVIDUAL_PLAYERS,
    CardRecommendation,
    resolve_card_recommendations,
    review_lineup,
)
from companion.domain.card_import import BasePlayerProfile
from companion.domain.catalog import ImportPlan, LocalCatalog
from companion.integrations import grok
from companion.platform.base_players import resolve_card_base_profile
from companion.ui import nav
from companion.ui.surfaces import add_player


# Acceptance fixture only: the production Add Player experience is generic.
_MILAN_XI_RECOMMENDATIONS: tuple[CardRecommendation, ...] = (
    CardRecommendation("Mike Maignan", "Current Milan keeper", position_hint="GK"),
    CardRecommendation("Cafu", "All-time Milan right back", position_hint="RB"),
    CardRecommendation("Alessandro Nesta", "All-time Milan centre back", position_hint="CB"),
    CardRecommendation("Paolo Maldini", "All-time Milan defender", position_hint="CB"),
    CardRecommendation("Theo Hernandez", "Milan left back", position_hint="LB"),
    CardRecommendation("David Beckham", "Milan right midfielder", position_hint="RM"),
    CardRecommendation("Andrea Pirlo", "Milan playmaker", position_hint="CM"),
    CardRecommendation("Ruud Gullit", "Milan box-to-box midfielder", position_hint="CM"),
    CardRecommendation("Ronaldinho", "Milan left midfielder", position_hint="LM"),
    CardRecommendation("Kaka", "Milan support striker", position_hint="CAM"),
    CardRecommendation("Andriy Shevchenko", "Milan striker", position_hint="ST"),
)


class CatalogStub:
    def prepare_cross_year_import(self, card, *, target_playerid):
        return ImportPlan(
            target_playerid=target_playerid,
            fields={"overallrating": 97, "acceleration": 90},
            source={"name": card["name"]},
        )

    def search(self, name, *, year="", ovr_min=None, ovr_max=None, limit=100):
        del year, ovr_min, ovr_max
        return (
            {
                "name": name,
                "person_id": 1397 if "Zidane" in name else 999,
                "year": 26,
                "overallrating": 94,
                "variant": "Base ICON",
                "club": "ICON",
                "positions_text": "CAM, CM",
                "_card_key": f"{name}-base",
            },
            {
                "name": name,
                "person_id": 1397 if "Zidane" in name else 999,
                "year": 26,
                "overallrating": 97,
                "variant": "Greats of the Game ICON",
                "club": "ICON",
                "positions_text": "CAM, CM",
                "_card_key": f"{name}-best",
            },
        )


class StoreStub:
    def __init__(self, state):
        self.state = state

    def snapshot(self):
        return self.state


def service_stub(tmp_path: Path | None = None):
    clock = SimpleNamespace(now=lambda: datetime.now(timezone.utc))
    state = AppState(
        bridge=BridgeState(
            Liveness(
                pill=Pill.ARMED,
                armed=True,
                message="ready",
                capabilities={"add_to_team": True},
                core_version=CORE_VERSION,
            )
        ),
        squad=SquadState(
            teamid=243,
            players=({"playerid": 10},),
            free_agents=(
                {
                    "playerid": 91234,
                    "overallrating": 48,
                    "source": "free_agent_template",
                },
            ),
            taken=clock.now(),
            stale=False,
            save_uid="save-test",
            session_id="session-test",
        ),
        prefs=Prefs(core_ops=frozenset({"add_to_team"})),
    )
    root = tmp_path if tmp_path is not None else Path(".")
    queue = root / "queue"
    (queue / "state").mkdir(parents=True, exist_ok=True)
    paths = SimpleNamespace(queue=queue, root=root)
    return SimpleNamespace(
        store=StoreStub(state), catalog=CatalogStub(), db=None, clock=clock, paths=paths
    )


def test_team_card_plan_copies_card_identity_face_and_forced_age(monkeypatch, tmp_path):
    monkeypatch.setattr(
        team,
        "resolve_card_base_profile",
        lambda _card: BasePlayerProfile(
            source_playerid=1397,
            identity={"nationality": 18, "gender": 0, "birthdate": 142332},
            appearance={
                "headassetid": 1397,
                "headclasscode": 0,
                "headtypecode": 2502,
            },
            face_verified=True,
        ),
    )
    plan = team.prepare_card_for_team(
        service_stub(tmp_path),
        {"name": "Zinedine Zidane"},
        forced_age=25,
    )
    assert plan.fields["overallrating"] == 97
    assert plan.fields["potential"] == 97
    assert plan.fields["birthdate"] == 152_567
    assert plan.fields["nationality"] == 18
    assert plan.fields["isretiring"] == 0
    assert plan.face["headassetid"] == 1397
    assert plan.names["firstname"] == "Zinedine"
    assert plan.names["surname"] == "Zidane"
    assert plan.names["commonname"] == "Zinedine Zidane"


def test_add_job_uses_live_low_rated_free_agent_and_verified_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(
        team,
        "resolve_card_base_profile",
        lambda _card: BasePlayerProfile(
            source_playerid=1397,
            identity={"birthdate": 142332},
            appearance={"headassetid": 1397, "headclasscode": 0},
            face_verified=True,
        ),
    )
    wire = team.build_add_card_job(
        service_stub(tmp_path), {"name": "Zinedine Zidane"}, forced_age=25
    ).to_wire(now=1)
    op = wire["ops"][0]
    assert op["op"] == "add_to_team"
    assert op["strategy"] == "dummy_overwrite"
    assert op["dummy_pool"] == [91234]
    assert op["player"]["fields"]["birthdate"] == 152_567
    assert op["player"]["fields"]["potential"] == 97
    assert op["player"]["face"]["headassetid"] == 1397
    assert wire["grants"]["allow_add_to_team"] is True
    assert wire["requires"]["core_version"] == SIGN_CORE_MIN


def test_sign_accepts_live_worker_at_add_to_team_floor(tmp_path):
    svc = service_stub(tmp_path)
    state = svc.store.snapshot()
    svc.store.state = replace(
        state,
        bridge=replace(
            state.bridge,
            liveness=replace(state.bridge.liveness, core_version=SIGN_CORE_MIN),
        ),
    )
    assert team._validate_team_add(svc).bridge.liveness.core_version == SIGN_CORE_MIN


def test_sign_refuses_worker_older_than_add_to_team_floor(tmp_path):
    svc = service_stub(tmp_path)
    state = svc.store.snapshot()
    svc.store.state = replace(
        state,
        bridge=replace(
            state.bridge,
            liveness=replace(state.bridge.liveness, core_version="2.6.24"),
        ),
    )
    with pytest.raises(JobValidationError, match="Sign needs 2.6.25"):
        team._validate_team_add(svc)


def test_add_job_keeps_iniesta_native_name_ids_attached(monkeypatch, tmp_path):
    monkeypatch.setattr(
        team,
        "resolve_card_base_profile",
        lambda _card: BasePlayerProfile(
            # Exact FC26 base identity for Iniesta. The zero jersey-name ID is
            # valid source data, not an instruction to detach the name.
            source_playerid=41,
            identity={
                "firstnameid": 2162,
                "lastnameid": 16352,
                "commonnameid": 16351,
                "playerjerseynameid": 0,
                "usercaneditname": 0,
            },
            appearance={},
            face_verified=False,
        ),
    )
    op = team.build_add_card_job(
        service_stub(tmp_path), {"name": "Andrés Iniesta", "playerid": 41}
    ).to_wire(now=1)["ops"][0]
    player = op["player"]
    assert op["v"] == 4
    assert player["name_strategy"] == "native_ids"
    assert player["detach_name_dictionary"] is False
    assert {
        key: player["fields"][key]
        for key in (
            "firstnameid", "lastnameid", "commonnameid",
            "playerjerseynameid", "usercaneditname",
        )
    } == {
        "firstnameid": 2162,
        "lastnameid": 16352,
        "commonnameid": 16351,
        "playerjerseynameid": 0,
        "usercaneditname": 0,
    }


def test_fc26_iniesta_base_profile_has_the_native_name_bundle():
    """Protect the real source row that exposed the blank-name regression."""
    profile = resolve_card_base_profile({"playerid": 41})
    if profile is None:
        pytest.skip("base_players.csv is not available in this test environment")
    assert {
        field: profile.identity[field]
        for field in (
            "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid",
            "usercaneditname",
        )
    } == {
        "firstnameid": 2162,
        "lastnameid": 16352,
        "commonnameid": 16351,
        "playerjerseynameid": 0,
        "usercaneditname": 0,
    }


def test_partial_or_zero_native_name_ids_fall_back_without_writing_them(monkeypatch, tmp_path):
    monkeypatch.setattr(
        team,
        "resolve_card_base_profile",
        lambda _card: BasePlayerProfile(
            source_playerid=1397,
            identity={"firstnameid": 101, "lastnameid": 0, "commonnameid": 303},
            appearance={},
            face_verified=False,
        ),
    )
    player = team.build_add_card_job(
        service_stub(tmp_path), {"name": "Zinedine Zidane"}
    ).to_wire(now=1)["ops"][0]["player"]
    assert player["name_strategy"] == "custom"
    assert player["detach_name_dictionary"] is True
    assert not any(field in player["fields"] for field in (
        "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid", "usercaneditname",
    ))


def test_grok_names_resolve_to_real_best_library_variants():
    resolved = resolve_card_recommendations(
        CatalogStub(),
        (
            CardRecommendation(
                name="Zinedine Zidane",
                reason="Elite creator",
                variant_hint="Icon",
                position_hint="midfielder",
            ),
        ),
        limit=3,
    )
    assert len(resolved) == 1
    assert resolved[0].card["_card_key"] == "Zinedine Zidane-best"
    assert resolved[0].reason == "Elite creator"


def test_milan_xi_acceptance_year_26_ovr_87_to_91():
    root = Path(__file__).resolve().parents[2]
    catalog = LocalCatalog(
        root / "card_db/catalog.sqlite", root / "card_db/universe.sqlite"
    )
    resolved = resolve_card_recommendations(
        catalog,
        _MILAN_XI_RECOMMENDATIONS,
        limit=11,
        year=26,
        ovr_min=87,
        ovr_max=91,
    )
    assert len(resolved) == 11
    assert len({row.card["person_id"] for row in resolved}) == 11
    assert all(int(row.card["year"]) == 26 for row in resolved)
    assert all(87 <= int(row.card["overallrating"]) <= 91 for row in resolved)
    assert all(
        profile is not None and profile.face_verified
        for profile in (resolve_card_base_profile(row.card) for row in resolved)
    )
    lineup = review_lineup([row.card for row in resolved], formation="4-4-1-1")
    assert lineup.valid
    assert lineup.filled_count == 11
    assert [slot.slot.label for slot in lineup.slots] == [
        "GK", "RB", "RCB", "LCB", "LB", "RM", "RCM", "LCM", "LM",
        "CAM / support striker", "ST",
    ]


def test_ai_request_constraints_are_enforced_locally():
    assert add_player._request_card_constraints(
        "best AC Milan XI year 26 OVR range between 87 - 91"
    ) == (26, 87, 91)


def test_grok_recommendation_api_returns_names_and_hints_only(monkeypatch):
    payload = {
        "summary": "Three Milan midfield greats",
        "recommendations": [
            {
                "name": "Kaka",
                "reason": "Creator",
                "variant_hint": "Icon",
                "club_hint": "AC Milan",
                "position_hint": "midfielder",
            }
        ],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda *_args, **_kwargs: {
            "choices": [{"message": {"content": json.dumps(payload)}}]
        },
    )
    summary, rows = grok.recommend_library_players(
        request="best Milan icon midfielder", max_results=3
    )
    assert summary == "Three Milan midfield greats"
    assert rows == (
        CardRecommendation(
            name="Kaka",
            reason="Creator",
            variant_hint="Icon",
            club_hint="AC Milan",
            position_hint="midfielder",
        ),
    )


def test_add_player_tab_is_registered_and_contains_required_controls():
    registry = nav.default_registry()
    assert registry.title_of("add_player") == "Sign"
    package_dir = Path(add_player.build.__code__.co_filename).resolve().parent
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(package_dir.glob("*.py"))
    )
    for marker in (
        "Set new players to age",
        "Search players in your local Library",
        "Search Library for a verified card",
        "Ask Codex",
        "Codex only suggests names",
        "Checkout",
        "Add to bag",
        "Bag",
        "Draft formation",
        "Favourites",
        "Recently Added",
        "dummy",
    ):
        assert marker in source
    readiness = inspect.getsource(add_player._readiness)
    assert "Enable Sign support." in readiness
    assert "Refresh squad." in readiness
    assert "Couldn't count free agents. Refresh squad." in readiness
    assert "AC Milan" not in source
    assert "milan_xi_recommendations" not in source


def test_deferred_worker_repair_waits_for_both_hosts_before_writing(tmp_path):
    """The repair must not replace Lua while FC/LE can still read it."""
    from companion.platform.procs import ProcessInfo

    snapshots = [
        (ProcessInfo(1, "FC26.exe"), ProcessInfo(2, "Launcher.exe")),
        (),
    ]
    sleeps: list[float] = []
    repairs: list[Path] = []

    class Procs:
        @staticmethod
        def list_processes():
            return snapshots.pop(0) if snapshots else ()

        @staticmethod
        def find_game(*, processes):
            return next((item for item in processes if item.name == "FC26.exe"), None)

        @staticmethod
        def find_le_launcher(*, processes):
            return next((item for item in processes if item.name == "Launcher.exe"), None)

    token = SimpleNamespace(cancelled=False)
    outcome, report, running = add_player._wait_for_team_add_repair(
        service_stub(tmp_path),
        token,
        procs_module=Procs,
        sleep=sleeps.append,
        repair=lambda *, queue_dir: repairs.append(queue_dir) or {"enabled": True},
    )

    assert outcome == "repaired"
    assert report == {"enabled": True}
    assert running == ()
    assert sleeps == [1.0]
    assert repairs == [tmp_path / "queue"]


def test_deferred_worker_repair_cancels_without_writing(tmp_path):
    class Token:
        cancelled = False

    token = Token()
    repairs: list[Path] = []

    class Procs:
        @staticmethod
        def list_processes():
            return (SimpleNamespace(name="FC26.exe"),)

        @staticmethod
        def find_game(*, processes):
            return processes[0]

        @staticmethod
        def find_le_launcher(*, processes):
            return None

    def cancel_after_one_poll(_seconds: float) -> None:
        token.cancelled = True

    outcome, report, _running = add_player._wait_for_team_add_repair(
        service_stub(tmp_path),
        token,
        procs_module=Procs,
        sleep=cancel_after_one_poll,
        repair=lambda *, queue_dir: repairs.append(queue_dir) or {"enabled": True},
    )

    assert outcome == "cancelled"
    assert report is None
    assert repairs == []


def test_natural_language_ovr_and_count_are_parsed_from_the_screenshot_prompt():
    prompt = (
        "3 defender man u legend/icon/hero/special card stat overall at least 87 "
        "max overall cant exceed 95"
    )
    assert add_player._request_card_constraints(prompt) == (None, 87, 95)
    assert add_player._request_player_count(prompt) == 3
    assert add_player._request_card_constraints(
        "best AC Milan XI year 26 OVR range between 87 - 91"
    ) == (26, 87, 91)


def test_resolve_picks_in_band_variant_and_omits_out_of_band_only_names():
    class BandCatalog:
        def search(self, name, *, year="", ovr_min=None, ovr_max=None, limit=100):
            del year, ovr_min, ovr_max, limit
            if "Vidic" in name or "Vidić" in name:
                return (
                    {
                        "name": "Nemanja Vidic",
                        "person_id": "vidic",
                        "year": 26,
                        "overallrating": 92,
                        "variant": "ICON",
                        "_card_key": "vidic-92",
                    },
                    {
                        "name": "Nemanja Vidic",
                        "person_id": "vidic",
                        "year": 26,
                        "overallrating": 98,
                        "variant": "FUTTIES ICON",
                        "_card_key": "vidic-98",
                    },
                )
            if "OnlyHigh" in name:
                return (
                    {
                        "name": name,
                        "person_id": "only-high",
                        "year": 26,
                        "overallrating": 98,
                        "variant": "FUTTIES",
                        "_card_key": "only-98",
                    },
                )
            return ()

    in_band = resolve_card_recommendations(
        BandCatalog(),
        (CardRecommendation("Nemanja Vidic"),),
        ovr_min=87,
        ovr_max=95,
    )
    assert len(in_band) == 1
    assert in_band[0].card["_card_key"] == "vidic-92"
    assert int(in_band[0].card["overallrating"]) == 92

    from companion.domain.add_player import resolve_recommendation_report

    omitted = resolve_recommendation_report(
        BandCatalog(),
        (CardRecommendation("OnlyHigh"),),
        ovr_min=87,
        ovr_max=95,
    )
    assert omitted.resolved == ()
    assert omitted.rejected_out_of_band
    assert "98" in omitted.rejected_out_of_band[0]
    assert "87-95" in omitted.rejected_out_of_band[0]


def test_individual_grok_keeps_partial_library_hits(monkeypatch):
    recs = (
        CardRecommendation("Rio Ferdinand"),
        CardRecommendation("Nemanja Vidic"),
        CardRecommendation("Jaap Stam"),
        CardRecommendation("Ghost Defender"),
    )
    monkeypatch.setattr(
        grok,
        "recommend_library_players",
        lambda **_kwargs: ("ok", recs),
    )

    class Cat:
        def search(self, name, **_kwargs):
            if "Ghost" in name:
                return ()
            overall = 92 if "Ferdinand" in name else 89 if "Stam" in name else 91
            return (
                {
                    "name": name,
                    "person_id": name,
                    "year": 26,
                    "overallrating": overall,
                    "_card_key": name,
                },
            )

    draft = add_player.grok_library_worker(
        request="3 defender overall at least 87 max overall cant exceed 95",
        count=4,
        catalog=Cat(),
        require_exact=False,
    )
    assert len(draft.resolved) == 3
    assert draft.missing == ("Ghost Defender",)
    assert draft.requested_count == 4
    assert draft.ovr_min == 87
    assert draft.ovr_max == 95


def test_formation_grok_still_requires_a_complete_set(monkeypatch):
    monkeypatch.setattr(
        grok,
        "recommend_library_players",
        lambda **_kwargs: (
            "partial",
            (CardRecommendation("Only One"), CardRecommendation("Missing Two")),
        ),
    )

    class Cat:
        def search(self, name, **_kwargs):
            if "Only One" in name:
                return (
                    {
                        "name": name,
                        "person_id": "one",
                        "year": 26,
                        "overallrating": 88,
                        "_card_key": "one",
                    },
                )
            return ()

    with pytest.raises(RuntimeError, match="Only 1/2"):
        add_player.grok_library_worker(
            request="build a pair",
            count=2,
            catalog=Cat(),
            require_exact=True,
        )


def test_active_team_add_jobs_ignore_terminal_and_unrelated_labels(tmp_path):
    svc = service_stub(tmp_path)
    svc.store.state = svc.store.state.with_(
        jobs=JobsState(
            active={
                "q": JobView("q", "Add Player: One", ApplyOutcome.QUEUED),
                "d": JobView("d", "Add Player 1/1: Two", ApplyOutcome.DEFERRED),
                "done": JobView("done", "Add Player: Done", ApplyOutcome.APPLIED),
                "sync": JobView("sync", "Sync squad", ApplyOutcome.QUEUED),
            },
            history=(JobView("old", "Add Player: Old", ApplyOutcome.APPLIED),),
        )
    )
    assert team.active_team_add_jobs(svc) == ("q", "d")
    svc.store.state = svc.store.state.with_(
        jobs=JobsState(
            active={"done": JobView("done", "Add Player: Done", ApplyOutcome.APPLIED)}
        )
    )
    assert team.active_team_add_jobs(svc) == ()
    _, busy = add_player._signing_progress_copy(svc)
    assert busy is False


def test_second_add_queues_on_a_different_dummy_while_a_signing_is_in_flight(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(team, "resolve_card_base_profile", lambda _card: None)
    submitted: list[object] = []
    monkeypatch.setattr(team, "submit_job", lambda _svc, job: submitted.append(job) or job.job_id)
    svc = service_stub(tmp_path)
    extra = {
        "playerid": 85308,
        "overallrating": 55,
        "source": "free_agent_template",
        "name": "Helper",
    }
    svc.store.state = svc.store.state.with_(
        squad=replace(
            svc.store.state.squad,
            free_agents=(*svc.store.state.squad.free_agents, extra),
        ),
        jobs=JobsState(
            active={"jid": JobView("jid", "Sign: Lionel Messi", ApplyOutcome.QUEUED)}
        ),
    )
    jobs_dir = tmp_path / "queue" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / "jid.json").write_text(
        json.dumps({"ops": [{"op": "add_to_team", "dummy_pool": [91234]}]}),
        encoding="utf-8",
    )
    preview, job_ids = team.add_cards_to_team(svc, ({"name": "Rio Ferdinand"},))
    assert job_ids
    assert preview.entries[0].dummy_id == 85308
    assert submitted[0].to_wire(now=1)["ops"][0]["dummy_pool"] == [85308]

    review = (
        Path(__file__).parents[2] / "companion" / "ui" / "surfaces" / "sign" / "review.py"
    ).read_text(encoding="utf-8")
    assert "Confirm waits until" not in review
    assert "different free-agent slot" in review


def test_dummy_ids_from_payload_reads_batch_pools():
    payload = {
        "ops": [
            {
                "op": "add_to_team",
                "batch": [
                    {"dummy_pool": [91234, 66040]},
                    {"dummy_pool": [85308]},
                ],
            }
        ]
    }
    assert team._dummy_ids_from_payload(payload) == {91234, 66040, 85308}


def test_sign_force_drain_text_lists_remaining_names(tmp_path):
    svc = service_stub(tmp_path)
    svc.store.state = svc.store.state.with_(
        jobs=JobsState(
            active={"a": JobView("a", "Sign 2: Iniesta, Maradona", ApplyOutcome.QUEUED)}
        )
    )
    text = team.sign_force_drain_text(svc)
    assert "Remaining: Sign 2: Iniesta, Maradona" in text
    assert "LECompanionV2_ForceDrain" in text


def test_selected_key_is_restored_after_a_simulated_rebuild():
    rows = (
        add_player._card_row({"name": "Rio Ferdinand", "_card_key": "rio"}),
        add_player._card_row({"name": "Jaap Stam", "_card_key": "stam"}),
    )
    selected, selection, wanted = add_player._restore_grid_selection(
        rows,
        checked_keys=("stam",),
        selected_key="rio",
    )
    assert selected["name"] == "Rio Ferdinand"
    assert [row["name"] for row in selection] == ["Jaap Stam"]
    assert wanted == {"stam"}


def test_update_review_cta_requeries_active_jobs_and_does_not_freeze_the_lock():
    source = inspect.getsource(add_player.build)
    body = source[source.index("def update_review_cta"):]
    body = body.split("def refresh_signing_progress")[0]
    assert "_active_add_count(svc)" in body
    assert "if active else" not in body
    assert "Add a verified card to your bag first." in body


def test_review_cta_uses_sign_list_not_current_search_checks():
    source = inspect.getsource(add_player.build)
    assert 'review_rows(sign_list_items(view.get("sign_list")))' in source
    assert "search.selection if search.selection" not in source
    review = (
        Path(__file__).parents[2] / "companion" / "ui" / "surfaces" / "sign" / "review.py"
    ).read_text(encoding="utf-8")
    assert "Keep shopping" in review
    assert "Sign next" not in review


def test_sign_catalog_expands_and_bag_stays_compact():
    # Geometry is exercised by test_sign_discovery at the supported sizes.
    # Keep a headless guard for the bag dock and isolated scrolling regions.
    basket = inspect.getsource(add_player.SignListPanel)
    assert "Your bag is empty" in basket
    assert "checkout_host.pack(side=\"bottom\"" in basket
    assert "_fit_scroll" in basket
    source = inspect.getsource(add_player.build)
    assert 'workspace.grid_columnconfigure(0, weight=1)' in source
    assert 'rail.grid_propagate(False)' in source
    assert 'results.pack(fill="both", expand=True)' in source
    assert 'mount="grid"' in source
    assert "DiscoveryPanel(" in source
    assert "PlayerDetailsPanel(" in source
    assert "LibrarySearchPanel(" in source


def test_confirm_add_label_pluralizes_cleanly():
    assert add_player._confirm_add_label(1) == "Apply 1 signing"
    assert add_player._confirm_add_label(3) == "Apply 3 signings"


def test_shop_cards_keeps_the_best_verified_card_for_each_player():
    from companion.ui.surfaces.sign.search import shop_cards, shop_feedback

    rows = (
        {
            "name": "Ronaldo", "person_id": 1, "year": 26, "overall": 91,
            "positions_text": "ST", "_face_status": "Verified",
        },
        {
            "name": "Ronaldo", "person_id": 1, "year": 27, "overall": 94,
            "positions_text": "ST", "_face_status": "Verified",
        },
        {
            "name": "Ronaldo", "person_id": 1, "year": 25, "overall": 99,
            "positions_text": "LW", "_face_status": "Missing",
        },
        {
            "name": "Messi", "person_id": 2, "year": 26, "overall": 93,
            "positions_text": "RW", "_face_status": "Verified",
        },
    )
    best = shop_cards(rows, verified_only=True, best_only=True)
    assert [(row["name"], row["year"]) for row in best] == [("Ronaldo", 27), ("Messi", 26)]
    strikers = shop_cards(rows, verified_only=True, best_only=False, position="ST")
    assert [row["year"] for row in strikers] == [26, 27]
    same_ovr = shop_cards(
        (
            {"name": "Kaka", "person_id": 3, "year": 26, "overall": 90, "_face_status": "Verified"},
            {"name": "Kaka", "person_id": 3, "year": 27, "overall": 90, "_face_status": "Verified"},
        ),
        verified_only=True,
        best_only=True,
    )
    assert same_ovr[0]["year"] == 27
    assert "best card" in shop_feedback(40, 8, best_only=True, capped=False)
    assert "No cards match" in shop_feedback(0, 0, best_only=True, capped=False)


def test_bag_copy_and_add_label_are_retail():
    assert add_player.add_to_bag_label(()) == "Add to bag"
    assert add_player.add_to_bag_label(({"name": "Iker Casillas"},)) == "Add Iker Casillas"
    assert add_player.add_to_bag_label(({"name": "A"}, {"name": "B"}, {"name": "C"})) == "Add 3"
    assert add_player.bag_summary_copy(()) == "Bag is empty"
    assert add_player.bag_summary_copy(({"name": "Casillas"},)) == "Bag · 1 · Casillas"
    assert add_player.bag_summary_copy(
        ({"name": "Casillas"}, {"name": "Hakimi"}, {"name": "Davies"})
    ) == "Bag · 3 · Casillas, Hakimi + 1 more"


def test_sign_list_adds_unique_people_and_skips_duplicates():
    from companion.domain.add_player import (
        SIGN_LIST_MAX,
        add_to_sign_list,
        cards_from_display_rows,
        drop_from_sign_list,
        sign_list_change_message,
        sign_list_key,
    )

    first = add_to_sign_list(
        (),
        (
            {"name": "Iker Casillas", "person_id": "casillas", "overallrating": 91},
            {"name": "Achraf Hakimi", "person_id": "hakimi", "overallrating": 89},
        ),
    )
    assert [item["name"] for item in first.items] == ["Iker Casillas", "Achraf Hakimi"]
    assert first.added == ("Iker Casillas", "Achraf Hakimi")
    assert "Added Iker Casillas, Achraf Hakimi" in sign_list_change_message(first)
    assert "to your bag" in sign_list_change_message(first)

    again = add_to_sign_list(
        first.items,
        (
            {"name": "Casillas Icon", "person_id": "casillas", "overallrating": 94},
            {"name": "Jude Bellingham", "person_id": "bellingham", "overallrating": 90},
        ),
    )
    assert [item["name"] for item in again.items] == [
        "Iker Casillas",
        "Achraf Hakimi",
        "Jude Bellingham",
    ]
    assert again.skipped == ("Casillas Icon",)
    assert "Already in the bag: Casillas Icon" in sign_list_change_message(again)

    remaining = drop_from_sign_list(again.items, sign_list_key(again.items[1]))
    assert [item["name"] for item in remaining] == ["Iker Casillas", "Jude Bellingham"]

    unwrapped = cards_from_display_rows(
        ({"_raw": {"name": "Thierry Henry", "person_id": "henry"}},)
    )
    assert unwrapped[0]["name"] == "Thierry Henry"

    filled = add_to_sign_list(
        ({"name": f"P{i}", "person_id": f"p{i}"} for i in range(SIGN_LIST_MAX)),
        ({"name": "Overflow", "person_id": "overflow"},),
    )
    assert filled.overflow == ("Overflow",)
    assert len(filled.items) == SIGN_LIST_MAX
    assert "Bag is full" in sign_list_change_message(filled)


def test_icon_variant_beats_higher_ovr_gold_when_hinted():
    class Cat:
        def search(self, name, **_kwargs):
            return (
                {
                    "name": name,
                    "person_id": "rio",
                    "year": 26,
                    "overallrating": 95,
                    "variant": "Gold Rare",
                    "club": "Manchester United",
                    "_card_key": "gold-95",
                },
                {
                    "name": name,
                    "person_id": "rio",
                    "year": 26,
                    "overallrating": 88,
                    "variant": "ICON",
                    "club": "ICON",
                    "_card_key": "icon-88",
                },
            )

    hinted = resolve_card_recommendations(
        Cat(),
        (CardRecommendation("Rio Ferdinand", variant_hint="Icon"),),
        ovr_min=87,
        ovr_max=95,
    )
    assert hinted[0].card["_card_key"] == "icon-88"

    raw = resolve_card_recommendations(
        Cat(),
        (CardRecommendation("Rio Ferdinand"),),
        ovr_min=87,
        ovr_max=95,
    )
    assert raw[0].card["_card_key"] == "gold-95"


def test_recent_signings_helper_summarizes_jobs(tmp_path):
    svc = service_stub(tmp_path)
    applied = JobView(
        "old",
        "Add Player: Iniesta",
        ApplyOutcome.APPLIED,
        result=JobResult(
            job_id="old",
            outcome=ApplyOutcome.APPLIED,
            ops=(
                OpResult(
                    op_id="op1",
                    op="add_to_team",
                    outcome=ApplyOutcome.APPLIED,
                    data={"dummy_id": "91234"},
                ),
            ),
        ),
    )
    queued = JobView("q", "Add Player: Zidane", ApplyOutcome.QUEUED)
    svc.store.state = svc.store.state.with_(
        jobs=JobsState(active={"q": queued}, history=(applied,))
    )
    items = add_player._recent_signings(svc)
    assert items[0] == {"name": "Zidane", "outcome": "Verifying", "dummy": ""}
    assert items[1] == {"name": "Iniesta", "outcome": "Applied", "dummy": "91234"}
    copy = add_player._recent_signings_copy(items)
    assert copy.startswith("Recent signs:")
    assert "Zidane (Verifying)" in copy
    assert "Iniesta (Applied · slot 91234)" in copy
    assert add_player._recent_signings_copy(()) == ""


def test_current_formation_is_individual_when_assistant_hidden():
    hidden = SimpleNamespace(
        visible={"value": False},
        formation=SimpleNamespace(get=lambda: "4-3-3"),
    )
    shown = SimpleNamespace(
        visible={"value": True},
        formation=SimpleNamespace(get=lambda: "4-3-3"),
    )
    assert add_player.SquadAssistantPanel.current_formation(hidden) == INDIVIDUAL_PLAYERS
    assert add_player.SquadAssistantPanel.current_formation(shown) == "4-3-3"
    source = inspect.getsource(add_player.build)
    assert "assistant.current_formation()" in source
    assert "formation=assistant.formation.get()" not in source
    assert "view=view" in source
    assert "on_queued=_after_sign_queued" in source


def test_card_face_status_and_row_annotation(monkeypatch):
    assert add_player._card_face_status({"_face_verified": True}) == "Verified"
    assert add_player._card_face_status({"_face_verified": False}) == "Missing"
    annotated = add_player._annotate_card_face(
        {"name": "Test", "_face_verified": True, "_card_key": "t"}
    )
    assert annotated["_face_status"] == "Verified"
    assert annotated["_face_verified"] is True
    row = add_player._card_row({"name": "Test", "_face_verified": True, "_card_key": "t"})
    assert row["face"] == "Verified"
    monkeypatch.setattr(
        "companion.platform.base_players.resolve_card_base_profile",
        lambda _card: None,
    )
    assert add_player._card_face_status({"name": "Unknown"}) == "—"
    unknown = add_player._annotate_card_face({"name": "Unknown"})
    assert unknown["_face_status"] == "—"
    assert "_face_verified" not in unknown
    assert add_player._card_face_status(unknown) == "—"


def test_grok_retry_merges_kept_cards_and_excludes_people(monkeypatch):
    recs = (
        CardRecommendation("Rio Ferdinand"),
        CardRecommendation("Nemanja Vidic"),
    )
    monkeypatch.setattr(
        grok,
        "recommend_library_players",
        lambda **_kwargs: ("ok", recs),
    )

    class Cat:
        def search(self, name, **_kwargs):
            return (
                {
                    "name": name,
                    "person_id": name,
                    "year": 26,
                    "overallrating": 90,
                    "_card_key": name,
                },
            )

    draft = add_player.grok_library_worker(
        request="defenders",
        count=2,
        catalog=Cat(),
        exclude_people=("Rio Ferdinand",),
    )
    assert [row.requested_name for row in draft.resolved] == ["Nemanja Vidic"]
    assert draft.missing == ("Rio Ferdinand",)

    retry = inspect.getsource(add_player.SquadAssistantPanel.retry_missing)
    assert "exclude_people=exclude" in retry
    assert "merged = kept + extra" in retry
    assert "replace_rows(merged" in retry
    ask = inspect.getsource(add_player.SquadAssistantPanel.ask_grok)
    assert "self._show_retry(False)" in ask
    worker = inspect.getsource(add_player.grok_library_worker)
    assert "exclude_people=exclude_people" in worker


def test_chosen_age_from_view_reads_review_foot_state():
    assert add_player.chosen_age_from_view({"force_age": False, "age": "25"}) is None
    assert add_player.chosen_age_from_view({"force_age": True, "age": "27"}) == 27
    with pytest.raises(ValueError, match="between 16 and 40"):
        add_player.chosen_age_from_view({"force_age": True, "age": "99"})
    with pytest.raises(ValueError, match="whole number"):
        add_player.chosen_age_from_view({"force_age": True, "age": "abc"})


def test_sign_job_labels_match_legacy_and_current_prefixes(tmp_path):
    from companion.domain.job import is_sign_job_label

    assert is_sign_job_label("Add Player: One")
    assert is_sign_job_label("Add Player 1/2: Two")
    assert is_sign_job_label("Sign: Zidane")
    assert is_sign_job_label("Sign 1/3: Iniesta")
    assert is_sign_job_label("Sign 10: Iniesta, Maradona")
    assert not is_sign_job_label("Sync squad")
    assert not is_sign_job_label("transfer Messi")

    svc = service_stub(tmp_path)
    svc.store.state = svc.store.state.with_(
        jobs=JobsState(
            active={
                "a": JobView("a", "Sign: One", ApplyOutcome.QUEUED),
                "b": JobView("b", "Add Player: Two", ApplyOutcome.DEFERRED),
                "c": JobView("c", "Sync squad", ApplyOutcome.QUEUED),
            }
        )
    )
    assert team.active_team_add_jobs(svc) == ("a", "b")
    _, busy = add_player._signing_progress_copy(svc)
    assert busy is True


def test_sign_enable_and_post_queue_copy_names_real_buttons():
    root = Path(__file__).parents[2]
    readiness = (root / "companion" / "ui" / "surfaces" / "sign" / "readiness.py").read_text(
        encoding="utf-8"
    )
    review = (root / "companion" / "ui" / "surfaces" / "sign" / "review.py").read_text(
        encoding="utf-8"
    )
    assert "press Enable Sign support" in readiness
    assert "Repair Sign support" not in readiness
    assert "Live Editor applies the whole bag after the next Career tick" in review
    assert "or Copy Lua drain" in review
    assert "Keep shopping" in review
    assert "Refresh squad after verification finishes" not in review
    init = (root / "companion" / "ui" / "surfaces" / "sign" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "Copy Lua drain" in init
    assert "_copy_sign_lua_drain" in init
    assert "Copy Lua drain if it sits" in readiness
