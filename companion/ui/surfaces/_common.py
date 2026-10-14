"""Shared layout helpers for surfaces. No domain logic."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .. import theme
from ..widgets.primitives import (
    button,
    ensure_ctk,
    muted_label,
    panel,
    section_header as section_header,
    text_label,
)

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def monogram(name: str, *, fallback: str = "FC") -> str:
    """Up to three initials from a club or player name."""
    letters = "".join(part[0] for part in str(name or "").split() if part)[:3].upper()
    return letters or fallback


def identity_row(
    parent: Any,
    *,
    letters: str,
    title: str,
    caption: str = "",
    ovr: Any = None,
) -> Any:
    """Broadcast lock-up: crest tile, display-face name, OVR chip and caption."""
    ensure_ctk()
    from ..widgets.ovr import ovr_chip

    frame = ctk.CTkFrame(parent, fg_color="transparent")
    mono = ctk.CTkFrame(
        frame, width=48, height=48, fg_color=theme.CARD_HOVER, corner_radius=theme.R_MD,
        border_width=1, border_color=theme.BORDER,
    )
    mono.pack(side="left", padx=(0, theme.SP3))
    mono.pack_propagate(False)
    text_label(mono, (letters or "FC")[:3], size=theme.T_HEADLINE, display=True,
               color=theme.ACCENT).pack(expand=True)
    names = ctk.CTkFrame(frame, fg_color="transparent")
    names.pack(side="left", fill="x", expand=True)
    title_label = text_label(names, title, size=theme.T_TITLE, display=True)
    title_label.pack(anchor="w")
    frame.title_label = title_label
    stats = ctk.CTkFrame(names, fg_color="transparent")
    stats.pack(anchor="w", pady=(2, 0))
    if ovr is not None:
        ovr_chip(stats, ovr, size="xs").pack(side="left", padx=(0, theme.SP2))
    caption_label = muted_label(stats, caption, size=theme.T_SMALL)
    caption_label.pack(side="left")
    frame.caption_label = caption_label
    return frame


def surface_root(parent: Any) -> Any:
    ensure_ctk()
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    return frame


def action_row(parent: Any, *actions: tuple[str, Callable[[], None], str]) -> Any:
    """Pack a horizontal row of buttons.

    Each action is ``(label, callback, kind)`` where kind is primary/accent/ghost/danger.
    """
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(theme.SP2, 0))
    for i, (label, cb, kind) in enumerate(actions):
        button(row, label, cb, kind=kind, height=theme.BTN_MD).pack(
            side="left", padx=(0 if i == 0 else theme.SP2, 0)
        )
    return row


def info_card(parent: Any, title: str, body: str) -> Any:
    card = panel(parent, level=1)
    card.pack(fill="x", pady=(0, theme.SP2))
    text_label(card, title, size=theme.T_BODY, bold=True).pack(
        anchor="w", padx=theme.SP4, pady=(theme.SP3, 2)
    )
    muted_label(card, body, size=theme.T_SMALL).pack(
        anchor="w", padx=theme.SP4, pady=(0, theme.SP3)
    )
    return card


def status_line(parent: Any, text: str) -> Any:
    return muted_label(parent, text, size=theme.T_CAPTION)


def collapse_below(container: Any, side_panel: Any, min_width: int, *, pack_kw: dict) -> None:
    """Hide ``side_panel`` while ``container`` is narrower than ``min_width``.

    The Club detail card and similar side panels are useful, but at the minimum
    window with the Activity drawer open they would squeeze the list they
    describe down to one column. The list wins; the panel returns when there is
    room again.
    """
    import tkinter

    state = {"shown": True, "job": None}

    def apply() -> None:
        state["job"] = None
        try:
            if not container.winfo_exists():
                return
            scale = float(container._get_widget_scaling())
            width = int(container.winfo_width() / max(scale, 0.5))
        except Exception:
            return
        if width <= 1:
            return
        want = width >= min_width
        if want == state["shown"]:
            return
        state["shown"] = want
        try:
            if want:
                side_panel.pack(**pack_kw)
            else:
                side_panel.pack_forget()
        except Exception:
            pass

    def schedule(_event: Any = None) -> None:
        if state["job"] is None:
            try:
                state["job"] = container.after_idle(apply)
            except Exception:
                state["job"] = None

    try:
        tkinter.Misc.bind(container, "<Configure>", schedule, add="+")
    except Exception:
        pass
    schedule()


def position_text(value: Any) -> str:
    """A position for display. Raw FC position ids (25) become names (ST)."""
    if value in (None, "", "—", "-"):
        return "—"
    try:
        from ...domain.squad_session import _FC_POS

        return _FC_POS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def squad_board_row(player: Mapping[str, Any]) -> dict[str, Any]:
    """Stable Club/Transfers roster row. ``id`` stays on the row for search."""
    raw_id = player.get("playerid") or player.get("id")
    try:
        player_id = int(raw_id) if raw_id not in (None, "", "—") else raw_id
    except (TypeError, ValueError):
        player_id = raw_id
    return {
        "name": player.get("name") or player.get("playername") or "—",
        "pos": position_text(
            player.get("position") or player.get("preferredposition1") or player.get("pos")
        ),
        "ovr": player.get("overallrating") or player.get("ovr") or "—",
        "pot": player.get("potential") or player.get("pot") or "—",
        "age": _board_age(player),
        "id": player_id,
        "_raw": dict(player),
    }


def _board_age(player: Mapping[str, Any]) -> Any:
    """Show a stored age, or the age implied by the exported birthdate."""
    raw = player.get("age")
    if raw not in (None, "", "—", 0, "0"):
        return raw
    from ...domain.card_import import age_from_birthdate

    years = age_from_birthdate(player.get("birthdate"))
    return years if years is not None else "—"


def squad_row_ovr(row: Mapping[str, Any]) -> int:
    raw = row.get("_raw") if isinstance(row.get("_raw"), Mapping) else {}
    value = row.get("ovr") or row.get("overallrating") or raw.get("overallrating")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def sort_squad_rows_by_ovr(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Highest overall first so the prompt checklist leads with stars."""
    return tuple(
        sorted(
            (dict(row) for row in rows),
            key=lambda row: (-squad_row_ovr(row), str(row.get("name") or "")),
        )
    )


def intended_prompt_rows(
    rows: Sequence[Mapping[str, Any]],
    included_ids: Sequence[Any] | None,
) -> tuple[dict[str, Any], ...]:
    """Players the Codex prompt is allowed to edit.

    ``included_ids is None`` means the whole list (first open). An empty
    sequence means the user unchecked everyone.
    """
    materialized = tuple(dict(row) for row in rows)
    if included_ids is None:
        return materialized
    want = set(included_ids)
    return tuple(row for row in materialized if row.get("id") in want)


def grok_scope_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    checked_ids: Sequence[Any] | None = None,
    checked_only: bool = False,
) -> tuple[tuple[dict[str, Any], ...], str]:
    """Who Ask Codex may edit from the one Club board.

    Default is the whole squad. ``checked_only`` uses board ticks.
    """
    materialized = tuple(dict(row) for row in rows)
    if not checked_only:
        return materialized, "squad"
    picked = intended_prompt_rows(materialized, list(checked_ids or ()))
    return picked, "checked"


def grok_scope_caption(
    squad_count: int,
    checked_count: int,
    *,
    checked_only: bool,
    cap: int = 25,
) -> str:
    """One line for Club: whole squad vs the names already ticked."""
    if checked_only:
        if checked_count <= 0:
            return "Check players on the board, or turn off Only checked."
        noun = "player" if checked_count == 1 else "players"
        if checked_count > cap:
            return f"Codex will edit {checked_count} checked — highest {cap} overall."
        return f"Codex will edit {checked_count} checked {noun}."
    if squad_count > cap:
        base = f"Codex will edit the whole squad ({squad_count}, highest {cap} overall)."
    else:
        base = f"Codex will edit the whole squad ({squad_count})."
    if checked_count:
        return f"{base} {checked_count} ticked stay for Plan / Open."
    return base


def cap_prompt_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int = 25,
) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Keep at most ``limit`` players, highest overall first."""
    materialized = tuple(dict(row) for row in rows)
    if len(materialized) <= limit:
        return materialized, False
    ranked = sort_squad_rows_by_ovr(materialized)
    return ranked[:limit], True


def filter_squad_rows(
    rows: Sequence[Mapping[str, Any]], query: str
) -> tuple[dict[str, Any], ...]:
    """Match name, position, or player id. Empty query keeps every row."""
    needle = (query or "").strip().casefold()
    materialized = tuple(dict(row) for row in rows)
    if not needle:
        return materialized
    return tuple(
        row
        for row in materialized
        if needle
        in " ".join(str(row.get(field) or "") for field in ("name", "pos", "id")).casefold()
    )


def set_status(svc: Any, text: str, *, tone: str = "info") -> None:
    """Publish chrome/toast status without importing the shell."""
    try:
        from ...app import events as E

        svc.store.dispatch(E.StatusSet(text, tone=tone))
    except Exception:
        pass
