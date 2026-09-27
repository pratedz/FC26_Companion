"""CE-style OVR / Best-At — exercises shipped ovr_formula module."""

from __future__ import annotations

from src import ovr_formula
from src import player_schema


def _full_attrs(**overrides):
    """Minimal complete attribute set for formula (all weight keys present)."""
    base = {
        "name": "Test",
        "preferredposition1": 25,
        "modifier": 0,
        "acceleration": 70,
        "sprintspeed": 70,
        "positioning": 70,
        "finishing": 70,
        "shotpower": 70,
        "longshots": 70,
        "volleys": 70,
        "penalties": 70,
        "vision": 70,
        "crossing": 70,
        "freekickaccuracy": 70,
        "shortpassing": 70,
        "longpassing": 70,
        "curve": 70,
        "agility": 70,
        "balance": 70,
        "reactions": 70,
        "ballcontrol": 70,
        "dribbling": 70,
        "composure": 70,
        "interceptions": 70,
        "headingaccuracy": 70,
        "defensiveawareness": 70,
        "standingtackle": 70,
        "slidingtackle": 70,
        "jumping": 70,
        "stamina": 70,
        "strength": 70,
        "aggression": 70,
        "gkdiving": 40,
        "gkhandling": 40,
        "gkkicking": 40,
        "gkreflexes": 40,
        "gkpositioning": 40,
    }
    base.update(overrides)
    return player_schema.normalize_player_card(base)


def test_st_vs_cb_ovrs_differ():
    st = _full_attrs(
        preferredposition1=25,
        finishing=95,
        positioning=95,
        shotpower=90,
        standingtackle=30,
        defensiveawareness=30,
        slidingtackle=25,
        interceptions=30,
    )
    cb = _full_attrs(
        preferredposition1=5,
        finishing=30,
        positioning=35,
        shotpower=40,
        standingtackle=95,
        defensiveawareness=95,
        slidingtackle=90,
        interceptions=90,
        headingaccuracy=90,
        strength=90,
    )
    st_ovr = ovr_formula.preferred_position_ovr(st)
    cb_ovr = ovr_formula.preferred_position_ovr(cb)
    assert st_ovr is not None and cb_ovr is not None
    assert 1 <= st_ovr <= 99
    assert 1 <= cb_ovr <= 99
    # ST weighted toward finishing should beat CB formula on ST attrs
    st_as_st = ovr_formula.calculate_position_ovr(st, 25)
    st_as_cb = ovr_formula.calculate_position_ovr(st, 5)
    assert st_as_st is not None and st_as_cb is not None
    assert st_as_st != st_as_cb


def test_best_at_returns_top_positions():
    card = _full_attrs(preferredposition1=25, finishing=99, positioning=99, dribbling=95)
    best = ovr_formula.best_at_positions(card, top_n=3)
    assert len(best) == 3
    assert all("pos_id" in b and "ovr" in b and "name" in b for b in best)
    assert best[0]["ovr"] >= best[1]["ovr"] >= best[2]["ovr"]
    assert 1 <= best[0]["ovr"] <= 99


def test_apply_calculated_ovr_sets_overallrating():
    card = _full_attrs(preferredposition1=14, shortpassing=90, vision=90, longpassing=88)
    out = ovr_formula.apply_calculated_ovr_to_card(card)
    assert "overallrating" in out
    assert 1 <= int(out["overallrating"]) <= 99
    line = ovr_formula.format_best_at_line(out)
    assert line.startswith("Best At:")


def test_ovr_formula_has_core_positions():
    for pid in (0, 5, 14, 18, 25, 27):
        assert str(pid) in ovr_formula.OVR_FORMULA
