"""Pure reducers: ``(AppState, Event) -> AppState``. All mutation is here.

Every branch returns a NEW state; nothing is mutated in place. This is what
makes a background thread holding a stale ``AppState`` harmless — in v1 a
background search could write ``app._hits`` after the user had moved on, and
five parallel result lists each had their own invalidation bug.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..domain.outcome import ApplyOutcome
from . import events as E
from .state import (
    AppState,
    BridgeState,
    CatalogState,
    EditorState,
    JobsState,
    JobView,
    Prefs,
    DEFAULT_CORE_OPS,
    SearchState,
    SquadState,
    TargetState,
)

def _drop_squad_for_liveness(state: AppState, lv: Any) -> bool:
    """True only for a real context change, never for a missing-file blink.

    ABC → XYZ drops. A squad with no session id drops when the first live
    session appears (yesterday's unscoped cache). ABC → "" does not drop.
    A non-empty save_uid change drops even when the Lua session id is stable.
    """
    prev = state.bridge.liveness
    squad = state.squad
    new_sid = str(getattr(lv, "session_id", "") or "")
    old_sid = str(prev.session_id or "")
    if new_sid and squad.session_id and squad.session_id != new_sid:
        return True
    if new_sid and old_sid and old_sid != new_sid and squad.players:
        return True
    if new_sid and not old_sid and not squad.session_id and squad.players:
        return True
    new_uid = str(getattr(lv, "save_uid", "") or "")
    old_uid = str(getattr(prev, "save_uid", "") or "")
    if new_uid and squad.save_uid and new_uid != squad.save_uid:
        return True
    if new_uid and old_uid and new_uid != old_uid and squad.players:
        return True
    return False


def _mark_stale_for_hub_exit(state: AppState, lv: Any) -> bool:
    """Without a save id, leaving the career hub is the save-switch signal.

    Hosts that expose GetSaveUID publish save_uid instead, and a hub flicker
    must not invalidate that stronger identity.
    """
    prev = state.bridge.liveness
    if str(getattr(lv, "save_uid", "") or "") or str(prev.save_uid or ""):
        return False
    prev_hub = bool((prev.capabilities or {}).get("hub_ready"))
    new_hub = bool((getattr(lv, "capabilities", None) or {}).get("hub_ready"))
    return bool(prev_hub and not new_hub and state.squad.players and not state.squad.stale)


_HISTORY_MAX = 200


def reduce(state: AppState, event: E.Event) -> AppState:  # noqa: C901 — a flat dispatch
    match event:
        case E.LivenessChanged(liveness=lv):
            changes: dict[str, Any] = {"bridge": replace(state.bridge, liveness=lv)}
            if _drop_squad_for_liveness(state, lv):
                changes["squad"] = SquadState()
                if state.target.source == "squad":
                    changes["target"] = TargetState()
                    changes["editor"] = EditorState()
            elif _mark_stale_for_hub_exit(state, lv):
                changes["squad"] = replace(state.squad, stale=True)
            return state.with_(**changes)

        case E.StatusSet(text=t, tone=tone):
            return state.with_(
                status=t,
                status_tone=str(tone or "info"),
                status_id=state.status_id + 1,
            )

        # ---- target ----------------------------------------------------
        case E.TargetLocked(playerid=pid, name=n, source=s, teamid=tid):
            return state.with_(
                target=TargetState(playerid=pid, name=n, source=s, teamid=tid),
                # A target change must never retain another player's patch.
                editor=EditorState(),
            )

        case E.TargetCleared():
            return state.with_(target=TargetState(), editor=EditorState())

        # ---- search ----------------------------------------------------
        case E.SearchStarted(query=q, year=y, filters=f, generation=g):
            return state.with_(
                search=replace(
                    state.search,
                    query=q,
                    year=y,
                    filters=dict(f),
                    busy=True,
                    error="",
                    generation=g,
                )
            )

        case E.SearchSucceeded(results=rows, generation=g):
            # A late reply from a superseded search is dropped, not rendered.
            if g != state.search.generation:
                return state
            return state.with_(
                search=replace(
                    state.search,
                    results=tuple(rows),
                    selected=-1,
                    busy=False,
                    error="",
                )
            )

        case E.SearchFailed(error=err, generation=g):
            if g != state.search.generation:
                return state
            return state.with_(
                search=replace(state.search, busy=False, error=err, results=())
            )

        case E.ResultSelected(index=i):
            return state.with_(search=replace(state.search, selected=i))

        # ---- editor ----------------------------------------------------
        case E.EditorLoaded(base=b, categories=c):
            return state.with_(
                editor=EditorState(base=dict(b), dirty={}, categories=c)
            )

        case E.FieldEdited(field_name=name, value=v):
            dirty = dict(state.editor.dirty)
            if name in state.editor.base and state.editor.base[name] == v:
                dirty.pop(name, None)  # edited back to the original = not dirty
            else:
                dirty[name] = v
            source = state.editor.source
            source_label = state.editor.source_label
            if source and source != "manual":
                source, source_label = "mixed", "Mixed sources"
            elif not source:
                source, source_label = "manual", "Manual edit"
            return state.with_(
                editor=replace(
                    state.editor,
                    dirty=dirty,
                    source=source,
                    source_label=source_label,
                )
            )

        case E.DraftStaged(fields=fields, source=source, source_label=label, warnings=warnings):
            dirty = {}
            for name, value in fields.items():
                if name not in state.editor.base or state.editor.base[name] != value:
                    dirty[name] = value
            return state.with_(
                editor=replace(
                    state.editor,
                    dirty=dirty,
                    source=source,
                    source_label=label,
                    warnings=tuple(str(item) for item in warnings),
                )
            )

        case E.EditorReset():
            return state.with_(
                editor=replace(
                    state.editor,
                    dirty={},
                    source="",
                    source_label="",
                    warnings=(),
                )
            )

        # ---- jobs ------------------------------------------------------
        case E.JobSubmitted(job_id=jid, label=lbl, at=at):
            view = JobView(
                job_id=jid, label=lbl, outcome=ApplyOutcome.QUEUED, submitted=at
            )
            return state.with_(
                jobs=replace(state.jobs, active={**state.jobs.active, jid: view})
            )

        case E.JobProgressed(job_id=jid, result=res):
            prev = state.jobs.active.get(jid)
            view = JobView(
                job_id=jid,
                label=prev.label if prev else res.label,
                outcome=res.outcome,
                submitted=prev.submitted if prev else None,
                result=res,
            )
            return state.with_(
                jobs=replace(state.jobs, active={**state.jobs.active, jid: view})
            )

        case E.JobFinished(job_id=jid, result=res):
            prev = state.jobs.active.get(jid) or next(
                (old for old in state.jobs.history if old.job_id == jid), None
            )
            view = JobView(
                job_id=jid,
                label=prev.label if prev else res.label,
                outcome=res.outcome,
                submitted=prev.submitted if prev else None,
                result=res,
            )
            active = dict(state.jobs.active)
            active.pop(jid, None)
            history = (view,) + tuple(old for old in state.jobs.history if old.job_id != jid)[: _HISTORY_MAX - 1]
            return state.with_(jobs=JobsState(active=active, history=history))

        case E.JobHistoryCleared():
            return state.with_(jobs=JobsState(active=state.jobs.active, history=()))

        case E.JobQueueCleared():
            return state.with_(jobs=JobsState(active={}, history=state.jobs.history))

        # ---- squad -----------------------------------------------------
        case E.SquadSynced(
            save_uid=uid, session_id=sid, teamid=tid, players=ps, taken=t,
            free_agents=fa
        ):
            return state.with_(
                squad=SquadState(
                    save_uid=uid,
                    session_id=sid,
                    teamid=tid,
                    players=tuple(ps),
                    free_agents=tuple(fa),
                    taken=t,
                    stale=False,
                )
            )

        case E.SquadInvalidated():
            return state.with_(squad=replace(state.squad, stale=True))

        # ---- catalog / prefs -------------------------------------------
        case E.CatalogProbed(rows=r, years=y, fts=f, healthy=h, error=err):
            return state.with_(
                catalog=CatalogState(
                    rows=r, years=tuple(y), fts=f, healthy=h, last_error=err
                )
            )

        case E.PrefsLoaded(values=v):
            d = dict(v)
            raw_core_ops = d.get("core_ops", DEFAULT_CORE_OPS)
            if not isinstance(raw_core_ops, (list, tuple, set, frozenset)):
                raw_core_ops = DEFAULT_CORE_OPS
            return state.with_(
                prefs=Prefs(
                    growth_mirror=bool(d.get("growth_mirror", True)),
                    verify_writes=bool(d.get("verify_writes", True)),
                    theme=str(d.get("theme", "dark")),
                    confirm_destructive=bool(d.get("confirm_destructive", True)),
                    http_push=bool(d.get("http_push", False)),
                    core_ops=frozenset(str(x) for x in raw_core_ops),
                    values=d,
                )
            )

        case E.PrefChanged(key=k, value=v):
            if "api_key" in str(k).lower() or str(k).lower() in {"openai_key", "openai_api_key"}:
                return state
            values = {**dict(state.prefs.values), k: v}
            return reduce(state, E.PrefsLoaded(values=values))

    return state
