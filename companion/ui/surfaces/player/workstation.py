"""Identity header, mode row, the AI panel, and draft actions."""

from __future__ import annotations

from typing import Any

from ....domain.ai_requests import load_requests, player_requests, save_request, saved_requests_path
from ... import theme
from ...widgets.ovr import ovr_chip
from ...widgets.primitives import (
    bind_recursive,
    button,
    elevation,
    entry,
    muted_label,
    section_title,
    shade,
    text_label,
    themed_scroll,
    tooltip,
    truncate,
)
from .._common import monogram, set_status

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

from .presets import (
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

# Where each request came from, as a word (colour is never the only signal).
_REQUEST_GROUPS: dict[str, tuple[str, str]] = {
    "club": ("Club", "info"),
    "idea": ("Idea", "muted"),
    "saved": ("Saved", "ok"),
}


def _rule(parent: Any, *, width: int = 1, height: int = 1) -> Any:
    """A 1 px BORDER rule. Plain Tk: a CTkFrame this thin draws nothing."""
    import tkinter

    return tkinter.Frame(parent, width=width, height=height, bg=theme.BORDER,
                         highlightthickness=0, bd=0)


def _build_page_header(root: Any, svc: Any, target: dict[str, Any]) -> None:
    """Identity header: portrait, who is being edited, and their ratings beside the name.

    The OVR and POT chips show the game's value and, once a draft changes them,
    the staged value beside it, so the effect of a card or AI proposal is
    readable without opening Review.
    """
    from ...widgets.ovr import ovr_delta_chips

    state = svc.store.snapshot()
    base = dict(state.editor.base or {})
    merged = state.editor.merged()
    row = ctk.CTkFrame(root, fg_color="transparent")
    row.pack(fill="x", pady=(0, theme.SP4))

    tile = ctk.CTkFrame(row, width=64, height=64, fg_color=theme.CARD_HOVER,
                        corner_radius=theme.R_MD, border_width=1, border_color=theme.BORDER)
    tile.pack(side="left", padx=(0, theme.SP4))
    tile.pack_propagate(False)
    name = str(target.get("name") or target.get("playerid") or "Player")
    from ..signed_portrait import avatar

    if target.get("playerid"):
        avatar(tile, svc, int(target["playerid"]), name, size=62)
    else:
        text_label(tile, monogram(name, fallback="PL")[:2], size=theme.T_HEADLINE,
                   display=True, color=theme.ACCENT).pack(expand=True)

    copy = ctk.CTkFrame(row, fg_color="transparent")
    copy.pack(side="left", anchor="center")
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

    # Ratings sit with the name they belong to, split off by a quiet rule,
    # instead of floating at the far edge of the window.
    _rule(row, width=1, height=44).pack(side="left", padx=theme.SP5)
    ratings = ctk.CTkFrame(row, fg_color="transparent")
    ratings.pack(side="left")
    for field, label in (("overallrating", "Overall"), ("potential", "Potential")):
        before = base.get(field, target.get("ovr") if field == "overallrating" else None)
        after = merged.get(field, before)
        cell = ctk.CTkFrame(ratings, fg_color="transparent")
        cell.pack(side="left", padx=(0, theme.SP5))
        if before is not None and after is not None and before != after:
            ovr_delta_chips(cell, before, after, size="md").pack(anchor="w")
        else:
            ovr_chip(cell, after if after is not None else before, size="md").pack(anchor="w")
        muted_label(cell, label, size=theme.T_CAPTION).pack(anchor="w", pady=(theme.SP1, 0))

    from .target_picker import _choose_another_player

    button(
        row,
        "Change player",
        lambda: _choose_another_player(svc),
        kind="secondary",
        icon="squad",
        height=theme.BTN_MD,
    ).pack(side="right", anchor="center")


def _build_session_toolbar(parent: Any, svc: Any, ed: dict[str, Any]) -> None:
    """Draft state on the mode row's right: what is staged, discard it, or reload."""
    from .target_picker import _reload, _reset
    from ....app.commands.player import player_read_pending

    reload_reason = ""
    if ed["has_changes"]:
        reload_reason = "Discard or apply the current draft before reloading live values."
    else:
        state = svc.store.snapshot()
        if player_read_pending(state, state.target.playerid):
            reload_reason = "A live read for this player is already queued."

    toolbar = ctk.CTkFrame(parent, fg_color="transparent")
    toolbar.pack(side="right")
    if not ed["has_changes"]:
        button(
            toolbar, "Reload from game", lambda: _reload(svc), kind="ghost",
            icon="refresh", height=theme.BTN_SM, disabled_reason=reload_reason,
        ).pack(side="right")
        muted_label(toolbar, "No changes staged", size=theme.T_SMALL).pack(
            side="right", padx=(0, theme.SP3)
        )
        return

    # "Review changes" is the Changes bar's one button; a second copy here
    # would be the same verb at two weights.
    button(
        toolbar, "Discard draft", lambda: _reset(svc), kind="ghost", icon="trash",
        height=theme.BTN_SM,
    ).pack(side="right")
    count = ed["dirty_count"]
    staged = text_label(
        toolbar,
        f"{count} change{'s' if count != 1 else ''} staged  ·  not in FC 26 yet",
        size=theme.T_SMALL, bold=True, color=theme.ACCENT,
    )
    staged.pack(side="right", padx=(0, theme.SP3))
    tooltip(staged, "Nothing is written to FC 26 until you apply it from Review changes below.")


def _build_workstation_strip(
    root: Any,
    svc: Any,
    ed: dict[str, Any],
    *,
    active: str = "cards",
    on_select: Any | None = None,
) -> Any:
    """The mode row: the edit-method switch on the left, draft state on the right."""
    from ...nav import segmented_rail

    _ = PLAYER_SOURCES
    host = ctk.CTkFrame(root, fg_color="transparent")
    host.pack(fill="x", pady=(0, theme.SP3))
    titles = [(key, title) for key, title, _blurb in _SOURCE_TAB_ORDER]
    rail = segmented_rail(
        host, titles, active=active,
        on_select=(lambda k: on_select(k)) if callable(on_select) else (lambda _k: None),
    )
    rail.configure(width=280)
    rail.pack(side="left")
    _build_session_toolbar(host, svc, ed)

    def set_active(key: str) -> None:
        rail.set_active(key)

    host.set_active = set_active  # type: ignore[attr-defined]
    return host


def _build_session_panel(
    root: Any, svc: Any, ed: dict[str, Any], view: dict[str, Any] | None = None
) -> None:
    """Ask AI: provider, the request, and the shared request library below it."""
    host = ctk.CTkFrame(root, fg_color=elevation(1), corner_radius=theme.R_MD,
                        border_width=1, border_color=theme.BORDER)
    host.pack(fill="both", expand=True, pady=(0, theme.SP2))
    top = ctk.CTkFrame(host, fg_color="transparent")
    top.pack(fill="x", padx=theme.SP4, pady=(theme.SP4, theme.SP2))
    source = ed.get("source_label") or "live player values"
    section_title(
        top, "Ask AI",
        detail=f"AI assistant. Proposals start from {source}; you review before anything is applied.",
    ).pack(side="left")

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

    mount_ai_provider_bar(host, svc).pack_configure(padx=theme.SP4, pady=(0, 0))

    ai_row = ctk.CTkFrame(host, fg_color="transparent")
    ai_row.pack(fill="x", padx=theme.SP4, pady=(theme.SP3, 0))
    prompt = entry(
        ai_row,
        placeholder="Describe an era/physique or playstyle build, e.g. a fast playmaker, max OVR 90",
        height=theme.BTN_LG,
    )
    prompt.pack(side="left", fill="x", expand=True)
    if view is not None and view.get("grok_prompt"):
        prompt.insert(0, str(view["grok_prompt"]))

    ai_feedback = muted_label(host, readiness_text, size=theme.T_SMALL, color=readiness_color,
                              anchor="w", justify="left")
    ai_feedback.pack(fill="x", padx=theme.SP4, pady=(theme.SP2, 0))

    def _refresh_feedback() -> None:
        from ....integrations.ai_provider import readiness as current

        text, color = _grok_readiness_copy(current())
        ai_feedback.configure(text=text, text_color=color)

    _watch_ai_changes(ai_feedback, _refresh_feedback)

    for warning in ed.get("warnings") or ():
        muted_label(host, f"Warning: {warning}", size=theme.T_SMALL, color=theme.WARNING).pack(
            anchor="w", padx=theme.SP4, pady=(theme.SP1, 0)
        )

    ai_state = {"busy": False}

    def ask() -> None:
        _ask_grok(
            svc, prompt.get(), feedback=ai_feedback, propose_button=propose_button,
            ai_state=ai_state,
        )

    propose_button = button(
        ai_row,
        "Propose changes",
        ask,
        # Review (Changes dock) remains the one primary CTA.
        kind="secondary",
        icon="ai",
        height=theme.BTN_LG,
        width=170,
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
    prompt.bind("<Return>", lambda _event: ask())

    _rule(host, height=1).pack(fill="x", padx=theme.SP4, pady=(theme.SP4, theme.SP3))
    _build_request_library(host, svc, prompt, view)


def _build_request_library(
    host: Any, svc: Any, prompt: Any, view: dict[str, Any] | None
) -> None:
    """Requests from Club's "Plan these players", build ideas, and saved ones.

    Clicking a request fills the prompt; it never sends it, so the user can
    still edit it before Propose. Saved requests are one shared file, so a
    request saved here also appears in Club's planner, and the other way round.
    """
    saved_path = saved_requests_path(svc.paths.root)
    heading = ctk.CTkFrame(host, fg_color="transparent")
    heading.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP2))
    section_title(
        heading, "Requests",
        detail="Same requests as Plan these players on Club. Click one to use it.",
    ).pack(side="left")

    def save_current_request() -> None:
        text = (prompt.get() or "").strip()
        if not text:
            set_status(svc, "Type or pick a request before saving it.")
            return
        dialog = ctk.CTkInputDialog(text="Name this request so you can reuse it.", title="Save request")
        name = dialog.get_input()
        if not name:
            return
        try:
            save_request(saved_path, name, text)
        except (ValueError, OSError) as exc:
            set_status(svc, str(exc))
            return
        set_status(svc, f"Saved request: {name.strip()}. It is on Club's planner too.")
        render()

    button(heading, "Save request", save_current_request, kind="ghost", icon="save",
           height=theme.BTN_SM).pack(side="right")

    fill = elevation(1)
    # The scroller takes the panel's remaining height; its canvas is the
    # panel's own colour, so unused height reads as panel, not as a slab.
    scroll = themed_scroll(host, fill=fill, height=150)
    scroll.pack(fill="both", expand=True, padx=(theme.SP3, theme.SP2), pady=(0, theme.SP3))
    grid = scroll.inner
    tiles: list[tuple[Any, str]] = []

    def persist_prompt(_event: Any = None) -> None:
        if view is not None:
            try:
                view["grok_prompt"] = prompt.get() or ""
            except Exception:
                pass
        mark_selected()

    def mark_selected() -> None:
        current = (prompt.get() or "").strip()
        for tile, text in tiles:
            try:
                tile.configure(border_color=theme.ACCENT if text == current else theme.BORDER)
            except Exception:
                pass

    def use(text: str) -> None:
        try:
            prompt.delete(0, "end")
            prompt.insert(0, text)
            prompt.focus_set()
            prompt.icursor("end")
        except Exception:
            pass
        persist_prompt()

    prompt.bind("<KeyRelease>", persist_prompt, add="+")
    state = {"columns": 0, "width": 0}

    def render() -> None:
        for child in list(grid.winfo_children()):
            child.destroy()
        tiles.clear()
        requests = player_requests(load_requests(saved_path))
        columns = state["columns"] or 2
        for index in range(3):
            grid.grid_columnconfigure(index, weight=1 if index < columns else 0,
                                      uniform="requests" if index < columns else "")
        for index, request in enumerate(requests):
            tile = _request_tile(grid, request, lambda t=request.text: use(t), width=state["width"] // columns)
            tile.grid(row=index // columns, column=index % columns, sticky="nsew",
                      padx=theme.SP1, pady=theme.SP1)
            tiles.append((tile, request.text))
        mark_selected()

    def relayout(event: Any) -> None:
        width = int(getattr(event, "width", 0) or 0)
        if width < 200:
            return
        columns = 3 if width >= 900 else 2
        # Re-render on a column change, or when the width moved enough that
        # the tiles' text wrap would be visibly wrong.
        if columns != state["columns"] or abs(width - state["width"]) > 40:
            state.update(columns=columns, width=width)
            render()

    scroll.bind("<Configure>", relayout, add="+")
    state.update(columns=2, width=720)
    render()


def _request_tile(parent: Any, request: Any, on_click: Any, *, width: int = 360) -> Any:
    """One request: its name, where it came from, and the start of its wording."""
    base, hot = elevation(2), elevation(3)
    tile = ctk.CTkFrame(parent, fg_color=base, corner_radius=theme.R_SM,
                        border_width=1, border_color=theme.BORDER, cursor="hand2")
    top = ctk.CTkFrame(tile, fg_color="transparent")
    top.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, 0))
    text_label(top, truncate(request.name, 34), size=theme.T_SMALL, bold=True).pack(side="left")
    word, tone = _REQUEST_GROUPS.get(request.group, ("Saved", "ok"))
    text_label(top, word, size=theme.T_CAPTION, color=theme.tone_fg(tone)).pack(side="right")
    wrap = max(160, width - 3 * theme.SP3)
    # Two lines of caption text; the tooltip carries the whole request.
    limit = max(60, int(wrap / 6.2) * 2)
    caption = muted_label(tile, truncate(request.text, limit), size=theme.T_CAPTION, anchor="w",
                          justify="left", wraplength=wrap)
    caption.pack(fill="x", padx=theme.SP3, pady=(2, theme.SP2))
    # The caption covers most of the tile, so it carries the full-text tooltip.
    tooltip(caption, request.text)

    def enter(_event: Any = None) -> None:
        try:
            tile.configure(fg_color=hot)
        except Exception:
            pass

    def leave(_event: Any = None) -> None:
        try:
            tile.configure(fg_color=base)
        except Exception:
            pass

    bind_recursive(tile, "<Enter>", enter)
    bind_recursive(tile, "<Leave>", leave)
    bind_recursive(tile, "<Button-1>", lambda _e=None: on_click())
    # Keyboard reach: Tab lands on each request, Enter or Space uses it, and
    # the focused tile lifts like a hovered one. CTkFrame.bind goes to its
    # canvas, which never holds focus, so these bind on the Tk frame itself.
    try:
        import tkinter

        tkinter.Frame.configure(tile, takefocus=1)
        for sequence, handler in (
            ("<FocusIn>", enter),
            ("<FocusOut>", leave),
            ("<Return>", lambda _e=None: on_click()),
            ("<space>", lambda _e=None: on_click()),
        ):
            tkinter.Misc.bind(tile, sequence, handler, "+")
    except Exception:
        pass
    tile._request = request  # type: ignore[attr-defined]
    tile._activate = on_click  # type: ignore[attr-defined]
    return tile


def _open_review(svc: Any) -> None:
    """Open the one mandatory review sheet, with an honest non-GUI fallback."""
    review = getattr(getattr(svc, "ui", None), "review", None)
    if callable(review) and review():
        return
    set_status(svc, "Changes are staged. Open Review in the bottom Changes bar to inspect them.")
