"""Injuries — scan the current Career squad and clear injuries.

Answers: "Who is injured, and can I clear it?"
"""

from __future__ import annotations

from typing import Any

from ...app.commands import injury as injury_cmd
from ...domain.outcome import ApplyOutcome, JobResult
from .. import theme
from ..widgets.primitives import (
    button,
    muted_label,
    panel,
    set_disabled,
    soft_fill,
    text_label,
    themed_scroll,
)
from ._common import monogram, section_header, set_status, surface_root

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    view = vm if isinstance(vm, dict) else {}
    view.setdefault("players", [])
    view.setdefault("error", "")
    view.setdefault("busy", False)
    view.setdefault("status", "")
    view.setdefault("scanned", False)

    section_header(
        root,
        "Injuries",
        subtitle="Review squad injuries and help your players return to action.",
    )

    actions = panel(root, level=1)
    actions.pack(fill="x", pady=(0, theme.SP3))
    action_row = ctk.CTkFrame(actions, fg_color="transparent")
    action_row.pack(fill="x", padx=theme.SP3, pady=theme.SP3)

    controls: dict[str, Any] = {"scan": None, "cure": None}

    btn_col = ctk.CTkFrame(action_row, fg_color="transparent")
    btn_col.pack(side="left", fill="y")

    controls["scan"] = button(
        btn_col,
        "Scan squad",
        None,
        kind="accent",
        height=theme.BTN_LG,
        width=132,
    )
    controls["scan"].pack(side="left")
    controls["cure"] = button(
        btn_col,
        "Heal injured",
        None,
        kind="secondary",
        height=theme.BTN_LG,
        width=140,
    )
    controls["cure"].pack(side="left", padx=(theme.SP2, 0))

    muted_label(
        actions,
        "Scan your squad, review the list, then heal one player or everyone listed.",
        size=11, wraplength=480, justify="left",
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    body = ctk.CTkFrame(root, fg_color="transparent")
    body.pack(fill="both", expand=True)

    def _clear(host: Any) -> None:
        for child in list(host.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

    def _wrapped(host: Any, text: str, *, size: int = 12, color: str | None = None) -> Any:
        label = muted_label(host, text, size=size, color=color, wraplength=360, justify="left")
        label.pack(fill="x", anchor="w")
        def resize(event: Any) -> None:
            if event.width > 1:
                label.configure(wraplength=max(160, event.width - theme.SP3 * 2))
        host.bind("<Configure>", resize, add="+")
        return label

    def _player_row(parent: Any, player: dict[str, Any], *, index: int) -> None:
        name = str(player.get("name") or player.get("playerid") or "Player")
        injury = str(player.get("injury") or "injured")
        letters = monogram(name, fallback="?")

        row = ctk.CTkFrame(
            parent,
            fg_color=theme.ROW_ALT if index % 2 else "transparent",
            corner_radius=theme.R_SM,
        )
        row.pack(fill="x", padx=theme.SP2, pady=(0, theme.SP1))

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SP2, pady=theme.SP2)

        mono = ctk.CTkFrame(
            inner,
            width=36,
            height=36,
            fg_color=soft_fill("error"),
            corner_radius=theme.R_PILL,
            border_width=1,
            border_color=theme.DANGER,
        )
        mono.pack(side="left", padx=(0, theme.SP2))
        mono.pack_propagate(False)
        text_label(mono, letters[:2], size=11, bold=True, color=theme.DANGER).pack(
            expand=True
        )

        heal = button(inner, "Heal", lambda pid=player["playerid"]: do_cure([pid]),
                      kind="secondary", width=72, height=theme.BTN_MD)
        heal.pack(side="right", padx=(theme.SP2, 0))
        set_disabled(heal, "Working…" if view.get("busy") else "")
        names = ctk.CTkFrame(inner, fg_color="transparent")
        names.pack(side="left", fill="x", expand=True)
        text_label(names, name, size=15, bold=True).pack(anchor="w")
        expected = injury_cmd.return_date_label(player.get("return_date"))
        description = f"Expected return · {expected}" if expected else injury
        _wrapped(names, description, size=12, color=theme.WARNING)

    def refresh_list() -> None:
        _clear(body)
        err = str(view.get("error") or "").strip()
        players = list(view.get("players") or [])
        status = str(view.get("status") or "").strip()
        busy = bool(view.get("busy"))

        card = panel(body, level=1)
        card.pack(fill="both" if players else "x", expand=bool(players))

        if err:
            banner = ctk.CTkFrame(
                card,
                fg_color=soft_fill("error"),
                corner_radius=theme.R_SM,
                border_width=1,
                border_color=theme.DANGER,
            )
            banner.pack(fill="x", padx=theme.SP3, pady=theme.SP3)
            text_label(
                banner, "Could not heal all players" if view.get("operation") == "cure" else "Could not read injuries",
                size=14, bold=True, color=theme.DANGER
            ).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 2))
            message = ctk.CTkFrame(banner, fg_color="transparent")
            message.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
            _wrapped(message, err)
            if not players:
                return

        scanning = busy and status.startswith("Scanning")
        curing = busy and status.startswith("Clearing")

        if not players:
            if scanning:
                title = "Scanning squad…"
                helper = "Looking up injured players on the current Career Mode squad."
            elif curing:
                title = "Clearing injuries…"
                helper = status
            elif status and status.startswith("Cured"):
                title = "Listed players have been healed"
                helper = status
            elif view.get("scanned"):
                title = "No injuries found in this scan"
                helper = status or "No injured players were found in the latest scan."
            else:
                title = "Check your squad for injuries"
                helper = "Scan the squad to list injured players by name."

            text_label(card, title, size=14, bold=True).pack(
                anchor="w", padx=theme.SP3, pady=(theme.SP3, 2)
            )
            message = ctk.CTkFrame(card, fg_color="transparent")
            message.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
            _wrapped(message, helper)
            return

        n = len(players)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
        text_label(
            header,
            f"{n} injured player{'s' if n != 1 else ''}",
            size=18,
            bold=True,
        ).pack(anchor="w")
        muted_label(
            header,
            f"Checked {view.get('covered', 'your')} squad players. Healing also restores full fitness."
            if not curing
            else "Clearing injuries for the listed players…",
            size=12, wraplength=480, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        scroll = themed_scroll(card, fill=theme.CARD, height=280)
        scroll.pack(fill="both", expand=True, padx=theme.SP1, pady=(0, theme.SP1))
        for i, player in enumerate(players, start=1):
            if isinstance(player, dict):
                _player_row(scroll.inner, player, index=i)
        fit = getattr(scroll, "fit_inner", None) or getattr(scroll, "sync_scroll", None)
        if callable(fit):
            try:
                fit()
            except Exception:
                pass

        if status:
            muted_label(card, status, size=10).pack(
                anchor="w", padx=theme.SP3, pady=(theme.SP1, theme.SP3)
            )

    def set_busy(busy: bool, *, reason: str = "") -> None:
        view["busy"] = busy
        for key in ("scan", "cure"):
            widget = controls.get(key)
            if widget is None:
                continue
            try:
                disabled = reason if busy else ""
                if key == "cure" and not busy and not view.get("players"):
                    disabled = "Scan and find injured players first"
                set_disabled(widget, disabled)
            except Exception:
                pass

    def on_scan_result(result: JobResult) -> None:
        if not result.outcome.is_terminal:
            return
        if injury_cmd.is_success(result):
            data = injury_cmd.scan_data(result)
            view["players"] = injury_cmd.parse_scan_players(data)
            view["error"] = ""
            view["scanned"] = True
            view["covered"] = data.get("covered", "your")
            n = len(view["players"])
            covered = data.get("covered")
            targets = result.counts.get("targets") if result.counts else None
            coverage = (
                f" Checked {covered} of {targets} squad records."
                if isinstance(covered, int) and isinstance(targets, int)
                and covered < targets else ""
            )
            view["status"] = (
                f"Found {n} injured player{'s' if n != 1 else ''}."
                if n
                else "No injuries found in the readable squad records."
            ) + coverage
            set_status(svc, view["status"])
        else:
            view["error"] = injury_cmd.result_message(result)
            view["status"] = ""
            set_status(svc, view["error"])
        set_busy(False)
        refresh_list()

    def on_cure_result(result: JobResult) -> None:
        if not result.outcome.is_terminal:
            return
        if injury_cmd.is_success(result) or result.outcome is ApplyOutcome.PARTIAL:
            cured = injury_cmd.cure_names(result)
            view["error"] = injury_cmd.result_message(result) if result.outcome is ApplyOutcome.PARTIAL else ""
            if cured:
                cured_ids = injury_cmd.cure_player_ids(result)
                if cured_ids:
                    view["players"] = [p for p in view.get("players", []) if p.get("playerid") not in cured_ids]
                elif result.outcome is not ApplyOutcome.PARTIAL:
                    view["players"] = [p for p in view.get("players", []) if p.get("name") not in cured]
                view["status"] = (
                    f"Cured {len(cured)}: " + ", ".join(cured)
                    + ". Reopen Team Management to refresh the game display."
                )
            else:
                view["status"] = "No injuries to clear."
            set_status(svc, view["status"])
        else:
            view["error"] = injury_cmd.result_message(result)
            view["status"] = ""
            set_status(svc, view["error"])
        set_busy(False)
        refresh_list()

    def do_scan() -> None:
        if view.get("busy"):
            return
        set_busy(True, reason="Scan already running…")
        view["players"] = []
        view["operation"] = "scan"
        view["scanned"] = False
        view["error"] = ""
        view["status"] = "Scanning squad…"
        refresh_list()
        try:
            injury_cmd.scan_injuries(svc, on_result=on_scan_result)
        except injury_cmd.InjuryError as exc:
            set_busy(False)
            view["error"] = str(exc)
            view["status"] = ""
            set_status(svc, view["error"])
            refresh_list()
        except Exception as exc:  # noqa: BLE001
            set_busy(False)
            view["error"] = str(exc)
            view["status"] = ""
            set_status(svc, view["error"])
            refresh_list()

    def do_cure(selected_ids: list[int] | None = None) -> None:
        if view.get("busy") or not view.get("players"):
            return
        set_busy(True, reason="Cure already running…")
        view["operation"] = "cure"
        view["error"] = ""
        view["status"] = "Clearing injuries…"
        refresh_list()
        playerids = selected_ids if selected_ids is not None else [
            int(p["playerid"])
            for p in (view.get("players") or [])
            if isinstance(p, dict) and p.get("playerid") is not None
        ]
        try:
            injury_cmd.cure_injuries(svc, playerids, on_result=on_cure_result)
        except injury_cmd.InjuryError as exc:
            set_busy(False)
            view["error"] = str(exc)
            view["status"] = ""
            set_status(svc, view["error"])
            refresh_list()
        except Exception as exc:  # noqa: BLE001
            set_busy(False)
            view["error"] = str(exc)
            view["status"] = ""
            set_status(svc, view["error"])
            refresh_list()

    controls["scan"].configure(command=do_scan)
    controls["cure"].configure(command=do_cure)

    set_busy(bool(view.get("busy")), reason="Working…")
    refresh_list()
    return root
