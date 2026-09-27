"""Workstation strip, build session panel, and draft actions."""

from __future__ import annotations

from typing import Any

from ....app.presenters import editor_view, header_view
from ....domain.player import ATTRIBUTE_GROUPS, FIELD_SPECS
from ... import theme
from ...widgets.ovr import ovr_chip
from ...widgets.primitives import (
    button,
    eyebrow,
    hover_for,
    muted_label,
    panel,
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
    _grok_readiness_copy,
    CODEX_PURPLE,
)

# Card library is the landing source. AI is the only other method.
PLAYER_SOURCES: tuple[tuple[str, str], ...] = (
    ("cards", "Card library"),
    ("presets", "AI"),
)

_SOURCE_TAB_ORDER: tuple[tuple[str, str, str], ...] = (
    ("cards", "Card Library", "Use a card's values."),
    ("presets", "AI", "Ask Codex to stage a proposal."),
)

# Right-rail fields with known FIELD_SPECS mapping (no invented work rates).
_DETAILS_FIELDS: tuple[str, ...] = (
    "skillmoves",
    "weakfootabilitytypecode",
    "preferredfoot",
    "bodytypecode",
)


def _build_page_header(root: Any, svc: Any, target: dict[str, Any]) -> None:
    """Keep only the identity needed to edit the right player."""
    row = ctk.CTkFrame(root, fg_color="transparent")
    row.pack(fill="x", pady=(0, theme.SP1))
    copy = ctk.CTkFrame(row, fg_color="transparent")
    copy.pack(side="left", fill="x", expand=True)
    name = str(target.get("name") or target.get("playerid") or "Player")
    text_label(copy, f"Edit {name}", size=20, bold=True).pack(anchor="w")
    position = str(target.get("pos") or "").strip()
    muted_label(
        copy,
        f"{position + ' · ' if position else ''}Use a card or ask AI, then review.",
        size=11,
    ).pack(anchor="w")
    from .target_picker import _choose_another_player

    button(
        row,
        "Change player",
        lambda: _choose_another_player(svc),
        kind="secondary",
        height=theme.BTN_MD,
        width=130,
    ).pack(side="right")


def _build_session_toolbar(root: Any, svc: Any, ed: dict[str, Any]) -> None:
    """Clear / Reload / staged caption — Change player lives on the page header."""
    from .target_picker import _reload, _reset
    from ....app.commands.player import player_read_pending

    reload_reason = ""
    if ed["has_changes"]:
        reload_reason = "Discard or apply the current draft before reloading live values."
    else:
        state = svc.store.snapshot()
        if player_read_pending(state, state.target.playerid):
            reload_reason = "A live read for this player is already queued."

    toolbar = ctk.CTkFrame(root, fg_color="transparent")
    toolbar.pack(fill="x", pady=(0, theme.SP2))
    button(
        toolbar, "Clear draft", lambda: _reset(svc), kind="ghost", height=theme.BTN_MD,
        disabled_reason="There are no staged edits to clear." if not ed["has_changes"] else "",
    ).pack(side="left")
    button(
        toolbar,
        "Reload live",
        lambda: _reload(svc),
        kind="ghost",
        height=theme.BTN_MD,
        disabled_reason=reload_reason,
    ).pack(side="left", padx=(theme.SP2, 0))
    if ed["has_changes"]:
        muted_label(
            toolbar,
            f"{ed['dirty_count']} change{'s' if ed['dirty_count'] != 1 else ''} staged — review here or in the bar below",
            size=11,
        ).pack(side="right")


def _fmt_meta(value: Any, *, empty: str = "—") -> str:
    if value in (None, "", "—"):
        return empty
    return str(value)


def _foot_label(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return _fmt_meta(value)
    if code == 1:
        return "Right (1)"
    if code == 2:
        return "Left (2)"
    return str(code)


def _build_target_summary(root: Any, svc: Any, ed: dict[str, Any]) -> None:
    """Left column: locked target identity from real target/editor/squad data only."""
    state = svc.store.snapshot()
    hv = header_view(state)["target"]
    merged = ed.get("merged") or {}
    host = panel(root, level=1)
    host.pack(fill="both", expand=True)

    hero = ctk.CTkFrame(host, fg_color="transparent")
    hero.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
    mono = ctk.CTkFrame(
        hero, width=56, height=56, fg_color=theme.ACCENT, corner_radius=theme.R_SM,
    )
    mono.pack(side="left", padx=(0, theme.SP3))
    mono.pack_propagate(False)
    letters = monogram(hv.get("name") or str(hv.get("playerid") or "P"), fallback="P")
    text_label(mono, letters[:3], size=16, bold=True, color=theme.BG).pack(expand=True)

    names = ctk.CTkFrame(hero, fg_color="transparent")
    names.pack(side="left", fill="x", expand=True)
    text_label(
        names, hv.get("name") or str(hv.get("playerid") or "Player"), size=16, bold=True,
    ).pack(anchor="w")
    bits = [part for part in (hv.get("pos"),) if part]
    if bits:
        muted_label(names, " · ".join(str(b) for b in bits), size=11).pack(anchor="w")
    muted_label(
        names,
        f"Career Mode · {hv.get('source') or 'current squad'}",
        size=10,
    ).pack(anchor="w", pady=(2, 0))

    chips = ctk.CTkFrame(host, fg_color="transparent")
    chips.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    ovr_val = merged.get("overallrating", hv.get("ovr"))
    pot_val = merged.get("potential")
    ovr_wrap = ctk.CTkFrame(chips, fg_color="transparent")
    ovr_wrap.pack(side="left", padx=(0, theme.SP3))
    ovr_chip(ovr_wrap, ovr_val, size="md").pack(side="left")
    muted_label(ovr_wrap, "OVR", size=10).pack(side="left", padx=(theme.SP1, 0))
    if pot_val not in (None, "", "—"):
        pot_wrap = ctk.CTkFrame(chips, fg_color="transparent")
        pot_wrap.pack(side="left")
        ovr_chip(pot_wrap, pot_val, size="md").pack(side="left")
        muted_label(pot_wrap, "POT", size=10).pack(side="left", padx=(theme.SP1, 0))

    phys = ctk.CTkFrame(host, fg_color="transparent")
    phys.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    height = merged.get("height")
    weight = merged.get("weight")
    foot = merged.get("preferredfoot")
    phys_bits = []
    if height not in (None, "", "—"):
        phys_bits.append(f"{height} cm")
    if weight not in (None, "", "—"):
        phys_bits.append(f"{weight} kg")
    if foot not in (None, "", "—"):
        phys_bits.append(_foot_label(foot))
    if phys_bits:
        muted_label(phys, " · ".join(phys_bits), size=11).pack(anchor="w")

    info = panel(host, level=2)
    info.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    head = ctk.CTkFrame(info, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    eyebrow(head, "Player information").pack(side="left")
    muted_label(
        head,
        ed.get("source_label") or "Live values",
        size=10,
    ).pack(side="right")

    rows: list[tuple[str, str]] = [
        ("Player ID", _fmt_meta(hv.get("playerid"))),
    ]
    nat = merged.get("nationality")
    if nat not in (None, "", "—"):
        rows.append(("Nationality ID", _fmt_meta(nat)))
    positions = []
    for key in ("preferredposition1", "preferredposition2", "preferredposition3"):
        val = merged.get(key)
        if val not in (None, "", "—", -1, "-1"):
            positions.append(str(val))
    if hv.get("pos") and str(hv.get("pos")) not in positions:
        positions.insert(0, str(hv["pos"]))
    if positions:
        rows.append(("Position(s)", ", ".join(positions[:4])))
    if foot not in (None, "", "—"):
        rows.append(("Preferred foot", _foot_label(foot)))
    age = None
    for raw in state.squad.players:
        try:
            pid = int(raw.get("playerid") or raw.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if hv.get("playerid") is not None and pid == int(hv["playerid"]):
            age = raw.get("age")
            break
    if age not in (None, "", "—"):
        rows.append(("Age", _fmt_meta(age)))
    contract = merged.get("contractvaliduntil")
    if contract not in (None, "", "—"):
        rows.append(("Contract year", _fmt_meta(contract)))
    if state.target.teamid is not None:
        rows.append(("Team ID", str(state.target.teamid)))
    rows.append(("Squad status", "Locked target"))
    rows.append(("Source", _fmt_meta(hv.get("source") or "squad", empty="current squad")))

    for label, value in rows:
        line = ctk.CTkFrame(info, fg_color="transparent")
        line.pack(fill="x", padx=theme.SP3, pady=1)
        muted_label(line, label, size=10).pack(side="left")
        text_label(line, value, size=11, bold=True).pack(side="right")
    muted_label(
        info,
        "Wages, market value, and portraits are not invented here.",
        size=9,
    ).pack(anchor="w", padx=theme.SP3, pady=(theme.SP1, theme.SP2))


def _build_details_rail(root: Any, svc: Any, ed: dict[str, Any]) -> None:
    """Right column: skills/foot/body codes with real FieldEdited paths."""
    from .manual import _field_row

    host = panel(root, level=1)
    host.pack(fill="both", expand=True)
    head = ctk.CTkFrame(host, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    eyebrow(head, "Skills & body").pack(side="left")
    muted_label(head, "Same fields as detailed editor", size=9).pack(side="right")
    muted_label(
        host,
        "Skill moves raw 0 = 1★. Work rates are not in the schema — omitted.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))

    body = ctk.CTkFrame(host, fg_color="transparent")
    body.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    for field in _DETAILS_FIELDS:
        spec = FIELD_SPECS[field]
        _field_row(
            body,
            svc,
            spec.name,
            spec.label,
            ed["merged"].get(spec.name),
            spec.name in ed["dirty"],
            spec.minimum,
            spec.maximum,
        )

    # Read-only key ratings snapshot (concept right rail for Presets).
    ratings = panel(host, level=2)
    ratings.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    eyebrow(ratings, "Key ratings").pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    merged = ed.get("merged") or {}
    for group_name, items in ATTRIBUTE_GROUPS:
        if group_name == "Goalkeeping":
            continue
        values = []
        for field_name, _label in items:
            raw = merged.get(field_name)
            try:
                values.append(int(raw))
            except (TypeError, ValueError):
                pass
        if not values:
            continue
        avg = sum(values) // len(values)
        line = ctk.CTkFrame(ratings, fg_color="transparent")
        line.pack(fill="x", padx=theme.SP3, pady=1)
        muted_label(line, group_name, size=10).pack(side="left")
        text_label(line, str(avg), size=11, bold=True, color=theme.TEXT).pack(side="right")
        bar = ctk.CTkProgressBar(
            ratings,
            height=6,
            progress_color=theme.SUCCESS if avg >= 80 else (theme.WARNING if avg >= 60 else theme.MUTED),
            fg_color=theme.BORDER,
        )
        bar.set(max(0.0, min(1.0, avg / 99.0)))
        bar.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP1))


def _build_workstation_strip(
    root: Any,
    svc: Any,
    ed: dict[str, Any],
    *,
    active: str = "cards",
    on_select: Any | None = None,
) -> Any:
    """Compact mode selector with the active editor immediately beneath it."""
    del svc, ed
    _ = PLAYER_SOURCES
    host = panel(root, level=1)
    host.pack(fill="x", pady=(0, theme.SP2))
    switch = ctk.CTkFrame(host, fg_color="transparent")
    switch.pack(fill="x", padx=theme.SP2, pady=theme.SP2)
    eyebrow(switch, "Edit with").pack(side="left", padx=(theme.SP1, theme.SP3))
    chips: dict[str, Any] = {}
    for key, title, _blurb in _SOURCE_TAB_ORDER:
        btn = button(
            switch,
            title,
            (lambda k=key: on_select(k)) if callable(on_select) else None,
            kind="ghost",
            height=theme.BTN_LG,
        )
        btn.pack(side="left", fill="x", expand=True, padx=(0, theme.SP1))
        chips[key] = btn

    def set_active(key: str) -> None:
        for name, btn in chips.items():
            on = name == key
            accent = CODEX_PURPLE if name == "presets" and on else theme.ACCENT
            try:
                btn.configure(
                    fg_color=theme.CARD_HOVER if on else "transparent",
                    border_color=accent if on else theme.BORDER,
                    text_color=theme.TEXT if on else theme.MUTED,
                )
            except Exception:
                pass

    host.set_active = set_active  # type: ignore[attr-defined]
    set_active(active)
    return host


def _build_session_panel(
    root: Any, svc: Any, ed: dict[str, Any], view: dict[str, Any] | None = None
) -> None:
    """One draft fed by a target-bound AI proposal."""
    host = panel(root, level=1)
    host.pack(fill="x", pady=(0, theme.SP2))
    top = ctk.CTkFrame(host, fg_color="transparent")
    top.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    eyebrow(top, "Ask AI").pack(side="left")
    source = ed.get("source_label") or "Live player values"
    muted_label(top, f"Current values: {source}", size=10).pack(side="right")

    # Keep the AI path honest and visible.  A footer-only message was too easy
    # to miss, which made a missing Codex login look like a silent no-op.
    try:
        from ....integrations.grok import status as grok_status

        readiness = grok_status()
        readiness_text, readiness_color = _grok_readiness_copy(readiness)
    except Exception as exc:  # noqa: BLE001
        readiness_text, readiness_color = f"Codex status unavailable: {exc}", theme.WARNING

    ai = panel(host, level=2)
    ai.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    try:
        ai.configure(border_color=CODEX_PURPLE)
    except Exception:
        pass
    ai_header = ctk.CTkFrame(ai, fg_color="transparent")
    ai_header.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, 0))
    from ...widgets.ai_bar import mount_ai_provider_bar

    mount_ai_provider_bar(ai, svc).pack_configure(padx=theme.SP3, pady=(theme.SP1, 0))
    text_label(ai_header, "AI assistant · Codex", size=13, bold=True, color=CODEX_PURPLE).pack(
        side="left"
    )
    button(
        ai_header,
        "Check Codex login",
        lambda: _connect_grok(svc, ai_feedback),
        kind="ghost",
        height=theme.BTN_SM,
        width=145,
    ).pack(side="right")
    ai_feedback = muted_label(ai, readiness_text, size=11, color=readiness_color)
    ai_feedback.pack(anchor="w", padx=theme.SP3, pady=(theme.SP1, 0))
    muted_label(
        ai,
        "Describe an era/physique or playstyle build. Codex stages a proposal for review.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))

    ai_row = ctk.CTkFrame(ai, fg_color="transparent")
    ai_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP1))
    prompt = ctk.CTkEntry(
        ai_row,
        height=theme.BTN_MD,
        placeholder_text="Ask GPT: make him a fast playmaker, max OVR 90...",
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
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

    examples = ctk.CTkFrame(ai, fg_color="transparent")
    examples.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP1))

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
            sample if len(sample) < 28 else sample[:25] + "…",
            (lambda t=sample: fill_example(t)),
            kind="ghost",
            height=theme.BTN_SM,
        ).pack(side="left", padx=(0, theme.SP1), pady=(0, theme.SP1))

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
        height=theme.BTN_MD,
        width=128,
    )
    propose_button.pack(side="left", padx=(theme.SP2, 0))
    try:
        propose_button.configure(
            fg_color=CODEX_PURPLE,
            hover_color=hover_for(CODEX_PURPLE),
            text_color=theme.TEXT,
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

    for warning in ed.get("warnings") or ():
        muted_label(host, f"Warning: {warning}", size=11).pack(
            anchor="w", padx=theme.SP3, pady=(0, theme.SP1)
        )


def _build_draft_actions(root: Any, svc: Any, ed: dict[str, Any]) -> None:
    """Blocked Apply stays visible; Review is also on this tab, not only the dock."""
    if not ed["has_changes"]:
        return
    state = svc.store.snapshot()
    blocked = editor_view(state).get("blocked_reason") or ""
    bar = panel(root, level=1)
    bar.pack(fill="x", pady=(theme.SP1, 0))
    inner = ctk.CTkFrame(bar, fg_color="transparent")
    inner.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    left = ctk.CTkFrame(inner, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)
    text_label(
        left,
        f"Staged changes ({ed['dirty_count']})",
        size=13,
        bold=True,
        color=CODEX_PURPLE,
    ).pack(anchor="w")
    muted_label(
        left,
        "You have changes ready to review. Nothing is written to the game yet.",
        size=11,
    ).pack(anchor="w")
    if blocked:
        muted_label(
            left,
            f"Apply is currently unavailable: {blocked}",
            size=11,
            color=theme.WARNING,
        ).pack(anchor="w", pady=(2, 0))
    actions = ctk.CTkFrame(inner, fg_color="transparent")
    actions.pack(side="right")
    from .target_picker import _reset

    button(
        actions,
        "Reset All",
        lambda: _reset(svc),
        kind="ghost",
        height=theme.BTN_MD,
    ).pack(side="left", padx=(0, theme.SP2))
    button(
        actions,
        "Review changes",
        lambda: _open_review(svc),
        kind="accent",
        height=theme.BTN_MD,
        width=150,
    ).pack(side="left")
    # Concept "Apply to Game" is intentionally omitted — Apply lives in Review.


def _open_review(svc: Any) -> None:
    """Open the one mandatory review sheet, with an honest non-GUI fallback."""
    review = getattr(getattr(svc, "ui", None), "review", None)
    if callable(review) and review():
        return
    set_status(svc, "Changes are staged. Open Review in the bottom Changes bar to inspect them.")
