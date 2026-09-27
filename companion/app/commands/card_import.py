"""Shared card staging flow for Player and Library surfaces."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from ...domain.card_import import (
    BasePlayerProfile,
    CardImportTopics,
    build_card_proposal,
)
from ...domain.player import PlayerValidationError
from ...platform.base_players import resolve_card_base_profile
from .. import events as E
from ..services import Services
from .builds import stage_proposal


@dataclass(frozen=True, slots=True)
class CardStageStart:
    target_playerid: int
    field_count: int = 0
    reload_job_id: str = ""
    warnings: tuple[str, ...] = ()


def prepare_card_proposal(
    svc: Services,
    card: Mapping[str, Any],
    topics: CardImportTopics,
    *,
    profile_resolver: Callable[[Mapping[str, Any]], BasePlayerProfile | None]
    = resolve_card_base_profile,
) -> tuple[int, Any]:
    state = svc.store.snapshot()
    if not state.target.locked or state.target.playerid is None:
        raise PlayerValidationError(
            "Choose a player from the verified live squad first."
        )
    if svc.catalog is None:
        raise PlayerValidationError("The local card Library is unavailable.")
    target_playerid = int(state.target.playerid)
    stats_plan = svc.catalog.prepare_cross_year_import(
        card, target_playerid=target_playerid
    )
    base_profile = (
        profile_resolver(card) if topics.name or topics.age or topics.face else None
    )
    proposed = build_card_proposal(
        card=card,
        target_playerid=target_playerid,
        topics=topics,
        stats_fields=stats_plan.fields,
        source=stats_plan.source,
        base_profile=base_profile,
        stats_warnings=tuple(stats_plan.warnings),
    )
    return target_playerid, proposed


def stage_card(
    svc: Services,
    card: Mapping[str, Any],
    topics: CardImportTopics | None = None,
    *,
    read_live_first: bool = True,
    on_staged: Callable[[CardStageStart], None] | None = None,
    profile_resolver: Callable[[Mapping[str, Any]], BasePlayerProfile | None]
    = resolve_card_base_profile,
) -> CardStageStart:
    """Stage a card now, with an optional exact live-value read first.

    Fast staging is deliberate: a card is still target-bound and review-only,
    but its comparison can only show fields the squad already supplied.  The
    caller must opt into this path, so no UI can mistake partial squad data for
    a full in-game snapshot.
    """
    selected_topics = topics or CardImportTopics()
    target_playerid, proposed = prepare_card_proposal(
        svc, card, selected_topics, profile_resolver=profile_resolver
    )
    initial = svc.store.snapshot()
    staged: CardStageStart | None = None

    def stage_now() -> CardStageStart:
        nonlocal staged
        latest = svc.store.snapshot()
        if latest.target.playerid != target_playerid:
            raise PlayerValidationError(
                "The selected player changed while the card was loading. Nothing was staged."
            )
        count = stage_proposal(svc, proposed)
        result = CardStageStart(
            target_playerid=target_playerid,
            field_count=count,
            warnings=tuple(proposed.warnings),
        )
        staged = result
        warning = f" {proposed.warnings[0]}" if proposed.warnings else ""
        svc.store.dispatch(
            E.StatusSet(
                f"Staged {count} card field(s) on the selected player. "
                f"Review before Apply.{warning}"
            )
        )
        if on_staged is not None:
            on_staged(result)
        return result

    from .player import editor_has_live_values, ensure_live_player

    if (
        read_live_first
        and not editor_has_live_values(initial)
        and not initial.editor.has_changes
        and getattr(svc, "transport", None) is not None
    ):
        # The compact squad summary is not a real player read.  Load the exact
        # current FC 26 values first so Review shows a trustworthy before →
        # after comparison.  A Card Library prefetch already in flight is
        # reused rather than queued again; Review opens when that read lands.
        def loaded(_row: Mapping[str, Any]) -> None:
            try:
                stage_now()
            except Exception as exc:  # noqa: BLE001
                svc.store.dispatch(E.StatusSet(str(exc)))

        job_id = ensure_live_player(svc, on_loaded=loaded)
        if staged is not None:  # Inline transport completed before return.
            return staged
        if job_id:
            svc.store.dispatch(
                E.StatusSet(
                    f"Reading the current player from FC 26 ({job_id[:8]}…). "
                    "The selected card will stage and open Review automatically."
                )
            )
            return CardStageStart(target_playerid=target_playerid, reload_job_id=job_id)
    if not editor_has_live_values(initial):
        # Existing staged edits belong to this target. Do not erase them just
        # to re-read; replacing the draft is safe but Review must disclose the
        # values it cannot compare.
        proposed = replace(
            proposed,
            warnings=tuple(
                dict.fromkeys(
                    (*proposed.warnings,
                     "Current live values were not fully loaded; unknown before-values are marked in Review."),
                )
            ),
        )
    return stage_now()


__all__ = [
    "CardImportTopics", "CardStageStart", "prepare_card_proposal", "stage_card",
]
