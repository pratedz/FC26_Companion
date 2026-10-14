"""Workstation strip, build session panel, and draft actions."""

from __future__ import annotations

from typing import Any

from ....app.presenters import editor_view, header_view
from ... import theme
from ...widgets.ovr import ovr_chip
from ...widgets.primitives import (
    button,
    entry,
    hover_for,
    muted_label,
    panel,
    section_title,
    shade,
    text_label,
)
from .._common import monogram, set_status

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

from .presets import (
    EXAMPLE_PROMPTS,
    _ask_grok,
    _connect_grok,
    _watch_ai_changes,
    _grok_readiness_copy,
    CODEX_PURPLE,
)

# Card library is the landing source. AI is the only other method.
PLAYER_SOURCES: tuple[tuple[str, str], ...] = (
    ("cards", "Card library"),
    ("presets", "AI"),
)

_SOURCE_TAB_ORDER: tuple[tuple[str, str, str], ...] = (
    ("cards", "Card Library", "Copy a card's ratings and details onto this player."),
    ("presets", "AI", "Describe a build; Codex proposes changes for you to review."),
)

def _build_page_header(root: Any, svc: Any, target: dict[str, Any]) -> None:
    """Identity header: who is being edited, their ratings, and any staged change.

    The OVR and POT chips show the game's value and, once a draft changes them,
    the staged value beside it, so the effect of a card or AI proposal is
    readable without opening Review.
    """
    from ...widgets.ovr import ovr_delta_chips

    state = svc.store.snapshot()
    base = dict(state.editor.base or {})
    merged = state.editor.merged()
    row = ctk.CTkFrame(root, fg_color="transparent")
    row.pack(fill="x", pady=(0, theme.SP3))

    tile = ctk.CTkFrame(row, width=60, height=60, fg_color=theme.CARD_HOVER,
                        corner_radius=theme.R_MD, border_width=1, border_color=theme.BORDER)
    tile.pack(side="left", padx=(0, theme.SP3))
    tile.pack_propagate(False)
    name = str(target.get("name") or target.get("playerid") or "Player")
    from ..signed_portrait import avatar

    if target.get("playerid"):
        avatar(tile, svc, int(target["playerid"]), name)
    else:
        text_label(tile, monogram(name, fallback="PL")[:2], size=theme.T_HEADLINE,
                   display=True, color=theme.ACCENT).pack(expand=True)

    copy = ctk.CTkFrame(row, fg_color="transparent")
    copy.pack(side="left", fill="x", expand=True)
    text_label(copy, f"Edit {name}", size=theme.T_TITLE, display=True).pack(anchor="w")
    facts = [str(target.get("pos") or "").strip()]
    team = next(
        (str(p.get("teamname") or "") for p in state.squad.players
         if str(p.get("playerid") or p.get("id")) == str(target.get("playerid"))),
        "",
    )
    facts.append(team)
    if target.get("playerid"):
        facts.append(f"ID {target['playerid']}")
    muted_label(copy, "  ·  ".join(f for f in facts if f), size=theme.T_SMALL).pack(
        anchor="w", pady=(2, 0)
    )

    ratings = ctk.CTkFrame(row, fg_color="transparent")
    ratings.pack(side="left", padx=(theme.SP4, theme.SP4))
    for field, label in (("overallrating", "Overall"), ("potential", "Potential")):
        before = base.get(field, target.get("ovr") if field == "overallrating" else None)
        after = merged.get(field, before)
        cell = ctk.CTkFrame(ratings, fg_color="transparent")
        cell.pack(side="left", padx=(0, theme.SP4))
        if before is not None and after is not None and before != after:
            ovr_delta_chips(cell, before, after, size="sm").pack(anchor="w")
        else:
            ovr_chip(cell, after if after is not None else before, size="sm").pack(anchor="w")
        muted_label(cell, label, size=theme.T_CAPTION).pack(anchor="w", pady=(2, 0))

    from .target_picker import _choose_another_player

    button(
        row,
        "Change player",
        lambda: _choose_another_player(svc),
        kind="secondary",
        icon="squad",
        height=theme.BTN_MD,
    ).pack(side="right", anchor="n")


def _build_session_toolbar(root: Any, svc: Any, ed: dict[str, Any]) -> None:
    """Draft controls in one line: what is staged, review it, discard it, reload."""
    from .target_picker import _reload, _reset
    from ....app.commands.player import player_read_pending

    reload_reason = ""
    if ed["has_changes"]:
        reload_reason = "Discard or apply the current draft before reloading live values."
    else:
        state = svc.store.snapshot()
        if player_read_pending(state, state.target.playerid):
            reload_reason = "A live read for this player is already queued."

    if not ed["has_changes"]:
        toolbar = ctk.CTkFrame(root, fg_color="transparent")
        toolbar.pack(fill="x", pady=(0, theme.SP3))
        muted_label(toolbar, "No changes staged.", size=theme.T_SMALL).pack(side="left")
        button(
            toolbar, "Reload from game", lambda: _reload(svc), kind="ghost",
            icon="refresh", height=theme.BTN_SM, disabled_reason=reload_reason,
        ).pack(side="right")
        return

    toolbar = panel(root, level=1, border_color=shade(theme.ACCENT, 0.45))
    toolbar.pack(fill="x", pady=(0, theme.SP3))
    inner = ctk.CTkFrame(toolbar, fg_color="transparent")
    inner.pack(fill="x", padx=theme.SP4, pady=theme.SP2)
    count = ed["dirty_count"]
    text_label(
        inner,
        f"{count} change{'s' if count != 1 else ''} staged",
        size=theme.T_SMALL, bold=True, color=theme.ACCENT,
    ).pack(side="left")
    muted_label(inner, "Nothing is written to FC 26 until you apply it from Review changes below.",
                size=theme.T_SMALL).pack(side="left", padx=(theme.SP3, 0))
    # "Review changes" is the Changes bar's one button; a second copy here
    # would be the same verb at two weights.
    button(
        inner, "Discard draft", lambda: _reset(svc), kind="ghost", icon="trash",
        height=theme.BTN_SM,
    ).pack(side="right")


def _build_workstation_strip(
    root: Any,
    svc: Any,
    ed: dict[str, Any],
    *,
    active: str = "cards",
    on_select: Any | None = None,
) -> Any:
    """The edit-method switch, using the app's one segmented control."""
    del svc, ed
    from ...nav import segmented_rail

    _ = PLAYER_SOURCES
    host = ctk.CTkFrame(root, fg_color="transparent")
    host.pack(fill="x", pady=(0, theme.SP3))
    titles = [(key, title) for key, title, _blurb in _SOURCE_TAB_ORDER]
    blurbs = {key: blurb for key, _title, blurb in _SOURCE_TAB_ORDER}
    rail = segmented_rail(
        host, titles, active=active,
        on_select=(lambda k: on_select(k)) if callable(on_select) else (lambda _k: None),
    )
    rail.configure(width=300)
    rail.pack(side="left")
    del blurbs  # each mode's panel explains itself; a caption here said it twice

    def set_active(key: str) -> None:
        rail.set_active(key)

    host.set_active = set_active  # type: ignore[attr-defined]
    return host


def _build_session_panel(
    root: Any, svc: Any, ed: dict[str, Any], view: dict[str, Any] | None = None
) -> None:
    """One draft fed by a target-bound AI proposal."""
    host = panel(root, level=1)
    host.pack(fill="x", pady=(0, theme.SP2))
    top = ctk.CTkFrame(host, fg_color="transparent")
    top.pack(fill="x", padx=theme.SP4, pady=(theme.SP3, theme.SP1))
    section_title(top, "Ask AI", detail="Codex proposes, you review before anything is applied.").pack(
        side="left"
    )

    # Keep the AI path honest and visible.  A footer-only message was too easy
    # to miss, which made a missing Codex login look like a silent no-op.
    try:
        from ....integrations.ai_provider import readiness as ai_readiness

        readiness_text, readiness_color = _grok_readiness_copy(ai_readiness())
    except Exception as exc:  # noqa: BLE001
        readiness_text, readiness_color = f"AI status unavailable: {exc}", theme.WARNING

    button(
        top,
        "Check AI status",
        lambda: _connect_grok(svc, ai_feedback),
        kind="ghost",
        icon="key",
        height=theme.BTN_SM,
    ).pack(side="right")

    from ...widgets.ai_bar import mount_ai_provider_bar

    mount_ai_provider_bar(host, svc).pack_configure(padx=theme.SP4, pady=(theme.SP1, 0))
    ai_feedback = muted_label(host, readiness_text, size=theme.T_SMALL, color=readiness_color)
    ai_feedback.pack(anchor="w", padx=theme.SP4, pady=(theme.SP2, 0))

    def _refresh_feedback() -> None:
        from ....integrations.ai_provider import readiness as current

        text, color = _grok_readiness_copy(current())
        ai_feedback.configure(text=text, text_color=color)

    _watch_ai_changes(ai_feedback, _refresh_feedback)

    ai_row = ctk.CTkFrame(host, fg_color="transparent")
    ai_row.pack(fill="x", padx=theme.SP4, pady=(theme.SP3, theme.SP2))
    prompt = entry(
        ai_row,
        placeholder="Describe the build, e.g. make him a fast playmaker, max OVR 90",
        height=theme.BTN_LG,
    )
    prompt.pack(side="left", fill="x", expand=True)
    if view is not None and view.get("grok_prompt"):
        prompt.insert(0, str(view["grok_prompt"]))

    def persist_prompt(_event: Any = None) -> None:
        if view is not None:
            try:
                view["grok_prompt"] = prompt.get() or ""
            except Exception:
                pass

    prompt.bind("<KeyRelease>", persist_prompt)

    examples = ctk.CTkFrame(host, fg_color="transparent")
    examples.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP3))
    muted_label(examples, "Try", size=theme.T_CAPTION).pack(side="left", padx=(0, theme.SP2))

    def fill_example(text: str) -> None:
        try:
            prompt.delete(0, "end")
            prompt.insert(0, text)
            persist_prompt()
        except Exception:
            pass

    for sample in EXAMPLE_PROMPTS:
        button(
            examples,
            sample if len(sample) < 30 else sample[:27] + "…",
            (lambda t=sample: fill_example(t)),
            kind="ghost",
            height=theme.BTN_SM,
        ).pack(side="left", padx=(0, theme.SP1))

    ai_state = {"busy": False}
    propose_button = button(
        ai_row,
        "Propose changes",
        lambda: _ask_grok(
            svc, prompt.get(), feedback=ai_feedback, propose_button=propose_button,
            ai_state=ai_state,
        ),
        # Review (Changes dock) remains the one primary CTA.
        kind="secondary",
        icon="ai",
        height=theme.BTN_LG,
        width=160,
    )
    propose_button.pack(side="left", padx=(theme.SP2, 0))
    try:
        from ...icons import ctk_icon

        propose_button.configure(
            fg_color=shade(CODEX_PURPLE, 0.3),
            hover_color=shade(CODEX_PURPLE, 0.42),
            border_color=shade(CODEX_PURPLE, 0.75),
            text_color=theme.TEXT,
            image=ctk_icon("ai", 16, tint=theme.TEXT),
        )
    except Exception:
        pass
    prompt.bind(
        "<Return>",
        lambda _event: _ask_grok(
            svc, prompt.get(), feedback=ai_feedback, propose_button=propose_button,
            ai_state=ai_state,
        ),
    )

    source = ed.get("source_label") or "live player values"
    muted_label(
        host,
        f"AI assistant: describe an era/physique or playstyle build. Proposals start from {source}.",
        size=theme.T_CAPTION,
    ).pack(anchor="w", padx=theme.SP4, pady=(0, theme.SP3))
    for warning in ed.get("warnings") or ():
        muted_label(host, f"Warning: {warning}", size=theme.T_SMALL, color=theme.WARNING).pack(
            anchor="w", padx=theme.SP4, pady=(0, theme.SP2)
        )


def _open_review(svc: Any) -> None:
    """Open the one mandatory review sheet, with an honest non-GUI fallback."""
    review = getattr(getattr(svc, "ui", None), "review", None)
    if callable(review) and review():
        return
    set_status(svc, "Changes are staged. Open Review in the bottom Changes bar to inspect them.")
