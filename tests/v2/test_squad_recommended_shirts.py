"""Recommended shirts cover the selected squad and explain signature collisions."""

from companion.domain.squad_plan import SquadMember, SquadPlan, safe_jersey_writes
from companion.domain.squad_session import (
    RosterPlayer,
    SquadSession,
    jersey_only,
    parse_recipe,
    plan_jersey_changes,
    recommended_shirt_requests,
    stage_per_player_plan,
)


def _final(roster, current, *, prompt="arrange these players by jersey no as you recomended", extra=()):
    changes, notes = plan_jersey_changes(parse_recipe(prompt), roster, current, extra_requests=extra)
    return {**current, **changes}, notes, changes


def test_ronaldinho_signature_overrides_arbitrary_ai_number_40():
    roster = (RosterPlayer(1, "Ronaldinho", "CAM"),)
    final, _notes, _changes = _final(roster, {1: 40}, extra=((1, 40),))
    assert final[1] == 10


def test_shared_number_10_has_a_familiar_alternative_and_explanation():
    roster = (
        RosterPlayer(1, "Lionel Messi", "RW"),
        RosterPlayer(2, "Ronaldinho", "CAM"),
        RosterPlayer(3, "Pelé", "CF"),
        RosterPlayer(4, "Diego Maradona", "CAM"),
    )
    final, notes, _changes = _final(roster, {1: 40, 2: 41, 3: 42, 4: 43})
    assert final == {1: 10, 2: 80, 3: 9, 4: 16}
    assert any("Lionel Messi" in note and "Ronaldinho" in note and "#80" in note for note in notes)
    assert len(set(final.values())) == len(roster)


def test_twenty_signed_stars_have_unique_shirts_independent_of_row_sorting():
    stars = (
        ("Lionel Messi", "RW"), ("Cristiano Ronaldo", "ST"),
        ("Ronaldinho", "CAM"), ("Pelé", "CF"), ("Diego Maradona", "CAM"),
        ("Mohamed Salah", "RW"), ("Steven Gerrard", "CM"),
        ("Fernando Torres", "ST"), ("Virgil van Dijk", "CB"),
        ("Paolo Maldini", "LB"), ("Gianluigi Buffon", "GK"),
        ("Zinedine Zidane", "CAM"), ("Johan Cruyff", "CF"),
        ("Cafu", "RB"), ("Roberto Carlos", "LB"), ("David Beckham", "RM"),
        ("Kaká", "CAM"), ("Thierry Henry", "ST"),
        ("Andrés Iniesta", "CM"), ("Xavi", "CM"),
    )
    roster = tuple(RosterPlayer(pid, name, position) for pid, (name, position) in enumerate(stars, 1))
    current = {player.playerid: 39 + player.playerid for player in roster}
    # Simulate AI simply keeping every arbitrary post-signing shirt.
    ai_requests = tuple(current.items())
    final, notes, changes = _final(roster, current, extra=ai_requests)
    reversed_final, reversed_notes, _changes = _final(tuple(reversed(roster)), current, extra=ai_requests)
    assert final == reversed_final
    assert notes == reversed_notes
    assert len(changes) == 20
    assert len(set(final.values())) == 20
    assert final[1] == 10
    assert final[2] == 7
    assert final[3] == 80
    assert final[11] == 1
    assert all(1 <= number <= 30 or number == 80 for number in final.values())


def test_twenty_unknown_signings_get_low_positional_numbers():
    roster = tuple(
        RosterPlayer(pid, f"Signing {pid}", ("GK", "CB", "CM", "ST")[(pid - 1) % 4])
        for pid in range(1, 21)
    )
    current = {player.playerid: player.playerid + 39 for player in roster}
    final, _notes, changes = _final(roster, current)
    assert len(changes) == 20
    assert len(set(final.values())) == 20
    assert all(1 <= number <= 30 for number in final.values())
    assert final[1] == 1
    assert final[2] in (4, 5, 6, 15, 16, 24)


def test_explicit_typed_shirt_beats_recommendation_and_ai():
    roster = (RosterPlayer(1, "Ronaldinho", "CAM"), RosterPlayer(2, "Messi", "RW"))
    final, notes, _changes = _final(
        roster, {1: 40, 2: 41}, prompt="Ronaldinho jersey 10; arrange jerseys as recommended",
        extra=((1, 40), (2, 40)),
    )
    assert final == {1: 10, 2: 30}
    assert any("Ronaldinho" in note and "Messi" in note and "#30" in note for note in notes)


def test_explicit_high_shirt_is_honored():
    roster = (RosterPlayer(1, "Ronaldinho", "CAM"),)
    final, _notes, _changes = _final(roster, {1: 50}, prompt="Ronaldinho jersey 40; arrange jerseys as recommended")
    assert final[1] == 40


def test_fallback_respects_other_selected_signature_numbers():
    roster = (
        RosterPlayer(1, "Messi", "RW"), RosterPlayer(2, "Ronaldinho", "CAM"),
        RosterPlayer(3, "Andrea Pirlo", "CM"), RosterPlayer(4, "Zinedine Zidane", "CAM"),
        RosterPlayer(5, "Mystery Signing", "CM"),
    )
    current = {1: 40, 2: 41, 3: 42, 4: 43, 5: 44}
    final, notes, _changes = _final(roster, current, prompt="Ronaldinho jersey 80; arrange shirts as recommended")
    assert final[1] == 10
    assert final[2] == 80
    assert final[3] == 21
    assert final[4] == 5
    assert final[5] == 8
    assert len(set(final.values())) == len(roster)
    assert any("Zinedine Zidane" in note and "#5" in note for note in notes)


def test_matching_cristiano_does_not_steal_brazilian_ronaldo_preference():
    roster = (RosterPlayer(1, "Cristiano Ronaldo", "ST"), RosterPlayer(2, "Ronaldo", "ST"))
    requests = recommended_shirt_requests(roster, set())
    assert dict(requests) == {1: 7, 2: 9}


def test_recommended_swaps_with_nonselected_holder_remain_safe_to_apply():
    roster = (RosterPlayer(1, "Ronaldinho", "CAM"), RosterPlayer(2, "Cafu", "RB"))
    current = {1: 40, 2: 41, 3: 10, 4: 2}
    final, _notes, changes = _final(roster, current)
    assert final == {1: 10, 2: 2, 3: 40, 4: 41}
    running = dict(current)
    for pid, number in safe_jersey_writes(current, changes):
        assert number not in {shirt for holder, shirt in running.items() if holder != pid}
        running[pid] = number
    assert running == final


def test_unrequested_ai_shirts_are_dropped_and_unselected_ids_ignored():
    roster = (RosterPlayer(1, "Ronaldinho", "CAM"),)
    final, _notes, changes = _final(roster, {1: 50}, prompt="improve playstyles", extra=((1, 40), (999, 7)))
    assert final == {1: 50} and changes == {}
    final, _notes, changes = _final(roster, {1: 50}, prompt="improve playstyles and give him a good shirt",
                                    extra=((1, 40), (999, 7)))
    assert final == {1: 40}
    assert 999 not in changes


def test_recommended_shirts_alone_do_not_change_attributes():
    recipe = parse_recipe("arrange these players by jersey no as you recomended")
    assert jersey_only(recipe)
    member = SquadMember(playerid=1, name="Ronaldinho", position="CAM", ovr=90, base={"jerseynumber": 40})
    plan = SquadPlan(members=(member,), shirt_book={1: 40})
    staged = stage_per_player_plan(plan, SquadSession(recipe=recipe, targets=()), {})
    assert staged.jersey_numbers == {1: 10}
    assert staged.overrides == {}


def test_recommendations_with_explicit_skills_playstyles_or_stats_are_mixed():
    assert not jersey_only(parse_recipe("5 skill star move; quick step+ playstyles+; arrange jerseys as recommended"))
    assert not jersey_only(parse_recipe("arrange jerseys as recommended and improve pace"))
    assert not jersey_only(parse_recipe("Messi jersey 10 and improve passing"))


def test_unknown_fullbacks_and_striker_get_their_position_numbers():
    roster = (
        RosterPlayer(1, "Left back", "LB"), RosterPlayer(2, "Right back", "RB"),
        RosterPlayer(3, "Centre back", "CB"), RosterPlayer(4, "Striker", "ST"),
    )
    final, _notes, _changes = _final(roster, {pid: 39 + pid for pid in range(1, 5)})
    assert final == {1: 3, 2: 2, 3: 4, 4: 9}
    assert 1 not in final.values()


def test_shared_surnames_do_not_inherit_another_players_signature():
    roster = (
        RosterPlayer(1, "Pau Torres", "CB"), RosterPlayer(2, "Gonçalo Ramos", "ST"),
        RosterPlayer(3, "Rico Henry", "LB"), RosterPlayer(4, "Robbie Keane", "ST"),
    )
    final, _notes, _changes = _final(roster, {pid: 39 + pid for pid in range(1, 5)})
    assert final[1] == 4
    assert final[2] == 9
    assert final[3] == 3
    assert final[4] == 19
