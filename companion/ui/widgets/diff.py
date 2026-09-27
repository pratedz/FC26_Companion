"""C16 — the diff preview. Mandatory before every apply (P4, §5.2).

WHY this is the most important widget in the kit
------------------------------------------------
P3's failure in v1 is the worst bug in the product: "Load from target" populated
only name / OVR / POT, but the form *looked* fully populated, so a user editing
one field silently wrote ~40 defaults into a career save. There was no undo
prompt and the damage was invisible until they loaded the game.

The structural fix is upstream (only ``edited`` fields are ever written), but
the fix a *user* can see is this sheet: before anything reaches the queue, the
app states in plain English exactly what it is about to do. §5.2's copy rules
are all encoded here, and each one exists because its absence caused a specific
loss of trust:

* **Say what did *not* change.** ``name``/``age``/``nationality``/``appearance``
  get an explicit "unchanged" line even when they are absent from the change
  set. Silence about these four is what made users believe the tool was
  rewriting their player.
* **Count, don't enumerate past 6.** "6 PlayStyles added — Finesse Shot, Rapid,
  …", not a 34-item dump nobody reads.
* **Unknown ``before`` is an em dash in PROV_UNKNOWN**, plus the words "not read
  from the game", plus a footer callout. The app is allowed to say it does not
  know (H6); it is not allowed to imply it does.
* **The OVR delta is hoisted** and rendered as two chips coloured by their own
  bands, so an upgrade reads as a colour jump (§6.3).
* **The button is ``Apply 5``, not ``OK``.** The count is part of the label.

:func:`build_diff` is pure — no Tk anywhere in its import path — because this is
the one piece of presentation logic that must be exhaustively tested. The
widgets below merely render its output.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from .. import theme
from .primitives import (
    ARROW,
    EM_DASH,
    button,
    divider,
    ensure_ctk,
    muted_label,
    panel,
    text_label,
    themed_scroll,
    ui_font,
)
from .ovr import OvrDelta, ovr_delta, ovr_delta_chips

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

Change = tuple[Any, Any]

#: The field whose delta is hoisted to the top of the sheet as chips.
HEADLINE_FIELDS: tuple[str, ...] = ("overallrating", "ovr", "overall")

#: Fields users worry about. §5.2: say what did *not* change, explicitly.
DEFAULT_REASSURE: tuple[str, ...] = ("name", "age", "nationality", "appearance")

#: Enough labels for the fields the spec names by hand; callers pass the rest.
DEFAULT_LABELS: Mapping[str, str] = {
    "overallrating": "Overall",
    "ovr": "Overall",
    "overall": "Overall",
    "potential": "Potential",
    "pot": "Potential",
    "playstyles": "PlayStyles",
    "preferredposition1": "Position",
    "position": "Position",
    "name": "Name",
    "firstname": "First name",
    "surname": "Surname",
    "age": "Age",
    "birthdate": "Date of birth",
    "nationality": "Nationality",
    "appearance": "Appearance",
    "height": "Height",
    "weight": "Weight",
    "skillmoves": "Skill moves",
    "weakfootabilitytypecode": "Weak foot",
}

_ENUMERATE_CAP = 6


def humanise(key: str) -> str:
    """``skill_moves`` / ``skillMoves`` / ``position1`` -> a readable label.

    A last resort only. All-lowercase run-together names (``preferredposition1``)
    cannot be split without a word list, so they come back as
    ``Preferredposition 1`` — which is why callers pass ``labels`` for anything
    a user actually reads.
    """
    text = str(key or "").replace("_", " ").strip()
    if not text:
        return ""
    out: list[str] = []
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ""
        if i and prev != " " and (
            (ch.isdigit() and not prev.isdigit())
            or (ch.isupper() and not prev.isupper())
        ):
            out.append(" ")
        out.append(ch)
    return "".join(out).capitalize()


def _label_for(key: str, labels: Mapping[str, str]) -> str:
    if key in labels:
        return labels[key]
    low = key.casefold()
    if low in labels:
        return labels[low]
    if low in DEFAULT_LABELS:
        return DEFAULT_LABELS[low]
    return humanise(key)


def _is_collection(value: Any) -> bool:
    return isinstance(value, (list, tuple, set, frozenset))


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if _is_collection(value):
        return {str(v) for v in value}
    return {str(value)}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _enumerate(items: Sequence[str], cap: int = _ENUMERATE_CAP) -> str:
    """Count, don't enumerate, past ``cap`` (§5.2)."""
    ordered = sorted(items)
    if len(ordered) <= cap:
        return ", ".join(ordered)
    return ", ".join(ordered[:cap]) + f", +{len(ordered) - cap} more"


def _fmt(value: Any) -> str:
    if value is None:
        return EM_DASH
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


@dataclass(frozen=True, slots=True)
class DiffLine:
    """One semantic change, phrased. Not a field dump.

    ``text`` is the value clause the sheet renders next to the label
    (``86 → 91``); ``short`` is the self-contained form the Changes bar uses
    (``Overall 86 → 91``). They are different strings on purpose — a one-line
    summary that reads "86 → 91, 4 added" tells the user nothing.
    """

    field: str
    label: str
    kind: str          # ovr | numeric | collection | boolean | scalar | unchanged | untouched
    text: str
    short: str
    before: Any = None
    after: Any = None
    group: str = "Other"
    note: str = ""
    tone: str = "info"
    before_known: bool = True

    @property
    def is_change(self) -> bool:
        """``unchanged``/``untouched`` lines are reassurance, not writes."""
        return self.kind not in ("unchanged", "untouched")


@dataclass(frozen=True, slots=True)
class DiffGroup:
    name: str
    lines: tuple[DiffLine, ...] = ()

    @property
    def change_count(self) -> int:
        return sum(1 for line in self.lines if line.is_change)


@dataclass(frozen=True, slots=True)
class DiffModel:
    """The whole preview: what changes, what does not, and what we never read."""

    lines: tuple[DiffLine, ...] = ()
    groups: tuple[DiffGroup, ...] = ()
    headline: DiffLine | None = None
    headline_delta: OvrDelta | None = None
    subject: str = ""
    subject_id: int | None = None
    unread: tuple[str, ...] = ()
    undo_available: bool | None = None
    blast_radius: str = ""

    @property
    def count(self) -> int:
        return sum(1 for line in self.lines if line.is_change)

    @property
    def has_changes(self) -> bool:
        return self.count > 0

    @property
    def title(self) -> str:
        if not self.has_changes:
            return "No changes staged."
        n = self.count
        return f"Review {n} change{'' if n == 1 else 's'} before applying"

    @property
    def apply_label(self) -> str:
        """§5.2: the button is ``Apply 5``, not ``OK``. The count is the label."""
        return f"Apply {self.count}" if self.has_changes else "Apply"

    @property
    def subject_text(self) -> str:
        if self.subject and self.subject_id is not None:
            return f"{self.subject}  (id {self.subject_id})"
        return self.subject or (f"id {self.subject_id}" if self.subject_id else "")

    @property
    def unread_note(self) -> str:
        """H6/P3: name the values we never read, in the user's words."""
        if not self.unread:
            return ""
        n = len(self.unread)
        which = _enumerate(self.unread, cap=4)
        noun = "value was" if n == 1 else "values were"
        return (
            f"{n} {noun} never read from the game ({which}) — they show as "
            f"{EM_DASH}. Only what you changed is written."
        )

    @property
    def undo_note(self) -> str:
        """§5.2's ``↩`` line. State undo availability *before* committing (H7)."""
        if self.undo_available is None:
            return ""
        if self.undo_available:
            return "An undo snapshot will be saved before this is applied."
        return "This change cannot be undone automatically."

    def summary(self, *, limit: int = 3) -> str:
        """The one-line form for the Changes bar (§5.1).

        Ordered so the OVR delta leads, because that is the number a user scans
        for. Overflow becomes "+N more" rather than an unbounded string that
        pushes the Apply button off a 1040 px window (A7).
        """
        changes = [line for line in self.lines if line.is_change]
        if not changes:
            return "No changes staged."
        if self.headline is not None:
            changes = [self.headline] + [c for c in changes if c is not self.headline]
        parts = [line.short for line in changes[:limit]]
        extra = len(changes) - len(parts)
        if extra > 0:
            parts.append(f"+{extra} more")
        head = f"{self.subject} · " if self.subject else ""
        return head + " · ".join(parts)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def pairs_from(base: Mapping[str, Any], dirty: Mapping[str, Any]) -> dict[str, Change]:
    """``EditorState.base`` + ``EditorState.dirty`` -> ``{field: (before, after)}``.

    A field the user edited that has no base value yields ``before=None``, which
    is exactly right: we are about to write something we never read, and the
    diff must say so.
    """
    return {key: (base.get(key), value) for key, value in dict(dirty).items()}


def _line_for(
    key: str,
    before: Any,
    after: Any,
    *,
    label: str,
    group: str,
    note: str,
) -> DiffLine:
    before_known = before is not None
    common = dict(field=key, label=label, group=group, note=note,
                  before=before, after=after, before_known=before_known)

    if before == after and before_known:
        return DiffLine(kind="unchanged", text="unchanged",
                        short=f"{label} unchanged", tone="muted", **common)

    if _is_collection(after) or _is_collection(before):
        old, new = _as_set(before), _as_set(after)
        added, removed = sorted(new - old), sorted(old - new)
        if not added and not removed:
            return DiffLine(kind="unchanged", text="unchanged",
                            short=f"{label} unchanged", tone="muted", **common)
        bits: list[str] = []
        if added:
            bits.append(f"{len(added)} added — {_enumerate(added)}")
        if removed:
            bits.append(f"{len(removed)} removed — {_enumerate(removed)}")
        signs = []
        if added:
            signs.append(f"+{len(added)}")
        if removed:
            signs.append(f"-{len(removed)}")
        return DiffLine(
            kind="collection",
            text="; ".join(bits),
            short=f"{'/'.join(signs)} {label}",
            tone="ok" if added and not removed else "info",
            **common,
        )

    if isinstance(after, bool) or isinstance(before, bool):
        return DiffLine(
            kind="boolean",
            text=f"{_fmt(before)} {ARROW} {_fmt(after)}",
            short=f"{label} {_fmt(after)}",
            tone="info",
            **common,
        )

    if _is_number(after) and (_is_number(before) or not before_known):
        tone = "info"
        if before_known and _is_number(before):
            tone = "ok" if after > before else "warn" if after < before else "info"
        return DiffLine(
            kind="ovr" if key.casefold() in HEADLINE_FIELDS else "numeric",
            text=f"{_fmt(before)} {ARROW} {_fmt(after)}",
            short=f"{label} {_fmt(before)} {ARROW} {_fmt(after)}",
            tone=tone,
            **common,
        )

    return DiffLine(
        kind="scalar",
        text=f"{_fmt(before)} {ARROW} {_fmt(after)}",
        short=f"{label} {_fmt(before)} {ARROW} {_fmt(after)}",
        tone="info",
        **common,
    )


def build_diff(
    changes: Mapping[str, Change],
    *,
    labels: Mapping[str, str] | None = None,
    groups: Mapping[str, str] | None = None,
    notes: Mapping[str, str] | None = None,
    subject: str = "",
    subject_id: int | None = None,
    reassure: Sequence[str] = DEFAULT_REASSURE,
    undo_available: bool | None = None,
    blast_radius: str = "",
) -> DiffModel:
    """Turn ``{field: (before, after)}`` into phrased, grouped, honest lines.

    ``before is None`` means *never read* (P3), not "zero" and not "empty". The
    resulting line renders an em dash, carries the "not read from the game"
    note, and is counted into :attr:`DiffModel.unread`.
    """
    labels = dict(labels or {})
    groups = dict(groups or {})
    notes = dict(notes or {})

    lines: list[DiffLine] = []
    unread: list[str] = []
    for key, pair in dict(changes).items():
        before, after = (pair if isinstance(pair, tuple) else (None, pair))
        label = _label_for(key, labels)
        line = _line_for(
            key, before, after,
            label=label,
            group=groups.get(key, "Changes"),
            note=notes.get(key, ""),
        )
        if not line.before_known and line.is_change:
            line = replace(line, note=line.note or "not read from the game")
            unread.append(label)
        lines.append(line)

    # §5.2: name the four fields users worry about, even when untouched.
    present = {k.casefold() for k in changes}
    for key in reassure:
        if key.casefold() in present:
            continue
        label = _label_for(key, labels)
        lines.append(
            DiffLine(
                field=key, label=label, kind="untouched", text="not touched",
                short=f"{label} unchanged", group=groups.get(key, "Not touched"),
                tone="muted", note=notes.get(key, ""),
            )
        )

    headline = next(
        (line for line in lines
         if line.field.casefold() in HEADLINE_FIELDS and line.is_change),
        None,
    )
    delta = ovr_delta(headline.before, headline.after) if headline is not None else None

    order: list[str] = []
    buckets: dict[str, list[DiffLine]] = {}
    for line in lines:
        buckets.setdefault(line.group, [])
        if line.group not in order:
            order.append(line.group)
        buckets[line.group].append(line)

    return DiffModel(
        lines=tuple(lines),
        groups=tuple(DiffGroup(name=name, lines=tuple(buckets[name])) for name in order),
        headline=headline,
        headline_delta=delta,
        subject=subject,
        subject_id=subject_id,
        unread=tuple(unread),
        undo_available=undo_available,
        blast_radius=blast_radius,
    )


def summarise(changes: Mapping[str, Change], **kw: Any) -> str:
    """One-line convenience for the Changes bar: build and summarise in one step."""
    return build_diff(changes, **kw).summary()


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


def _line_row(parent: Any, line: DiffLine) -> Any:
    ensure_ctk()
    row = ctk.CTkFrame(parent, fg_color="transparent")
    text_label(
        row, line.label, size=12,
        color=theme.MUTED if line.is_change else theme.MUTED_DIM, width=150,
    ).pack(side="left", anchor="n")

    col = ctk.CTkFrame(row, fg_color="transparent")
    col.pack(side="left", fill="x", expand=True, anchor="w")
    value = ctk.CTkLabel(
        col,
        text=line.text,
        font=ui_font(12, bold=line.is_change),
        # P3: an unread "before" is dimmed to PROV_UNKNOWN, never TEXT.
        text_color=(
            theme.PROV_UNKNOWN if not line.before_known
            else theme.TEXT if line.is_change else theme.MUTED_DIM
        ),
        anchor="w",
        justify="left",
        wraplength=440,
    )
    value.pack(anchor="w", fill="x")

    def _rewrap(event: Any) -> None:
        """Wrap to the *actual* width. A fixed wraplength clips a long
        PlayStyles line in any panel narrower than it (§5.2 must stay readable
        at the 1040 px minimum window)."""
        try:
            value.configure(wraplength=max(140, int(event.width) - theme.SP2))
        except Exception:
            pass

    try:
        from .primitives import debounce

        col.bind("<Configure>", debounce(col, 120, _rewrap), add="+")
    except Exception:
        pass
    if line.note:
        muted_label(col, line.note, size=10).pack(anchor="w")
    return row


def diff_view(parent: Any, model: DiffModel, *, on_view_raw: Callable[[], None] | None = None) -> Any:
    """Render a :class:`DiffModel`. Used by the sheet, the drawer and Verify."""
    ensure_ctk()
    host = ctk.CTkFrame(parent, fg_color="transparent")

    if model.subject_text:
        text_label(host, model.subject_text.upper(), size=13, bold=True).pack(
            anchor="w", pady=(0, theme.SP2)
        )

    if model.headline is not None and model.headline_delta is not None:
        head = panel(host, level=2)
        head.pack(fill="x", pady=(0, theme.SP3))
        inner = ctk.CTkFrame(head, fg_color="transparent")
        inner.pack(padx=theme.SP4, pady=theme.SP3, anchor="w")
        muted_label(inner, model.headline.label.upper(), size=10).pack(anchor="w")
        ovr_delta_chips(inner, model.headline.before, model.headline.after,
                        size="lg").pack(anchor="w", pady=(theme.SP1, 0))
        if model.headline_delta.band_changed:
            muted_label(
                inner,
                f"{model.headline_delta.band_before} {ARROW} "
                f"{model.headline_delta.band_after}",
                size=10,
            ).pack(anchor="w", pady=(theme.SP1, 0))

    for group in model.groups:
        if not group.lines:
            continue
        block = ctk.CTkFrame(host, fg_color="transparent")
        block.pack(fill="x", pady=(0, theme.SP3))
        muted_label(block, group.name.upper(), size=10).pack(anchor="w",
                                                             pady=(0, theme.SP1))
        for line in group.lines:
            if line is model.headline:
                continue  # already shown, prominently
            _line_row(block, line).pack(fill="x", pady=1)

    if model.unread_note:
        warn = ctk.CTkFrame(
            host, fg_color=theme.CARD, corner_radius=theme.R_SM,
            border_width=1, border_color=theme.WARNING,
        )
        warn.pack(fill="x", pady=(0, theme.SP3))
        ctk.CTkLabel(
            warn, text=model.unread_note, font=ui_font(11),
            text_color=theme.WARNING, wraplength=560, justify="left", anchor="w",
        ).pack(fill="x", padx=theme.SP3, pady=theme.SP2)

    if model.blast_radius:
        muted_label(host, f"⚠ {model.blast_radius}", size=11).pack(
            anchor="w", pady=(0, theme.SP2)
        )
    if model.undo_note:
        muted_label(host, f"↩ {model.undo_note}", size=11).pack(anchor="w")
    if on_view_raw is not None:
        button(host, f"▸ {len(model.lines)} raw fields · view Lua", on_view_raw,
               kind="ghost", height=26).pack(anchor="w", pady=(theme.SP3, 0))
    return host


def diff_sheet(
    parent: Any,
    model: DiffModel,
    *,
    on_apply: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
    on_view_raw: Callable[[], None] | None = None,
    blocked_reason: str = "",
) -> Any:
    """The modal review sheet. One primary (``Apply N``), ``Esc`` cancels (A1).

    Returns the ``CTkToplevel`` so the caller can keep or drop the reference;
    it destroys itself on either action.
    """
    ensure_ctk()
    win = ctk.CTkToplevel(parent)
    win.title("Review changes")
    win.configure(fg_color=theme.BG)
    win.geometry("680x560")
    win.transient(parent.winfo_toplevel())

    def _close() -> None:
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass
        if on_cancel is not None:
            on_cancel()

    header = ctk.CTkFrame(win, fg_color="transparent")
    header.pack(fill="x", padx=theme.SP5, pady=(theme.SP5, theme.SP3))
    text_label(header, model.title, size=18, bold=True).pack(anchor="w")

    scroll = themed_scroll(win, fill=theme.BG, height=320)
    scroll.pack(fill="x", padx=theme.SP5)
    diff_view(scroll.inner, model, on_view_raw=on_view_raw).pack(fill="x")

    divider(win).pack(fill="x", pady=(theme.SP3, 0))
    footer = ctk.CTkFrame(win, fg_color="transparent")
    footer.pack(fill="x", padx=theme.SP5, pady=theme.SP4)
    if blocked_reason:
        muted_label(
            footer,
            f"Can't apply yet: {blocked_reason}",
            size=10,
            color=theme.WARNING,
            wraplength=360,
        ).pack(side="left", fill="x", expand=True, padx=(0, theme.SP2))

    def _apply() -> None:
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass
        on_apply()

    apply_btn = button(footer, model.apply_label, _apply, kind="primary", width=150,
                       height=34, disabled_reason=blocked_reason)
    apply_btn.pack(side="right")
    button(footer, "Cancel", _close, kind="ghost", height=34).pack(
        side="right", padx=(0, theme.SP2)
    )

    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda _e: _close())
    try:
        win.after(50, win.grab_set)          # modal, but never before it maps
        win.after(60, apply_btn.focus_set)   # A2: focus lands on the primary
    except Exception:
        pass
    return win
