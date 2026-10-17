"""Club shirt map and deterministic, per-player shirt fixes.

The shirt map must match what the in-game write check sees: every
``teamplayerlinks`` row of the club, not only the senior list. A loaned-out
player still wears his number there, so a planner that cannot see him plans a
duplicate and the worker refuses it (``shirt_taken``).

Fixes here never call an AI and never move a teammate who is not part of the
plan; moving a wearer happens only through the explicit ``swap_shirt``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from .squad_plan import ShirtIssue, SquadMember, SquadPlan


def _number(raw: Any) -> int:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    return value if 1 <= value <= 99 else 0


def _pid(row: Mapping[str, Any]) -> int:
    raw = dict(row.get("_raw") or {})
    try:
        return int(row.get("playerid") or row.get("id") or raw.get("playerid") or 0)
    except (TypeError, ValueError):
        return 0


def shirt_map_from_squad(
    players: Iterable[Mapping[str, Any]],
    team_shirts: Iterable[Mapping[str, Any]] = (),
    members: Sequence[SquadMember] = (),
) -> tuple[dict[int, int], dict[int, str], frozenset[int]]:
    """``(playerid -> shirt, playerid -> name, hidden ids)`` for the whole club.

    ``team_shirts`` (every club link row) wins when the loaded core exports
    it; otherwise the senior export is all there is. Every holder is kept, so
    two players sharing a number both stay visible.
    """
    book: dict[int, int] = {}
    names: dict[int, str] = {}
    senior: set[int] = set()
    for row in players or ():
        if not isinstance(row, Mapping):
            continue
        pid = _pid(row)
        if pid <= 0:
            continue
        senior.add(pid)
        book[pid] = _number(row.get("jerseynumber"))
        names[pid] = str(row.get("name") or (row.get("_raw") or {}).get("name") or pid)
    hidden: set[int] = set()
    rows = [row for row in (team_shirts or ()) if isinstance(row, Mapping)]
    if rows:
        linked: dict[int, int] = {}
        for row in rows:
            pid = _pid(row)
            if pid <= 0:
                continue
            linked[pid] = _number(row.get("jerseynumber"))
            if row.get("name") and pid not in names:
                names[pid] = str(row["name"])
            if row.get("senior") is False or pid not in senior:
                hidden.add(pid)
        # The link table is what the game checks. A senior export row that
        # has no link (stale export) keeps its last known number.
        book.update(linked)
    for member in members:
        book.setdefault(member.playerid, _number(member.base.get("jerseynumber")))
        names.setdefault(member.playerid, member.name)
    hidden -= {member.playerid for member in members}
    return book, names, frozenset(hidden)


def with_shirt_map(plan: SquadPlan, players: Iterable[Mapping[str, Any]],
                   team_shirts: Iterable[Mapping[str, Any]] = ()) -> SquadPlan:
    players, team_shirts = tuple(players or ()), tuple(team_shirts or ())
    if not players and not team_shirts:
        return plan  # no squad read yet: keep what the plan already knows
    book, names, hidden = shirt_map_from_squad(players, team_shirts, plan.members)
    return replace(plan, shirt_book=book, shirt_names={**dict(plan.shirt_names), **names}, shirt_hidden=hidden)


def holders_by_number(book: Mapping[int, int | None]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for pid, raw in book.items():
        number = _number(raw)
        if number:
            out.setdefault(number, []).append(int(pid))
    return out


def shared_numbers(book: Mapping[int, int | None], names: Mapping[int, str]) -> list[str]:
    """Plain warnings for numbers two club players already share."""
    notes = []
    for number, pids in sorted(holders_by_number(book).items()):
        if len(pids) > 1:
            who = " and ".join(str(names.get(pid) or pid) for pid in sorted(pids))
            notes.append(f"#{number} is worn by both {who}. Give one of them another number.")
    return notes


def final_shirts(plan: SquadPlan) -> dict[int, int]:
    """Every club player's number once this plan's shirt writes are done."""
    final = {int(pid): _number(number) for pid, number in plan.shirt_book.items()}
    for member in plan.members:
        final.setdefault(member.playerid, _number(member.base.get("jerseynumber")))
    final.update({int(pid): int(number) for pid, number in plan.jersey_numbers.items()})
    return final


def shirt_issue_message(number: int, holders: Sequence[str]) -> str:
    if not holders:
        return f"No free shirt number is left for #{number}. Pick another number."
    if len(holders) == 1:
        return f"#{number} is worn by {holders[0]}. Pick another number or swap."
    return f"#{number} is worn by {' and '.join(holders)}. Pick another number."


def make_shirt_issue(playerid: int, number: int, holders: Sequence[int],
                     names: Mapping[int, str]) -> ShirtIssue:
    labels = [str(names.get(pid) or pid) for pid in holders]
    single = holders[0] if len(holders) == 1 else None
    return ShirtIssue(playerid=int(playerid), number=int(number), holder=single,
                      holder_name=labels[0] if len(labels) == 1 else " and ".join(labels),
                      message=shirt_issue_message(number, labels))


def preferred_numbers(name: str, position: str) -> tuple[int, ...]:
    """Familiar shirts first, then position numbers, then the rest. No #1 outfield."""
    from .squad_session import _FAMOUS_SHIRTS, _POSITION_SHIRTS, _fold_name, _needle_hits, _pos_token, position_group

    tokens = _fold_name(name).split()
    matches = [(index, needles, shirts) for index, (needles, shirts) in enumerate(_FAMOUS_SHIRTS)
               if all(_needle_hits(needle, tokens) for needle in needles)]
    famous: tuple[int, ...] = ()
    if matches:
        famous = min(matches, key=lambda item: (-len(item[1]), item[0]))[2]
    group = position_group(position)
    positional = (*_POSITION_SHIRTS.get(_pos_token(position), ()), *_POSITION_SHIRTS.get(group, ()))
    keeper = group == "gk"
    ordered = (*famous, *positional, *range(1 if keeper else 2, 31), *range(31, 100))
    return tuple(number for number in dict.fromkeys(ordered) if keeper or number != 1)


def _member(plan: SquadPlan, pid: int) -> SquadMember | None:
    return next((member for member in plan.members if member.playerid == int(pid)), None)


def fix_shirt(plan: SquadPlan, playerid: int) -> tuple[SquadPlan, str]:
    """Re-plan one player's shirt against the plan's (fresh) shirt map.

    Prefers the number that was asked for if it is free now, then the
    player's familiar shirts, then a position number. Never gives #1 to an
    outfield player and never moves anybody else.
    """
    pid = int(playerid)
    member = _member(plan, pid)
    name = member.name if member else str(plan.shirt_names.get(pid) or pid)
    position = member.position if member else ""
    issue = plan.shirt_issue_for(pid)
    wanted = issue.number if issue else int(plan.jersey_numbers.get(pid) or 0)
    final = final_shirts(plan)
    current = final.get(pid, 0) if pid not in plan.jersey_numbers else _number(plan.shirt_book.get(pid))
    if member is not None and not current:
        current = _number(member.base.get("jerseynumber"))
    taken = {number for other, number in final.items() if other != pid and number}
    from .squad_session import position_group

    keeper = position_group(position) == "gk"
    candidates = [number for number in ((wanted,) if wanted else ()) + preferred_numbers(name, position)
                  if keeper or number != 1]
    chosen = next((number for number in candidates if number not in taken), None)
    issues = tuple(item for item in plan.shirt_issues if item.playerid != pid)
    numbers = dict(plan.jersey_numbers)
    if chosen is None:
        message = f"No free shirt number is left for {name}."
        kept = issue or ShirtIssue(pid, wanted or 0, message=message)
        return replace(plan, shirt_issues=issues + (replace(kept, message=message),)), message
    if chosen == current:
        numbers.pop(pid, None)
        note = f"{name} keeps #{chosen}."
    else:
        numbers[pid] = chosen
        note = f"{name} gets #{chosen}" + (f" instead of #{wanted}." if wanted and wanted != chosen else ".")
    return replace(plan, jersey_numbers=numbers, shirt_issues=issues), note


def swap_shirt(plan: SquadPlan, playerid: int) -> tuple[SquadPlan, str]:
    """The explicit choice: take the number and give its wearer this player's shirt."""
    pid = int(playerid)
    issue = plan.shirt_issue_for(pid)
    if issue is None:
        raise ValueError("This player has no shirt problem to swap.")
    final = final_shirts(plan)
    holders = [other for other, number in final.items() if number == issue.number and other != pid]
    if len(holders) != 1:
        raise ValueError(issue.message or f"#{issue.number} cannot be swapped.")
    holder = holders[0]
    member = _member(plan, pid)
    name = member.name if member else str(plan.shirt_names.get(pid) or pid)
    holder_name = str(plan.shirt_names.get(holder) or getattr(_member(plan, holder), "name", "") or holder)
    mine = final.get(pid, 0) if pid not in plan.jersey_numbers else _number(plan.shirt_book.get(pid))
    taken = {number for other, number in final.items() if other not in (pid, holder) and number}
    holder_member = _member(plan, holder)
    from .squad_session import position_group

    holder_keeper = holder_member is not None and position_group(holder_member.position) == "gk"
    if mine and mine != issue.number and mine not in taken and (mine != 1 or holder_keeper):
        given = mine
    else:
        given = next((number for number in range(99, 1, -1) if number not in taken and number != issue.number), None)
    if given is None:
        raise ValueError(f"No free shirt number is left for {holder_name}.")
    numbers = dict(plan.jersey_numbers)
    numbers[pid] = issue.number
    numbers[holder] = given
    note = f"{holder_name} moves from #{issue.number} to #{given} so {name} can wear #{issue.number}."
    return replace(
        plan,
        jersey_numbers=numbers,
        shirt_issues=tuple(item for item in plan.shirt_issues if item.playerid != pid),
        shirt_names={**dict(plan.shirt_names), holder: holder_name},
        warnings=tuple(plan.warnings) + (note,),
    ), note
