"""Club — home surface: setup checklist or squad board.

Answers: "What is my squad, and is it ready?"
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Sequence

from ...app.commands.squad_mirror import apply_mirror_tracking, live_indicator_text
from ...app.presenters import liveness_view, squad_view
from .. import theme
from ..widgets.primitives import (
    button,
    ensure_ctk,
    muted_label,
    panel,
    pill,
    restyle_button,
    search_box,
    section_title,
    set_disabled,
    text_label,
    tooltip,
    update_pill,
)
from ..widgets.states import loading_row, skeleton_table
from ..widgets.table import Column, DataGrid, TableModel
from ._common import (
    collapse_below,
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
    refresh_club_selection(svc, view)
    state = svc.store.snapshot()
    live = liveness_view(state.bridge.liveness)
    # Explicit clock so age/TTL captions are real (tests inject fixed now via clock).
    now = svc.clock.now() if getattr(svc, "clock", None) is not None else None
    squad = club_squad_view(svc, state, now)
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
            subtitle="Keep Career Mode open. The list appears when the live export finishes.",
        )
        banner = panel(root, level=1)
        banner.pack(fill="x", pady=(0, theme.SP3))
        loading_row(banner, "Reading squad from Career Mode…").pack(
            anchor="w", padx=theme.SP4, pady=theme.SP3
        )
        skeleton_table(root).pack(fill="both", expand=True, pady=(theme.SP2, 0))
    elif phase == "setup":
        _build_setup(root, svc, live, squad, worker)
    else:
        _build_squad(
            root, svc, live, squad, worker, view, sync_pending=sync_pending,
        )
    return root


def club_squad_view(svc: Any, state: Any, now: Any) -> dict[str, Any]:
    """squad_view, minus the TTL "aged" flag while the live mirror tracks."""
    return apply_mirror_tracking(squad_view(state, now=now), svc)


def club_phase(*, has_players: bool, sync_pending: bool) -> str:
    """setup, reading, or board. Shared by build() and tests."""
    if has_players:
        return "board"
    return "reading" if sync_pending else "setup"


def refresh_club_selection(svc: Any, view: dict[str, Any]) -> bool:
    """Consume a return-to-Club selection only in its refreshed Career scope."""
    take = getattr(getattr(svc, "ui", None), "take_club_selection", None)
    pending = take() if callable(take) else None
    if pending is not None:
        view["pending_club_selection"] = pending
    state = svc.store.snapshot()
    squad, live = state.squad, state.bridge.liveness
    scope = (squad.save_uid, squad.teamid)
    old_scope = view.get("selection_scope")
    if old_scope is not None and old_scope != scope:
        view["checked_ids"] = []
        view["recently_added"] = False
    view["selection_scope"] = scope
    pending = view.get("pending_club_selection")
    if not pending:
        return False
    uid, tid = pending.get("save_uid"), pending.get("teamid")
    if ((live.save_uid and uid and live.save_uid != uid)
            or (squad.save_uid and uid and squad.save_uid != uid)
            or (squad.teamid is not None and tid is not None and squad.teamid != tid)):
        view.pop("pending_club_selection", None)
        view["checked_ids"] = []
        view["recently_added"] = False
        set_status(svc, "These players belong to another Career save or club. Refresh and select this squad.")
        return False
    if club_squad_view(svc, state, svc.clock.now()).get("stale") or not squad.taken or not uid or scope != (uid, tid):
        view["checked_ids"] = []
        return False
    roster_ids = {int(row.get("playerid") or row.get("id")) for row in squad.players
                  if row.get("playerid") or row.get("id")}
    ids = tuple(int(pid) for pid in pending.get("playerids", ()))
    view["checked_ids"] = [pid for pid in ids if pid in roster_ids]
    view["recently_added"] = bool(pending.get("recently_added"))
    view["squad_query"] = ""
    view.pop("pending_club_selection", None)
    missing = len(ids) - len(view["checked_ids"])
    if missing:
        set_status(svc, f"Selected {len(view['checked_ids'])} current squad players; {missing} are no longer in this club.")
    return True


def _build_setup(
    root: Any, svc: Any, live: dict, squad: dict, worker: dict[str, Any]
) -> None:
    del squad
    section_header(root, "Club", subtitle="Connect to your Career save to see your squad.")
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
    hero_top.pack(fill="x", padx=theme.SP5, pady=theme.SP5)
    copy = ctk.CTkFrame(hero_top, fg_color="transparent")
    copy.pack(side="left", fill="x", expand=True)
    text_label(copy, next_title, size=theme.T_HEADLINE, display=True).pack(anchor="w")
    muted_label(copy, next_copy, size=theme.T_BODY, wraplength=560, justify="left").pack(
        anchor="w", pady=(theme.SP2, 0)
    )
    if ready:
        button(
            hero_top, "Read my squad", lambda: _read_squad(svc),
            kind="primary", height=theme.BTN_LG, width=150,
        ).pack(side="right", padx=(theme.SP4, 0))
    elif game_open or worker_ok:
        button(
            hero_top, "Connect in this Career", lambda: _repair_connection(svc),
            kind="primary", height=theme.BTN_LG, width=190,
        ).pack(side="right", padx=(theme.SP4, 0))
        button(
            hero_top, "Check connection", lambda: _refresh_connection(svc),
            kind="secondary", height=theme.BTN_LG, width=150,
        ).pack(side="right", padx=(theme.SP2, 0))
    else:
        button(
            hero_top, "Install Companion worker", lambda: _install_worker(svc),
            kind="primary", height=theme.BTN_LG, width=200,
            disabled_reason=(
                "Worker installation is disabled while this visual-test app root is active."
                if _is_temporary_app_root(svc) else ""
            ),
        ).pack(side="right", padx=(theme.SP4, 0))

    note = ctk.CTkFrame(root, fg_color="transparent")
    note.pack(fill="x", padx=theme.SP1)
    muted_label(
        note,
        "Reading the squad only copies it into the Companion. Nothing changes in FC 26 "
        "until you review and apply an edit.",
        size=theme.T_SMALL,
    ).pack(anchor="w")


def _readiness_chips(
    parent: Any,
    *,
    worker_ok: bool,
    game_open: bool,
    live: dict,
    squad_ready: bool,
    svc: Any = None,
) -> Any:
    """The four connection steps, as one strip. Real worker/game/LE state only.

    On the squad board (``squad_ready``) the strip only appears while a step
    needs attention; the header chip already says "Connected" when all is well.
    """
    ensure_ctk()
    from ..icons import ctk_icon

    armed = bool(live.get("armed"))
    pill_name = str(live.get("pill") or "")
    career_ok = armed and pill_name in ("LIVE", "ARMED", "WAITING")
    conn_ok = armed and pill_name in ("LIVE", "ARMED")
    items = (
        ("Worker", "Installed" if worker_ok else "Needs install", worker_ok),
        ("FC 26", "Running" if game_open else "Not detected", game_open),
        ("Career Mode", "Ready" if career_ok else "Waiting", career_ok),
        ("Connection", "Connected" if conn_ok else ("Waiting" if armed else "Offline"), conn_ok),
    )
    if squad_ready and all(done for _t, _d, done in items):
        return None
    if squad_ready:
        # On the board, only the steps that need attention, plus a way forward.
        items = tuple(item for item in items if not item[2])
    row = panel(parent, level=1)
    row.pack(fill="x", pady=(0, theme.SP3))
    if squad_ready and svc is not None:
        button(row, "Check connection", lambda: _refresh_connection(svc), kind="secondary",
               icon="refresh", height=theme.BTN_SM).pack(side="right", padx=theme.SP4)
    for i, (title, detail, done) in enumerate(items):
        cell = ctk.CTkFrame(row, fg_color="transparent")
        cell.pack(side="left", fill="x", expand=True, padx=(theme.SP4 if i == 0 else theme.SP2, 0),
                  pady=theme.SP3)
        image = ctk_icon("success" if done else "warning", 18,
                         tint=theme.SUCCESS if done else theme.WARNING)
        ctk.CTkLabel(cell, text="" if image else ("OK" if done else "!"), image=image,
                     width=22).pack(side="left", padx=(0, theme.SP2))
        words = ctk.CTkFrame(cell, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True)
        text_label(words, title, size=theme.T_SMALL, bold=True).pack(anchor="w")
        muted_label(words, detail, size=theme.T_CAPTION,
                    color=theme.MUTED if done else theme.WARNING).pack(anchor="w")
    return row


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


def _avg_ovr(squad: dict) -> int | None:
    ovrs: list[int] = []
    for player in squad.get("players") or ():
        try:
            ovrs.append(int(player.get("overallrating") or player.get("ovr")))
        except (TypeError, ValueError):
            pass
    return round(sum(ovrs) / len(ovrs)) if ovrs else None


def _squad_caption(squad: dict, avg_ovr: int | None) -> str:
    text = f"{squad['count']} players  ·  {squad['caption']}"
    if avg_ovr is not None:
        text = f"average  ·  {text}"
    if squad.get("stale"):
        text += "  ·  stale, refresh recommended"
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
    from ..widgets.ovr import ovr_chip

    for child in list(host.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    if row is None:
        muted_label(
            host,
            "Click a player to see them here. Double-click opens them in Player.",
            size=theme.T_SMALL,
            wraplength=220,
            justify="left",
        ).pack(anchor="w", padx=theme.SP4, pady=(theme.SP2, theme.SP4))
        return
    name = str(row.get("name") or "—")
    letters = monogram(name, fallback="PL")
    head = ctk.CTkFrame(host, fg_color="transparent")
    head.pack(fill="x", padx=theme.SP4, pady=(theme.SP2, theme.SP3))
    mono = ctk.CTkFrame(
        head, width=56, height=56, fg_color=theme.CARD_HOVER, corner_radius=theme.R_MD,
    )
    mono.pack(side="left", padx=(0, theme.SP3))
    mono.pack_propagate(False)
    from .signed_portrait import avatar, controls as portrait_controls
    try:
        portrait_pid = int(row.get("id"))
    except (ValueError, TypeError):
        portrait_pid = 0
    if portrait_pid > 0:
        avatar(mono, svc, portrait_pid, name)
    else:
        text_label(mono, letters[:2], size=theme.T_HEADLINE, display=True,
                   color=theme.ACCENT).pack(expand=True)
    meta = ctk.CTkFrame(head, fg_color="transparent")
    meta.pack(side="left", fill="x", expand=True)
    text_label(meta, name, size=theme.T_HEADING, bold=True, wraplength=150,
               justify="left").pack(anchor="w")
    muted_label(
        meta,
        f"{row.get('pos') or '—'}  ·  Age {row.get('age') or '—'}",
        size=theme.T_SMALL,
    ).pack(anchor="w", pady=(2, 0))

    stats = ctk.CTkFrame(host, fg_color="transparent")
    stats.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP3))
    for label, key in (("Overall", "ovr"), ("Potential", "pot")):
        cell = ctk.CTkFrame(stats, fg_color="transparent")
        cell.pack(side="left", padx=(0, theme.SP4))
        try:
            value = int(row.get(key))
        except (TypeError, ValueError):
            value = None
        ovr_chip(cell, value, size="md").pack(anchor="w")
        muted_label(cell, label, size=theme.T_CAPTION).pack(anchor="w", pady=(theme.SP1, 0))

    pid = row.get("id")
    muted_label(host, f"Player ID {pid}" if pid not in (None, "", "—") else "No player ID",
                size=theme.T_CAPTION).pack(anchor="w", padx=theme.SP4, pady=(0, theme.SP3))
    muted_label(host, "Double-click the row, or use Open below, to edit in Player.",
                size=theme.T_CAPTION, wraplength=220, justify="left").pack(
        anchor="w", padx=theme.SP4, pady=(0, theme.SP3))
    if portrait_pid > 0:
        portrait_controls(host, svc, portrait_pid)


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

    # ---- header: the club is the page title -------------------------------
    head = ctk.CTkFrame(root, fg_color="transparent")
    head.pack(fill="x", pady=(0, theme.SP4))
    identity = identity_row(
        head,
        letters=monogram(team),
        title=team,
        caption=_squad_caption(squad, avg_ovr),
        ovr=avg_ovr,
    )
    identity.pack(side="left", fill="x", expand=True)

    # "Live · updated 3 s ago" beside the caption while the core's live squad
    # mirror has just updated. The shell's 1 s poll re-ages it; no timer here.
    live_badge: dict[str, Any] = {"pill": None, "shown": False}
    caption_row = getattr(getattr(identity, "caption_label", None), "master", None)

    def refresh_live_indicator() -> None:
        text = live_indicator_text(svc)
        badge = live_badge["pill"]
        if not text:
            if badge is not None and live_badge["shown"]:
                badge.pack_forget()
                live_badge["shown"] = False
            return
        if badge is None:
            if caption_row is None:
                return
            badge = pill(caption_row, text, tone="ok")
            live_badge["pill"] = badge
        else:
            update_pill(badge, text, tone="ok")
        if not live_badge["shown"]:
            badge.pack(side="left", padx=(theme.SP2, 0))
            live_badge["shown"] = True

    refresh_live_indicator()
    root.refresh_live_indicator = refresh_live_indicator
    refresh_is_primary = bool(squad.get("needs_refresh") or squad.get("stale")) and not sync_pending
    button(
        head, "Reading squad…" if sync_pending else "Refresh squad", lambda: _read_squad(svc),
        kind="primary" if refresh_is_primary else "secondary", height=theme.BTN_MD,
        icon="refresh",
        disabled_reason="A squad read is already queued." if sync_pending else "",
    ).pack(side="right", anchor="n", pady=(theme.SP1, 0))

    _readiness_chips(
        root,
        worker_ok=bool(worker.get("installed")),
        game_open=_fc_is_running(),
        live=live,
        squad_ready=True,
        svc=svc,
    )

    if not live["armed"] or squad.get("stale") or sync_pending:
        sync_strip = panel(root, level=1)
        sync_strip.pack(fill="x", pady=(0, theme.SP3))
        if sync_pending:
            loading_row(sync_strip, "Reading squad… Keep Career Mode open.").pack(
                anchor="w", padx=theme.SP4, pady=theme.SP3
            )
        elif not live["armed"]:
            row = ctk.CTkFrame(sync_strip, fg_color="transparent")
            row.pack(fill="x", padx=theme.SP4, pady=theme.SP3)
            pill(row, "Offline", tone="warn").pack(side="left")
            muted_label(
                row,
                "Connection lost. Showing your last squad; reconnect Live Editor, then refresh.",
                size=theme.T_SMALL,
            ).pack(side="left", padx=(theme.SP2, 0))
        else:
            row = ctk.CTkFrame(sync_strip, fg_color="transparent")
            row.pack(fill="x", padx=theme.SP4, pady=theme.SP3)
            pill(row, "Stale", tone="warn").pack(side="left")
            muted_label(
                row,
                "This squad may be out of date. Refresh when Career Mode is ready.",
                size=theme.T_SMALL,
            ).pack(side="left", padx=(theme.SP2, 0))

    all_rows = tuple(squad_board_row(player) for player in squad["players"])
    live_keys = {row["id"] for row in all_rows}
    restored = [key for key in (view.get("checked_ids") or []) if key in live_keys]
    view["checked_ids"] = restored
    model = TableModel(
        columns=(
            Column("name", "NAME", width=150, stretch=True, max_width=380),
            Column("pos", "POS", width=52, kind="pos"),
            Column("ovr", "OVR", width=50, numeric=True, kind="ovr"),
            Column("pot", "POT", width=50, numeric=True, kind="ovr", priority=1),
            Column("age", "AGE", width=48, numeric=True, priority=2),
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
        reason = "Tick two or more players to plan them, or one to open it."
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
            label = "Plan players"
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
                    same_button.pack(side="right", padx=(theme.SP2, 0), before=release_button)
            else:
                same_button.pack_forget()
        except NameError:
            pass
        except Exception:
            pass
        try:
            select_caption.configure(
                text=(
                    f"{n} player{'s' if n != 1 else ''} ticked"
                    if n
                    else "Tick players to plan or release them together."
                ),
                text_color=theme.TEXT if n else theme.MUTED,
            )
        except NameError:
            pass
        except Exception:
            pass
        try:
            shown = model.visible_count
            total = len(all_rows)
            count_label.configure(
                text=f"{shown} of {total} players" if shown != total else f"{total} players"
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

    # ---- action bar: packed before the board so a tall squad can never push
    # Plan / Release below the window.
    foot = panel(root, level=1)
    foot.pack(side="bottom", fill="x", pady=(theme.SP3, 0))
    actions = ctk.CTkFrame(foot, fg_color="transparent")
    actions.pack(fill="x", padx=theme.SP4, pady=theme.SP3)
    select_caption = text_label(
        actions, "Tick players to plan or release them together.", size=theme.T_SMALL,
        color=theme.MUTED,
    )
    select_caption.pack(side="left")
    action_button = button(
        actions, "Plan players",
        run_club_action,
        kind="secondary" if refresh_is_primary else "primary",
        height=theme.BTN_MD,
        icon="ai",
        disabled_reason="Tick two or more players to plan them, or one to open it.",
    )
    action_button.pack(side="right")
    release_button = button(
        actions,
        "Release from club",
        release_selected,
        kind="danger",
        height=theme.BTN_MD,
        disabled_reason="Tick the players you want to release.",
    )
    release_button.pack(side="right", padx=(0, theme.SP2))
    same_button = button(
        actions,
        "Same change for all",
        lambda: _open_squad_planner(svc, [dict(r) for r in model.selected_rows()]),
        kind="ghost",
        height=theme.BTN_MD,
    )
    tooltip(same_button, "Give every ticked player the same values.")

    # ---- board: the list fills whatever height the window leaves ----
    board = ctk.CTkFrame(root, fg_color="transparent")
    board.pack(fill="both", expand=True)

    detail_card = panel(board, level=1, width=272)
    detail_pack = {"side": "right", "fill": "y", "padx": (theme.SP3, 0)}
    detail_card.pack(**detail_pack)
    try:
        detail_card.pack_propagate(False)
    except Exception:
        pass
    section_title(detail_card, "Selected player").pack(
        anchor="w", padx=theme.SP4, pady=(theme.SP3, theme.SP1)
    )
    detail_body = ctk.CTkFrame(detail_card, fg_color="transparent")
    detail_body.pack(fill="both", expand=True)

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
    search_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
    query = ctk.StringVar(value=str(view.get("squad_query") or ""))
    search = search_box(
        search_row, variable=query, placeholder="Filter by name, position, or ID",
    )
    search.pack(side="left", fill="x", expand=True)
    search_box_entry = search.entry

    def toggle_recently_added() -> None:
        view["recently_added"] = not bool(view.get("recently_added"))
        apply_filter()

    recent_button = button(search_row, "Recently added", toggle_recently_added,
                           kind="ghost", height=theme.BTN_MD)
    recent_button.pack(side="left", padx=(theme.SP2, 0))
    tooltip(recent_button, "Show only players you signed recently.")

    count_row = ctk.CTkFrame(table_card, fg_color="transparent")
    count_row.pack(fill="x", padx=theme.SP4, pady=(0, theme.SP2))
    count_label = muted_label(count_row, f"{squad['count']} players", size=theme.T_CAPTION)
    count_label.pack(side="left")
    muted_label(count_row, "Click a row to inspect  ·  Double-click or Enter to open",
                size=theme.T_CAPTION).pack(side="right")

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
    collapse_below(board, detail_card, 700, pack_kw=dict(detail_pack, before=main))

    def apply_filter(*_args: Any) -> None:
        view["squad_query"] = query.get() or ""
        needle = (query.get() or "").strip()
        recent_only = bool(view.get("recently_added"))
        recent_button.configure(text="All players" if recent_only else "Recently added")
        restyle_button(recent_button, "accent" if recent_only else "ghost")
        if not needle and not recent_only:
            model.set_filter_fn(None)
        else:
            allowed = {row["id"] for row in filter_squad_rows(all_rows, needle)}
            if recent_only:
                from .sign.handoff import recently_added_ids
                recent_ids = set(recently_added_ids(svc))
                allowed &= recent_ids
                model.checked &= recent_ids
            model.set_filter_fn(lambda row, ids=allowed: row.get("id") in ids)
        grid.refresh()
        on_sel(model.selected_rows())

    query.trace_add("write", apply_filter)
    apply_filter()
    root.search_entry = search_box_entry

    def refresh_board() -> bool:
        """Update the squad rows without destroying the grid."""
        nonlocal all_rows
        refresh_club_selection(svc, view)
        state_now = svc.store.snapshot()
        now_at = svc.clock.now() if getattr(svc, "clock", None) is not None else None
        fresh = club_squad_view(svc, state_now, now_at)
        pending_now = any(
            (not job.done) and job.label == "Sync squad"
            for job in state_now.jobs.active.values()
        )
        # Pending flipped means the page mode (board vs reading strip) changed.
        # Let the shell rebuild. Row-only updates stay on this grid.
        if pending_now != bool(sync_pending) or not fresh["players"]:
            return False
        rows_now = tuple(squad_board_row(player) for player in fresh["players"])
        all_rows = rows_now
        grid.set_rows(rows_now)
        live_now = {row["id"] for row in rows_now}
        view["checked_ids"] = [key for key in (view.get("checked_ids") or []) if key in live_now]
        model.checked = set(view["checked_ids"])
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
        refresh_live_indicator()
        apply_filter()
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
    try:
        from ...core.log import get_logger
        from .planner import open_planner

        get_logger("ui.club").info(
            "opening squad session n=%s prompt=%s",
            len(rows),
            text[:80],
        )
        open_planner(svc, rows, mode="per_player", prompt=text, club_name=team)
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
