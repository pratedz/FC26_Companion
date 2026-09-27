"""Experimental Phase 4 team assignment.

The command refuses to guess a dummy. It uses only free-agent ids exported
from the currently loaded save and leaves the operation disabled unless both
the app preference and the live worker capability say it is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
import json

from ... import SIGN_CORE_MIN, core_at_least
from ...domain.card_import import forced_birthdate_for_age
from ...domain.job import Grants, Job, JobValidationError, is_sign_job_label, op_add_to_team
from ...platform.base_players import resolve_card_base_profile
from ..services import Services
from .apply import submit_job

# Squad free-agent lists are re-checked live by Lua. A short display TTL still
# applies in Club, but Add Player must not bounce the user into a refresh loop
# while they search Library cards and build a draft.
TEAM_ADD_SQUAD_TTL_SECONDS = 900.0
# Extra live free-agent ids on the same job, never shared with another signing.
_DUMMY_BACKUP_LIMIT = 3
_NATIVE_NAME_ID_FIELDS = (
    "firstnameid",
    "lastnameid",
    "commonnameid",
    "playerjerseynameid",
)


def _as_int(value: Any) -> int | None:
    """Read a catalog number without accepting booleans or empty values."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class TeamCardPlan:
    fields: Mapping[str, Any]
    names: Mapping[str, str]
    face: Mapping[str, Any]
    # A real FC26 base-player row supplies name dictionary IDs which Career
    # resolves itself.  This is materially safer than detaching a dummy name
    # and hoping a custom row wins the next UI cache refresh.
    name_strategy: str = "custom"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TeamAddEntry:
    """One reviewed card and its exact live-verified free-agent target."""

    card: Mapping[str, Any]
    plan: TeamCardPlan
    dummy_id: int
    dummy_name: str = "Free agent"
    dummy_overall: int = 0

    @property
    def name(self) -> str:
        return str(self.card.get("name") or self.card.get("playername") or "Player")

    @property
    def face_verified(self) -> bool:
        # ``prepare_card_for_team`` only fills this map from a verified FC26
        # base profile. A non-empty map is therefore an honest real-face check.
        return bool(self.plan.face)

@dataclass(frozen=True, slots=True)
class TeamAddPreview:
    """Preflight result shown before a player add writes any queue files."""

    entries: tuple[TeamAddEntry, ...]
    available_slots: int
    forced_age: int | None = None

    @property
    def selected_count(self) -> int:
        return len(self.entries)

    @property
    def face_verified_count(self) -> int:
        return sum(1 for entry in self.entries if entry.face_verified)

    @property
    def warnings(self) -> tuple[str, ...]:
        messages: list[str] = []
        for entry in self.entries:
            messages.extend(f"{entry.name}: {warning}" for warning in entry.plan.warnings)
        return tuple(dict.fromkeys(messages))


def prepare_card_for_team(
    svc: Services,
    card: Mapping[str, Any],
    *,
    forced_age: int | None = None,
) -> TeamCardPlan:
    """Build a complete, validated card clone for an existing dummy row."""
    if svc.catalog is None:
        raise JobValidationError("The local card Library is unavailable.")
    stats = svc.catalog.prepare_cross_year_import(card, target_playerid=1)
    fields = dict(stats.fields)
    # Card-stat import must never smuggle partial/zero dictionary IDs into a
    # signing. Native identity is selected below only after all four IDs pass
    # verification; custom fallback keeps the dummy IDs until its guarded
    # post-transfer activation.
    for field in _NATIVE_NAME_ID_FIELDS:
        fields.pop(field, None)
    fields.pop("usercaneditname", None)
    warnings = list(stats.warnings)
    base = resolve_card_base_profile(card)
    name_strategy = "custom"
    if base is not None:
        for field in ("nationality", "gender"):
            value = base.identity.get(field)
            if value not in (None, ""):
                fields[field] = int(value)
        if forced_age is None:
            birthdate = base.identity.get("birthdate")
            if birthdate not in (None, "") and int(birthdate) > 0:
                fields["birthdate"] = int(birthdate)
        native_name_ids = _verified_native_name_ids(base.identity)
        if native_name_ids is not None:
            fields.update(native_name_ids)
            # Native dictionary IDs are the FC-visible identity.  Do not
            # leave the dummy's custom-name flag set when using them.
            fields["usercaneditname"] = 0
            name_strategy = "native_ids"
    if forced_age is not None:
        fields["birthdate"] = forced_birthdate_for_age(forced_age)
    elif "birthdate" not in fields:
        warnings.append("Verified birthdate unavailable; age was not changed.")
    # A card source may omit potential altogether (notably some historic and
    # special-card variants).  Leaving it out would retain the low-rated free
    # agent dummy's potential, producing impossible rows such as 90 OVR / 41
    # POT.  FC expects potential to be at least the current overall, so make
    # that conservative floor explicit in every Add Player payload.
    overall = _as_int(fields.get("overallrating"))
    potential = _as_int(fields.get("potential"))
    if overall is not None and overall > 0:
        safe_potential = max(overall, potential or overall)
        if potential != safe_potential:
            fields["potential"] = safe_potential
            if potential is None:
                warnings.append(
                    f"Card potential unavailable; set potential to its {overall} overall."
                )
            else:
                warnings.append(
                    f"Card potential was below its {overall} overall; raised it to {safe_potential}."
                )
    # A newly added card must not inherit the dummy's retirement flag.
    fields["isretiring"] = 0
    face: dict[str, Any] = {}
    if base is not None and base.face_verified:
        face.update(base.appearance)
    else:
        warnings.append("Verified FC26 real face unavailable; the dummy face is retained.")
    return TeamCardPlan(
        fields=fields,
        names=_names(card),
        face=face,
        name_strategy=name_strategy,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _player_payload(plan: TeamCardPlan) -> dict[str, Any]:
    return {
        "fields": dict(plan.fields),
        "names": dict(plan.names),
        "face": dict(plan.face),
        # Native card IDs stay attached; only cards with no fully verified
        # FC26 IDs use the guarded custom-name fallback.
        "name_strategy": plan.name_strategy,
        "detach_name_dictionary": plan.name_strategy == "custom",
    }


def _dummy_filter_for(dummy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "max_playerid": 459999,
        "max_overall": 75,
        "expected_overallrating": int(dummy["overallrating"]),
        "must_exist": True,
        "must_be_free_agent": True,
        "must_not_be_retiring": True,
        "must_not_be_presigned_or_loaned": True,
        "must_be_unused_in_save": True,
    }


def _resolved_dummy_pool(
    requested: Sequence[int],
    by_id: Mapping[int, Mapping[str, Any]],
) -> list[int]:
    pool: list[int] = []
    seen: set[int] = set()
    for pid in requested:
        if pid in seen or pid not in by_id:
            continue
        pool.append(int(pid))
        seen.add(int(pid))
        if len(pool) >= 1 + _DUMMY_BACKUP_LIMIT:
            break
    if not pool:
        raise JobValidationError(
            "The selected free-agent dummy is no longer verified in this live Career save. "
            "Refresh squad and review again."
        )
    return pool


def _member_body(
    *,
    pool: Sequence[int],
    dummy: Mapping[str, Any],
    plan: TeamCardPlan,
) -> dict[str, Any]:
    return {
        "dummy_pool": [int(pid) for pid in pool],
        "dummy_filter": _dummy_filter_for(dummy),
        "player": _player_payload(plan),
        "contract": {"transfersum": 0, "wage": 5000, "months": 60},
    }


def _sign_job_label(names: Sequence[str]) -> str:
    cleaned = [str(name).strip() or "Player" for name in names]
    if len(cleaned) == 1:
        return f"Sign: {cleaned[0]}"
    joined = ", ".join(cleaned)
    prefix = f"Sign {len(cleaned)}: "
    budget = 180 - len(prefix)
    if len(joined) <= budget:
        return prefix + joined
    return prefix + joined[: max(0, budget - 1)] + "…"


def _add_job_envelope(
    state: Any,
    op: Any,
    label: str,
) -> Job:
    requires: dict[str, Any] = {"core_version": SIGN_CORE_MIN}
    if state.squad.save_uid:
        requires["save_uid"] = state.squad.save_uid
    return Job(
        ops=(op,),
        label=label,
        origin="ui.add_player.batch",
        grants=Grants(
            allow_add_to_team=True,
        ),
        requires=requires,
        # Batch Sign still has to finish inside the core's 5000ms hard maximum.
        budget_ms=5000,
        max_attempts=3,
    )


def build_add_card_job(
    svc: Services,
    card: Mapping[str, Any],
    *,
    forced_age: int | None = None,
    dummy_pool: Sequence[int] | None = None,
    prepared_plan: TeamCardPlan | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
) -> Job:
    state = _validate_team_add(svc)
    free_agents = _verified_free_agent_rows(svc)
    by_id = {int(row["playerid"]): row for row in free_agents}
    requested = tuple(int(pid) for pid in (dummy_pool or ()))
    if not requested:
        requested = tuple(
            int(row["playerid"]) for row in free_agents[: 1 + _DUMMY_BACKUP_LIMIT]
        )
    if not requested:
        raise JobValidationError("Each player must use one exact, live-verified free-agent dummy.")
    dummy = by_id.get(requested[0])
    if dummy is None:
        raise JobValidationError(
            "The selected free-agent dummy is no longer verified in this live Career save. "
            "Refresh squad and review again."
        )
    pool = _resolved_dummy_pool(requested, by_id)
    dummy = by_id[pool[0]]
    plan = prepared_plan or prepare_card_for_team(svc, card, forced_age=forced_age)
    member = _member_body(pool=pool, dummy=dummy, plan=plan)
    op = op_add_to_team(
        "add",
        teamid=int(state.squad.teamid),
        dummy_pool=tuple(pool),
        dummy_filter=member["dummy_filter"],
        player=member["player"],
        contract=member["contract"],
        batch=(member,),
        verify={"name": True, "team": True, "fields": True},
        experimental=True,
    )
    display_name = str(card.get("name") or card.get("playername") or "Player")
    if batch_index is not None and batch_total is not None and batch_total > 1:
        label = f"Sign {batch_index}/{batch_total}: {display_name}"
    else:
        label = f"Sign: {display_name}"
    return _add_job_envelope(state, op, label)


def add_card_to_team(
    svc: Services,
    card: Mapping[str, Any],
    *,
    forced_age: int | None = None,
) -> str:
    _preview, job_ids = add_cards_to_team(svc, (card,), forced_age=forced_age)
    return job_ids[0]


def preview_cards_for_team(
    svc: Services,
    cards: Sequence[Mapping[str, Any]],
    *,
    forced_age: int | None = None,
) -> TeamAddPreview:
    """Validate a whole draft and reserve a distinct safe slot per card.

    No queue files are created here. The UI can show exact face/identity
    warnings and the number of low-rated free-agent slots that will be used
    before the player confirms the batch.
    """
    _validate_team_add(svc)
    free_agents = _verified_free_agent_rows(svc)
    selected = tuple(dict(card) for card in cards if isinstance(card, Mapping))
    if not selected:
        raise JobValidationError("Select at least one Library card first.")
    duplicates = _duplicate_people(selected)
    if duplicates:
        raise JobValidationError(
            "Choose one card per player. Duplicate selection: " + ", ".join(duplicates)
        )
    if not free_agents:
        raise JobValidationError(
            "No verified free-agent player was found in this loaded Career save. "
            "This version will not create or overwrite a club player."
        )
    if len(selected) > len(free_agents):
        raise JobValidationError(
            f"This draft has {len(selected)} players, but only {len(free_agents)} live-verified "
            "free-agent dummy slot(s) are available. Refresh after completed adds or select fewer players."
        )
    entries = tuple(
        TeamAddEntry(
            card=card,
            plan=prepare_card_for_team(svc, card, forced_age=forced_age),
            dummy_id=int(free_agents[index]["playerid"]),
            dummy_name=str(free_agents[index].get("name") or "Free agent"),
            dummy_overall=int(free_agents[index].get("overallrating") or 0),
        )
        for index, card in enumerate(selected)
    )
    return TeamAddPreview(
        entries=entries,
        available_slots=len(free_agents),
        forced_age=forced_age,
    )


def build_add_card_jobs(
    svc: Services,
    cards: Sequence[Mapping[str, Any]],
    *,
    forced_age: int | None = None,
) -> tuple[TeamAddPreview, tuple[Job, ...]]:
    """Build one resumable Sign job for the reviewed bag."""
    preview = preview_cards_for_team(svc, cards, forced_age=forced_age)
    return preview, _jobs_from_preview(svc, preview)


def add_cards_to_team(
    svc: Services,
    cards: Sequence[Mapping[str, Any]],
    *,
    forced_age: int | None = None,
) -> tuple[TeamAddPreview, tuple[str, ...]]:
    """Submit a reviewed batch as one Sign job with disjoint dummy pools."""
    preview, jobs = build_add_card_jobs(svc, cards, forced_age=forced_age)
    return preview, tuple(submit_job(svc, job) for job in jobs)


def add_reviewed_cards_to_team(
    svc: Services,
    reviewed: TeamAddPreview,
) -> tuple[str, ...]:
    """Queue exactly the targets shown in a review, or require a new review.

    The live free-agent pool can change between opening the review sheet and
    pressing Confirm.  Re-previewing without comparison would be safe for the
    save, but dishonest to the manager: they might have approved another
    dummy.  A changed target, face result, or warning is therefore a hard
    re-review boundary.

    A previous signing may still be verifying in FC. Queue the next bag on
    different reserved dummies; Lua still will not overlap pick_dummy with a
    live TransferPlayer.
    """
    cards = tuple(entry.card for entry in reviewed.entries)
    current = preview_cards_for_team(svc, cards, forced_age=reviewed.forced_age)
    if _preview_signature(current) != _preview_signature(reviewed):
        raise JobValidationError(
            "Safe targets changed since this draft was reviewed. Review the draft again before queueing it."
        )
    return tuple(submit_job(svc, job) for job in _jobs_from_preview(svc, reviewed))


def _exclusive_dummy_pools(
    primaries: Sequence[int],
    available: Sequence[int],
) -> tuple[tuple[int, ...], ...]:
    """One reviewed dummy per bag member, plus leftovers striped as exclusive backups."""
    ordered = tuple(int(pid) for pid in primaries)
    taken = set(ordered)
    rest = [int(pid) for pid in available if int(pid) not in taken]
    count = len(ordered)
    pools: list[tuple[int, ...]] = []
    for index, primary in enumerate(ordered):
        extras = tuple(rest[index::count][:_DUMMY_BACKUP_LIMIT] if count else ())
        pools.append((primary,) + extras)
    return tuple(pools)


def _jobs_from_preview(svc: Services, preview: TeamAddPreview) -> tuple[Job, ...]:
    """Queue one Sign job for the reviewed bag with disjoint dummy pools."""
    state = _validate_team_add(svc)
    free_agents = _verified_free_agent_rows(svc)
    by_id = {int(row["playerid"]): row for row in free_agents}
    free_ids = [int(row["playerid"]) for row in free_agents]
    pools = _exclusive_dummy_pools(
        tuple(entry.dummy_id for entry in preview.entries),
        free_ids,
    )
    members: list[dict[str, Any]] = []
    names: list[str] = []
    for index, entry in enumerate(preview.entries):
        pool = _resolved_dummy_pool(pools[index], by_id)
        dummy = by_id[pool[0]]
        members.append(_member_body(pool=pool, dummy=dummy, plan=entry.plan))
        names.append(entry.name)
    if not members:
        raise JobValidationError("Select at least one Library card first.")
    first = members[0]
    op = op_add_to_team(
        "add",
        teamid=int(state.squad.teamid),
        dummy_pool=tuple(first["dummy_pool"]) if len(members) == 1 else None,
        dummy_filter=first["dummy_filter"] if len(members) == 1 else None,
        player=first["player"] if len(members) == 1 else None,
        contract=first["contract"] if len(members) == 1 else None,
        batch=tuple(members),
        verify={"name": True, "team": True, "fields": True},
        experimental=True,
    )
    return (_add_job_envelope(state, op, _sign_job_label(names)),)


def _preview_signature(preview: TeamAddPreview) -> tuple[tuple[Any, ...], ...]:
    """The user-visible safety facts that must not change after review."""
    return tuple(
        (
            entry.dummy_id,
            entry.dummy_name,
            entry.dummy_overall,
            entry.face_verified,
            tuple(entry.plan.warnings),
        )
        for entry in preview.entries
    )


def active_team_add_jobs(svc: Services) -> tuple[str, ...]:
    """In-flight Add Player jobs only. Terminal leftovers in ``active`` do not lock Sign."""
    return tuple(
        view.job_id
        for view in svc.store.snapshot().jobs.active.values()
        if is_sign_job_label(str(view.label or "")) and not view.done
    )


def remaining_sign_labels(svc: Services) -> tuple[str, ...]:
    """In-flight Sign job labels, for the Copy Lua drain comment."""
    labels: list[str] = []
    active = svc.store.snapshot().jobs.active
    for job_id in active_team_add_jobs(svc):
        view = active.get(job_id)
        raw = str(getattr(view, "label", "") or job_id)
        labels.append(raw)
    return tuple(labels)


def sign_force_drain_text(svc: Services) -> str:
    """Lua Engine poke plus remaining Sign names. Does not embed job JSON."""
    labels = remaining_sign_labels(svc)
    comment = "-- Remaining: " + ", ".join(labels) if labels else "-- Remaining: none"
    try:
        snippet = str(svc.transport.force_drain_snippet())
    except Exception:
        snippet = (
            "if type(_G.LECompanionV2_ForceDrain) == 'function' then "
            "_G.LECompanionV2_ForceDrain() "
            'elseif Log then Log("[LEC] core not armed") end'
        )
    return comment + "\n" + snippet


def _dummy_ids_from_mapping(payload: Mapping[str, Any]) -> set[int]:
    out: set[int] = set()
    for pid in payload.get("dummy_pool") or ():
        try:
            out.add(int(pid))
        except (TypeError, ValueError):
            continue
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    for key in ("dummy_id", "playerid"):
        raw = data.get(key)
        if raw in (None, "", "—"):
            continue
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    for member in payload.get("batch") or ():
        if isinstance(member, Mapping):
            out |= _dummy_ids_from_mapping(member)
    return out


def _dummy_ids_from_payload(payload: Mapping[str, Any]) -> set[int]:
    out: set[int] = set()
    ops = payload.get("ops") or ()
    if ops:
        for op in ops:
            if isinstance(op, Mapping):
                out |= _dummy_ids_from_mapping(op)
        return out
    return _dummy_ids_from_mapping(payload)


def _in_flight_dummy_ids(svc: Services) -> set[int]:
    """Free-agent slots already reserved by a queued or verifying signing."""
    out: set[int] = set()
    for job_id in active_team_add_jobs(svc):
        view = svc.store.snapshot().jobs.active.get(job_id)
        result = getattr(view, "result", None) if view is not None else None
        if result is not None:
            payload = {
                "ops": [
                    {
                        "op": getattr(op, "op", ""),
                        "dummy_pool": (getattr(op, "data", {}) or {}).get("dummy_pool") or (),
                        "data": getattr(op, "data", {}) or {},
                    }
                    for op in getattr(result, "ops", ())
                ]
            }
            out |= _dummy_ids_from_payload(payload)
        queue = getattr(getattr(svc, "paths", None), "queue", None)
        if queue is not None:
            name = f"{job_id}.json"
            for folder in ("jobs", "claimed"):
                path = Path(queue) / folder / name
                if not path.is_file():
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(raw, Mapping):
                    out |= _dummy_ids_from_payload(raw)
        transport = getattr(svc, "transport", None)
        for job in getattr(transport, "submitted", ()) or ():
            if str(getattr(job, "job_id", "") or "") != job_id:
                continue
            for op in getattr(job, "ops", ()) or ():
                body = getattr(op, "body", {}) or {}
                if isinstance(body, Mapping):
                    out |= _dummy_ids_from_payload(body)
                    out |= _dummy_ids_from_payload({"ops": [body]})
    return out


def confirm_team_add(
    confirmed: bool = False,
    player_count: int = 1,
    *,
    ask: Any | None = None,
) -> bool:
    """OK/Cancel confirmation gate shared by the review UI and tests.

    ``ask`` is an optional zero-arg callable (e.g. a message box) used by the
    desktop UI. When omitted, ``confirmed`` is the pure decision (for tests).
    """
    _ = max(1, int(player_count or 1))
    if ask is not None:
        try:
            return bool(ask())
        except Exception:
            return False
    return bool(confirmed)


def _validate_team_add(svc: Services) -> Any:
    """Centralize the safety gates shared by preview, single add and batch add."""
    state = svc.store.snapshot()
    if not state.prefs.experimental_add_to_team:
        raise JobValidationError("Sign support is off. Enable Sign support, restart Live Editor, then refresh squad.")
    if not state.bridge.armed:
        raise JobValidationError("Start FC 26 through Live Editor and load Career Mode.")
    if not bool(state.bridge.liveness.capabilities.get("add_to_team")):
        raise JobValidationError("Restart Live Editor after enabling Sign support.")
    running_core = str(state.bridge.liveness.core_version or "")
    if running_core and not core_at_least(running_core, SIGN_CORE_MIN):
        raise JobValidationError(
            f"Live Editor is running core {running_core}; Sign needs {SIGN_CORE_MIN} or newer. "
            "Restart Live Editor, then refresh squad."
        )
    if not state.squad.teamid:
        raise JobValidationError("Read your current Career squad before adding a player.")
    if _squad_unusable_for_team_add(svc):
        raise JobValidationError(
            "Your squad view is no longer current. Refresh squad in this Live Editor session before adding players."
        )
    return state


def _squad_unusable_for_team_add(svc: Services) -> bool:
    """True when Add Player cannot trust the cached squad/free-agent probe."""
    from .squad import is_stale

    state = svc.store.snapshot()
    sq = state.squad
    live = state.bridge.liveness
    if live.session_id and sq.session_id and sq.session_id != live.session_id:
        return True
    if sq.stale or sq.taken is None or not sq.teamid:
        return True
    # is_stale() uses the short Club TTL (2 minutes). Add Player drafts take
    # longer, and Lua re-validates every free-agent slot before writing.
    if is_stale(svc):
        age = sq.age_seconds(svc.clock.now())
        if age is None or age > TEAM_ADD_SQUAD_TTL_SECONDS:
            return True
    return False


def _free_agent_pool(svc: Services) -> list[int]:
    """IDs only, kept for small UI counters and backward-compatible callers."""
    return [int(row["playerid"]) for row in _verified_free_agent_rows(svc)]


def _consumed_free_agent_ids(svc: Services) -> set[int]:
    """Playerids already reserved by a prior Add Player run in this save."""
    state = svc.store.snapshot()
    save_uid = str(state.squad.save_uid or "").strip()
    if not save_uid:
        return set()
    try:
        key = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in save_uid)
        path = svc.paths.queue / "state" / f"consumed_free_agent_dummies_{key}.json"
        if not path.is_file():
            return set()
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, Mapping):
        return set()
    if str(payload.get("save_uid") or "") != save_uid:
        return set()
    raw = payload.get("playerids")
    if not isinstance(raw, Mapping):
        return set()
    out: set[int] = set()
    for key, value in raw.items():
        if value in (None, False, 0, "0"):
            continue
        try:
            out.add(int(key))
        except (TypeError, ValueError):
            continue
    return out


def _verified_free_agent_rows(svc: Services) -> list[dict[str, Any]]:
    """Current-session, low-rated free agents verified by the live worker.

    The desktop cache is only a convenience for preview.  Lua repeats the
    membership, loan/presign, rating and per-save-consumption checks directly
    before it writes anything.
    """
    state = svc.store.snapshot()
    from ...domain.safe_dummy_candidates import REAL_FREE_AGENT_CANDIDATES

    local_names = {candidate.playerid: candidate.name for candidate in REAL_FREE_AGENT_CANDIDATES}
    candidates = state.squad.free_agents
    protected: set[int] = set()
    for player in state.squad.players:
        try:
            protected.add(int(player.get("playerid") or player.get("id")))
        except (TypeError, ValueError):
            pass
    consumed = _consumed_free_agent_ids(svc)
    reserved = _in_flight_dummy_ids(svc)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for row in candidates if isinstance(candidates, (list, tuple)) else ():
        if not isinstance(row, Mapping):
            continue
        try:
            pid = int(row.get("playerid") or row.get("id"))
            ovr = int(row.get("overallrating") or row.get("ovr") or 0)
        except (TypeError, ValueError):
            continue
        source = str(row.get("source") or "")
        # The worker emits only this bounded, live-probed pool.  Reject old
        # cached/unlabelled entries rather than ever guessing a dummy.
        if (
            0 < pid <= 459999
            and ovr <= 75
            and pid not in protected
            and pid not in consumed
            and pid not in reserved
            and source in {"free_agent_template", "free_agent_helper"}
        ):
            normalized = dict(row)
            normalized["playerid"] = pid
            normalized["overallrating"] = ovr
            if not normalized.get("name") and pid in local_names:
                normalized["name"] = local_names[pid]
            ranked.append((ovr, pid, normalized))
    return [row for _ovr, _pid, row in sorted(ranked)[:20]]


def _duplicate_people(cards: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for card in cards:
        identity = str(
            card.get("person_id")
            or card.get("base_player_id")
            or card.get("playerid")
            or card.get("_card_key")
            or card.get("obs_id")
            or card.get("name")
            or card.get("playername")
            or ""
        )
        if identity in seen:
            duplicates.append(str(card.get("name") or card.get("playername") or "Player"))
        seen.add(identity)
    return tuple(dict.fromkeys(duplicates))


def _names(card: Mapping[str, Any]) -> dict[str, str]:
    """Build the editedplayernames map for Add Player jobs.

    Career Squad Hub displays ``commonname`` for detached custom names.
    An empty commonname is the blank-label failure mode, so every path ends
    with a non-empty common/display name (including accented library cards).
    """
    display = str(card.get("name") or card.get("playername") or "").strip()
    first = str(card.get("firstname") or card.get("firstName") or "").strip()
    surname = str(
        card.get("surname") or card.get("lastname") or card.get("lastName") or ""
    ).strip()
    if not first and not surname:
        first, _, surname = display.partition(" ")
        first, surname = first.strip(), surname.strip()
    if not surname:
        surname = first
        first = ""
    jersey = str(
        card.get("playerjerseyname") or card.get("nickname") or surname or display
    ).strip()
    common = str(card.get("commonname") or card.get("commonName") or "").strip()
    if not common:
        if first and surname:
            common = f"{first} {surname}".strip()
        else:
            common = display or surname or first
    if not common:
        common = "Player"
    if not jersey:
        jersey = common
    return {
        "firstname": first,
        "surname": surname or common,
        "playerjerseyname": jersey,
        "commonname": common,
    }


def _verified_native_name_ids(identity: Mapping[str, Any]) -> dict[str, int] | None:
    """Return a complete authoritative FC dictionary identity, or ``None``.

    FC base rows legitimately use zero for an optional component (commonly a
    jersey-name ID).  It is safe only as part of a *complete source row* with
    at least one real ID; missing/partial rows still fall back to the guarded
    custom-name path rather than mixing dummy and card identity.
    """
    values: dict[str, int] = {}
    for field in _NATIVE_NAME_ID_FIELDS:
        value = _as_int(identity.get(field))
        if value is None or value < 0:
            return None
        values[field] = value
    return values if any(value > 0 for value in values.values()) else None


__all__ = [
    "TeamAddEntry",
    "TeamAddPreview",
    "TeamCardPlan",
    "active_team_add_jobs",
    "add_cards_to_team",
    "add_reviewed_cards_to_team",
    "add_card_to_team",
    "build_add_card_jobs",
    "build_add_card_job",
    "confirm_team_add",
    "preview_cards_for_team",
    "prepare_card_for_team",
    "remaining_sign_labels",
    "sign_force_drain_text",
]
