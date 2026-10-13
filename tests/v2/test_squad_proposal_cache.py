import pytest

from companion.domain.squad_session import parse_recipe
from companion.integrations import grok
from companion.integrations.squad_cache import SquadProposalCache


def test_partial_rebuild_reuses_valid_players_and_retries_only_changed_or_failed(monkeypatch):
    calls, failed = [], {6}

    def propose(chunk, **kwargs):
        ids = tuple(row["playerid"] for row in chunk)
        calls.append(ids)
        if any(pid in failed for pid in ids):
            raise RuntimeError("mock group failure")
        return ({pid: {"stamina": 90} for pid in ids}, {pid: "Test" for pid in ids}, {})

    monkeypatch.setattr(grok, "_propose_squad_chunk", propose)
    rows = [{"playerid": pid, "target_ovr": 90, "current_values": {"stamina": 70}} for pid in range(1, 7)]
    cache = SquadProposalCache(scope="save-A:team-11")
    kwargs = dict(players=rows, request="improve stamina", recipe=parse_recipe("improve stamina"), cache=cache)
    problems = []
    patch, _, _ = grok.propose_squad_players(selection_revision="one", problems=problems, **kwargs)
    assert set(patch) == {1, 2, 3, 4, 5} and problems
    assert set(calls) == {(1, 2, 3, 4, 5), (6,)}
    failed.clear()
    calls.clear()
    patch, _, _ = grok.propose_squad_players(selection_revision="two", **kwargs)
    assert set(patch) == set(range(1, 7)) and calls == [(6,)]
    patch[1]["stamina"] = 1
    calls.clear()
    patch, _, _ = grok.propose_squad_players(selection_revision="three", **kwargs)
    assert patch[1]["stamina"] == 90 and calls == []
    rows[1]["current_values"]["stamina"] = 80
    grok.propose_squad_players(selection_revision="four", **kwargs)
    assert calls == [(2,)]


def test_cancelled_cached_request_returns_no_stale_proposals(monkeypatch):
    monkeypatch.setattr(grok, "_propose_squad_chunk", lambda chunk, **kw: ({1: {"stamina": 90}}, {}, {}))
    kwargs = dict(players=[{"playerid": 1}], request="stamina", recipe=parse_recipe("stamina"),
                  selection_revision="one", cache=SquadProposalCache())
    grok.propose_squad_players(**kwargs)
    assert grok.propose_squad_players(cancel_check=lambda: True, **kwargs) == ({}, {}, {})


def test_hint_cache_is_window_owned_and_target_sensitive():
    class Catalog:
        calls = 0
        def search(self, name, **kwargs):
            self.calls += 1
            return [{"name": name, "overallrating": kwargs["ovr_min"] + 1, "position": "CM"}]

    catalog = Catalog()
    cache = SquadProposalCache()
    first = grok.library_hint_for("A", "CM", 90, catalog, cache=cache)
    assert grok.library_hint_for("A", "CM", 90, catalog, cache=cache) == first and catalog.calls == 1
    grok.library_hint_for("A", "CM", 91, catalog, cache=cache)
    assert catalog.calls == 2
    grok.library_hint_for("A", "CM", 90, catalog, cache=SquadProposalCache())
    assert catalog.calls == 3


def test_explicit_individual_and_affected_player_repairs_keep_valid_teammates(monkeypatch):
    calls = []
    def propose(chunk, **kwargs):
        ids = tuple(row["playerid"] for row in chunk)
        calls.append(ids)
        return ({pid: {"stamina": 90} for pid in ids}, {}, {})
    monkeypatch.setattr(grok, "_propose_squad_chunk", propose)
    cache = SquadProposalCache()
    kwargs = dict(players=[{"playerid": pid} for pid in range(1, 21)],
                  request="improve stamina", recipe=parse_recipe("improve stamina"), cache=cache,
                  selection_revision="one")
    grok.propose_squad_players(**kwargs)
    assert len(calls) == 4
    calls.clear()
    cache.invalidate_players({3})
    patches, _, _ = grok.propose_squad_players(**kwargs)
    assert calls == [(3,)] and len(patches) == 20
    calls.clear()
    cache.invalidate_players({3, 8})
    patches, _, _ = grok.propose_squad_players(**kwargs)
    assert calls == [(3, 8)] and len(patches) == 20
