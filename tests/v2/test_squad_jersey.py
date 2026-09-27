"""Club Codex plans can assign a shirt and move whoever already wears it."""

from companion.domain.squad_plan import (
    SHIRT_OPS_PER_JOB,
    SquadMember,
    SquadPlan,
    build_apply_job,
    build_apply_jobs,
    safe_jersey_writes,
)
from companion.domain.squad_session import (
    RosterPlayer,
    SquadSession,
    assign_jersey_numbers,
    extract_named_jerseys,
    jersey_only,
    parse_recipe,
    plan_jersey_changes,
    stage_per_player_plan,
)


def test_prompt_reads_a_named_shirt_number():
    assert extract_named_jerseys("messi jersey 10") == (("messi", 10),)
    assert extract_named_jerseys("jersey 7 for ronaldo") == (("ronaldo", 7),)
    assert extract_named_jerseys("arrange jersey no 11 for salah") == (("salah", 11),)
    assert extract_named_jerseys(
        "improve this player playstyle and playstyle + and arrange jersey no. as you recomended"
    ) == ()
    assert extract_named_jerseys("make the squad prime around 85-95") == ()
    assert jersey_only(parse_recipe("messi jersey 10"))


def test_taken_shirt_moves_to_the_requesters_old_number():
    changes, notes = assign_jersey_numbers(
        {1: 30, 2: 10},
        [(1, 10)],
        names={1: "Messi", 2: "Kane"},
    )
    assert changes == {1: 10, 2: 30}
    assert any("Kane" in note and "#30" in note for note in notes)


def test_taken_shirt_uses_a_free_number_when_the_requester_has_none():
    changes, _notes = assign_jersey_numbers({1: 0, 2: 10, 3: 1}, [(1, 10)])
    assert changes[1] == 10
    assert changes[2] == 2


def test_recommend_prompt_uses_famous_shirts_and_moves_the_real_wearer():
    recipe = parse_recipe(
        "improve this player playstyle and playstyle + and arrange jersey no. as you recomended"
    )
    roster = (
        RosterPlayer(1, "Mohamed Salah", "RW", 90, 90),
        RosterPlayer(2, "Steven Gerrard", "CM", 90, 90),
        RosterPlayer(3, "Virgil van Dijk", "CB", 88, 88),
        RosterPlayer(4, "Emiliano Buendia", "RW", 78, 78),
    )
    changes, notes = plan_jersey_changes(
        recipe,
        roster,
        {1: 0, 2: 0, 3: 0, 4: 10},
        names={1: "Mohamed Salah", 2: "Steven Gerrard", 3: "Virgil van Dijk", 4: "Emiliano Buendia"},
    )
    assert changes[1] == 11
    assert changes[2] == 8
    assert changes[3] == 4
    assert 4 not in changes
    assert not any("Buendia" in note or "Buendía" in note for note in notes)


def test_unknown_shirt_list_does_not_invent_numbers_for_the_squad():
    changes, _notes = assign_jersey_numbers({1: 0, 2: 0, 3: 0}, [(1, 11), (2, 8), (3, 4)])
    assert changes == {1: 11, 2: 8, 3: 4}


def test_stage_and_apply_write_the_shirt_on_the_squad_link():
    recipe = parse_recipe("messi jersey 10")
    messi = SquadMember(
        playerid=1,
        name="Messi",
        ovr=90,
        base={"jerseynumber": 30, "teamid": 2, "overallrating": 90},
    )
    plan = SquadPlan(
        members=(messi,),
        shirt_book={1: 30, 2: 10},
        shirt_names={1: "Messi", 2: "Kane"},
    )
    staged = stage_per_player_plan(plan, SquadSession(recipe=recipe, targets=()), {})
    assert staged.overrides == {}
    assert staged.jersey_numbers[1] == 10
    assert staged.jersey_numbers[2] == 30
    wire = build_apply_job(staged).to_wire(now=1)
    shirts = [op for op in wire["ops"] if op["table"] == "teamplayerlinks"]
    assert_no_shared_shirt({1: 30, 2: 10}, shirts)
    final = apply_shirts({1: 30, 2: 10}, shirts)
    assert final[1] == 10
    assert final[2] == 30
    assert all(op.get("displace") is not True for op in shirts)
    assert shirts[-1]["teamid"] == 2
    assert shirts[-1]["growth_mirror"] == "off"


def test_crash_squad_never_shares_a_shirt_mid_write():
    """The Manchester City plan wrote #4, #5 and #33 while they were still worn."""
    current = {
        273839: 12,  # Pierce Charles
        73669: 4,    # Cafu
        278901: 16,  # Bouaddi
        254243: 5,   # Anderson
        242641: 21,  # Aït-Nouri
        277427: 33,  # O'Reilly
    }
    targets = {
        273839: 4,
        278901: 5,
        242641: 33,
        277427: 21,
        73669: 2,
        254243: 16,
    }
    writes = safe_jersey_writes(current, targets)
    worn = dict(current)
    seen: dict[int, int] = {}
    for pid, number in current.items():
        if 1 <= number <= 99:
            seen[number] = pid
    for pid, number in writes:
        holder = seen.get(number)
        assert holder in (None, pid), (pid, number, holder)
        old = worn.get(pid, 0)
        if old in seen and seen[old] == pid:
            seen.pop(old, None)
        worn[pid] = number
        seen[number] = pid
    assert worn[273839] == 4
    assert worn[73669] == 2
    assert worn[278901] == 5
    assert worn[254243] == 16
    assert worn[242641] == 33
    assert worn[277427] == 21


def test_long_shirt_plan_is_split_under_the_worker_budget():
    member = SquadMember(playerid=1, name="Keeper", base={"teamid": 10, "jerseynumber": 1})
    numbers = {pid: pid for pid in range(2, 22)}
    book = {pid: 0 for pid in numbers}
    book[1] = 1
    plan = SquadPlan(members=(member,), jersey_numbers=numbers, shirt_book=book)
    jobs = build_apply_jobs(plan, label="Squad plan")
    assert len(jobs) > 1
    seen: list[str] = []
    for job in jobs:
        shirts = [op for op in job.ops if op.body.get("table") == "teamplayerlinks"]
        assert 1 <= len(shirts) <= SHIRT_OPS_PER_JOB
        assert job.budget_ms == 5000
        seen.extend(op.id for op in shirts)
    wire = build_apply_job(plan).to_wire(now=1)
    expected = [op["id"] for op in wire["ops"] if op.get("table") == "teamplayerlinks"]
    assert seen == expected


def apply_shirts(current: dict[int, int], shirts: list[dict]) -> dict[int, int]:
    worn = dict(current)
    for op in shirts:
        worn[int(op["key"]["value"])] = int(op["fields"]["jerseynumber"])
    return worn


def assert_no_shared_shirt(current: dict[int, int], shirts: list[dict]) -> None:
    worn = dict(current)
    seen = {number: pid for pid, number in worn.items() if 1 <= number <= 99}
    for op in shirts:
        pid = int(op["key"]["value"])
        number = int(op["fields"]["jerseynumber"])
        holder = seen.get(number)
        assert holder in (None, pid)
        old = worn.get(pid, 0)
        if seen.get(old) == pid:
            seen.pop(old, None)
        worn[pid] = number
        seen[number] = pid
