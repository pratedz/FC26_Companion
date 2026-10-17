"""Check & fix in-game names of verified signings (#8).

FC 26 player names come from the game database: the four name-dictionary ids
on the players row, or — when those are detached — the editedplayernames row.
Live Editor's SetGameLocString/AddCustomGameID do NOT control them (verified
by reverse engineering), so the fix re-applies add_to_team's own name step.

Flow: ``check_signed_names`` compares, for every verified signed player of
the CURRENT save still in the refreshed squad, the name the worker exported
(``SquadState.game_names``; editedplayernames first, else GetPlayerName) with
the name from the signing receipt. The user reviews the mismatches, then
``queue_names_sync`` sends ONE ``names.sync`` job for exactly those players.
The worker re-checks club + save, skips players FC already shows correctly,
and reads every write back (ingame/le_companion/ops/names_sync.lua).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ...domain.job import Grants, Job, JobValidationError, op_names_sync
from ...domain.outcome import JobResult
from ...domain.signing_identity import signed_player_identities
from ..services import Services
from .apply import submit_job

# Lua's %s%p: whitespace and ASCII punctuation. Non-ASCII letters (ć, é) stay.
_SEP = r"[\s!-/:-@\[-`{-~]+"
_DICT_IDS = ("firstnameid", "lastnameid", "commonnameid", "playerjerseynameid")


def _norm(value: Any) -> str:
    return re.sub(_SEP, "", str(value or "").lower())


def _tokens(value: Any) -> list[str]:
    return [token for token in re.split(_SEP, str(value or "").lower()) if token]


def visible_name_matches(actual: Any, expected: Any) -> bool:
    """Python twin of add_to_team.lua's visible_name_matches.

    Accepts "Iniesta" for "Andrés Iniesta" (a complete component sequence)
    but never "Ben" for "Benedict"; an empty expected never matches here.
    """
    if not str(expected or "").strip():
        return False
    if _norm(actual) == _norm(expected):
        return True
    if str(actual or "").strip().lower() == str(expected or "").strip().lower():
        return True
    got, want = _tokens(actual), _tokens(expected)
    if not want or len(want) > len(got):
        return False
    return any(got[start:start + len(want)] == want for start in range(len(got) - len(want) + 1))


@dataclass(frozen=True, slots=True)
class NameCheckRow:
    playerid: int
    signed_name: str
    game_name: str
    strategy: str
    entry: Mapping[str, Any]
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class NamesCheck:
    save_uid: str
    teamid: int
    mismatches: tuple[NameCheckRow, ...]
    matched: tuple[NameCheckRow, ...]
    absent: tuple[int, ...] = ()

    @property
    def checked(self) -> int:
        return len(self.mismatches) + len(self.matched)


def _identities(svc: Services, save_uid: str) -> dict[int, dict[str, Any]]:
    """Newest verified signing per Career player id in this save."""
    out: dict[int, dict[str, Any]] = {}
    if svc.db is None:
        return out
    for record in svc.db.job_records(save_uid=save_uid, op="add_to_team", limit=None):
        if not isinstance(record.job_json, Mapping) or not isinstance(record.result_json, Mapping):
            continue
        try:
            result = JobResult.from_wire(record.result_json)
            if result.job_id != record.job_id:
                continue
            resolved = signed_player_identities(record.job_json, result, save_uid=save_uid)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        for pid, identity in resolved.items():
            out.setdefault(pid, identity)  # job_records is newest first
    return out


def _sync_entry(pid: int, player: Mapping[str, Any]) -> dict[str, Any] | None:
    names = player.get("names") if isinstance(player.get("names"), Mapping) else {}
    clean = {key: str(names.get(key) or "").strip()
             for key in ("firstname", "surname", "commonname", "playerjerseyname")}
    clean = {key: value for key, value in clean.items() if value}
    if not clean:
        return None
    strategy = str(player.get("name_strategy") or "custom")
    entry: dict[str, Any] = {"playerid": pid, "names": clean, "name_strategy": strategy}
    if strategy == "native_ids":
        fields = player.get("fields") if isinstance(player.get("fields"), Mapping) else {}
        ids: dict[str, int] = {}
        for key in _DICT_IDS:
            try:
                ids[key] = int(fields[key])
            except (KeyError, TypeError, ValueError):
                return None
        if not any(value > 0 for value in ids.values()) or any(value < 0 for value in ids.values()):
            return None
        entry["native_name_ids"] = ids
    else:
        entry["name_strategy"] = "custom"
        entry["detach_name_dictionary"] = player.get("detach_name_dictionary") is not False
    return entry


def _matches(row_name: str, signed: str, entry: Mapping[str, Any]) -> bool:
    if visible_name_matches(row_name, signed):
        return True
    if entry.get("name_strategy") == "native_ids":
        names = entry.get("names") or {}
        return any(visible_name_matches(row_name, names.get(key))
                   for key in ("playerjerseyname", "surname"))
    return False


def check_signed_names(svc: Services) -> NamesCheck:
    """Diff in-game names against signing receipts. Reads only; queues nothing."""
    state = svc.store.snapshot()
    squad = state.squad
    if squad.stale or not squad.save_uid or not squad.teamid or not squad.players:
        raise JobValidationError("Refresh squad first, then check names.")
    live = state.bridge.liveness
    if live.session_id and squad.session_id and live.session_id != squad.session_id:
        raise JobValidationError("Squad data is from another Live Editor session. Refresh squad first.")
    game = {int(pid): str(name) for pid, name in squad.game_names}
    roster: set[int] = set()
    for player in squad.players:
        try:
            roster.add(int(player.get("playerid", player.get("id"))))
        except (TypeError, ValueError, OverflowError):
            continue
    mismatches: list[NameCheckRow] = []
    matched: list[NameCheckRow] = []
    absent: list[int] = []
    for pid, identity in sorted(_identities(svc, squad.save_uid).items()):
        if pid not in roster:
            absent.append(pid)
            continue
        player = identity.get("player") if isinstance(identity.get("player"), Mapping) else {}
        entry = _sync_entry(pid, player)
        if entry is None:
            continue
        signed = str(identity.get("name") or "")
        # Without an exported in-game name we cannot claim a mismatch.
        if pid not in game:
            continue
        row = NameCheckRow(pid, signed, game[pid], str(entry["name_strategy"]), entry,
                           str(identity.get("job_id") or ""))
        (matched if _matches(game[pid], signed, entry) else mismatches).append(row)
    return NamesCheck(squad.save_uid, int(squad.teamid), tuple(mismatches), tuple(matched), tuple(absent))


def review_lines(check: NamesCheck, *, limit: int = 15) -> list[str]:
    lines = [f"• {row.game_name or '(blank)'} → {row.signed_name}" for row in check.mismatches[:limit]]
    if len(check.mismatches) > limit:
        lines.append(f"• and {len(check.mismatches) - limit} more")
    return lines


def build_names_sync_job(svc: Services, check: NamesCheck) -> Job:
    if not check.mismatches:
        raise JobValidationError("Every signed player already shows the signed name.")
    state = svc.store.snapshot()
    if state.squad.save_uid != check.save_uid or state.squad.teamid != check.teamid:
        raise JobValidationError("The Career save changed since this check. Check names again.")
    if not state.prefs.experimental_add_to_team:
        raise JobValidationError("Sign support is off. Enable Sign support to fix signed names.")
    if not state.bridge.armed:
        raise JobValidationError("Start FC 26 through Live Editor and load Career Mode.")
    caps = state.bridge.liveness.capabilities or {}
    if not caps.get("names_sync"):
        raise JobValidationError("Restart Live Editor with the updated worker to fix names.")
    names = [row.signed_name for row in check.mismatches]
    label = ("Fix names: " + ", ".join(names))[:180]
    return Job(
        ops=(op_names_sync("names", teamid=check.teamid,
                           players=[row.entry for row in check.mismatches]),),
        label=label,
        origin="ui.club.names",
        grants=Grants(allow_add_to_team=True),
        requires={"save_uid": check.save_uid},
        # The worker waits on Career events for FC's resolver; one attempt is
        # enough because a re-run re-checks everything anyway.
        max_attempts=2,
        budget_ms=5000,
    )


def queue_names_sync(svc: Services, check: NamesCheck) -> str:
    return submit_job(svc, build_names_sync_job(svc, check))


def check_report(check: NamesCheck) -> dict[str, Any]:
    """Plain dict for the CLI."""
    def row(item: NameCheckRow) -> dict[str, Any]:
        return {"playerid": item.playerid, "signed_name": item.signed_name,
                "game_name": item.game_name, "strategy": item.strategy}

    return {
        "save_uid": check.save_uid,
        "teamid": check.teamid,
        "checked": check.checked,
        "mismatches": [row(item) for item in check.mismatches],
        "matched": [row(item) for item in check.matched],
        "signed_not_in_squad": list(check.absent),
    }


__all__ = [
    "NameCheckRow",
    "NamesCheck",
    "build_names_sync_job",
    "check_report",
    "check_signed_names",
    "queue_names_sync",
    "review_lines",
    "visible_name_matches",
]
