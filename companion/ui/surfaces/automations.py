"""Automation workspace — quick Career boosts with an honest visible queue.

V1's useful idea was *queue now, keep clicking*: submitting an operation must
never make the user wait for the previous one to finish in FC.  V2 already has
the safer version of that queue (one protocol job = one immutable result), so
this surface renders the shared JobsState rather than maintaining a second,
race-prone list of files.

The first view has one obvious decision: prepare the current squad for the
next match. Individual boosts, queue administration and specialist tools stay
available, but appear only when the player asks for them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from ...app import events as E
from ...app.commands.apply import submit_job
from ...app.commands.squad import sync_squad, sync_status_text
from ...app.presenters import jobs_view, liveness_view
from ...domain.automation_guide import (
    preflight_team_scope,
    trusted_cached_teamid,
)
from ...domain.job import JobValidationError
from ...domain.pack_library import (
    PackAction,
    actions as pack_actions,
    build_job as build_pack_job,
    load_v1_packs,
    quick_rail,
)
from ...domain.profile_library import (
    action_for as profile_action_for,
    actions as profile_actions,
    build_job as build_profile_job,
)
from .. import theme
from ..widgets.primitives import button, eyebrow, muted_label, panel, pill, set_disabled, text_label
from ._common import section_header, set_status, surface_root

try:  # pragma: no cover - imported only by the desktop surface
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


# These are the V1 boost controls people actually used.  Each is a proven
# one-shot ``career.set`` host call in the V2 worker, not a stock-script
# placeholder or a recurring promise.  FC26 LE has no compatible getter for
# these transient Career values, so completion means the host accepted calls,
# not a database read-back verification.
PRIORITY_PROFILES: tuple[tuple[str, str, str, str], ...] = (
    ("full_fitness", "Fitness", "100", "Set the current senior squad to match fitness."),
    ("full_sharpness", "Sharpness", "100", "Set the current senior squad sharpness."),
    ("full_form", "Form", "100", "Set the current senior squad form."),
    ("full_morale", "Morale", "100", "Set the current senior squad morale."),
)

# Prominent V1-inspired combined actions. Everything else that is verified
# remains reachable from the compact chooser below without becoming a wall of
# equally-weighted cards.
PRIMARY_PACK_IDS: tuple[str, ...] = (
    "squad_boost",
    "matchday",
    "morale_day",
    "match_ready",
)

# Optional-tool shortcut cards drawn from real native inventory only.
_FEATURED_OPTIONAL_IDS: tuple[str, ...] = (
    "growth_sync",
    "ping",
    "signing_settle",
    "club_refresh",
)

# Prepare-squad hero metrics (squad_boost_pack values — not individual morale).
_PREPARE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("Fitness", "100", theme.SUCCESS),
    ("Sharpness", "100", theme.ACCENT),
    ("Form", "100", theme.ACCENT),
    ("Morale", "120", theme.WARNING),
)

# Parsed profiles.json with mtime invalidation — keep json.loads off rebuilds.
_PROFILES_CACHE: dict[str, Any] = {"path": "", "mtime": None, "data": None}


def _read_profiles_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _profiles_from_cache(path: Path) -> dict[str, Any] | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if (
        _PROFILES_CACHE.get("path") == str(path)
        and _PROFILES_CACHE.get("mtime") == mtime
        and _PROFILES_CACHE.get("data") is not None
    ):
        cached = _PROFILES_CACHE["data"]
        return cached if isinstance(cached, dict) else {}
    return None


def _store_profiles_cache(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    _PROFILES_CACHE["path"] = str(path)
    _PROFILES_CACHE["mtime"] = mtime
    _PROFILES_CACHE["data"] = data
    return data


def _load_profiles_sync(svc: Any) -> dict[str, Any]:
    path = Path(svc.paths.profiles_file)
    cached = _profiles_from_cache(path)
    if cached is not None:
        return cached
    return _store_profiles_cache(path, _read_profiles_file(path))


def _ensure_profiles_async(
    svc: Any,
    vm: dict[str, Any] | None,
    on_ready: Callable[[dict[str, Any]], None],
) -> None:
    """Serve warm cache immediately; cold-load via executor then call ``on_ready``."""
    path = Path(svc.paths.profiles_file)
    cached = _profiles_from_cache(path)
    if cached is not None:
        on_ready(cached)
        return
    view = vm if isinstance(vm, dict) else {}
    if view.get("_profiles_loading"):
        return
    view["_profiles_loading"] = True

    def work(_token: Any) -> None:
        data = _read_profiles_file(path)

        def done() -> None:
            view["_profiles_loading"] = False
            _store_profiles_cache(path, data)
            on_ready(data)

        svc.executor.on_ui_thread(done)

    try:
        svc.executor.submit("automations.load_profiles", work)
    except Exception:
        view["_profiles_loading"] = False
        on_ready(_load_profiles_sync(svc))


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    """Build a non-scroll-first Automation workspace.

    Every run submits immediately and follows in the background.  Rebuilds on
    JobsState changes (declared in ``shell.py``) make the queue truthful without
    polling or duplicating the transport queue on the UI thread.
    """
    root = surface_root(parent)
    state = svc.store.snapshot()
    live = liveness_view(state.bridge.liveness)

    section_header(
        root,
        "Automations",
        subtitle="Prepare the current Career squad now. FC applies each action at a safe screen.",
    )

    view = vm if isinstance(vm, dict) else {}
    _squad_status(root, svc, state)
    # The controls people came here to use must never move below a large queue
    # after an earlier player-edit session.  Detail belongs in Activity; this
    # page stays a compact boost workstation.
    _priority_workspace(root, svc, state, live)
    queue_host = ctk.CTkFrame(root, fg_color="transparent")
    queue_host.pack(fill="x")

    def refresh_automation_queue() -> None:
        for child in list(queue_host.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        _queue_panel(queue_host, svc, svc.store.snapshot())

    refresh_automation_queue()
    root.refresh_automation_queue = refresh_automation_queue
    _secondary_workspace(root, svc, state, live, rail=quick_rail(), vm=view)
    return root


def _squad_status(parent: Any, svc: Any, state: Any) -> None:
    """Squad readiness strip — trust hint without inventing competition/crest data."""
    squad = state.squad
    n_players = len(squad.players or ())
    hint = trusted_cached_teamid(state, now=svc.clock.now())
    strip = panel(parent, level=1)
    strip.pack(fill="x", pady=(0, theme.SP2))
    row = ctk.CTkFrame(strip, fg_color="transparent")
    row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)

    status = ctk.CTkFrame(row, fg_color="transparent")
    status.pack(side="left", fill="x", expand=True)

    if hint is not None:
        title = f"Squad ready: {n_players} players"
        badge = "Fresh Career squad"
        badge_color = theme.SUCCESS
        detail = "All key metrics are available. You can run automations now."
    elif n_players:
        title = f"Squad cached: {n_players} players"
        badge = "FC will use the currently loaded squad"
        badge_color = theme.WARNING
        detail = "Local cache is present but not a fresh trusted team hint."
    else:
        title = "Using the squad currently loaded in FC"
        badge = "No local read needed"
        badge_color = theme.WARNING
        detail = "Empty local cache does not block Career boosts when the bridge is armed."

    head = ctk.CTkFrame(status, fg_color="transparent")
    head.pack(anchor="w", fill="x")
    text_label(head, title, size=12, bold=True).pack(side="left")
    muted_label(head, badge, size=10, color=badge_color).pack(side="left", padx=theme.SP2)
    muted_label(status, detail, size=10, wraplength=720, justify="left").pack(
        anchor="w", pady=(theme.SP1, 0)
    )

    actions = ctk.CTkFrame(row, fg_color="transparent")
    actions.pack(side="right", padx=(theme.SP2, 0))
    if not n_players:
        button(
            actions,
            "Open Club",
            lambda: svc.ui.navigate("club") if getattr(svc, "ui", None) else None,
            kind="ghost",
            height=theme.BTN_MD,
            width=92,
        ).pack(side="left", padx=(0, theme.SP1))
    refresh_col = ctk.CTkFrame(actions, fg_color="transparent")
    refresh_col.pack(side="left")
    button(
        refresh_col,
        "Refresh Squad",
        lambda: _refresh_squad(svc),
        kind="ghost",
        height=theme.BTN_MD,
        width=108,
        disabled_reason="" if state.bridge.armed else state.bridge.liveness.message,
    ).pack(anchor="e")
    muted_label(refresh_col, "Re-scan your squad for latest data.", size=9).pack(
        anchor="e", pady=(2, 0)
    )


def _priority_workspace(parent: Any, svc: Any, state: Any, live: dict[str, Any]) -> None:
    card = panel(parent, level=1)
    card.pack(fill="x", pady=(0, theme.SP2))

    head = ctk.CTkFrame(card, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP1))
    eyebrow(head, "Match ready").pack(side="left")
    muted_label(head, "Your next-match shortcut", size=10).pack(side="left", padx=theme.SP2)
    pill(head, "Recommended", tone="info").pack(side="right")

    hero = ctk.CTkFrame(card, fg_color=theme.CARD, corner_radius=theme.R_SM)
    hero.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    hero_inner = ctk.CTkFrame(hero, fg_color="transparent")
    hero_inner.pack(fill="x", padx=theme.SP3, pady=theme.SP3)

    copy = ctk.CTkFrame(hero_inner, fg_color="transparent")
    copy.pack(side="left", fill="x", expand=True)
    text_label(copy, "Prepare Squad", size=18, bold=True).pack(anchor="w")
    muted_label(
        copy,
        "Get your entire squad match ready with one click. Sets Fitness to 100, "
        "Sharpness to 100, Form to 100 and Morale to 120 for the current Career senior squad.",
        size=10,
        wraplength=560,
        justify="left",
    ).pack(anchor="w", pady=(theme.SP1, theme.SP2))

    metrics = ctk.CTkFrame(copy, fg_color="transparent")
    metrics.pack(anchor="w", fill="x")
    for index, (label, value, color) in enumerate(_PREPARE_METRICS):
        metrics.grid_columnconfigure(index, weight=1, uniform="prepare-metric")
        chip = ctk.CTkFrame(metrics, fg_color=theme.PANEL, corner_radius=theme.R_XS)
        chip.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else theme.SP1, 0))
        muted_label(chip, label, size=9, color=color).pack(anchor="w", padx=theme.SP2, pady=(theme.SP1, 0))
        text_label(chip, value, size=16, bold=True).pack(anchor="w", padx=theme.SP2, pady=(0, theme.SP1))

    cta = ctk.CTkFrame(hero_inner, fg_color="transparent")
    cta.pack(side="right", padx=(theme.SP3, 0))
    button(
        cta,
        "Prepare Squad",
        lambda: _run_pack(svc, "squad_boost"),
        kind="primary",
        height=theme.BTN_LG,
        width=160,
        disabled_reason=_automation_disabled_reason(state, "squad_boost"),
    ).pack(anchor="e")
    muted_label(cta, "Queues immediately — no review step.", size=9).pack(anchor="e", pady=(theme.SP1, 0))

    muted_label(
        card,
        "Runs once for the current Career senior squad. Keep playing while FC applies it at a safe screen.",
        size=10,
        wraplength=900,
        justify="left",
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    details = ctk.CTkFrame(card, fg_color="transparent")
    grid = ctk.CTkFrame(details, fg_color="transparent")
    grid_visible = {"value": False}
    for column, (profile_id, title, value, description) in enumerate(PRIORITY_PROFILES):
        grid.grid_columnconfigure(column, weight=1, uniform="automation-priority")
        _priority_tile(
            grid,
            svc,
            state,
            live,
            profile_id=profile_id,
            title=title,
            value=value,
            description=description,
        ).grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else theme.SP1, 0))

    def toggle_individual() -> None:
        grid_visible["value"] = not grid_visible["value"]
        if grid_visible["value"]:
            details.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            grid.pack(fill="x", pady=(0, theme.SP2))
            customize.configure(text="Hide individual boosts")
        else:
            details.pack_forget()
            customize.configure(text="Customize individual boosts")

    controls = ctk.CTkFrame(card, fg_color="transparent")
    controls.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    customize = button(
        controls,
        "Customize individual boosts",
        toggle_individual,
        kind="ghost",
        height=theme.BTN_MD,
    )
    customize.pack(side="left")
    muted_label(
        controls,
        "Quick Actions — run individual squad boosts on all available senior players.",
        size=10,
    ).pack(side="left", padx=theme.SP2)

    packs = ctk.CTkFrame(details, fg_color="transparent")
    packs.pack(fill="x")
    eyebrow(packs, "Other matchday actions").pack(anchor="w", pady=(0, theme.SP1))
    muted_label(
        packs,
        "Combined packs from the existing quick rail. Still queue immediately.",
        size=10,
    ).pack(anchor="w", pady=(0, theme.SP1))
    pack_buttons = ctk.CTkFrame(packs, fg_color="transparent")
    pack_buttons.pack(fill="x")
    for pack_id, label, kind in (
        ("matchday", "Fitness + sharpness", "secondary"),
        ("morale_day", "Form + morale + sharpness", "secondary"),
        ("match_ready", "Fitness + sharpness + export", "ghost"),
    ):
        button(
            pack_buttons,
            label,
            lambda pid=pack_id: _run_pack(svc, pid),
            kind=kind,
            height=theme.BTN_MD,
            disabled_reason=_automation_disabled_reason(state, pack_id),
        ).pack(side="left", padx=(0, theme.SP1))


def _priority_tile(
    parent: Any,
    svc: Any,
    state: Any,
    live: dict[str, Any],
    *,
    profile_id: str,
    title: str,
    value: str,
    description: str,
) -> Any:
    del live
    tile = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=theme.R_SM)
    text_label(tile, title, size=12, bold=True).pack(anchor="w", padx=theme.SP2, pady=(theme.SP2, 0))
    muted_label(tile, f"Set all players to {value}", size=10).pack(anchor="w", padx=theme.SP2)
    if description:
        muted_label(tile, description, size=9, wraplength=180, justify="left").pack(
            anchor="w", padx=theme.SP2, pady=(2, 0)
        )
    button(
        tile,
        f"Queue {title.lower()}",
        lambda pid=profile_id: _run_profile(svc, pid),
        kind="secondary",
        height=theme.BTN_MD,
        disabled_reason=_automation_disabled_reason(state, profile_id),
    ).pack(anchor="w", padx=theme.SP2, pady=(theme.SP1, theme.SP2))
    return tile


def _automation_disabled_reason(state: Any, item_id: str) -> str:
    if not state.bridge.armed:
        return state.bridge.liveness.message
    return preflight_team_scope(state, item_id) or ""


def _queue_panel(parent: Any, svc: Any, state: Any) -> None:
    """A compact activity strip; detailed queue administration lives in Activity."""
    view = jobs_view(state)
    card = panel(parent, level=1)
    card.pack(fill="x", pady=(0, theme.SP2))
    head = ctk.CTkFrame(card, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    in_flight = len(view["active"])
    eyebrow(head, "Job Queue").pack(side="left")
    # Keep the word Activity in the source/UI for shell handoff + regression greps.
    muted_label(head, "Activity", size=10).pack(side="left", padx=(theme.SP1, 0))
    pill(
        head,
        f"{in_flight} waiting" if in_flight else "No actions waiting",
        tone="warn" if in_flight else "muted",
    ).pack(side="left", padx=theme.SP2)
    if view["history"]:
        muted_label(head, f"{len(view['history'])} recent result(s)", size=10).pack(side="left")
    button(
        head,
        "Open Activity",
        lambda: svc.ui.open_activity() if getattr(svc, "ui", None) else None,
        kind="secondary",
        height=theme.BTN_MD,
        width=108,
    ).pack(side="right")

    if view["active"] or view["history"]:
        management = ctk.CTkFrame(card, fg_color="transparent")
        management_visible = {"value": False}

        def toggle_management() -> None:
            management_visible["value"] = not management_visible["value"]
            if management_visible["value"]:
                management.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
                manage.configure(text="Hide queue controls")
            else:
                management.pack_forget()
                manage.configure(text="Manage queue")

        manage = button(head, "Manage queue", toggle_management, kind="ghost", height=theme.BTN_MD, width=104)
        manage.pack(side="right", padx=(0, theme.SP1))
        if view["history"]:
            button(
                management,
                "Clear finished",
                lambda: _clear_finished(svc),
                kind="ghost",
                height=theme.BTN_MD,
            ).pack(side="left", padx=(0, theme.SP1))
        if view["active"]:
            button(
                management,
                "Cancel waiting actions",
                lambda: _clear_all_queue(svc),
                kind="danger",
                height=theme.BTN_MD,
            ).pack(side="left")

    # Keep only the current item visible. The drawer owns row-level detail,
    # retry and destructive cleanup so they never compete with playing a match.
    current = list(view["active"])[:1]
    if current:
        item = current[0]
        stage = dict(item.get("stage") or {})
        chip = str(stage.get("chip") or "Queued")
        detail = str(stage.get("detail") or item.get("detail") or "waiting for a safe Career screen")
        row = ctk.CTkFrame(card, fg_color=theme.CARD, corner_radius=theme.R_XS)
        row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SP2, pady=theme.SP2)
        text_label(inner, str(item.get("label") or "queued action"), size=11, bold=True).pack(
            side="left"
        )
        pill(inner, chip, tone="warn" if chip != "Queued" else "muted").pack(
            side="left", padx=theme.SP2
        )
        muted_label(inner, detail, size=10, wraplength=640).pack(side="left", fill="x", expand=True)
    elif not view["history"]:
        muted_label(
            card,
            "No jobs waiting. Queued automations appear here with their real status from Activity.",
            size=10,
        ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))


def _queue_row(parent: Any, svc: Any, item: dict[str, Any], *, compact: bool = False) -> None:
    row = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=theme.R_XS)
    row.pack(fill="x", pady=(0, 2))
    outcome = dict(item.get("outcome") or {})
    tone = str(outcome.get("tone") or "muted")
    text_label(
        row,
        str(outcome.get("label") or "Queued"),
        size=10,
        bold=True,
        color=theme.tone_fg(tone),
        width=74,
    ).pack(side="left", padx=(theme.SP2, 0), pady=4)
    mid = ctk.CTkFrame(row, fg_color="transparent")
    mid.pack(side="left", fill="x", expand=True, pady=4)
    text_label(mid, str(item.get("label") or item.get("job_id") or "job"), size=11).pack(anchor="w")
    if not compact:
        muted_label(mid, str(item.get("detail") or item.get("job_id") or ""), size=10, wraplength=700).pack(anchor="w")
    elif item.get("active"):
        muted_label(
            mid,
            str((item.get("stage") or {}).get("detail") or "Waiting for a safe Career event"),
            size=10,
        ).pack(anchor="w")
    job_id = str(item.get("job_id") or "")
    if item.get("retry_suggested") and automation_retryable(svc, job_id):
        button(
            row,
            "Retry",
            lambda jid=job_id: retry_recorded_automation(svc, jid),
            kind="secondary",
            height=theme.BTN_MD,
            width=64,
        ).pack(side="right", padx=theme.SP2, pady=4)


def _clear_finished(svc: Any) -> None:
    svc.store.dispatch(E.JobHistoryCleared())
    set_status(svc, "Finished jobs cleared from this queue. The Activity audit is still saved.")


def _clear_all_queue(svc: Any) -> None:
    from ...app.commands.queue import clear_all

    count = len(jobs_view(svc.store.snapshot())["active"])
    if not _confirm_clear_queue(count):
        set_status(svc, "Queued jobs were kept.")
        return
    clear_all(svc)


def _confirm_clear_queue(count: int) -> bool:
    """Name the cancellation scope before removing pending work."""
    if count <= 0:
        return False
    try:
        from tkinter import messagebox

        noun = "job" if count == 1 else "jobs"
        return bool(messagebox.askokcancel(
            "Cancel queued jobs",
            f"Cancel {count} waiting {noun}? Jobs already claimed by FC are kept for safety.",
        ))
    except Exception:
        return False


def _secondary_workspace(
    parent: Any,
    svc: Any,
    state: Any,
    live: dict[str, Any],
    *,
    rail: Iterable[PackAction],
    vm: dict[str, Any] | None = None,
) -> None:
    """Put specialist one-shot tools behind an intentional disclosure."""
    del live
    box = panel(parent, level=1)
    box.pack(fill="x", pady=(0, theme.SP2))
    head = ctk.CTkFrame(box, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    eyebrow(head, "Optional tools").pack(side="left")
    muted_label(head, "Additional squad tools and specialist automations", size=10).pack(
        side="left", padx=theme.SP2
    )
    # Leave the rest of the workspace deliberately quiet after the one
    # match-day decision.  Without this sentence the short collapsed card
    # reads like a section that failed to load rather than a deliberate
    # progressive disclosure.
    muted_label(
        box,
        "Nothing else is needed for match day. Expand this only for a specialist action.",
        size=10,
        wraplength=900,
        justify="left",
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    # ``rail`` deliberately remains an argument: it documents that this
    # compact fallback is part of the same V1-derived action inventory.  The
    # priority controls own its matchday items, so do not duplicate them here.
    del rail
    content = ctk.CTkFrame(box, fg_color="transparent")
    loading = muted_label(head, "Loading tools…", size=10)
    loading.pack(side="right")
    reveal_holder: dict[str, Any] = {"btn": None}

    def mount_native(raw: dict[str, Any]) -> None:
        try:
            loading.pack_forget()
        except Exception:
            pass
        native = [
            (
                f"{_automation_scope(item_id)} | {label}",
                kind,
                item_id,
                " | ".join(part for part in (_automation_scope(item_id), description) if part),
            )
            for label, kind, item_id, description in _native_items(svc, profiles=raw)
        ]
        if not native:
            muted_label(head, "No additional actions available", size=10).pack(side="right")
            return

        display = {
            label: (kind, item_id, description)
            for label, kind, item_id, description in native
        }
        selected = ctk.StringVar(value=native[0][0])
        for child in list(content.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

        by_id = {item_id: (kind, description) for _label, kind, item_id, description in native}
        pack_labels = {item.id: item for item in pack_actions(load_v1_packs())}
        featured = [pid for pid in _FEATURED_OPTIONAL_IDS if pid in by_id]
        if featured:
            eyebrow(content, "Featured optional actions").pack(anchor="w", pady=(0, theme.SP1))
            featured_row = ctk.CTkFrame(content, fg_color="transparent")
            featured_row.pack(fill="x", pady=(0, theme.SP2))
            for index, pack_id in enumerate(featured):
                kind, description = by_id[pack_id]
                item = pack_labels.get(pack_id)
                short_label = item.label if item is not None else pack_id
                blurb = (item.description if item is not None else "") or description
                featured_row.grid_columnconfigure(index, weight=1, uniform="optional-featured")
                tile = ctk.CTkFrame(featured_row, fg_color=theme.CARD, corner_radius=theme.R_SM)
                tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else theme.SP1, 0))
                text_label(tile, short_label, size=11, bold=True).pack(
                    anchor="w", padx=theme.SP2, pady=(theme.SP2, 0)
                )
                muted_label(
                    tile,
                    blurb,
                    size=9,
                    wraplength=200,
                    justify="left",
                ).pack(anchor="w", padx=theme.SP2, pady=(2, 0))
                run = (
                    (lambda pid=pack_id: _run_pack(svc, pid))
                    if kind == "pack"
                    else (lambda pid=pack_id: _run_profile(svc, pid))
                )
                button(
                    tile,
                    "Queue job",
                    run,
                    kind="secondary",
                    height=theme.BTN_MD,
                    disabled_reason=_automation_disabled_reason(state, pack_id),
                ).pack(anchor="w", padx=theme.SP2, pady=(theme.SP1, theme.SP2))

        muted_label(content, "All native optional actions", size=10).pack(anchor="w", pady=(0, theme.SP1))
        row = ctk.CTkFrame(content, fg_color="transparent")
        row.pack(fill="x", pady=(0, theme.SP1))
        picker = ctk.CTkOptionMenu(
            row,
            values=list(display),
            variable=selected,
            width=350,
            fg_color=theme.CARD,
            button_color=theme.BORDER,
            button_hover_color=theme.CARD_HOVER,
        )
        picker.pack(side="left")
        detail = muted_label(content, native[0][3], size=10, wraplength=900, justify="left")

        def run_selected() -> None:
            kind, item_id, _description = display.get(selected.get(), ("", "", ""))
            if kind == "pack":
                _run_pack(svc, item_id)
            elif kind == "profile":
                _run_profile(svc, item_id)

        action_button = button(
            row,
            "Queue selected action",
            run_selected,
            kind="secondary",
            height=theme.BTN_MD,
            width=130,
            disabled_reason=_automation_disabled_reason(state, native[0][2]),
        )
        action_button.pack(side="left", padx=theme.SP2)

        def choose(label: str) -> None:
            _kind, item_id, description = display.get(label, ("", "", ""))
            try:
                detail.configure(text=description)
                set_disabled(action_button, _automation_disabled_reason(state, item_id))
            except Exception:
                pass

        picker.configure(command=choose)
        detail.pack(anchor="w", pady=(0, theme.SP2))

        expanded = {"value": False}

        def toggle_tools() -> None:
            expanded["value"] = not expanded["value"]
            if expanded["value"]:
                content.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
                reveal.configure(text="Hide tools")
            else:
                content.pack_forget()
                reveal.configure(text="Show tools")

        if reveal_holder["btn"] is None:
            reveal = button(head, "Show tools", toggle_tools, kind="ghost", height=theme.BTN_MD, width=92)
            reveal.pack(side="right")
            reveal_holder["btn"] = reveal
        else:
            reveal = reveal_holder["btn"]
            reveal.configure(command=toggle_tools)

    _ensure_profiles_async(svc, vm, mount_native)


def _native_items(
    svc: Any, *, profiles: dict[str, Any] | None = None
) -> Iterable[tuple[str, str, str, str]]:
    """One compact selectable inventory; duplicate priority buttons stay out."""
    seen: set[tuple[str, str]] = set()
    priority = {profile_id for profile_id, *_ in PRIORITY_PROFILES}
    flow_ids = set(PRIMARY_PACK_IDS)
    for item in pack_actions(load_v1_packs()):
        if item.mode != "native" or item.id in flow_ids:
            continue
        key = ("pack", item.id)
        if key not in seen:
            seen.add(key)
            yield (f"Workflow · {item.label}", "pack", item.id, item.description or item.reason)
    raw = profiles if profiles is not None else _load_profiles_sync(svc)
    for item in profile_actions(raw):
        if item.mode != "native" or item.id in priority:
            continue
        key = ("profile", item.id)
        if key not in seen:
            seen.add(key)
            # Some V1 labels say "Daily" although V2 deliberately maps them
            # to a one-shot typed action.  Always show the migration reason so
            # the compact picker cannot quietly promise recurring automation.
            detail = " · ".join(part for part in (item.description, item.reason) if part)
            yield (f"Profile · {item.label}", "profile", item.id, detail)


def _automation_scope(item_id: str, category: str = "") -> str:
    """Player-facing scope label; a generic picker must not hide who changes."""
    ident = str(item_id).lower()
    if ident == "ping" or category == "diag":
        return "Diagnostic - no save changes"
    if "export" in ident:
        return "Data export - no save changes"
    if "growth" in ident:
        return "Development - current senior squad"
    if ident.endswith("_team") or "team" in ident:
        return "Team-wide edit - current Career team"
    return "Current senior squad - one-shot"


def _requires_scope_confirmation(item_id: str) -> bool:
    ident = str(item_id).lower()
    return ident.endswith("_team") or ident == "growth_sync"


def _confirm_scope(item_id: str) -> bool:
    """Matchday stays fast; broad player-table actions require confirmation."""
    if not _requires_scope_confirmation(item_id):
        return True
    try:
        from tkinter import messagebox

        return bool(messagebox.askokcancel(
            "Confirm team-wide action",
            f"{_automation_scope(item_id)}. This is a one-shot queued action. Continue?",
        ))
    except Exception:
        return False


def _pending_count(svc: Any) -> int:
    raw = _load_profiles_sync(svc)
    return sum(1 for item in profile_actions(raw) if item.mode == "pending") + sum(
        1 for item in pack_actions(load_v1_packs()) if item.mode == "pending"
    )


def _refresh_squad(svc: Any) -> None:
    if not svc.store.snapshot().bridge.armed:
        set_status(svc, f"Refresh squad not queued: {svc.store.snapshot().bridge.liveness.message}")
        return
    try:
        set_status(svc, sync_status_text(sync_squad(svc)))
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not queue squad refresh: {exc}")


def _run_pack(svc: Any, pack_id: str) -> None:
    items = {item.id: item for item in pack_actions(load_v1_packs())}
    item = items.get(pack_id)
    if item is None:
        set_status(svc, f"Unknown automation: {pack_id}")
        return
    _submit_automation(svc, item.label, item.id, lambda teamid: build_pack_job(item.id, teamid=teamid))


def _run_profile(svc: Any, profile_id: str) -> None:
    raw = _load_profiles_sync(svc)
    item = profile_action_for(profile_id, raw)
    if item is None or item.mode != "native":
        set_status(svc, f"Unknown automation profile: {profile_id}")
        return
    _submit_automation(svc, item.label, item.id, lambda teamid: build_profile_job(item.id, teamid=teamid))


def automation_retryable(svc: Any, job_id: str) -> bool:
    """Only retry a durable job whose origin maps to a known typed action."""
    if not job_id or getattr(svc, "db", None) is None:
        return False
    try:
        record = svc.db.job_record(job_id)
        raw = record.job_json if record is not None else None
        origin = str(raw.get("origin") or "") if isinstance(raw, dict) else ""
    except Exception:
        return False
    return origin.startswith("ui.packs.") or origin.startswith("ui.profiles.")


def retry_recorded_automation(svc: Any, job_id: str) -> None:
    """Rebuild a failed automation through today's validated registry.

    Raw historical JSON is never replayed: the new job resolves FC's current
    senior squad and cannot inherit a former club's cached team id.
    """
    if getattr(svc, "db", None) is None:
        set_status(svc, "Retry unavailable: no local job audit is open.")
        return
    try:
        record = svc.db.job_record(job_id)
        raw = record.job_json if record is not None else None
        origin = str(raw.get("origin") or "") if isinstance(raw, dict) else ""
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Retry unavailable: {exc}")
        return
    if origin.startswith("ui.packs."):
        _run_pack(svc, origin.removeprefix("ui.packs."))
    elif origin.startswith("ui.profiles."):
        _run_profile(svc, origin.removeprefix("ui.profiles."))
    else:
        set_status(svc, "Retry is unavailable for this historical job.")


def _submit_automation(
    svc: Any,
    label: str,
    item_id: str,
    builder: Callable[[int | None], Any],
) -> None:
    state = svc.store.snapshot()
    if not state.bridge.armed:
        set_status(svc, f"{label} not queued: {state.bridge.liveness.message}")
        return
    refusal = preflight_team_scope(state, item_id)
    if refusal:
        set_status(svc, refusal)
        return
    if not _confirm_scope(item_id):
        set_status(svc, "Team-wide action was not queued.")
        return
    teamid = trusted_cached_teamid(state, now=svc.clock.now())
    try:
        job_id = submit_job(
            svc,
            builder(teamid),
            # Unlike V1, do not spawn a 45-second waiter for every click. The
            # shell already collects exact per-job result files each second;
            # four queued clicks used to starve that shared four-worker pool.
            follow=False,
        )
        hint = f" for team {teamid}" if teamid is not None else " using FC's current team"
        set_status(
            svc,
            f"{label} queued ({job_id[:8]}…){hint}. Open Team Management or return to the Career hub to let FC run it safely."
        )
    except JobValidationError as exc:
        set_status(svc, f"{label} refused: {exc}")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"{label} could not queue: {exc}")
