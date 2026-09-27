"""Stage build proposals without bypassing the normal Review/Apply path."""

from __future__ import annotations

from ...domain.builds import (
    BuildProposal,
    expand_overall_patch,
    is_ratings_only_patch,
)
from ...domain.player import PlayerValidationError, normalize_patch
from .. import events as E
from ..services import Services


def stage_proposal(svc: Services, proposed: BuildProposal) -> int:
    state = svc.store.snapshot()
    if not state.target.locked:
        raise PlayerValidationError("Select a player from your live squad first.")
    fields = normalize_patch(proposed.fields)
    warnings = list(proposed.warnings)
    # Defense in depth: never stage overall-only patches that leave in-stats
    # untouched (the Neymar "OVR 92 but old attributes" Career Mode bug).
    if is_ratings_only_patch(fields):
        from .player import editor_has_live_values

        if not editor_has_live_values(state):
            raise PlayerValidationError(
                "Read this player's live stats before changing overall."
            )
        current = dict(state.editor.merged())
        expanded = expand_overall_patch(current, fields)
        if is_ratings_only_patch(expanded):
            raise PlayerValidationError(
                "Live stats do not include attributes, so overall cannot be rebuilt."
            )
        if len(expanded) > len(fields):
            fields = expanded
            warnings.append(
                "Overall-only proposal was expanded to full attributes before review."
            )
    svc.store.dispatch(
        E.DraftStaged(
            fields=fields,
            source=proposed.source,
            source_label=proposed.label,
            warnings=tuple(dict.fromkeys(warnings)),
        )
    )
    return len(fields)
