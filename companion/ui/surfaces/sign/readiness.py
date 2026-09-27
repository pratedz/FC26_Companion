"""Signing readiness banner, free-agent counts, and worker repair."""



from __future__ import annotations



import time

from typing import Any, Callable, Sequence



from .... import SIGN_CORE_MIN, core_at_least

from ....domain.job import is_sign_job_label

from ... import theme

from ...widgets.primitives import button, muted_label, panel, pill, text_label

from .._common import set_status



try:  # pragma: no cover - desktop only

    import customtkinter as ctk

except ImportError:  # pragma: no cover

    ctk = None  # type: ignore[assignment]





_TEAM_ADD_REPAIR_KEY = "add_player.repair_worker"

_TEAM_ADD_REPAIR_POLL_SECONDS = 1.0



def _readiness(root: Any, svc: Any, state: Any, view: dict[str, Any] | None = None) -> None:

    """Ready / Action needed card. Shows only real free-agent slot counts — never a fake roster cap."""

    box = panel(root, level=1)

    box.pack(fill="x", pady=(0, theme.SP2))

    # Soft readiness wash without inventing data.

    try:

        box.configure(border_width=1, border_color=theme.BORDER)

    except Exception:

        pass

    row = ctk.CTkFrame(box, fg_color="transparent")

    row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)

    enabled = state.prefs.experimental_add_to_team

    capable = bool(state.bridge.liveness.capabilities.get("add_to_team"))

    slots = _free_agent_count(svc)

    reason = ""

    if not enabled:

        reason = "Enable Sign support."

    elif not state.bridge.armed:

        reason = "Start FC 26 through Live Editor and load Career Mode."

    elif not capable:

        reason = "Restart Live Editor so Sign support loads."

    elif state.bridge.liveness.core_version and not core_at_least(
        state.bridge.liveness.core_version, SIGN_CORE_MIN
    ):

        reason = f"Repair the worker. Sign needs core {SIGN_CORE_MIN} or newer."

    elif not state.squad.teamid:

        reason = "Refresh squad."

    elif slots is None:

        reason = "Couldn't count free agents. Refresh squad."

    else:

        try:

            from ....app.commands.team import _squad_unusable_for_team_add



            if _squad_unusable_for_team_add(svc):

                reason = "Refresh squad."

        except Exception:

            reason = "Refresh squad before adding players."

    ready = not reason



    lead = ctk.CTkFrame(row, fg_color="transparent")
    lead.pack(side="left", fill="x", expand=True)
    title_row = ctk.CTkFrame(lead, fg_color="transparent")
    title_row.pack(anchor="w", fill="x")
    pill(title_row, "Ready" if ready else "Action needed", tone="ok" if ready else "warn").pack(side="left")
    slot_copy = f"{slots} safe free-agent slot{'s' if slots != 1 else ''}" if slots is not None else "Slots unavailable"
    support_copy = "Sign support enabled" if enabled else "Sign support disabled"
    text_label(title_row, slot_copy + " · " + support_copy, size=12, bold=True,
               color=theme.TEXT, wraplength=490, justify="left").pack(side="left", padx=(theme.SP2, 0))
    if reason:
        muted_label(lead, reason, size=11, color=theme.WARNING, wraplength=480, justify="left").pack(
            anchor="w", pady=(theme.SP1, 0))

    actions = ctk.CTkFrame(row, fg_color="transparent")

    actions.pack(side="right", padx=(theme.SP2, 0))

    from ....app.commands.squad import sync_squad, sync_status_text



    def _refresh_squad() -> None:

        try:

            ticket = sync_squad(svc)
            set_status(svc, sync_status_text(ticket))

        except Exception as exc:  # noqa: BLE001

            set_status(svc, str(exc))



    button(actions, "Refresh squad", _refresh_squad, kind="ghost", height=theme.BTN_MD).pack(

        side="right"

    )

    if not enabled:

        button(

            actions,

            "Enable Sign support",

            lambda: _toggle_team_add(svc, True),

            kind="secondary",

            height=theme.BTN_MD,

        ).pack(side="right", padx=(0, theme.SP2))

    elif not capable or (

        state.bridge.liveness.core_version

        and not core_at_least(state.bridge.liveness.core_version, SIGN_CORE_MIN)

    ):

        # A stale/corrupt worker configuration used to trap the user in an

        # endless "restart" loop. This repairs only Companion-owned files,

        # enables the one opt-in operation, then makes the required restart

        # meaningful.

        repair_button = button(

            actions,

            "Repair when FC + LE close",

            lambda: None,

            kind="secondary",

            height=theme.BTN_MD,

        )

        repair_button.pack(side="right", padx=(0, theme.SP2))



        def start_or_cancel_repair() -> None:

            _schedule_team_add_repair(svc, view if view is not None else {}, repair_button)



        repair_button.configure(command=start_or_cancel_repair)





def _host_process_names(procs_module: Any) -> tuple[str, ...]:

    """Return the actual FC/LE host names from one process snapshot."""

    processes = procs_module.list_processes()

    game = procs_module.find_game(processes=processes)

    launcher = procs_module.find_le_launcher(processes=processes)

    return tuple(item.name for item in (game, launcher) if item is not None)





def _wait_for_team_add_repair(

    svc: Any,

    token: Any,

    *,

    poll_seconds: float = _TEAM_ADD_REPAIR_POLL_SECONDS,

    sleep: Callable[[float], None] = time.sleep,

    procs_module: Any = None,

    repair: Callable[..., dict[str, Any]] | None = None,

) -> tuple[str, dict[str, Any] | None, tuple[str, ...]]:

    """Wait outside Tk until both hosts exit, then replace our worker once.



    No worker file is touched while either process is present.  The installer

    repeats the same host check immediately before writing, so a game launched

    between polls remains safe.  Returning data keeps all UI/state updates on

    the UI thread.

    """

    if procs_module is None or repair is None:

        from ....platform import le_install, procs



        procs_module = procs_module or procs

        repair = repair or le_install.repair_experimental_add_to_team



    while not token.cancelled:

        running = _host_process_names(procs_module)

        if not running:

            if token.cancelled:

                return "cancelled", None, ()

            report = repair(queue_dir=svc.paths.queue)

            return "repaired", report, ()

        sleep(poll_seconds)

    return "cancelled", None, ()





def _schedule_team_add_repair(svc: Any, view: dict[str, Any], repair_button: Any) -> None:

    """Start/cancel the deferred worker repair without blocking the UI."""

    executor = getattr(svc, "executor", None)

    if executor is None:

        set_status(svc, "Worker repair is unavailable in this window.")

        return

    if view.get("team_add_repair_waiting"):

        try:

            executor.cancel(_TEAM_ADD_REPAIR_KEY)

        except Exception:

            pass

        view["team_add_repair_waiting"] = False

        try:

            repair_button.configure(text="Repair when FC + LE close", state="normal")

        except Exception:

            pass

        set_status(svc, "Signing repair cancelled. The worker was not changed.")

        return



    # InlineExecutor is deliberately synchronous; waiting in it would freeze

    # visual tests and any embedding host.  The desktop app uses ThreadExecutor.

    if executor.__class__.__name__ == "InlineExecutor":

        set_status(svc, "Close FC 26 and Live Editor, then repair signing support from the desktop app.")

        return



    view["team_add_repair_waiting"] = True

    try:

        repair_button.configure(text="Cancel queued repair", state="normal")

    except Exception:

        pass

    set_status(svc, "Waiting for FC 26 and Live Editor to close. The worker will be repaired automatically; click Cancel to stop.")



    def work(token: Any) -> None:

        try:

            outcome, report, _running = _wait_for_team_add_repair(svc, token)

        except Exception as exc:  # noqa: BLE001

            outcome, report = "failed", {"error": str(exc)}



        def complete() -> None:

            view["team_add_repair_waiting"] = False

            try:

                repair_button.configure(text="Repair when FC + LE close", state="normal")

            except Exception:

                pass

            if outcome == "cancelled":

                set_status(svc, "Signing repair cancelled. The worker was not changed.")

                return

            if outcome == "repaired" and report:

                from ....app import events as E



                configured = set(svc.store.snapshot().prefs.core_ops)

                configured.add("add_to_team")

                svc.store.dispatch(E.PrefChanged("core_ops", sorted(configured)))

                set_status(

                    svc,

            "Sign support repaired. Start FC 26 from Live Editor once, load Career Mode, then refresh squad.",

                )

                return

            set_status(svc, f"Could not repair signing support: {report.get('error', 'unknown error') if report else 'unknown error'}")



        executor.on_ui_thread(complete)



    try:

        executor.submit(_TEAM_ADD_REPAIR_KEY, work)

    except Exception as exc:  # noqa: BLE001

        view["team_add_repair_waiting"] = False

        try:

            repair_button.configure(text="Repair when FC + LE close", state="normal")

        except Exception:

            pass

        set_status(svc, f"Could not start signing repair: {exc}")





def _toggle_team_add(svc: Any, enabled: bool) -> None:

    from ....app import events as E

    from ....platform import le_install, procs



    if enabled:

        processes = procs.list_processes()

        if procs.find_game(processes=processes) or procs.find_le_launcher(processes=processes):

            set_status(

                svc,

                "Close FC 26 and Live Editor completely, then press Enable Sign support. "

                "This does not change your Career save.",

            )

            return



    try:

        report = (

            le_install.repair_experimental_add_to_team(queue_dir=svc.paths.queue)

            if enabled

            else le_install.set_experimental_add_to_team(False)

        )

        configured = set(svc.store.snapshot().prefs.core_ops)

        configured.add("add_to_team") if enabled else configured.discard("add_to_team")

        svc.store.dispatch(E.PrefChanged("core_ops", sorted(configured)))

        state = "enabled" if report["enabled"] else "disabled"

        message = (

            "Sign support repaired and enabled. Close Live Editor completely, then start FC 26 from it once and refresh squad."

            if enabled

            else f"Sign support {state}. Restart Live Editor, then refresh squad."

        )

        set_status(svc, message)

    except Exception as exc:  # noqa: BLE001

        set_status(svc, f"Could not enable Sign support: {exc}")





def _free_agent_count(svc: Any) -> int | None:

    try:

        from ....app.commands.team import _free_agent_pool



        return len(_free_agent_pool(svc))

    except Exception:

        return None





def _active_add_count(svc: Any) -> int:

    try:

        from ....app.commands.team import active_team_add_jobs



        return len(active_team_add_jobs(svc))

    except Exception:

        return 0





def _signing_progress_copy(svc: Any) -> tuple[str, bool]:

    """Inline Sign strip: remaining wait vs ready-to-search."""

    count = _active_add_count(svc)

    if not count:

        return (

            "Search, then review. Existing squad players are never overwritten.",

            False,

        )

    label = ""

    try:

        views = [

            view

            for view in svc.store.snapshot().jobs.active.values()

            if is_sign_job_label(str(view.label or "")) and not view.done

        ]

        raw = str(views[0].label or "") if views else ""

        if ":" in raw:

            label = raw.split(":", 1)[-1].strip()

    except Exception:

        label = ""

    if label:

        message = (

            f"Signing {label}… Live Editor applies the whole bag on the next Career tick. "

            "Copy Lua drain if it sits. Open Activity to follow it."

        )

    else:

        message = (

            f"{count} signing(s) in progress. Live Editor applies the whole bag on the next Career tick. "

            "Copy Lua drain if it sits. Open Activity to follow it."

        )

    return message, True





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





def _copy_sign_lua_drain(svc: Any) -> None:

    """Clipboard backup: paste into Live Editor Lua Engine to drain now."""

    from ....app.commands.team import sign_force_drain_text



    try:

        text = sign_force_drain_text(svc)

    except Exception as exc:  # noqa: BLE001

        set_status(svc, f"Force drain unavailable: {exc}")

        return

    copied = _copy_ui_clipboard(text)

    wrote = ""

    try:

        path = svc.paths.root / "sign_force_drain.lua"

        path.write_text(text, encoding="utf-8")

        wrote = path.name

    except Exception:

        wrote = ""

    if copied:

        set_status(

            svc,

            "Copied Lua drain. Live Editor > Features > Lua Engine > paste > Execute.",

        )

    elif wrote:

        set_status(svc, f"Wrote {wrote} — paste into LE Lua Engine if stuck.")

    else:

        set_status(svc, "Could not copy Lua drain.")





def _recent_signings(svc: Any, *, limit: int = 5) -> tuple[dict[str, str], ...]:

    """Latest Add Player jobs for the Sign tab strip."""

    try:

        state = svc.store.snapshot()

    except Exception:

        return ()

    rows: list[dict[str, str]] = []

    seen: set[str] = set()

    views = (*state.jobs.active.values(), *state.jobs.history)

    for view in views:

        label = str(view.label or "")

        if not is_sign_job_label(label) or view.job_id in seen:

            continue

        seen.add(str(view.job_id))

        name = label.split(":", 1)[-1].strip() or label

        dummy = ""

        result = getattr(view, "result", None)

        ops = getattr(result, "ops", ()) if result is not None else ()

        for op in ops:

            data = getattr(op, "data", {}) or {}

            dummy = str(data.get("dummy_id") or data.get("playerid") or "")

            if dummy:

                break

        outcome = getattr(getattr(view, "outcome", None), "label", "") or ""

        if not view.done:

            outcome = "Verifying"

        rows.append({"name": name, "outcome": str(outcome), "dummy": dummy})

        if len(rows) >= limit:

            break

    return tuple(rows)





def _recent_signings_copy(items: Sequence[dict[str, str]]) -> str:

    if not items:

        return ""

    parts = []

    for item in items:

        dummy = f" · slot {item['dummy']}" if item.get("dummy") else ""

        parts.append(f"{item['name']} ({item['outcome']}{dummy})")

    return "Recent signs: " + " · ".join(parts)


