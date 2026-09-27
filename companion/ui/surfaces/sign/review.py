"""Review window, lineup validation messaging, and confirmations."""

from __future__ import annotations

from typing import Any

from ....domain.add_player import INDIVIDUAL_PLAYERS
from ... import theme
from ...widgets.primitives import button, eyebrow, muted_label, panel, text_label, themed_scroll
from .._common import set_status
from .readiness import _active_add_count

try:  # pragma: no cover - desktop only
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def _confirm_add_label(count: int) -> str:
    n = max(1, int(count or 1))
    return "Apply 1 signing" if n == 1 else f"Apply {n} signings"


def _open_review_window(
    root: Any,
    svc: Any,
    preview: Any,
    lineup: Any,
    age: int | None,
    *,
    view: dict[str, Any] | None = None,
    on_queued: Any | None = None,
) -> None:
    """One visible preflight. Confirm queues even if an earlier signing is still applying."""
    try:
        host = root.winfo_toplevel()
    except Exception:
        host = root
    win = ctk.CTkToplevel(host)
    win.title("Checkout")
    win.geometry("760x650")
    win.minsize(650, 520)
    win.configure(fg_color=theme.BG)
    try:
        win.transient(host)
        win.grab_set()
        win.lift()
        win.focus_force()
    except Exception:
        pass
    try:
        root.signing_review_open = True
    except Exception:
        pass

    def _release_modal() -> None:
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            root.signing_review_open = False
        except Exception:
            pass

    def _close() -> None:
        _release_modal()
        try:
            win.destroy()
        except Exception:
            pass

    def _on_destroy(event: Any) -> None:
        if event.widget is win:
            _release_modal()

    try:
        win.protocol("WM_DELETE_WINDOW", _close)
        win.bind("<Destroy>", _on_destroy)
    except Exception:
        pass

    header = panel(win, level=1)
    header.pack(fill="x", padx=theme.SP3, pady=theme.SP3)
    try:
        header.configure(border_width=1, border_color=theme.BORDER)
    except Exception:
        pass
    text_label(header, "Checkout", size=18, bold=True).pack(
        anchor="w", padx=theme.SP3, pady=(theme.SP3, 2)
    )
    age_text = f"Age {age} on every new player" if age is not None else "Card ages stay as they are"
    muted_label(
        header,
        f"{preview.selected_count} player"
        f"{'s' if preview.selected_count != 1 else ''} will join your team. "
        f"Each one replaces an unused free agent. Your current squad stays. {age_text}.",
        size=11,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, 2))
    muted_label(
        header,
        "Only cards with a verified face are in this list.",
        size=10,
        color=theme.SUCCESS,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))
    muted_label(
        header,
        "Current squad players are never selected as signing targets.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))
    if _active_add_count(svc):
        muted_label(
            header,
            "An earlier signing is still applying in FC. Confirm queues this card on a different free-agent slot.",
            size=10,
            color=theme.WARNING,
        ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    scroll = themed_scroll(win, fill=theme.BG, height=360)
    scroll.pack(fill="both", expand=True, padx=theme.SP3, pady=(0, theme.SP2))
    body = scroll.inner
    if lineup.formation != INDIVIDUAL_PLAYERS:
        eyebrow(body, f"Formation — {lineup.formation}").pack(anchor="w")
        for reviewed in lineup.slots:
            card = reviewed.card or {}
            name = str(card.get("name") or card.get("playername") or "Missing")
            ovr = str(card.get("overallrating") or card.get("overall") or "-")
            native = ", ".join(reviewed.native_positions) or "no verified position"
            row = panel(body, level=1)
            row.pack(fill="x", pady=(theme.SP1, 0))
            try:
                row.configure(border_width=1, border_color=theme.BORDER)
            except Exception:
                pass
            text_label(row, reviewed.slot.label, size=11, bold=True, width=145).pack(
                side="left", padx=theme.SP2, pady=theme.SP2
            )
            text_label(row, name, size=12, width=205).pack(side="left", padx=(0, theme.SP1))
            muted_label(row, f"OVR {ovr} - {native}", size=10).pack(side="left")
            muted_label(row, "real face verified", size=10, color=theme.SUCCESS).pack(
                side="right", padx=theme.SP2
            )
    else:
        eyebrow(body, "Selected players").pack(anchor="w")
        for entry in preview.entries:
            row = panel(body, level=1)
            row.pack(fill="x", pady=(theme.SP1, 0))
            try:
                row.configure(border_width=1, border_color=theme.BORDER)
            except Exception:
                pass
            ovr = entry.card.get("overallrating") or entry.card.get("overall") or "-"
            pos = entry.card.get("positions_text") or entry.card.get("preferredposition1") or "-"
            text_label(row, entry.name, size=13, bold=True, width=220).pack(
                side="left", padx=theme.SP2, pady=theme.SP2
            )
            muted_label(row, f"OVR {ovr} · {pos}", size=10).pack(side="left")
            muted_label(
                row,
                f"replaces unused {entry.dummy_name} (OVR {entry.dummy_overall})",
                size=10,
                color=theme.WARNING,
            ).pack(side="left", padx=(theme.SP2, 0))
            muted_label(row, "real face verified", size=10, color=theme.SUCCESS).pack(
                side="right", padx=theme.SP2
            )

    if preview.warnings:
        warning = panel(body, level=1)
        warning.pack(fill="x", pady=(theme.SP2, 0))
        eyebrow(warning, "Card notes").pack(
            anchor="w", padx=theme.SP2, pady=(theme.SP1, 0)
        )
        for note in preview.warnings:
            muted_label(warning, note, size=10, color=theme.WARNING, wraplength=670).pack(
                anchor="w", padx=theme.SP2, pady=(0, theme.SP1)
            )

    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP3))
    age_row = ctk.CTkFrame(foot, fg_color="transparent")
    age_row.pack(fill="x", pady=(0, theme.SP2))
    force_age = bool((view or {}).get("force_age") if view is not None else age is not None)
    age_var = ctk.BooleanVar(value=force_age)
    age_entry = ctk.CTkEntry(
        age_row,
        width=48,
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    )
    age_entry.insert(0, str((view or {}).get("age") or (age if age is not None else "25")))

    def _read_review_age() -> int | None:
        if not age_var.get():
            if view is not None:
                view["force_age"] = False
            return None
        try:
            value = int((age_entry.get() or "").strip())
        except ValueError:
            raise ValueError("New-player age must be a whole number from 16 to 40.")
        if not 16 <= value <= 40:
            raise ValueError("New-player age must be between 16 and 40.")
        if view is not None:
            view["force_age"] = True
            view["age"] = str(value)
        return value

    def _sync_age_entry() -> None:
        try:
            age_entry.configure(state="normal" if age_var.get() else "disabled")
        except Exception:
            pass

    ctk.CTkCheckBox(
        age_row,
        text="Set new players to age",
        variable=age_var,
        command=_sync_age_entry,
        width=180,
        height=24,
        checkbox_width=18,
        checkbox_height=18,
        fg_color=theme.ACCENT,
        hover_color=theme.CARD_HOVER,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    ).pack(side="left")
    age_entry.pack(side="left", padx=(theme.SP2, 0))
    muted_label(age_row, "Leave off to keep card birthdates.", size=10).pack(
        side="left", padx=(theme.SP2, 0)
    )
    _sync_age_entry()

    actions = ctk.CTkFrame(foot, fg_color="transparent")
    actions.pack(fill="x")
    button(actions, "Cancel", _close, kind="ghost", height=theme.BTN_LG).pack(side="left")

    def submit() -> None:
        from ....app.commands.team import add_reviewed_cards_to_team, preview_cards_for_team

        try:
            chosen_age = _read_review_age()
            current = preview
            if chosen_age != age:
                cards = tuple(entry.card for entry in preview.entries)
                current = preview_cards_for_team(svc, cards, forced_age=chosen_age)
            job_ids = add_reviewed_cards_to_team(svc, current)
            names = ", ".join(entry.name for entry in current.entries)
            try:
                from ....core.log import get_logger

                get_logger("ui.sign").info(
                    "queued %s signings from bag: %s",
                    len(job_ids),
                    names,
                )
            except Exception:
                pass
            set_status(
                svc,
                (
                    f"Queued {current.selected_count} player"
                    f"{'s' if current.selected_count != 1 else ''} as one signing. "
                    "Live Editor applies the whole bag after the next Career tick "
                    "(or Copy Lua drain). You can keep shopping."
                ),
            )
            if callable(on_queued):
                try:
                    on_queued()
                except Exception:
                    pass
            try:
                confirm.pack_forget()
            except Exception:
                pass
            button(
                actions,
                "Keep shopping",
                _close,
                kind="primary",
                height=theme.BTN_LG,
                width=140,
            ).pack(side="right")
        except Exception as exc:  # noqa: BLE001
            set_status(svc, f"Could not queue player draft: {exc}")

    confirm = button(
        actions,
        _confirm_add_label(preview.selected_count),
        submit,
        kind="primary",
        height=theme.BTN_LG,
        width=190,
    )
    confirm.pack(side="right")


def _ask_ok_cancel(parent: Any, message: str) -> bool:
    """Simple OK / Cancel confirmation — no typed phrase required."""
    try:
        from tkinter import messagebox

        return bool(
            messagebox.askokcancel(
                "Confirm player draft",
                message,
                parent=parent.winfo_toplevel() if parent is not None else None,
            )
        )
    except Exception:
        # Headless / test hosts: treat as declined rather than auto-queue.
        return False


def _report_invalid_lineup(svc: Any, lineup: Any) -> None:
    if lineup.duplicate_people:
        set_status(svc, "Choose one card per player: " + ", ".join(lineup.duplicate_people))
        return
    if lineup.missing_slots:
        missing = ", ".join(slot.label for slot in lineup.missing_slots)
        set_status(
            svc,
            f"Draft is incomplete for {lineup.formation}. Missing compatible cards for: {missing}.",
        )
        return
    if lineup.extra_cards:
        names = ", ".join(
            str(card.get("name") or card.get("playername") or "Player")
            for card in lineup.extra_cards
        )
        set_status(
            svc,
            f"Draft does not fit {lineup.formation}. Remove or replace: {names}.",
        )
        return
    set_status(svc, "Draft does not fit the selected formation.")
