"""Small UI action bridge shared by lazily-built surfaces.

Surfaces receive ``Services`` rather than the Shell.  This bridge lets a
surface request navigation without importing or reaching into the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(slots=True)
class UiActions:
    _navigate: Callable[[str], None] | None = None
    _review: Callable[[], None] | None = None
    _open_activity: Callable[[], None] | None = None
    _open_signing: Callable[[dict[str, Any]], None] | None = None
    _pending_signing_card: dict[str, Any] | None = None
    _open_player_card: Callable[[dict[str, Any]], None] | None = None
    _pending_player_card: dict[str, Any] | None = None
    _open_settings: Callable[[], None] | None = None

    def bind_navigation(self, callback: Callable[[str], None]) -> None:
        self._navigate = callback

    def navigate(self, destination: str) -> bool:
        callback = self._navigate
        if callback is None:
            return False
        callback(str(destination))
        return True

    def bind_review(self, callback: Callable[[], None]) -> None:
        """Bind the shell's mandatory diff preview without exposing the shell."""
        self._review = callback

    def review(self) -> bool:
        """Open the mandatory review sheet when a real shell is attached."""
        callback = self._review
        if callback is None:
            return False
        callback()
        return True

    def bind_activity(self, callback: Callable[[], None]) -> None:
        """Bind the Activity drawer without treating it as a main tab."""
        self._open_activity = callback

    def open_activity(self) -> bool:
        callback = self._open_activity
        if callback is None:
            return False
        callback()
        return True

    def bind_signing(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Bind a safe Sign/Add Player handoff without exposing the shell."""
        self._open_signing = callback

    def open_signing(self, card: Mapping[str, Any]) -> bool:
        """Open the signing flow with a defensive copy of its selected card."""
        payload = dict(card)
        self._pending_signing_card = payload
        callback = self._open_signing
        if callback is not None:
            callback(payload)
            return True
        # The default path keeps payload on this bridge for Add Player's VM
        # while retaining compatibility with shells that only bind navigation.
        if self.navigate("add_player"):
            return True
        self._pending_signing_card = None
        return False

    def take_signing_card(self) -> dict[str, Any] | None:
        """Consume the selected Library card exactly once in the signing flow."""
        card = self._pending_signing_card
        self._pending_signing_card = None
        return dict(card) if card is not None else None

    def bind_player_card(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Bind a Library → Player Card Library handoff without exposing the shell."""
        self._open_player_card = callback

    def open_player_card(self, card: Mapping[str, Any]) -> bool:
        """Open Player Card Library with a defensive copy of the selected card."""
        payload = dict(card)
        self._pending_player_card = payload
        callback = self._open_player_card
        if callback is not None:
            callback(payload)
            return True
        if self.navigate("player"):
            return True
        self._pending_player_card = None
        return False

    def take_player_card(self) -> dict[str, Any] | None:
        """Consume the Library card exactly once on the Player Card Library."""
        card = self._pending_player_card
        self._pending_player_card = None
        return dict(card) if card is not None else None

    def bind_settings(self, callback: Callable[[], None]) -> None:
        self._open_settings = callback

    def open_settings(self) -> bool:
        callback = self._open_settings
        if callback is None:
            return False
        callback()
        return True
