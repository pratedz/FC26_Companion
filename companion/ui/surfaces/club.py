"""Club — home surface: setup checklist or squad board.

Answers: "What is my squad, and is it ready?"
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Sequence

from ...app.presenters import liveness_view, squad_view
from .. import theme
from ..widgets.primitives import button, ensure_ctk, eyebrow, muted_label, panel, pill, set_disabled, text_label
from ..widgets.states import loading_row, skeleton_table
from ..widgets.table import Column, DataGrid, TableModel
from ._common import (
    cap_prompt_rows,
    filter_squad_rows,
    identity_row,
    monogram,
    section_header,
    set_status,
    squad_board_row,
    surface_root,
)

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    view = vm if isinstance(vm, dict) else {}
    view.setdefault("squad_query", "")
    view.setdefault("checked_ids", [])
    state = svc.store.snapshot()
    live = liveness_view(state.bridge.liveness)
    # Explicit clock so age/TTL captions are real (tests inject fixed now via clock).
    now = svc.clock.now() if getattr(svc, "clock", None) is not None else None
    squad = squad_view(state, now=now)
    worker = _worker_status()

    sync_pending = any(
        (not view.done) and view.label == "Sync squad"
        for view in state.jobs.active.values()
    )
    phase = club_phase(has_players=bool(squad["players"]), sync_pending=sync_pending)
    from ...app.commands.sync_trace import sync_debug_enabled, sync_log

    if sync_debug_enabled():
        live_raw = state.bridge.liveness
        sync_log(
            "CLUB",
            f"phase={phase} players={len(squad['players'])} stale={squad.get('stale')} "
            f"sync_active={sync_pending} session_id={live_raw.session_id or '-'} "
            f"save_uid={getattr(live_raw, 'save_uid', '') or '-'} "
            f"hub_ready={bool((live_raw.capabilities or {}).get('hub_ready'))} "
            f"armed={live_raw.armed} teamid={state.squad.teamid if state.squad.teamid is not None else '-'}",
        )
    # A disconnected bridge makes cached data stale, not worthless. Preserve
    # the last known squad instead of replacing it with first-run setup.
    if phase == "reading":
        section_header(
            root,
            "Reading your squad",
            subtitle="Keep Career Mode open. The grid appears when the live export finishes.",
        )
        banner = panel(root, level=1)
        banner.pack(fill="x", pady=(0, theme.SP2))
        loading_row(banner, "Reading squad from Career Mode…").pack(
            anchor="w", padx=theme.SP3, pady=theme.SP2
        )
        skeleton_table(root).pack(fill="both", expand=True, pady=(theme.SP2, 0))
    elif phase == "setup":
        _build_setup(root, svc, live, squad, worker)
    else:
        _build_squad(
            root, svc, live, squad, worker, view, sync_pending=sync_pending,
        )
    return root


def club_phase(*, has_players: bool, sync_pending: bool) -> str:
    """setup, reading, or board. Shared by build() and tests."""
    if has_players:
        return "board"
    return "reading" if sync_pending else "setup"


def _build_setup(
    root: Any, svc: Any, live: dict, squad: dict, worker: dict[str, Any]
) -> None:
    del squad
    section_header(
        root,
        "Club",
        subtitle="What is my squad, and is it ready?",
    )
    worker_ok = bool(worker.get("installed"))
    ready = bool(live["armed"])
    game_open = _fc_is_running()
    if ready:
        next_title = "Read the squad from your active save"
        next_copy = (
            "Your save is connected. Read the current squad to unlock player edits and matchday boosts."
        )
    elif game_open or worker_ok:
        next_title = "Connect from this Career save"
        next_copy = (
            "FC 26 can stay open. Copy the connection script, then in Live Editor open "
            "Features > Lua Engine, paste, and Execute. "
            "If Lua Engine says the worker is not loaded, restart FC 26 once."
        )
    else:
        next_title = "Install the Companion worker"
        next_copy = (
            "Install the worker once, then start FC 26 through Live Editor and open Career Mode. "
            "Your data stays local until the save is ready."
        )

    _readiness_chips(
        root,
        worker_ok=worker_ok,
        game_open=game_open,
        live=live,
        squad_ready=False,
    )

    hero = panel(root, level=1)
    hero.pack(fill="x", pady=(0, theme.SP3))
    hero_top = ctk.CTkFrame(hero, fg_color="transparent")
    hero_top.pack(fill="x", padx=theme.SP4, pady=(theme.SP3, theme.SP2))
    copy = ctk.CTkFrame(hero_top, fg_color="transparent")
    copy.pack(side="left", fill="x", expand=True)
    eyebrow(copy, "Next step").pack(anchor="w")
    text_label(copy, next_title, size=16, bold=True).pack(anchor="w", pady=(2, 0))
    muted_label(copy, next_copy, size=11).pack(anchor="w", pady=(2, 0))
    if ready:
        button(
            hero_top, "Read my squad", lambda: _read_squad(svc),
            kind="primary", height=theme.BTN_LG, width=150,
        ).pack(side="right", padx=(theme.SP3, 0))
    elif game_open or worker_ok:
        button(
            hero_top, "Connect in this Career", lambda: _repair_connection(svc),
            kind="primary", height=theme.BTN_LG, width=190,
        ).pack(side="right", padx=(theme.SP3, 0))
        button(
            hero_top, "Check connection", lambda: _refresh_connection(svc),
            kind="ghost", height=theme.BTN_LG, width=142,
        ).pack(side="right", padx=(theme.SP2, 0))
    else:
        button(
            hero_top, "Install Companion worker", lambda: _install_worker(svc),
            kind="primary", height=theme.BTN_LG, width=190,
            disabled_reason=(
                "Worker installation is disabled while this visual-test app root is active."
                if _is_temporary_app_root(svc) else ""
            ),
        ).pack(side="right", padx=(theme.SP3, 0))

    guide = panel(root, level=1)
    guide.pack(fill="x")
    eyebrow(guide, "What happens next").pack(
        anchor="w", padx=theme.SP3, pady=(theme.SP2, 0)
    )
    muted_label(
        guide,
        "Read Squad only copies the active Career squad into this Companion. Nothing is changed in FC 26 until you review and apply a later edit.",
        size=11,
    ).pack(anchor="w", padx=theme.SP3, pady=(theme.SP1, theme.SP3))


def _readiness_chips(
    parent: Any,
    *,
    worker_ok: bool,
    game_open: bool,
    live: dict,
    squad_ready: bool,
) -> Any:
    """Compact readiness row — real worker/game/LE state only."""
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(0, theme.SP2))
    armed = bool(live.get("armed"))
    pill_name = str(live.get("pill") or "")
    career_ok = armed and pill_name in ("LIVE", "ARMED", "WAITING")
    conn_ok = armed and pill_name in ("LIVE", "ARMED")
    conn_label = pill_name if armed else "Offline"
    items = (
        ("Worker", "Installed" if worker_ok else "Needs install", worker_ok),
        ("FC 26", "Running" if game_open else "Not detected", game_open),
        ("Career Mode", "Ready" if career_ok else "Waiting", career_ok),
        ("Connection", conn_label if conn_ok else ("Waiting" if armed else "Offline"), conn_ok),
    )
    if squad_ready:
        items = items  # squad readiness shown via board caption, not a fifth chip
    for i, (title, detail, done) in enumerate(items):
        chip = panel(row, level=1)
        chip.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else theme.SP2, 0))
        inner = ctk.CTkFrame(chip, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
        mark = "✓" if done else "·"
        text_label(
            inner, mark, size=13, bold=True,
            color=theme.SUCCESS if done else theme.MUTED, width=18,
        ).pack(side="left")
        words = ctk.CTkFrame(inner, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True)
        text_label(words, title, size=12, bold=True).pack(anchor="w")
        muted_label(words, detail, size=10).pack(anchor="w")
    return row


def _setup_status(
    parent: Any, number: str, title: str, detail: str, done: bool, *, last: bool = False
) -> None:
    """One first-run step row inside the hero panel — not a nested card."""
    del last
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(0, theme.SP1))
    marker = "✓" if done else number
    text_label(
        row, marker, size=13, bold=True,
        color=theme.SUCCESS if done else theme.MUTED, width=24,
    ).pack(side="left")
    words = ctk.CTkFrame(row, fg_color="transparent")
    words.pack(side="left", fill="x", expand=True)
    text_label(words, title, size=13, bold=True).pack(anchor="w")
    muted_label(words, detail, size=11).pack(anchor="w")


def _fc_is_running() -> bool:
    """True when FC 26 is open, so install must not be the only next step."""
    try:
        from ...platform import procs

        return procs.find_game() is not None
    except Exception:
        return False


def _worker_status() -> dict[str, Any]:
    """Inspect the actual LE data directory; never infer install from packaging."""
    try:
        from ...platform import le_install

        return le_install.status()
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "error": str(exc)}


def _is_temporary_app_root(svc: Any) -> bool:
    """Never let a disposable visual-test root repoint the real LE worker."""
    try:
        root = Path(svc.paths.root).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        root.relative_to(temp_root)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _copy_ui_clipboard(text: str) -> bool:
    """Copy from the existing Tk UI thread; no shell/process side effects."""
    try:
        import tkinter

        root = getattr(tkinter, "_default_root", None)
        if root is None:
            return False
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return True
    except Exception:
        return False


def _repair_connection(svc: Any) -> None:
    """Repair only the queue target, then prepare a no-drain Lua reconnect."""
    if _is_temporary_app_root(svc):
        set_status(
            svc,
            "Connection repair is disabled in visual-test mode so it cannot change your Live Editor worker.",
        )
        return
    try:
        from ...platform import le_install

        report = le_install.repair_queue_config(queue_dir=svc.paths.queue)
        copied = _copy_ui_clipboard(
            le_install.reconfigure_current_worker_snippet(queue_dir=svc.paths.queue)
        )
        if copied:
            change = "Worker queue restored" if report.get("changed") else "Worker queue is correct"
            set_status(
                svc,
                f"{change}. Script copied: Live Editor > Features > Lua Engine > paste > Execute, then Check connection.",
            )
        else:
            set_status(
                svc,
                "Worker queue restored, but clipboard copy failed. Open Settings for the connection script, then execute it in Live Editor's Lua Engine.",
            )
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not prepare connection repair: {exc}")


def _step_row(
    parent: Any,
    n: int,
    title: str,
    detail: str,
    *,
    done: bool = False,
    automatic: bool = False,
    action_label: str = "",
    action: Any = None,
    primary: bool = False,
) -> None:
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    mark = "✓" if done else ("◌" if automatic or action else "○")
    color = theme.SUCCESS if done else (theme.ACCENT if primary else theme.MUTED)
    text_label(row, f"{mark}  {n}", size=13, bold=True, color=color, width=48).pack(
        side="left", anchor="n"
    )
    mid = ctk.CTkFrame(row, fg_color="transparent")
    mid.pack(side="left", fill="x", expand=True)
    text_label(mid, title, size=13, bold=True).pack(anchor="w")
    muted_label(mid, detail, size=11).pack(anchor="w")
    if automatic:
        muted_label(row, "automatic", size=10).pack(side="right", padx=(theme.SP2, 0))
    elif action_label and action is not None:
        button(
            row, action_label, action,
            kind="ghost",
            height=theme.BTN_SM,
        ).pack(side="right")


def _avg_ovr(squad: dict) -> int | None:
    ovrs: list[int] = []
    for player in squad.get("players") or ():
        try:
            ovrs.append(int(player.get("overallrating") or player.get("ovr")))
        except (TypeError, ValueError):
            pass
    return round(sum(ovrs) / len(ovrs)) if ovrs else None


def _squad_caption(squad: dict, avg_ovr: int | None) -> str:
    del avg_ovr
    text = f"avg  ·  {squad['count']} players  ·  {squad['caption']}"
    if squad.get("stale"):
        text += "  ·  stale — refresh recommended"
    return text


def _focus_player(model: TableModel) -> dict | None:
    """Single checked row, else keyboard/cursor focus — never invent a player."""
    checked = [dict(r) for r in model.selected_rows()]
    if len(checked) == 1:
        return checked[0]
    focused = model.cursor_row()
    if focused is not None:
        return dict(focused)
    return None


def _paint_selection_card(host: Any, row: dict | None, svc: Any) -> None:
    """Side card from real board fields only (name/pos/ovr/pot/age)."""
    ensure_ctk()
    for child in list(host.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    if row is None:
        muted_label(
            host,
            "Select a player to inspect. Double-click or Open to edit on Player.",
            size=11,
            wraplength=200,
        ).pack(anchor="w", padx=theme.SP3, pady=theme.SP3)
        return
    name = str(row.get("name") or "—")
    letters = monogram(name, fallback="PL")
    head = ctk.CTkFrame(host, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
    mono = ctk.CTkFrame(
        head, width=56, height=56, fg_color=theme.CARD_HOVER, corner_radius=theme.R_MD,
    )
    mono.pack(side="left", padx=(0, theme.SP3))
    mono.pack_propagate(False)
    text_label(mono, letters[:2], size=16, bold=True).pack(expand=True)
    meta = ctk.CTkFrame(head, fg_color="transparent")
    meta.pack(side="left", fill="x", expand=True)
    text_label(meta, name, size=14, bold=True).pack(anchor="w")
    muted_label(
        meta,
        f"{row.get('pos') or '—'}  ·  Age {row.get('age') or '—'}",
        size=11,
    ).pack(anchor="w", pady=(2, 0))

    stats = ctk.CTkFrame(host, fg_color="transparent")
    stats.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    for label, key in (("OVR", "ovr"), ("POT", "pot")):
        cell = panel(stats, level=2)
        cell.pack(side="left", fill="x", expand=True, padx=(0, theme.SP2))
        text_label(cell, str(row.get(key) or "—"), size=18, bold=True, color=theme.SUCCESS).pack(
            anchor="w", padx=theme.SP3, pady=(theme.SP2, 0)
        )
        muted_label(cell, label, size=10).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    pid = row.get("id")
    muted_label(host, f"ID {pid}" if pid not in (None, "", "—") else "No player id", size=10).pack(
        anchor="w", padx=theme.SP3, pady=(0, theme.SP2)
    )
    button(
        host,
        "Open in Player Page",
        lambda r=row: _select_player(svc, r),
        kind="accent",
        height=theme.BTN_MD,
    ).pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))


def _build_squad(
    root: Any,
    svc: Any,
    live: dict,
    squad: dict,
    worker: dict[str, Any],
    view: dict[str, Any],
    *,
    sync_pending: bool = False,
) -> None:
    ensure_ctk()

    head = ctk.CTkFrame(root, fg_color="transparent")
    head.pack(fill="x", pady=(0, theme.SP2))
    left = ctk.CTkFrame(head, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)
    section_header(
        left,
        "Club",
        subtitle="What is my squad, and is it ready?",
    )

    right = ctk.CTkFrame(head, fg_color="transparent")
    right.pack(side="right", anchor="n", pady=(theme.SP1, 0))
    # One primary CTA: prefer Refresh when the squad needs it; otherwise Open selected.
    refresh_is_primary = bool(squad.get("needs_refresh") or squad.get("stale")) and not sync_pending
    button(
        right, "Reading squad…" if sync_pending else "Refresh squad", lambda: _read_squad(svc),
        kind="primary" if refresh_is_primary else "ghost", height=theme.BTN_MD,
        disabled_reason="A squad read is already queued." if sync_pending else "",
    ).pack(side="left")

    _readiness_chips(
        root,
        worker_ok=bool(worker.get("installed")),
        game_open=_fc_is_running(),
        live=live,
        squad_ready=True,
    )

    team = "Squad"
    if squad["players"]:
        sample = squad["players"][0]
        team = str(sample.get("teamname") or sample.get("club") or "Squad")
    ovrs: list[int] = []
    for player in squad["players"]:
        try:
            ovrs.append(int(player.get("overallrating") or player.get("ovr")))
        except (TypeError, ValueError):
            pass
    avg_ovr = round(sum(ovrs) / len(ovrs)) if ovrs else None
    identity = identity_row(
        root,
        letters=monogram(team),
        title=team,
        caption=_squad_caption(squad, avg_ovr),
        ovr=avg_ovr,
    )
    identity.pack(anchor="w", pady=(0, theme.SP2))

    if not live["armed"] or squad.get("stale") or sync_pending:
        sync_strip = panel(root, level=1)
        sync_strip.pack(fill="x", pady=(0, theme.SP2))
        if sync_pending:
            loading_row(sync_strip, "Reading squad… Keep Career Mode open.").pack(
                anchor="w", padx=theme.SP3, pady=theme.SP2
            )
            skeleton_table(root, rows=4).pack(fill="x", pady=(0, theme.SP2))
        elif not live["armed"]:
            row = ctk.CTkFrame(sync_strip, fg_color="transparent")
            row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
            pill(row, "Offline", tone="warn").pack(side="left")
            muted_label(
                row,
                "Connection lost — showing your last squad. Reconnect Live Editor, then refresh.",
                size=11,
            ).pack(side="left", padx=(theme.SP2, 0))
        else:
            row = ctk.CTkFrame(sync_strip, fg_color="transparent")
            row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
            pill(row, "Stale", tone="warn").pack(side="left")
            muted_label(
                row,
                "Squad data is stale — refresh when Career Mode is ready.",
                size=11,
            ).pack(side="left", padx=(theme.SP2, 0))

    all_rows = tuple(squad_board_row(player) for player in squad["players"])
    live_keys = {row["id"] for row in all_rows}
    restored = [key for key in (view.get("checked_ids") or []) if key in live_keys]
    view["checked_ids"] = restored
    model = TableModel(
        columns=(
            Column("name", "NAME", width=220, stretch=True),
            Column("pos", "POS", width=50),
            Column("ovr", "OVR", width=46, numeric=True, kind="ovr"),
            Column("pot", "POT", width=46, numeric=True, kind="ovr"),
            Column("age", "AGE", width=46, numeric=True),
        ),
        rows=all_rows,
        key_field="id",
        multi_select=True,
    )
    model.checked = set(restored)
    selection: list[dict] = []
    focus_token: dict[str, Any] = {"key": object()}

    def on_sel(picked: tuple) -> None:
        selection.clear()
        selection.extend(dict(r) for r in picked)
        view["checked_ids"] = list(model.checked)
        _sync_club_cta()

    def _sync_club_cta(*_args: Any) -> None:
        checked = [dict(r) for r in model.selected_rows()]
        focused = model.cursor_row()
        n = len(checked)
        reason = "Tick players to plan."
        if n >= 2:
            label = f"Plan these {n}"
            reason = ""
        elif n == 1:
            label = f"Open {checked[0].get('name') or 'player'}"
            reason = ""
        elif focused is not None:
            label = f"Open {focused.get('name') or 'player'}"
            reason = ""
        else:
            label = "Plan squad"
        try:
            action_button.configure(text=label)
            set_disabled(action_button, reason)
        except NameError:
            pass
        except Exception:
            pass
        try:
            release_reason = "" if n else "Tick the players you want to release."
            if not live["armed"]:
                release_reason = "Load Career Mode before releasing players."
            elif squad.get("stale"):
                release_reason = "Refresh squad before releasing players."
            set_disabled(release_button, release_reason)
        except NameError:
            pass
        except Exception:
            pass
        try:
            if n >= 2:
                if not same_button.winfo_manager():
                    same_button.pack(side="left", padx=(theme.SP2, 0))
            else:
                same_button.pack_forget()
        except NameError:
            pass
        except Exception:
            pass
        try:
            select_caption.configure(
                text=(
                    f"{n} player{'s' if n != 1 else ''} selected — plan, open, or release"
                    if n
                    else "Tick players to plan, open, or release."
                )
            )
        except NameError:
            pass
        except Exception:
            pass
        try:
            focus_row = _focus_player(model)
            key = focus_row.get("id") if focus_row else None
            if key != focus_token["key"]:
                focus_token["key"] = key
                _paint_selection_card(detail_body, focus_row, svc)
        except NameError:
            pass
        except Exception:
            pass

    def run_club_action() -> None:
        checked = [dict(r) for r in model.selected_rows()]
        if len(checked) >= 2:
            _ask_squad_grok(svc, "", team, checked)
            return
        if len(checked) == 1:
            _select_player(svc, checked[0])
            return
        row = model.cursor_row()
        if row is not None:
            _select_player(svc, dict(row))
            return
        set_status(svc, "Tick two or more players to plan them.")

    def release_selected() -> None:
        checked = [dict(row) for row in model.selected_rows()]
        names = [
            str(row.get("name") or "Player")
            for row in checked
            if str(row.get("name") or "").strip() not in ("", "—")
        ]
        if not names:
            set_status(svc, "Tick the players you want to release.")
            return
        if not _confirm_release(root, names):
            set_status(svc, "Release cancelled.")
            return
        try:
            from ...app.commands.release import queue_club_release

            queue_club_release(svc, checked)
            set_status(
                svc,
                f"Queued release of {len(names)} player{'s' if len(names) != 1 else ''}. "
                "Refresh squad after Live Editor finishes.",
            )
        except Exception as exc:  # noqa: BLE001
            set_status(svc, str(exc))

    # Actions stay on screen. Pack them before the list so a tall squad
    # cannot push Plan / Release / Codex below the window.
    foot = ctk.CTkFrame(root, fg_color="transparent", height=112)
    foot.pack(side="bottom", fill="x", pady=(theme.SP2, 0))
    try:
        foot.pack_propagate(False)
    except Exception:
        pass

    actions_card = panel(foot, level=1)
    actions_card.pack(fill="both", expand=True)
    eyebrow(actions_card, "Squad actions").pack(
        anchor="w", padx=theme.SP3, pady=(theme.SP2, 0)
    )
    select_caption = muted_label(
        actions_card, "Tick players to plan, open, or release.", size=11
    )
    select_caption.pack(anchor="w", padx=theme.SP3, pady=(theme.SP1, 0))
    actions = ctk.CTkFrame(actions_card, fg_color="transparent")
    actions.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP2))
    action_button = button(
        actions, "Plan squad",
        run_club_action,
        kind="secondary" if refresh_is_primary else "primary",
        height=theme.BTN_MD,
        disabled_reason="Tick players to plan.",
    )
    action_button.pack(side="left")
    release_button = button(
        actions,
        "Release from club",
        release_selected,
        kind="danger",
        height=theme.BTN_MD,
        disabled_reason="Tick the players you want to release.",
    )
    release_button.pack(side="left", padx=(theme.SP2, 0))
    same_button = button(
        actions,
        "Same numbers",
        lambda: _open_squad_planner(svc, [dict(r) for r in model.selected_rows()]),
        kind="ghost",
        height=theme.BTN_MD,
    )

    # ---- board: the list fills whatever height the window leaves ----
    board = ctk.CTkFrame(root, fg_color="transparent")
    board.pack(fill="both", expand=True)

    main = ctk.CTkFrame(board, fg_color="transparent")
    main.pack(side="left", fill="both", expand=True)

    table_card = panel(main, level=1)
    table_card.pack(fill="both", expand=True)
    try:
        # The list must not report "14 rows tall" or the action bar is clipped.
        table_card.pack_propagate(False)
    except Exception:
        pass

    search_row = ctk.CTkFrame(table_card, fg_color="transparent")
    search_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    query = ctk.StringVar(value=str(view.get("squad_query") or ""))
    search_box = ctk.CTkEntry(
        search_row,
        textvariable=query,
        placeholder_text="Filter by name, position, or ID…",
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    )
    search_box.pack(side="left", fill="x", expand=True)
    button(
        search_row,
        "Clear",
        lambda: query.set(""),
        kind="ghost",
        height=theme.BTN_MD,
        width=62,
    ).pack(side="left", padx=(theme.SP2, 0))

    count_row = ctk.CTkFrame(table_card, fg_color="transparent")
    count_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP1))
    muted_label(count_row, f"{squad['count']} players", size=11).pack(side="left")

    grid = DataGrid(
        table_card,
        model,
        show_checkboxes=True,
        fill_available=True,
        visible_rows=8,
        select_on_click=False,
        on_row_click=lambda _row: _sync_club_cta(),
        on_row_activate=lambda row: _select_player(svc, row),
        on_selection_change=on_sel,
    )
    grid.pack(fill="both", expand=True, padx=theme.SP2, pady=(0, theme.SP2))

    detail_card = panel(board, level=1)
    detail_card.pack(side="right", fill="y", padx=(theme.SP2, 0))
    detail_card.configure(width=240)
    try:
        detail_card.pack_propagate(False)
    except Exception:
        pass
    eyebrow(detail_card, "Selected").pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
    detail_body = ctk.CTkFrame(detail_card, fg_color="transparent")
    detail_body.pack(fill="both", expand=True)

    def apply_filter(*_args: Any) -> None:
        view["squad_query"] = query.get() or ""
        needle = (query.get() or "").strip()
        if not needle:
            model.set_filter_fn(None)
        else:
            allowed = {row["id"] for row in filter_squad_rows(all_rows, needle)}
            model.set_filter_fn(lambda row, ids=allowed: row.get("id") in ids)
        grid.refresh()
        on_sel(model.selected_rows())

    query.trace_add("write", apply_filter)
    apply_filter()

    def refresh_board() -> bool:
        """Update the squad rows without destroying the grid."""
        state_now = svc.store.snapshot()
        now_at = svc.clock.now() if getattr(svc, "clock", None) is not None else None
        fresh = squad_view(state_now, now=now_at)
        pending_now = any(
            (not job.done) and job.label == "Sync squad"
            for job in state_now.jobs.active.values()
        )
        # Pending flipped means the page mode (board vs reading strip) changed.
        # Let the shell rebuild. Row-only updates stay on this grid.
        if pending_now != bool(sync_pending) or not fresh["players"]:
            return False
        rows_now = tuple(squad_board_row(player) for player in fresh["players"])
        grid.set_rows(rows_now)
        live_now = {row["id"] for row in rows_now}
        view["checked_ids"] = [key for key in (view.get("checked_ids") or []) if key in live_now]
        try:
            identity.caption_label.configure(text=_squad_caption(fresh, _avg_ovr(fresh)))
            identity.title_label.configure(
                text=str(
                    (fresh["players"][0].get("teamname") or fresh["players"][0].get("club") or "Squad")
                )
            )
        except Exception:
            from ...core.log import get_logger

            get_logger("ui.club").exception("club caption refresh failed")
        _sync_club_cta()
        return True

    root.refresh_board = refresh_board


def _ask_squad_grok(
    svc: Any,
    prompt: str,
    team: str,
    rows: Sequence[Any],
) -> None:
    text = (prompt or "").strip()
    if not rows:
        set_status(svc, "No squad players to send to Codex.")
        return
    capped, truncated = cap_prompt_rows(rows)
    if truncated:
        set_status(
            svc,
            f"Using the highest-OVR {len(capped)} players (session cap).",
            tone="warn",
        )
    try:
        from ...core.log import get_logger
        from .planner import open_planner

        get_logger("ui.club").info(
            "opening squad session n=%s truncated=%s prompt=%s",
            len(capped),
            truncated,
            text[:80],
        )
        open_planner(svc, capped, mode="per_player", prompt=text, club_name=team)
    except Exception as exc:  # noqa: BLE001
        try:
            from ...core.log import get_logger

            get_logger("ui.club").exception("squad session failed to open")
        except Exception:
            pass
        set_status(svc, f"Squad session unavailable: {exc}")


def _confirm_release(parent: Any, names: Sequence[str]) -> bool:
    """OK/Cancel before a squad release. Decline when no dialog can be shown."""
    shown = list(names[:12])
    extra = len(names) - len(shown)
    lines = "\n".join(f"• {name}" for name in shown)
    if extra:
        lines += f"\n• and {extra} more"
    count = len(names)
    message = (
        f"Release {count} player{'s' if count != 1 else ''} from your club?\n\n"
        f"{lines}\n\n"
        "Live Editor removes them from the squad. Companion cannot undo this."
    )
    try:
        from tkinter import messagebox

        host = parent.winfo_toplevel() if parent is not None else None
        return bool(messagebox.askokcancel("Release from club", message, parent=host))
    except Exception:
        return False


def _open_squad_planner(svc: Any, selection: list[dict]) -> None:
    if not selection:
        set_status(svc, "Select one or more players (checkboxes) first.")
        return
    try:
        from .planner import open_planner

        open_planner(svc, selection)
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Squad Planner unavailable: {exc}")


def _select_player(svc: Any, row: dict) -> None:
    from ...app.commands.player import lock_player

    raw = row.get("_raw") or row
    pid = raw.get("playerid") or raw.get("id")
    try:
        pid_i = int(pid) if pid not in (None, "", "—") else None
    except (TypeError, ValueError):
        pid_i = None
    name = str(raw.get("name") or raw.get("playername") or row.get("name") or "")
    if pid_i is None:
        set_status(svc, "That row has no player id.")
        return
    lock_player(
        svc,
        pid_i,
        record=raw,
        name=name,
        source="squad",
        teamid=svc.store.snapshot().squad.teamid,
    )
    set_status(svc, f"Selected {name} ({pid_i}).")
    if getattr(svc, "ui", None) is not None:
        svc.ui.navigate("player")


def _install_worker(svc: Any) -> None:
    if _is_temporary_app_root(svc):
        set_status(
            svc,
            "Worker installation is disabled in visual-test mode so it cannot repoint your Live Editor worker.",
            tone="warn",
        )
        return

    def work(_token: Any) -> None:
        try:
            from ...platform import le_install

            report = le_install.install(queue_dir=svc.paths.queue, dry_run=False)
            ok = bool(report.get("ok"))
            msg = (
                "Worker installed — restart Live Editor to arm."
                if ok
                else f"Install failed: {'; '.join(report.get('errors') or ['unknown'])}"
            )
            tone = "ok" if ok else "error"
        except Exception as exc:  # noqa: BLE001
            msg = f"Install failed: {exc}"
            tone = "error"
        set_status(svc, msg, tone=tone)

    try:
        svc.executor.submit("club.install", work)
        set_status(svc, "Installing worker…", tone="info")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Install failed: {exc}", tone="error")


def _read_squad(svc: Any) -> None:
    """Request a live export; never promote another save's disk cache."""
    live = svc.store.snapshot().bridge
    if live.armed:
        try:
            from ...app.commands.squad import sync_squad, sync_status_text

            set_status(svc, sync_status_text(sync_squad(svc)))
            return
        except Exception as exc:  # noqa: BLE001
            set_status(svc, f"Could not queue squad export: {exc}")
            return

    # Never replace code inside a running FC process. The 2.3.0 experiment did
    # exactly that and was removed after a native game crash.
    set_status(
        svc,
        "The running Live Editor worker is not compatible with this Companion. "
        "No in-game repair was attempted. The installed update will load on the "
        "next normal FC start; old team caches are not used.",
    )
    return


def _refresh_connection(svc: Any) -> None:
    """Poll Live Editor now, then repaint Club with the actual connection state."""
    try:
        from ...app.commands.apply import refresh_liveness

        refresh_liveness(svc)
        live = svc.store.snapshot().bridge.liveness
        if live.armed:
            set_status(svc, "Live Editor connected. Read your squad to begin.")
        else:
            set_status(
                svc,
                live.message or "Live Editor is still waiting for an open Career save.",
            )
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not check the Live Editor connection: {exc}")
    finally:
        if getattr(svc, "ui", None) is not None:
            svc.ui.navigate("club")
