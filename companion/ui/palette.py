"""Command palette — pure command list + dispatch (Ctrl+K).

Commands are data; the shell binds them to navigation/actions. Tests assert
the catalog without opening Tk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class PaletteCommand:
    id: str
    label: str
    kind: str  # navigate | activity | settings | doctor | apply
    target: str = ""


def palette_commands() -> tuple[PaletteCommand, ...]:
    """Minimal Tier-A catalog: surfaces, activity, settings, doctor, apply."""
    return (
        PaletteCommand("nav.club", "Club: your squad", "navigate", "club"),
        PaletteCommand("nav.player", "Player: edit one player", "navigate", "player"),
        PaletteCommand("nav.add_player", "Sign: search and sign cards", "navigate", "add_player"),
        PaletteCommand("nav.injury", "Injuries: scan and heal", "navigate", "injury"),
        PaletteCommand("nav.automations", "Matchday: prepare the squad", "navigate", "automations"),
        PaletteCommand("nav.library", "Library: find a card", "navigate", "library"),
        PaletteCommand(
            "nav.club.planner",
            "Plan several players (multi-select in Club)",
            "navigate",
            "club",
        ),
        PaletteCommand("activity", "Open Activity", "activity"),
        PaletteCommand("settings", "Open Settings", "settings"),
        PaletteCommand("doctor", "Run a health check", "doctor"),
        PaletteCommand("apply", "Review staged changes", "apply"),
    )


def filter_commands(query: str, commands: Sequence[PaletteCommand] | None = None) -> tuple[PaletteCommand, ...]:
    q = (query or "").strip().lower()
    items = tuple(commands) if commands is not None else palette_commands()
    if not q:
        return items
    return tuple(c for c in items if q in c.label.lower() or q in c.id.lower())


def dispatch_palette(
    command: PaletteCommand,
    *,
    navigate: Callable[[str], Any] | None = None,
    open_activity: Callable[[], Any] | None = None,
    open_settings: Callable[[], Any] | None = None,
    run_doctor: Callable[[], Any] | None = None,
    open_apply: Callable[[], Any] | None = None,
) -> str:
    """Run one command; returns a short status string for the shell."""
    if command.kind == "navigate":
        if navigate is None:
            return f"Navigate unavailable: {command.target}"
        navigate(command.target)
        return f"Opened {command.label}"
    if command.kind == "activity":
        if open_activity is None:
            return "Activity unavailable"
        open_activity()
        return "Opened Activity"
    if command.kind == "settings":
        if open_settings is None:
            return "Settings unavailable"
        open_settings()
        return "Opened Settings"
    if command.kind == "doctor":
        if run_doctor is None:
            return "Doctor unavailable"
        run_doctor()
        return "Doctor finished"
    if command.kind == "apply":
        if open_apply is None:
            return "Apply unavailable"
        open_apply()
        return "Opened Review / Apply"
    return f"Unknown command {command.id}"
