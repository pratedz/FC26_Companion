"""Import Player — enrich, plan, apply Lua names, product.import_card wiring."""

from __future__ import annotations

from typing import Any, Dict
from unittest import mock

import pytest

from src import import_player
from src import player_apply
from src import product


def _sample_card(**extra: Any) -> Dict[str, Any]:
    c: Dict[str, Any] = {
        "name": "Pele",
        "overallrating": 98,
        "baseId": 237067,
        "pace": 95,
        "shooting": 96,
        "passing": 93,
        "dribbling": 96,
        "defending": 60,
        "physical": 76,
        "preferredposition1": 21,
    }
    c.update(extra)
    return c


def test_split_display_name_parts():
    n = import_player.split_display_name("Cristiano Ronaldo")
    assert n.first == "Cristiano"
    assert n.surname == "Ronaldo"
    assert n.jersey == "Ronaldo"
    assert n.has_any

    one = import_player.split_display_name("Pelé ★")
    # may strip noise; single token path
    assert one.has_any


def test_resolve_enrich_from_base_and_name():
    bits = import_player.resolve_enrich(_sample_card())
    assert bits.available()["name"] is True
    assert bits.name.surname or bits.name.common
    # base_players should supply head/birth for real base ids when CSV present
    # tolerate missing CSV in CI-like envs
    if bits.available()["head"]:
        assert bits.head.get("headassetid", 0) > 0
    if bits.available()["birthdate"]:
        assert int(bits.birthdate or 0) > 0


def test_build_import_plan_apply_includes_face_career_when_copy():
    card = _sample_card()
    # Force identity on card so plan doesn't depend on CSV
    card["headassetid"] = 237067
    card["hashighqualityhead"] = 1
    card["birthdate"] = 130766
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="apply",
            card=card,
            target_playerid=20801,
            copy_name=True,
            copy_head=True,
            copy_birthdate=True,
        )
    )
    assert plan.mode == "apply"
    assert plan.copy_name and plan.copy_head and plan.copy_birthdate
    cats = plan.field_categories()
    assert "face" in cats and "career" in cats
    assert "attributes" in cats
    assert "head" in plan.preflight and "birth" in plan.preflight
    assert plan.card.get("birthdate") == 130766
    assert plan.card.get("headassetid") == 237067


def test_build_import_plan_apply_requires_target():
    with pytest.raises(ValueError, match="target"):
        import_player.build_import_plan(
            import_player.ImportRequest(mode="apply", card=_sample_card(), target_playerid=None)
        )


def test_build_import_plan_create_warns_on_head():
    card = _sample_card(headassetid=237067, birthdate=130766)
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="create",
            card=card,
            copy_name=True,
            copy_head=True,
            copy_birthdate=True,
        )
    )
    assert plan.mode == "create"
    assert any("freeze" in w.lower() for w in plan.warnings)


def test_prepare_create_card_generic_head_default():
    card = _sample_card(headassetid=237067, birthdate=130766)
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="create",
            card=card,
            copy_name=True,
            copy_head=False,
            copy_birthdate=True,
        )
    )
    out = import_player.prepare_create_card(plan)
    assert out.get("_import_use_real_face") is False
    assert int(out.get("hashighqualityhead") or 0) == 0
    assert int(out.get("birthdate") or 0) == 130766
    assert out.get("firstname") or out.get("surname") or out.get("commonname")


def test_generate_import_apply_lua_writes_editedplayernames():
    card = _sample_card(headassetid=237067, birthdate=130766)
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="apply",
            card=card,
            target_playerid=20801,
            copy_name=True,
            copy_head=True,
            copy_birthdate=True,
        )
    )
    lua = import_player.generate_import_apply_lua(plan)
    assert "editedplayernames" in lua
    assert "usercaneditname" in lua
    assert "Pele" in lua or "PELE" in lua.upper() or "firstname" in lua
    assert "target_playerid = 20801" in lua
    assert "headassetid" in lua or "birthdate" in lua


def test_player_apply_name_parts_block():
    card = _sample_card()
    lua = player_apply.generate_apply_player_lua(
        card,
        12345,
        enabled_categories={"attributes", "career"},
        name_parts={
            "firstname": "Test",
            "surname": "Player",
            "commonname": "T.Player",
            "playerjerseyname": "PLAYER",
        },
    )
    assert "editedplayernames" in lua
    assert "InsertDBTableRow" in lua
    assert "CreateRecord" not in lua
    assert "if found then" in lua
    assert "Test" in lua and "Player" in lua
    assert "tostring(true)" in lua  # names flag in status line


def test_player_apply_lua_comment_strips_breakout():
    card = _sample_card(name='x]] EVIL() --[[y')
    lua = player_apply.generate_apply_player_lua(
        card,
        1,
        enabled_categories={"attributes"},
    )
    header = lua.split("\n", 1)[0]
    assert header.startswith("--[[")
    assert header.rstrip().endswith("]]")
    # Card name must not close the long-comment early (only one terminator at EOL)
    assert header.count("]]") == 1
    assert "[" not in header[4:-2]
    assert "]" not in header[4:-2]


def test_undo_apply_rejects_injected_field_and_label(tmp_path, monkeypatch):
    from src import undo_apply

    monkeypatch.setattr(undo_apply.paths, "app_root", lambda: tmp_path)
    undo_apply.save_snapshot(
        target_id=10,
        fields=[("acceleration", 90), ('evil"]}=1;--', 1)],
        label='x]] print("pwn") --[[',
    )
    lua = undo_apply.generate_undo_lua()
    assert "acceleration" in lua
    assert "evil" not in lua
    header = lua.split("\n", 1)[0]
    assert header.startswith("--[[")
    assert header.rstrip().endswith("]]")
    assert header.count("]]") == 1
    assert "[" not in header[4:-2]
    assert "]" not in header[4:-2]
    # Injected field must not become a Lua string key
    assert 'evil"' not in lua
    assert "}=" not in lua


def test_product_import_card_apply_calls_turbo(monkeypatch):
    card = _sample_card(headassetid=1, birthdate=100)
    called: Dict[str, Any] = {}

    def fake_turbo(lua, **kwargs):
        called["lua"] = lua
        called["kwargs"] = kwargs
        return mock.Mock(ok=True, detail="ok")

    monkeypatch.setattr(product, "turbo_apply_lua", fake_turbo)
    monkeypatch.setattr(
        product.companion_config,
        "update_config",
        lambda **kw: called.setdefault("cfg", kw),
    )

    res = product.import_card(
        card,
        mode="apply",
        target_playerid=20801,
        copy_name=True,
        copy_head=True,
        copy_birthdate=True,
        wait=False,
    )
    assert called.get("lua")
    assert "editedplayernames" in called["lua"]
    assert called["kwargs"].get("kind") == "import"
    assert called["kwargs"].get("target_id") == 20801
    assert called.get("cfg", {}).get("default_target_playerid") == 20801
    assert res.ok is True


def test_product_import_card_create_routes_add(monkeypatch):
    card = _sample_card(headassetid=99, birthdate=100)
    called: Dict[str, Any] = {}

    def fake_add(c, *, teamid=None, mode="auto", wait=None, use_real_face=True, **kwargs):
        # Mirror real add_card_to_user_team signature: teamid= only (not team_id=).
        # **kwargs rejects unexpected names so a team_id= regression TypeErrors here.
        if kwargs:
            raise TypeError(f"unexpected keyword arguments: {sorted(kwargs)}")
        called["card"] = c
        called["teamid"] = teamid
        called["kwargs"] = {
            "teamid": teamid,
            "mode": mode,
            "wait": wait,
            "use_real_face": use_real_face,
        }
        return mock.Mock(ok=True)

    monkeypatch.setattr(product, "add_card_to_user_team", fake_add)

    product.import_card(
        card,
        mode="create",
        copy_name=True,
        copy_head=False,
        copy_birthdate=True,
        team_id=243,
        wait=False,
    )
    assert called["kwargs"].get("use_real_face") is False
    assert called["card"].get("_import_use_real_face") is False
    assert called["teamid"] == 243
    assert "team_id" not in called["kwargs"]


def test_build_import_plan_name_only_skips_career_category():
    card = _sample_card(headassetid=1, birthdate=130766, nationality=27)
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="apply",
            card=card,
            target_playerid=20801,
            copy_name=True,
            copy_head=False,
            copy_birthdate=False,
        )
    )
    cats = plan.field_categories()
    assert "career" not in cats
    assert plan.card.get("birthdate") in (None, "", 0) or plan.card.get("birthdate") == ""
    assert plan.card.get("usercaneditname") == 1


def test_build_import_plan_skills_omit_defaults_when_source_blank():
    """Catalog cards without SM/WF/foot must not force editor defaults into Apply."""
    from src import player_schema

    card = _sample_card()  # no skillmoves / weakfoot / preferredfoot
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="apply",
            card=card,
            target_playerid=20801,
            copy_name=False,
            copy_head=False,
            copy_birthdate=False,
        )
    )
    updates = player_schema.card_to_field_updates(
        plan.card, enabled_categories=plan.field_categories()
    )
    fields = {f for f, _ in updates}
    assert "skillmoves" not in fields
    assert "weakfootabilitytypecode" not in fields
    assert "preferredfoot" not in fields
