"""Shared step progress header for multi-step flows (Cards browse/edit, etc.).

Completed steps use SUCCESS + check; the active step is ACCENT bold;
future steps are MUTED. Separators stay muted.
"""

from __future__ import annotations

from typing import Any, List, Tuple

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore

from ..ui_theme import (
    ACCENT,
    FONT_UI,
    MUTED,
    SIZE_META,
    SP1,
    SUCCESS,
)

# Circled digits ①–⑩ for step indices; fall back to plain numbers beyond that.
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
_SEP = " → "


def _marker(index: int) -> str:
    if 0 <= index < len(_CIRCLED):
        return _CIRCLED[index]
    return str(index + 1)


def _step_style(index: int, name: str, active: int) -> Tuple[str, str, str]:
    """Return (label_text, text_color, font_weight) for a step."""
    if index < active:
        return f"✓ {name}", SUCCESS, "normal"
    if index == active:
        return f"{_marker(index)} {name}", ACCENT, "bold"
    return f"{_marker(index)} {name}", MUTED, "normal"


def step_header(parent: Any, steps: list[str], active: int) -> Any:
    """Build a horizontal step header.

    ``active`` is the 0-based index of the current step.
    Labels and step names are stored on the returned frame so
    :func:`update_step_header` can restyle without rebuilding.
    """
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    names: List[str] = list(steps)
    labels: List[Any] = []
    seps: List[Any] = []
    active = max(0, min(int(active), max(len(names) - 1, 0))) if names else 0

    for i, name in enumerate(names):
        if i > 0:
            sep = ctk.CTkLabel(
                frame,
                text=_SEP,
                text_color=MUTED,
                font=ctk.CTkFont(family=FONT_UI, size=SIZE_META),
            )
            sep.pack(side="left", padx=(SP1, SP1))
            seps.append(sep)

        text, color, weight = _step_style(i, name, active)
        lab = ctk.CTkLabel(
            frame,
            text=text,
            text_color=color,
            font=ctk.CTkFont(family=FONT_UI, size=SIZE_META, weight=weight),
        )
        lab.pack(side="left")
        labels.append(lab)

    # Public-ish attrs for update_step_header / callers
    frame._step_names = names  # type: ignore[attr-defined]
    frame._step_labels = labels  # type: ignore[attr-defined]
    frame._sep_labels = seps  # type: ignore[attr-defined]
    frame._active = active  # type: ignore[attr-defined]
    return frame


def update_step_header(frame: Any, active: int) -> None:
    """Restyle an existing step header created by :func:`step_header`."""
    names: List[str] = getattr(frame, "_step_names", None) or []
    labels: List[Any] = getattr(frame, "_step_labels", None) or []
    if not names or not labels or len(names) != len(labels):
        return
    active = max(0, min(int(active), len(names) - 1))
    frame._active = active  # type: ignore[attr-defined]
    for i, (name, lab) in enumerate(zip(names, labels)):
        text, color, weight = _step_style(i, name, active)
        try:
            lab.configure(
                text=text,
                text_color=color,
                font=ctk.CTkFont(family=FONT_UI, size=SIZE_META, weight=weight),
            )
        except Exception:
            pass
