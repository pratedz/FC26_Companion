"""Career squad Grok session: rank, pin, cohesion, per-player overrides."""

from __future__ import annotations

import json
from pathlib import Path

from companion.domain.builds import outfield_attribute_names
from companion.domain.squad_plan import (
    MAX_OVERRIDE_FIELDS,
    build_apply_job,
    plan_from_rows,
)
from companion.domain.squad_session import (
    IDENTITY_FIELDS,
    SquadTarget,
    cap_playstyle_plus,
    cohesion_clamp,
    parse_recipe,
    refine_session,
    roster_from_rows,
    sanitize_patch,
    stage_per_player_plan,
    build_session,
    lock_python_scope,
    merge_recipe,
)
from companion.integrations import grok


ROOT = Path(__file__).parents[2]


def _row(pid: int, name: str, pos: str, ovr: int, pot: int | None = None, **extra) -> dict:
    raw = {
        "playerid": pid,
        "name": name,
        "position": pos,
        "overallrating": ovr,
        "potential": pot if pot is not None else ovr + 2,
        **extra,
    }
    return {"id": pid, "name": name, "pos": pos, "ovr": ovr, "pot": raw["potential"], "_raw": raw}


def _fat(ovr: int = 80, **extra) -> dict[str, int]:
    fields = {name: max(1, min(99, ovr - 4)) for name in outfield_attribute_names()}
    fields["overallrating"] = ovr
    fields["potential"] = min(99, ovr + 4)
    fields.update(extra)
    return fields


def test_default_model_is_grok_46() -> None:
    assert grok.DEFAULT_MODEL == "gpt-6-luna"


def test_lift_band_selects_only_75_to_80() -> None:
    roster = roster_from_rows(
        [
            _row(1, "Too low", "CM", 74, 84),
            _row(2, "Gavi", "CM", 79, 88),
            _row(3, "Edge", "CM", 80, 86),
            _row(4, "Too high", "CM", 81, 88),
            _row(5, "Star", "ST", 91, 91),
        ]
    )
    recipe = parse_recipe("make players with stat 75-80 into 84")
    session = build_session(roster, recipe)
    assert recipe.ovr_min == 75 and recipe.ovr_max == 80
    assert recipe.target_ovr == 84
    assert recipe.soft_band is False
    assert session.playerids == (2, 3)
    assert {t.target_ovr for t in session.targets} == {84}


def test_midfield_only_drops_st_and_cb() -> None:
    roster = roster_from_rows(
        [
            _row(1, "Lewandowski", "ST", 90, 90),
            _row(2, "Araujo", "CB", 86, 89),
            _row(3, "Pedri", "CM", 86, 93),
            _row(4, "Gavi", "CAM", 79, 88),
        ]
    )
    session = build_session(roster, parse_recipe("upgrade midfield only"))
    names = {t.name for t in session.targets}
    assert names == {"Pedri", "Gavi"}


def test_selected_gk_kept_unless_explicitly_excluded() -> None:
    roster = roster_from_rows(
        [
            _row(1, "Messi", "RW", 90, 90),
            _row(2, "Ter Stegen", "GK", 88, 88),
        ]
    )
    selected = build_session(roster, parse_recipe("make the team prime around 85-95"))
    assert selected.playerids == (1, 2)
    skipped = build_session(roster, parse_recipe("make the team prime around 85-95, skip keepers"))
    assert skipped.playerids == (1,)
    included = build_session(roster, parse_recipe("prime keepers around 85-95"))
    assert 2 in included.playerids


def test_prime_band_messi_face_fati_prospect() -> None:
    roster = roster_from_rows(
        [
            _row(158023, "Messi", "RW", 90, 90),
            _row(253072, "Ansu Fati", "LW", 78, 88),
        ]
    )
    recipe = parse_recipe("make our team prime, overall around 85-95", "Barcelona")
    session = build_session(roster, recipe)
    assert recipe.effective_band == (85, 95)
    by_name = {t.name: t for t in session.targets}
    assert by_name["Messi"].rung == "face"
    assert by_name["Ansu Fati"].rung == "prospect"
    assert by_name["Messi"].target_ovr == 95
    assert by_name["Ansu Fati"].target_ovr == 85
    assert by_name["Messi"].target_ovr > by_name["Ansu Fati"].target_ovr


def test_ladder_lock_rejects_grok_swap() -> None:
    roster = roster_from_rows(
        [
            _row(158023, "Messi", "RW", 90, 90),
            _row(253072, "Ansu Fati", "LW", 78, 88),
        ]
    )
    recipe = parse_recipe("prime around 85-95")
    session = build_session(
        roster,
        recipe,
        grok_targets=[
            {"playerid": 158023, "target_ovr": 85, "rung": "prospect", "why": "wrong"},
            {"playerid": 253072, "target_ovr": 95, "rung": "face", "why": "wrong"},
        ],
    )
    by_name = {t.name: t for t in session.targets}
    assert by_name["Messi"].target_ovr >= by_name["Ansu Fati"].target_ovr
    assert by_name["Messi"].rung == "face"
    assert by_name["Ansu Fati"].rung == "prospect"


def test_pin_and_exclude() -> None:
    roster = roster_from_rows(
        [
            _row(1, "Pedri", "CM", 86, 93),
            _row(2, "Gavi", "CM", 79, 88),
            _row(3, "Academy", "CB", 65, 82),
        ]
    )
    session = build_session(roster, parse_recipe("prime around 85-95"))
    refined = refine_session(session, excluded_ids=(3,), pins={1: 90})
    assert 3 not in refined.playerids
    pedri = refined.target_for(1)
    assert pedri is not None and pedri.pinned is True and pedri.target_ovr == 90


def test_soft_around_vs_exact_to() -> None:
    exact = parse_recipe("lift everyone to 84")
    assert exact.soft_band is False
    assert exact.effective_band == (84, 84)
    soft = parse_recipe("make overall around 84")
    assert soft.soft_band is True
    assert soft.effective_band == (83, 85)


def test_overrides_apply_without_shared_template() -> None:
    rows = [
        _row(10, "Gavi", "CM", 80, 88, **_fat(80)),
        _row(11, "Pedri", "CM", 86, 93, **_fat(86)),
    ]
    plan = plan_from_rows(rows)
    session = build_session(roster_from_rows(rows), parse_recipe("players 75-90 into 84"))
    staged = stage_per_player_plan(
        plan,
        session,
        {
            10: {"overallrating": 84, "firstnameid": 1, "gkdiving": 99},
            11: {"overallrating": 84, "acceleration": 90},
        },
    )
    assert staged.template == {}
    assert staged.mode == "per_player"
    assert 10 in staged.overrides and 11 in staged.overrides
    assert "firstnameid" not in staged.overrides[10]
    assert "gkdiving" not in staged.overrides[10]
    assert len(staged.overrides[10]) <= MAX_OVERRIDE_FIELDS
    assert len(staged.overrides[10]) > 20  # overall-only expanded
    job = build_apply_job(staged)
    wire = job.to_wire()
    assert len(wire["ops"]) == 2
    assert all(op["op"] == "set_fields" for op in wire["ops"])
    assert "1 identity" in staged.blast_summary() or "0 identity" in staged.blast_summary()


def test_identity_and_gk_stripped_unless_asked() -> None:
    target_session = build_session(
        roster_from_rows([_row(1, "Araujo", "CB", 86, 89)]),
        parse_recipe("make him 88 overall"),
    )
    target = target_session.targets[0]
    cleaned = sanitize_patch(
        {
            "overallrating": 88,
            "firstnameid": 12,
            "headassetid": 99,
            "gkdiving": 90,
            "finishing": 99,
        },
        recipe=target_session.recipe,
        target=target,
        current=_fat(86, finishing=55),
    )
    assert not (IDENTITY_FIELDS & set(cleaned))
    assert "gkdiving" not in cleaned
    assert cleaned["finishing"] <= 82


def test_better_overalls_keep_more_playstyle_plus() -> None:
    from companion.domain.squad_session import playstyle_plus_cap

    assert playstyle_plus_cap(84) == 2
    assert playstyle_plus_cap(85) == 3
    assert playstyle_plus_cap(88) == 4
    assert playstyle_plus_cap(92) == 5
    roster = roster_from_rows(
        [
            _row(1, "Cristiano Ronaldo", "ST", 85, 85, **_fat(85)),
            _row(4, "Jadon Sancho", "LM", 79, 80, **_fat(79)),
        ]
    )
    session = build_session(
        roster, parse_recipe("make cr7 overall 88 sancho overall 84")
    )
    plan = plan_from_rows(
        [
            _row(1, "Cristiano Ronaldo", "ST", 85, 85, **_fat(85)),
            _row(4, "Jadon Sancho", "LM", 79, 80, **_fat(79)),
        ]
    )
    staged = stage_per_player_plan(
        plan,
        session,
        {
            1: {"overallrating": 88, "icontrait1": 0b11111},
            4: {"overallrating": 84, "icontrait1": 0b11111},
        },
    )
    assert bin(int(staged.overrides[1]["icontrait1"])).count("1") == 4
    assert bin(int(staged.overrides[4]["icontrait1"])).count("1") == 2


def test_no_limit_keeps_every_playstyle_plus_bit() -> None:
    recipe = parse_recipe("improve these players adding playstyles+ no limit")
    assert recipe.unlimited_playstyles
    assert recipe.wants_playstyles
    roster = roster_from_rows(
        [_row(1, "Ronaldinho", "CAM", 84, 84, **_fat(84))]
    )
    session = build_session(roster, recipe)
    plan = plan_from_rows(
        [_row(1, "Ronaldinho", "CAM", 84, 84, **_fat(84))]
    )
    staged = stage_per_player_plan(
        plan,
        session,
        {1: {"overallrating": 84, "icontrait1": 0b11111}},
    )
    assert staged.overrides[1]["icontrait1"] == 0b11111
    from companion.domain.squad_session import propose_payload

    payload = propose_payload(plan, session)
    assert payload[0]["playstyle_plus_max"] is None


def test_playstyle_plus_capped_at_two_bits() -> None:
    raw = cap_playstyle_plus({"icontrait1": 0b1111, "icontrait2": 0}, max_plus=2)
    assert bin(int(raw["icontrait1"])).count("1") == 2
    unlimited = cap_playstyle_plus({"icontrait1": 0b1111}, max_plus=8)
    assert unlimited["icontrait1"] == 0b1111


def test_cohesion_academy_cannot_pass_face() -> None:
    roster = roster_from_rows(
        [
            _row(1, "Messi", "RW", 90, 90),
            _row(2, "Academy CB", "CB", 65, 82),
        ]
    )
    session = build_session(roster, parse_recipe("prime around 85-95"))
    overrides, notes = cohesion_clamp(
        {
            1: {"overallrating": 95, "potential": 95},
            2: {"overallrating": 99, "potential": 99},
        },
        session,
    )
    assert overrides[1]["overallrating"] == 95
    assert overrides[2]["overallrating"] < overrides[1]["overallrating"]
    assert notes


def test_interpret_reapplies_band_filter(monkeypatch) -> None:
    roster = roster_from_rows(
        [
            _row(2, "Gavi", "CM", 79, 88),
            _row(5, "Star", "ST", 91, 91),
        ]
    )
    content = {
        "recipe": {"intent": "lift_band", "ovr_min": 70, "ovr_max": 99, "target_ovr": 84},
        "targets": [
            {"playerid": 2, "target_ovr": 84, "rung": "star", "why": "band"},
            {"playerid": 5, "target_ovr": 84, "rung": "face", "why": "sneak"},
        ],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{"message": {"content": json.dumps(content)}}]
        },
    )
    session = grok.interpret_squad_edit(
        roster=roster,
        request="make players between 75-80 into 84",
    )
    assert session.playerids == (2,)
    assert session.targets[0].target_ovr == 84


def test_propose_squad_players_revision_and_shape(monkeypatch) -> None:
    from companion.domain.squad_session import parse_recipe as parse

    recipe = parse("players 75-80 into 84")
    current = _fat(79)
    payload = [
        {
            "playerid": 7,
            "name": "Gavi",
            "position": "CM",
            "rung": "star",
            "target_ovr": 84,
            "pinned": False,
            "current_values": current,
        }
    ]
    rev = "abc123abc123aaaa"
    captured: list = []

    def fake_request(messages, model):
        captured.append(messages)
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "selection_revision": rev,
                        "players": [{
                            "playerid": 7,
                            "summary": "Gavi stays a creator at 84",
                            "changes": [
                                {"field": "overallrating", "value": 84},
                                {"field": "shortpassing", "value": 90},
                            ],
                        }],
                    })
                }
            }]
        }

    monkeypatch.setattr(grok, "_request", fake_request)
    patches, reasons, _hints = grok.propose_squad_players(
        players=payload,
        selection_revision=rev,
        request="make 75-80 into 84",
        recipe=recipe,
    )
    assert patches[7]["overallrating"] == 84
    assert "creator" in reasons[7]
    system = captured[0][0]["content"]
    assert "DIFFERENT patch" in system or "different patch" in system.casefold()


def test_propose_runs_player_chunks_together(monkeypatch) -> None:
    from companion.domain.squad_session import parse_recipe as parse

    recipe = parse("improve playstyles")
    rev = "abc123abc123aaaa"
    calls: list[int] = []
    sent: list[str] = []

    def fake_request(messages, model):
        sent.append(messages[1]["content"])
        body = json.loads(messages[1]["content"])
        ids = [row["playerid"] for row in body["players"]]
        calls.append(len(ids))
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "selection_revision": rev,
                        "players": [
                            {
                                "playerid": pid,
                                "summary": "kept",
                                "changes": [{"field": "overallrating", "value": 84}],
                            }
                            for pid in ids
                        ],
                    })
                }
            }]
        }

    monkeypatch.setattr(grok, "_request", fake_request)
    players = [
        {"playerid": pid, "name": f"P{pid}", "position": "CM", "target_ovr": 84, "current_values": {}}
        for pid in range(1, 7)
    ]
    patches, _reasons, _hints = grok.propose_squad_players(
        players=players,
        selection_revision=rev,
        request="improve playstyles",
        recipe=recipe,
    )
    assert sorted(patches) == [1, 2, 3, 4, 5, 6]
    assert len(calls) == 2
    assert "tattoohead" not in sent[0]
    assert "acceleration" in sent[0]


def test_pin_survives_grok_overall() -> None:
    rows = [_row(1, "Pedri", "CM", 86, 93, **_fat(86))]
    plan = plan_from_rows(rows)
    session = refine_session(
        build_session(roster_from_rows(rows), parse_recipe("prime around 85-95")),
        pins={1: 90},
    )
    staged = stage_per_player_plan(plan, session, {1: {"overallrating": 88}})
    assert staged.overrides[1]["overallrating"] == 90


def test_prime_band_does_not_rewrite_age() -> None:
    recipe = parse_recipe("make our team prime, overall around 85-95")
    assert recipe.intent == "prime"
    assert "age" not in recipe.families
    assert "body" not in recipe.families
    overall = parse_recipe("make overall around 85-95")
    assert "age" not in overall.families
    assert "body" not in overall.families
    younger = parse_recipe("make the squad younger")
    assert "age" in younger.families


def test_bare_99_is_not_overstat() -> None:
    recipe = parse_recipe("prime around 85-95")
    assert recipe.intent == "prime"
    over = parse_recipe("make them all 99 ovr")
    assert over.intent == "overstat"


def test_lock_scope_keeps_python_families() -> None:
    original = parse_recipe("make our team prime, overall around 85-95")
    merged = merge_recipe(
        original, {"families": ["attrs", "age", "body", "identity"]}
    )
    locked = lock_python_scope(original, merged)
    assert "age" not in locked.families
    assert "body" not in locked.families
    assert "identity" not in locked.families


def test_interpret_falls_back_when_grok_down(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(grok, "_request", boom)
    session = grok.interpret_squad_edit(
        roster=roster_from_rows([_row(158023, "Messi", "RW", 90, 90)]),
        request="make our team prime, overall around 85-95",
    )
    assert session.playerids == (158023,)
    assert session.targets[0].rung == "face"
    assert any("GPT ranking skipped" in note for note in session.warnings)


def test_club_and_planner_session_copy() -> None:
    club = (ROOT / "companion" / "ui" / "surfaces" / "club.py").read_text(encoding="utf-8")
    planner = (ROOT / "companion" / "ui" / "surfaces" / "planner.py").read_text(
        encoding="utf-8"
    )
    assert "messi jersey 10" in planner
    assert "_ask_squad_grok" in club
    assert "Only checked" not in club
    assert "Players this prompt will edit" not in club
    assert "Use grid" not in club
    assert 'mode="per_player"' in club
    assert "Same numbers on every selected player" in planner or "Same patch for all" in planner
    assert "Rebuild" in planner
    assert "These players" in planner
    assert "Codex is still writing each build." in planner
    assert "_sync_session_apply" in planner
    assert "_draft_session" in planner
    assert "interpret_squad_edit" in planner
    assert "propose_squad_players" in planner
    assert "Plan these players" in planner


def test_intended_prompt_rows_none_means_all() -> None:
    from companion.ui.surfaces._common import (
        cap_prompt_rows,
        grok_scope_caption,
        grok_scope_rows,
        intended_prompt_rows,
    )

    rows = [
        {"id": 1, "name": "Low", "ovr": 70},
        {"id": 2, "name": "High", "ovr": 90},
        {"id": 3, "name": "Mid", "ovr": 80},
    ]
    assert [r["id"] for r in intended_prompt_rows(rows, None)] == [1, 2, 3]
    assert [r["id"] for r in intended_prompt_rows(rows, [2])] == [2]
    assert intended_prompt_rows(rows, []) == ()
    capped, truncated = cap_prompt_rows(rows, limit=2)
    assert truncated is True
    assert [r["id"] for r in capped] == [2, 3]
    whole, kind = grok_scope_rows(rows, checked_ids=[2], checked_only=False)
    assert kind == "squad" and [r["id"] for r in whole] == [1, 2, 3]
    picked, kind = grok_scope_rows(rows, checked_ids=[2], checked_only=True)
    assert kind == "checked" and [r["id"] for r in picked] == [2]
    empty, kind = grok_scope_rows(rows, checked_ids=[], checked_only=True)
    assert kind == "checked" and empty == ()
    assert "whole squad" in grok_scope_caption(36, 2, checked_only=False)
    assert "2 checked" in grok_scope_caption(36, 2, checked_only=True)
    assert "turn off Only checked" in grok_scope_caption(36, 0, checked_only=True)


def test_draft_session_before_grok() -> None:
    from companion.ui.surfaces.planner import _draft_session

    rows = [
        _row(158023, "Messi", "RW", 90, 90),
        _row(253072, "Ansu Fati", "LW", 78, 88),
    ]
    plan = plan_from_rows(rows)
    session = _draft_session(plan, "make our team prime, overall around 85-95", "Barcelona")
    by_name = {item.name: item for item in session.targets}
    assert by_name["Messi"].rung == "face"
    assert by_name["Ansu Fati"].rung == "prospect"
    from companion.ui.surfaces.planner import session_interpret_worker

    errors: list[BaseException] = []

    class _Tok:
        cancelled = False

    session_interpret_worker(
        _Tok(),
        roster=[_row(1, "A", "CM", 80)],
        prompt="  ",
        club_name="Barcelona",
        on_success=lambda *_: None,
        on_error=errors.append,
    )
    assert errors
    assert "describe" in str(errors[0]).casefold()


def test_current_squad_plan_release_version() -> None:
    from companion import __version__

    assert __version__ == "2.10.40"


def test_relative_pace_under_80() -> None:
    recipe = parse_recipe("+3 pace for everyone under 80 pace")
    assert recipe.intent == "relative"
    assert recipe.relative_ops
    session = build_session(
        roster_from_rows([_row(1, "Gavi", "CM", 79, 88)]),
        recipe,
    )
    cleaned = sanitize_patch(
        {},
        recipe=recipe,
        target=session.targets[0],
        current=_fat(79, acceleration=70, sprintspeed=72),
    )
    assert cleaned["acceleration"] == 73
    assert cleaned["sprintspeed"] == 75
    skipped = sanitize_patch(
        {},
        recipe=recipe,
        target=session.targets[0],
        current=_fat(79, acceleration=88, sprintspeed=90),
    )
    assert skipped.get("acceleration", 88) >= 88
    assert "sprintspeed" not in skipped or skipped["sprintspeed"] >= 90


def test_plan_hint_shows_each_named_overall() -> None:
    from companion.domain.squad_session import plan_hint

    rows = [
        _row(1, "Cristiano Ronaldo", "ST", 85, 85),
        _row(2, "Riyad Mahrez", "RM", 84, 84),
        _row(3, "Fabinho", "CDM", 82, 82),
        _row(4, "Jadon Sancho", "LM", 79, 80),
    ]
    empty = plan_hint(rows, "")
    assert "Cristiano Ronaldo" in empty
    assert "cr7 overall 88" in empty
    typed = plan_hint(
        rows,
        "make cr7 overall 88 mahrez overall 85 fabinho overall 85 sancho overall 84",
    )
    assert "Cristiano Ronaldo → 88" in typed
    assert "Riyad Mahrez → 85" in typed
    assert "Fabinho → 85" in typed
    assert "Jadon Sancho → 84" in typed
    assert "playstyles" in typed


def test_named_overalls_pin_each_player_and_keep_playstyles() -> None:
    rows = [
        _row(1, "Cristiano Ronaldo", "ST", 85, 85, **_fat(85)),
        _row(2, "Riyad Mahrez", "RM", 84, 84, **_fat(84)),
        _row(3, "Fabinho", "CDM", 82, 82, **_fat(82)),
        _row(4, "Jadon Sancho", "LM", 79, 80, **_fat(79)),
        _row(5, "Ben Wilson", "GK", 68, 68),
    ]
    prompt = "make cr7 overall 88 mahrez overall 85 fabinho overall 85 sancho overall 84"
    recipe = parse_recipe(prompt)
    assert "playstyles" in recipe.families
    assert [ovr for _name, ovr in recipe.named_overalls] == [88, 85, 85, 84]
    session = build_session(roster_from_rows(rows), recipe)
    by_id = {item.playerid: item for item in session.targets}
    assert set(by_id) == {1, 2, 3, 4}
    assert by_id[1].target_ovr == 88 and by_id[1].pinned
    assert by_id[2].target_ovr == 85 and by_id[2].pinned
    assert by_id[3].target_ovr == 85 and by_id[3].pinned
    assert by_id[4].target_ovr == 84 and by_id[4].pinned
    plan = plan_from_rows(rows)
    staged = stage_per_player_plan(
        plan,
        session,
        {
            1: {"overallrating": 99, "trait1": 12, "icontrait1": 0b1111, "acceleration": 90},
            2: {"overallrating": 70, "trait1": 3, "finishing": 80},
            3: {"overallrating": 85, "trait2": 9, "shortpassing": 86},
            4: {"overallrating": 84, "trait1": 5, "dribbling": 84},
        },
    )
    assert staged.overrides[1]["overallrating"] == 88
    assert staged.overrides[1]["trait1"] == 12
    assert bin(int(staged.overrides[1]["icontrait1"])).count("1") == 4
    assert staged.overrides[2]["overallrating"] == 85
    assert staged.overrides[2]["trait1"] == 3
    assert staged.overrides[4]["overallrating"] == 84


def test_named_overalls_survive_a_collapsed_codex_band(monkeypatch) -> None:
    rows = [
        _row(1, "Cristiano Ronaldo", "ST", 85, 85),
        _row(2, "Riyad Mahrez", "RM", 84, 84),
    ]
    content = {
        "recipe": {
            "intent": "lift_band",
            "target_ovr": 99,
            "band_lo": 99,
            "band_hi": 99,
        },
        "targets": [
            {"playerid": 1, "target_ovr": 99, "rung": "face", "why": "same"},
            {"playerid": 2, "target_ovr": 99, "rung": "star", "why": "same"},
        ],
    }
    monkeypatch.setattr(
        grok,
        "_request",
        lambda messages, model: {
            "choices": [{"message": {"content": json.dumps(content)}}]
        },
    )
    session = grok.interpret_squad_edit(
        roster=roster_from_rows(rows),
        request="make cr7 overall 88 mahrez overall 85",
    )
    assert session.target_for(1).target_ovr == 88
    assert session.target_for(2).target_ovr == 85
    assert session.target_for(1).pinned and session.target_for(2).pinned
    assert "playstyles" in session.recipe.families


def test_plain_overall_ask_does_not_invent_names() -> None:
    recipe = parse_recipe("make him 88 overall")
    assert recipe.named_overalls == ()
    assert "playstyles" not in recipe.families
    prime = parse_recipe("make our team prime, overall around 85-95")
    assert prime.named_overalls == ()
    assert "playstyles" not in prime.families


def test_pinned_overall_is_not_pulled_below_the_face() -> None:
    roster = roster_from_rows(
        [
            _row(1, "Cristiano Ronaldo", "ST", 85, 85),
            _row(4, "Jadon Sancho", "LM", 79, 80),
        ]
    )
    session = build_session(
        roster,
        parse_recipe("make cr7 overall 84 sancho overall 90"),
    )
    overrides, notes = cohesion_clamp(
        {
            1: {"overallrating": 84},
            4: {"overallrating": 90},
        },
        session,
    )
    assert overrides[4]["overallrating"] == 90
    assert notes == ()


def _plus_bits(count: int) -> int:
    value = 0
    for bit in range(count):
        value |= 1 << bit
    return value


def _target(ovr: int) -> SquadTarget:
    return SquadTarget(
        playerid=1,
        name="Youth",
        position="CM",
        ovr=ovr,
        pot=ovr,
        target_ovr=ovr,
        rung="rotation",
        why="test",
    )


def test_no_limit_ceiling_turns_the_playstyle_cap_off() -> None:
    prompt = (
        "1. arrange these players jersey no by recommendation "
        "2. improve these players playstyle+ no limit ceiling"
    )
    recipe = parse_recipe(prompt)
    assert recipe.unlimited_playstyles is True
    assert recipe.wants_playstyles is True
    six = _plus_bits(6)
    cleaned = sanitize_patch(
        {"overallrating": 70, "icontrait1": six},
        recipe=recipe,
        target=_target(70),
        current={"overallrating": 70},
    )
    assert cleaned["icontrait1"] == six


def test_standard_ceiling_keeps_two_playstyle_plus_below_85() -> None:
    recipe = parse_recipe("improve these players playstyles")
    assert recipe.unlimited_playstyles is False
    cleaned = sanitize_patch(
        {"overallrating": 70, "icontrait1": _plus_bits(6)},
        recipe=recipe,
        target=_target(70),
        current={"overallrating": 70},
    )
    assert bin(cleaned["icontrait1"]).count("1") == 2


def test_high_overall_standard_ceiling_is_five() -> None:
    recipe = parse_recipe("improve these players playstyles")
    cleaned = sanitize_patch(
        {"overallrating": 92, "icontrait1": _plus_bits(8)},
        recipe=recipe,
        target=_target(92),
        current={"overallrating": 92},
    )
    assert bin(cleaned["icontrait1"]).count("1") == 5


def test_bad_field_on_one_player_keeps_the_other() -> None:
    rows = [_row(1, "A", "CM", 80), _row(2, "B", "ST", 81)]
    recipe = parse_recipe("improve these players playstyle+ no limit ceiling")
    session = build_session(roster_from_rows(rows), recipe)
    plan = plan_from_rows(rows)
    staged = stage_per_player_plan(
        plan,
        session,
        {
            1: {"overallrating": 80, "not_a_real_field": 9},
            2: {"overallrating": 84, "icontrait1": _plus_bits(4)},
        },
    )
    assert 1 not in staged.overrides or "not_a_real_field" not in staged.overrides.get(1, {})
    assert staged.overrides[2]["icontrait1"] == _plus_bits(4)
    assert any("A:" in note or "not_a_real_field" in note for note in staged.warnings)


def test_one_codex_group_can_fail_without_dropping_the_other(monkeypatch) -> None:
    def fake(chunk, **_kwargs):
        if any(int(row["playerid"]) == 6 for row in chunk):
            return (
                {int(row["playerid"]): {"overallrating": 80} for row in chunk},
                {},
                {},
            )
        raise RuntimeError("response truncated")

    monkeypatch.setattr(grok, "_propose_squad_chunk", fake)
    problems: list[str] = []
    patches, _reasons, _hints = grok.propose_squad_players(
        players=[{"playerid": i, "name": f"P{i}"} for i in range(1, 7)],
        selection_revision="rev",
        request="improve playstyles",
        recipe=parse_recipe("improve playstyles"),
        problems=problems,
    )
    assert 6 in patches
    assert 1 not in patches
    assert problems and "truncated" in problems[0]


def test_twenty_two_selected_players_stay_in_the_session() -> None:
    rows = [_row(i, f"Player {i}", "CM", 70 + (i % 20)) for i in range(1, 23)]
    recipe = parse_recipe(
        "arrange these players jersey no by recommendation and improve playstyle+ no limit ceiling"
    )
    session = build_session(roster_from_rows(rows), recipe)
    assert recipe.unlimited_playstyles is True
    assert len(session.targets) == 22
    assert session.truncated is False
