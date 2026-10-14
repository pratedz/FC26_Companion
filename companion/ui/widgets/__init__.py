"""The v2 widget kit — §6.6's component set, and the only place widgets exist.

Adding a component requires adding it to §6.6 first. That rule is what stops the
v1 outcome, where the same capsule/badge/pill was reimplemented five times with
five different hard-coded washes because nobody could find the existing one.

Everything here is import-safe without customtkinter and without a display: the
widget factories raise :class:`primitives.CtkUnavailable` when called, but the
pure models (:class:`table.TableModel`, :func:`diff.build_diff`,
:class:`ovr.OvrDelta`) import and run headlessly so ``tests/v2`` can cover the
logic that actually decides what a user is told.
"""

from __future__ import annotations

from .diff import (
    DiffGroup,
    DiffLine,
    DiffModel,
    build_diff,
    diff_sheet,
    diff_view,
    pairs_from,
    summarise,
)
from .ovr import OvrDelta, ovr_chip, ovr_delta, ovr_delta_chips, ovr_text
from .primitives import (
    ARROW,
    CTK_AVAILABLE,
    EM_DASH,
    CtkUnavailable,
    badge,
    button,
    card,
    debounce,
    display_value,
    divider,
    elevation,
    hairline,
    display_font,
    ensure_ctk,
    entry,
    icon_button,
    key_value_row,
    muted_label,
    panel,
    pill,
    num_font,
    provenance_color,
    restyle_button,
    search_box,
    section_header,
    section_title,
    set_disabled,
    shade,
    stat_chip,
    text_label,
    tooltip,
    update_pill,
)
from .states import (
    DeadEndError,
    ErrorCopy,
    empty_state,
    error_state,
    loading_row,
    require_action,
    skeleton,
    skeleton_block,
    skeleton_table,
)
from .table import Column, DataGrid, TableModel
from .toast import ToastHost

__all__ = [
    # primitives
    "ARROW", "CTK_AVAILABLE", "EM_DASH", "CtkUnavailable", "badge", "button",
    "card", "debounce", "display_font", "display_value", "divider", "elevation", "ensure_ctk",
    "entry", "hairline", "icon_button", "key_value_row", "muted_label", "num_font", "panel",
    "pill", "provenance_color", "restyle_button", "search_box", "section_header",
    "section_title", "set_disabled", "shade", "stat_chip",
    "text_label", "tooltip", "update_pill",
    # ovr
    "OvrDelta", "ovr_chip", "ovr_delta", "ovr_delta_chips", "ovr_text",
    # states
    "DeadEndError", "ErrorCopy", "empty_state", "error_state", "loading_row",
    "require_action", "skeleton", "skeleton_block", "skeleton_table",
    # table
    "Column", "DataGrid", "TableModel",
    # diff
    "DiffGroup", "DiffLine", "DiffModel", "build_diff", "diff_sheet", "diff_view",
    "pairs_from", "summarise",
    # toast
    "ToastHost",
]
