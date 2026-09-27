"""Skeleton, EmptyState, ErrorState, LoadingRow — the four terminal states.

WHY these are one module with one rule
--------------------------------------
H5: *"Every terminal state names the next action as a control. Prose alone is
not an exit."* v1 had the opposite: the Catalog tab rendered a dead panel with
no message at all, empty searches showed a bare "no results" string, and errors
vanished entirely because the handler closed over an ``except ... as e`` name
that Python had already deleted (13 instances). All three are the same bug —
a state the user cannot get out of.

So :func:`empty_state` and :func:`error_state` **refuse to build without an
action**. That is enforced by :func:`require_action`, which raises
``ValueError`` — a loud failure at build time in a test is strictly better than
a silent dead end in front of a user. It is the one place in the widget kit
where being unhelpful is a hard error.

Two supporting rules from §8.2:

* P-5 — **skeletons are static**. No ``after()`` animation loops, no opacity
  thrash. This is carried over verbatim from the documented v1.10 freeze
  incident (``src/ui_perf.py``); a shimmering skeleton is what froze the app.
* P7 — instant paint. A skeleton exists so the surface can be packed in one
  frame while the real work is still on a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .. import theme
from .primitives import (
    button,
    ensure_ctk,
    muted_label,
    panel,
    text_label,
    ui_font,
)

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

Action = tuple[str, Callable[[], None]]


class DeadEndError(ValueError):
    """A terminal state was built with no way out of it. See H5."""


def require_action(label: str, action: Callable[[], None] | None) -> Action:
    """Validate a mandatory call-to-action. Pure; the H5 guard is tested directly.

    Returns the normalised ``(label, callable)`` pair so the caller does not
    have to re-check truthiness.
    """
    text = str(label or "").strip()
    if not text or action is None or not callable(action):
        raise DeadEndError(
            "A terminal state must name its next action as a real control "
            "(H5: prose alone is not an exit)."
        )
    return (text, action)


@dataclass(frozen=True, slots=True)
class ErrorCopy:
    """The two halves of an honest error message (H4).

    ``message`` is the app's plain-English translation; ``cause`` is the raw
    worker/OS text, verbatim. Showing only the translation loses the detail a
    diagnosis needs; showing only the raw text is what made v1 unusable when a
    Lua traceback surfaced as the entire user-facing message.
    """

    message: str
    cause: str = ""

    @property
    def has_cause(self) -> bool:
        return bool(self.cause.strip())


# ---------------------------------------------------------------------------
# Skeleton — static, always
# ---------------------------------------------------------------------------


def skeleton_block(parent: Any, *, width: int = 200, height: int = 14) -> Any:
    """One static placeholder bar. Never animated (P-5)."""
    ensure_ctk()
    frame = ctk.CTkFrame(
        parent,
        width=width,
        height=height,
        fg_color=theme.SKELETON,
        corner_radius=theme.R_XS,
        border_width=0,
    )
    frame.pack_propagate(False)
    return frame


def skeleton(
    parent: Any,
    *,
    rows: int = 6,
    row_height: int = 16,
    gap: int = theme.SP2,
) -> Any:
    """A stack of static bars with slight width variation.

    The variation is what makes it read as *content* without motion — the
    alternative (uniform bars) reads as a broken layout, and the alternative to
    that (a shimmer) is prohibited.
    """
    ensure_ctk()
    host = ctk.CTkFrame(parent, fg_color="transparent")
    n = max(0, int(rows))
    widths = (320, 260, 300, 220)
    for i in range(n):
        bar = skeleton_block(host, width=widths[i % len(widths)], height=row_height)
        bar.pack(anchor="w", fill="x", pady=(0, gap if i < n - 1 else 0))
    return host


def skeleton_table(
    parent: Any,
    *,
    columns: Sequence[int] = (28, 220, 50, 46, 46, 46),
    rows: int = 8,
    row_height: int = 16,
) -> Any:
    """Skeleton shaped like the data grid, so the swap-in does not shift layout.

    §3.3 asks for exactly this on Club: 8 static rows while the squad reads.
    """
    ensure_ctk()
    host = ctk.CTkFrame(parent, fg_color="transparent")
    for r in range(max(0, int(rows))):
        line = ctk.CTkFrame(host, fg_color="transparent")
        line.pack(fill="x", pady=(0, theme.SP2))
        for c, width in enumerate(columns):
            # Taper trailing columns slightly so the block does not read as a
            # solid rectangle.
            w = width if c < 2 else max(18, width - (r % 3) * 6)
            skeleton_block(line, width=w, height=row_height).pack(
                side="left", padx=(0, theme.SP2)
            )
    return host


def loading_row(parent: Any, text: str = "Loading…", *, detail: str = "") -> Any:
    """A single quiet line for inline async work.

    P-3: indeterminate work shows elapsed time or a plain label, never a fake
    percentage. ``detail`` is where the caller puts the elapsed seconds.
    """
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        row,
        text="◌",
        font=ui_font(13),
        text_color=theme.ACCENT,
        width=18,
    ).pack(side="left")
    text_label(row, text, size=12, color=theme.MUTED).pack(side="left", padx=(theme.SP1, 0))
    if detail:
        muted_label(row, detail, size=10).pack(side="left", padx=(theme.SP2, 0))
    return row


# ---------------------------------------------------------------------------
# Empty — never a dead end
# ---------------------------------------------------------------------------


def empty_state(
    parent: Any,
    *,
    headline: str,
    body: str = "",
    action_label: str = "",
    action: Callable[[], None] | None = None,
    glyph: str = "⬡",
    secondary_label: str = "",
    secondary: Callable[[], None] | None = None,
) -> Any:
    """C19. Icon, headline, body, and a **required** named next action.

    Raises :class:`DeadEndError` when no action is supplied. That is deliberate:
    an empty state without an exit is the defect this widget exists to prevent,
    and letting it build would move the failure from a test to a user.
    """
    primary = require_action(action_label, action)
    ensure_ctk()

    frame = panel(parent, level=1)
    inner = ctk.CTkFrame(frame, fg_color="transparent")
    inner.place(relx=0.5, rely=0.46, anchor="center")

    ctk.CTkLabel(
        inner, text=glyph, font=ui_font(30), text_color=theme.ACCENT
    ).pack(pady=(0, theme.SP3))
    text_label(inner, headline, size=16, bold=True, anchor="center").pack(
        pady=(0, theme.SP2)
    )
    if body:
        ctk.CTkLabel(
            inner,
            text=body,
            font=ui_font(12),
            text_color=theme.MUTED,
            wraplength=420,
            justify="center",
        ).pack(pady=(0, theme.SP4))

    actions = ctk.CTkFrame(inner, fg_color="transparent")
    actions.pack()
    button(actions, primary[0], primary[1], kind="accent", width=180, height=32).pack(
        side="left"
    )
    if secondary_label and secondary is not None:
        button(actions, secondary_label, secondary, kind="ghost", height=32).pack(
            side="left", padx=(theme.SP2, 0)
        )
    return frame


# ---------------------------------------------------------------------------
# Error — persistent, inline, keeps the raw text
# ---------------------------------------------------------------------------


def error_state(
    parent: Any,
    *,
    message: str,
    cause: str = "",
    action_label: str = "Retry",
    action: Callable[[], None] | None = None,
    extra_actions: Sequence[Action] = (),
    glyph: str = "▲",
) -> Any:
    """C21. An error that **stays** until it is resolved or dismissed (H4).

    Never a toast, never auto-dismissed, and never a replacement for data the
    user can still see — callers render this *above* stale rows rather than
    instead of them (§3.3 "Never replace real data with an error screen").

    Like :func:`empty_state` it requires an action; ``Retry`` with a no-op is
    not acceptable, so the caller must pass something real.
    """
    primary = require_action(action_label, action)
    ensure_ctk()
    copy = ErrorCopy(message=str(message or "Something went wrong."), cause=str(cause or ""))

    frame = ctk.CTkFrame(
        parent,
        fg_color=theme.CARD,
        corner_radius=theme.R_SM,
        border_width=1,
        border_color=theme.DANGER,
    )
    body = ctk.CTkFrame(frame, fg_color="transparent")
    body.pack(fill="x", padx=theme.SP3, pady=theme.SP3)

    head = ctk.CTkFrame(body, fg_color="transparent")
    head.pack(fill="x")
    ctk.CTkLabel(
        head, text=glyph, font=ui_font(13, bold=True), text_color=theme.DANGER, width=20
    ).pack(side="left", anchor="n")
    ctk.CTkLabel(
        head,
        text=copy.message,
        font=ui_font(12, bold=True),
        text_color=theme.TEXT,
        wraplength=560,
        justify="left",
        anchor="w",
    ).pack(side="left", fill="x", expand=True)

    if copy.has_cause:
        # The worker's own words, verbatim and selectable-looking (mono).
        ctk.CTkLabel(
            body,
            text=copy.cause,
            font=ctk.CTkFont(family=theme.MONO, size=10),
            text_color=theme.MUTED,
            wraplength=560,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=(20, 0), pady=(theme.SP2, 0))

    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(anchor="w", padx=(20, 0), pady=(theme.SP3, 0))
    button(row, primary[0], primary[1], kind="secondary", height=28).pack(side="left")
    for label, fn in extra_actions:
        if label and callable(fn):
            button(row, label, fn, kind="ghost", height=28).pack(
                side="left", padx=(theme.SP2, 0)
            )
    return frame
