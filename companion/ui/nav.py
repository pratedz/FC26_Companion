"""The surface registry: five tabs, one drawer, registered by name and built late.

WHY a registry instead of five imports in ``shell.py``
------------------------------------------------------
Two v1 failures, one mechanism.

**The dead Catalog tab.** v1 built its tabs behind a bare ``except Exception:
pass``. When the Catalog tab raised during construction the exception was
swallowed, the tab rendered as an empty frame, and the app reported nothing.
Users concluded the feature did not exist. The fix is *not* to remove the
try/except — a surface that raises must still not take the window down — it is
to make the failure **visible and actionable**: the registry catches, records
the traceback, and asks the caller to render an :func:`states.error_state` in
the tab's place, with a Retry that rebuilds it.

**Eager construction.** P-1 puts cold start to interactive under 400 ms with
only the Club shell built. If ``shell.py`` did ``from .surfaces.library import
build``, importing the shell would import every surface, its search index code
and its catalog client. So a :class:`Surface` names its builder as a *dotted
path string*; nothing is imported until the user clicks the tab.

The consequence worth stating: ``shell.py`` has no import edge to any surface,
so a syntax error in Automations cannot prevent Club from painting.
"""

from __future__ import annotations

import importlib
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

from . import theme
from .widgets.primitives import ensure_ctk

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

#: A surface builder has the same dumb-view signature as everything else:
#: ``(parent, svc, vm) -> widget``. The registry supplies ``vm=None`` and lets
#: the surface construct its own per-view ViewModel.
Builder = Callable[..., Any]

TAB_WIDTH = 112

#: How much traceback to show a *user*. H4 wants the raw cause kept, not a wall
#: of it — an unfiltered import failure is ~14 lines of ``importlib._bootstrap``
#: frames wrapping one useful sentence.
_TRACE_LINES = 6
_NOISE = ("importlib._bootstrap", "<frozen ", "_bootstrap_external")


def _short_traceback(limit: int = _TRACE_LINES) -> str:
    """The app frames and the exception line; the interpreter's plumbing dropped."""
    lines = traceback.format_exc().strip().splitlines()
    keep = [ln for ln in lines if not any(n in ln for n in _NOISE)]
    if keep and keep[0].startswith("Traceback"):
        keep = keep[1:]  # the message is already the headline above this
    return "\n".join(keep[-limit:]).strip()


@dataclass(frozen=True, slots=True)
class Surface:
    """One destination. ``module``/``attr`` are resolved lazily, on first visit."""

    key: str
    title: str
    module: str
    attr: str = "build"
    hotkey: str = ""
    glyph: str = ""
    order: int = 0
    #: Answers "what is this for?" — §2.2's one-line job statement.
    purpose: str = ""


#: §2.2 — five primary surfaces, in tab order. Tabs are click-only (no number keys).
SURFACES: tuple[Surface, ...] = (
    Surface("club", "Club", "companion.ui.surfaces.club", order=1,
            glyph="⌂", purpose="What is my squad, and is it ready?"),
    Surface("player", "Player", "companion.ui.surfaces.player", order=2,
            glyph="◉", purpose="Change this one player."),
    Surface("add_player", "Sign", "companion.ui.surfaces.add_player",
            order=3, glyph="+", purpose="Review and sign a Library card for my current team."),
    Surface("injury", "Injuries", "companion.ui.surfaces.injury",
            order=4, glyph="✚", purpose="Scan and clear squad injuries."),
    Surface("automations", "Automations", "companion.ui.surfaces.automations",
            order=5, glyph="⟳", purpose="Run a scripted operation on many players."),
    Surface("library", "Library", "companion.ui.surfaces.library", order=6,
            glyph="▤", purpose="Find a card, a player, or more data."),
)

#: The Activity drawer (§3.8) is registered the same way so the shell does not
#: import it either — a broken drawer must not cost the user their window.
DRAWER = Surface(
    "activity", "Activity", "companion.ui.surfaces.activity",
    hotkey="Ctrl+J", order=99, purpose="What did I do, and can I take it back?",
)


class SurfaceError(RuntimeError):
    """A surface failed to import or build. Carries the traceback for the UI."""

    def __init__(self, key: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.key = key
        self.detail = detail


class SurfaceRegistry:
    """Registration, lazy resolution, and per-surface failure isolation."""

    def __init__(self, surfaces: Sequence[Surface] = ()) -> None:
        self._surfaces: dict[str, Surface] = {}
        self._built: dict[str, Any] = {}
        self.errors: dict[str, SurfaceError] = {}
        for surface in surfaces:
            self.register(surface)

    # ---- registration -------------------------------------------------

    def register(self, surface: Surface) -> None:
        self._surfaces[surface.key] = surface

    def __contains__(self, key: str) -> bool:
        return key in self._surfaces

    def __iter__(self) -> Iterator[Surface]:
        return iter(self.ordered())

    def get(self, key: str) -> Surface | None:
        return self._surfaces.get(key)

    def ordered(self) -> tuple[Surface, ...]:
        return tuple(sorted(self._surfaces.values(), key=lambda s: (s.order, s.key)))

    def keys(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.ordered())

    def hotkeys(self) -> dict[str, str]:
        """``{"1": "club", …}`` for A3's number-key surface switching."""
        return {s.hotkey: s.key for s in self.ordered() if s.hotkey and len(s.hotkey) == 1}

    # ---- resolution ---------------------------------------------------

    def resolve(self, key: str) -> Builder:
        """Import the surface module and return its builder. Raises on failure.

        Separated from :meth:`build` so tests can assert the *import* contract
        without a Tk parent, and so the error text distinguishes "module is
        missing" from "module raised while building".
        """
        surface = self._surfaces.get(key)
        if surface is None:
            raise SurfaceError(key, f"No surface registered as {key!r}.")
        try:
            module = importlib.import_module(surface.module)
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            # Bind the message NOW. v1 lost 13 error messages by letting a
            # deferred callback close over an `except ... as e` name that
            # Python had already deleted.
            message = f"{surface.title} could not be loaded: {exc}"
            raise SurfaceError(key, message, _short_traceback()) from exc
        builder = getattr(module, surface.attr, None)
        if not callable(builder):
            raise SurfaceError(
                key,
                f"{surface.module} has no callable {surface.attr!r}.",
                f"found: {sorted(n for n in vars(module) if not n.startswith('_'))}",
            )
        return builder

    def build(
        self,
        key: str,
        parent: Any,
        svc: Any,
        *,
        vm: Any = None,
        error_factory: Callable[..., Any] | None = None,
        retry: Callable[[], None] | None = None,
    ) -> Any:
        """Build a surface, substituting an ErrorState if it fails.

        Returns the widget either way, so a caller can always pack *something*.
        ``error_factory`` is injectable purely so this can be tested without Tk.
        """
        try:
            builder = self.resolve(key)
            widget = builder(parent, svc, vm)
            if widget is None:
                raise SurfaceError(
                    key, f"{key} built nothing.",
                    "A surface builder must return the widget it created.",
                )
            self.errors.pop(key, None)
            self._built[key] = widget
            return widget
        except SurfaceError as exc:
            self.errors[key] = exc
            return self._render_error(key, parent, exc, error_factory, retry)
        except Exception as exc:  # noqa: BLE001 — a broken surface is not a dead app
            message = f"{self.title_of(key)} failed to open: {exc}"
            wrapped = SurfaceError(key, message, _short_traceback())
            self.errors[key] = wrapped
            return self._render_error(key, parent, wrapped, error_factory, retry)

    def _render_error(
        self,
        key: str,
        parent: Any,
        exc: SurfaceError,
        error_factory: Callable[..., Any] | None,
        retry: Callable[[], None] | None,
    ) -> Any:
        factory = error_factory
        if factory is None:
            from .widgets.states import error_state

            factory = error_state
        return factory(
            parent,
            message=str(exc),
            cause=exc.detail,
            action_label="Retry",
            action=retry or (lambda: None),
        )

    def title_of(self, key: str) -> str:
        surface = self._surfaces.get(key)
        return surface.title if surface else key

    def built(self, key: str) -> Any:
        return self._built.get(key)

    def forget(self, key: str) -> None:
        """Drop a cached surface so the next visit rebuilds it (Retry)."""
        self._built.pop(key, None)
        self.errors.pop(key, None)


def default_registry() -> SurfaceRegistry:
    """The five surfaces plus the Activity drawer."""
    registry = SurfaceRegistry(SURFACES)
    registry.register(DRAWER)
    return registry


# ---------------------------------------------------------------------------
# The tab bar
# ---------------------------------------------------------------------------


def tab_bar(
    parent: Any,
    registry: SurfaceRegistry,
    *,
    active: str,
    on_select: Callable[[str], None],
    include: Sequence[str] = (),
) -> Any:
    """§3.1's tab strip: label plus a 2 px underline on the active surface.

    Returns the frame with a ``set_active(key)`` attached, so the shell repaints
    the strip without rebuilding it — rebuilding a tab bar on every state change
    is what made v1's chrome flicker.
    """
    ensure_ctk()
    keys = tuple(include) or tuple(
        s.key for s in registry.ordered() if s.key != DRAWER.key
    )
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    tabs: dict[str, tuple[Any, Any]] = {}

    for key in keys:
        surface = registry.get(key)
        if surface is None:
            continue
        column = ctk.CTkFrame(frame, fg_color="transparent")
        column.pack(side="left", padx=(0, theme.SP1))
        caption = surface.title
        if surface.glyph:
            caption = f"{surface.glyph}  {surface.title}"
        label = ctk.CTkButton(
            column,
            text=caption,
            command=lambda k=key: on_select(k),
            fg_color="transparent",
            hover_color=theme.CARD_HOVER,
            text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT, size=13),
            corner_radius=theme.R_SM,
            width=TAB_WIDTH,
            height=theme.BTN_MD,
        )
        label.pack()
        # The underline must declare TAB_WIDTH: an unsized CTkFrame requests
        # CTk's 200 px default, which silently widened every tab column to 200
        # and pushed the connection chip off the right of the chrome.
        rule = ctk.CTkFrame(column, height=2, width=TAB_WIDTH,
                            fg_color="transparent", corner_radius=0)
        rule.pack(fill="x", padx=theme.SP2)
        if surface.purpose:
            from .widgets.primitives import tooltip

            tip = surface.purpose
            if surface.hotkey:
                tip = f"{tip}   ({surface.hotkey})"
            tooltip(label, tip)
        tabs[key] = (label, rule)

    def set_active(key: str) -> None:
        for k, (label, rule) in tabs.items():
            on = k == key
            try:
                label.configure(
                    text_color=theme.TEXT if on else theme.MUTED,
                    font=ctk.CTkFont(family=theme.FONT, size=13,
                                     weight="bold" if on else "normal"),
                )
                rule.configure(fg_color=theme.ACCENT if on else "transparent")
            except Exception:
                pass

    frame.set_active = set_active  # type: ignore[attr-defined]
    set_active(active)
    return frame


SIDEBAR_WIDTH = 196


def side_nav(
    parent: Any,
    registry: SurfaceRegistry,
    *,
    active: str,
    on_select: Callable[[str], None],
    include: Sequence[str] = (),
) -> Any:
    """Vertical page list. Same ``set_active`` contract as :func:`tab_bar`."""
    ensure_ctk()
    keys = tuple(include) or tuple(
        s.key for s in registry.ordered() if s.key != DRAWER.key
    )
    frame = ctk.CTkFrame(
        parent,
        fg_color=theme.CHROME,
        corner_radius=0,
        width=SIDEBAR_WIDTH,
        border_width=0,
    )
    frame.pack_propagate(False)
    brand = ctk.CTkFrame(frame, fg_color="transparent")
    brand.pack(fill="x", padx=theme.SP3, pady=(theme.SP4, theme.SP3))
    ctk.CTkLabel(
        brand,
        text="FC26",
        font=ctk.CTkFont(family=theme.FONT, size=18, weight="bold"),
        text_color=theme.TEXT,
        anchor="w",
    ).pack(anchor="w")
    ctk.CTkLabel(
        brand,
        text="Companion",
        font=ctk.CTkFont(family=theme.FONT, size=12),
        text_color=theme.MUTED,
        anchor="w",
    ).pack(anchor="w")

    tabs: dict[str, tuple[Any, Any]] = {}
    for key in keys:
        surface = registry.get(key)
        if surface is None:
            continue
        row = ctk.CTkFrame(frame, fg_color="transparent", height=40)
        row.pack(fill="x", padx=theme.SP2, pady=1)
        row.pack_propagate(False)
        mark = ctk.CTkFrame(row, width=3, fg_color="transparent", corner_radius=0)
        mark.pack(side="left", fill="y")
        caption = surface.title
        if surface.glyph:
            caption = f"{surface.glyph}   {surface.title}"
        label = ctk.CTkButton(
            row,
            text=caption,
            command=lambda k=key: on_select(k),
            fg_color="transparent",
            hover_color=theme.CARD_HOVER,
            text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT, size=14),
            anchor="w",
            corner_radius=theme.R_SM,
            height=36,
        )
        label.pack(side="left", fill="both", expand=True, padx=(theme.SP1, 0))
        if surface.purpose:
            from .widgets.primitives import tooltip

            tooltip(label, surface.purpose)
        tabs[key] = (label, mark)

    def set_active(key: str) -> None:
        for k, (label, mark) in tabs.items():
            on = k == key
            try:
                label.configure(
                    fg_color=theme.CARD if on else "transparent",
                    text_color=theme.TEXT if on else theme.MUTED,
                    font=ctk.CTkFont(
                        family=theme.FONT, size=14, weight="bold" if on else "normal",
                    ),
                )
                mark.configure(fg_color=theme.ACCENT if on else "transparent")
            except Exception:
                pass

    frame.set_active = set_active  # type: ignore[attr-defined]
    set_active(active)
    return frame


def segmented_rail(
    parent: Any,
    items: Sequence[tuple[str, str]],
    *,
    active: str,
    on_select: Callable[[str], None],
) -> Any:
    """Token-styled source switcher — underline, not CTkTabview chrome."""
    ensure_ctk()
    frame = ctk.CTkFrame(
        parent,
        fg_color=theme.PANEL,
        corner_radius=theme.R_MD,
        border_width=1,
        border_color=theme.BORDER,
        height=44,
    )
    frame.pack_propagate(False)
    inner = ctk.CTkFrame(frame, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=theme.SP2, pady=theme.SP1)
    tabs: dict[str, tuple[Any, Any]] = {}
    for key, title in items:
        column = ctk.CTkFrame(inner, fg_color="transparent")
        column.pack(side="left", padx=(0, theme.SP1), fill="y")
        label = ctk.CTkButton(
            column,
            text=title,
            command=lambda k=key: on_select(k),
            fg_color="transparent",
            hover_color=theme.CARD_HOVER,
            text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT, size=13),
            corner_radius=theme.R_SM,
            width=140,
            height=theme.BTN_MD,
        )
        label.pack()
        rule = ctk.CTkFrame(
            column, height=2, width=140, fg_color="transparent", corner_radius=0,
        )
        rule.pack(fill="x", padx=theme.SP2)
        tabs[key] = (label, rule)

    def set_active(key: str) -> None:
        for k, (label, rule) in tabs.items():
            on = k == key
            try:
                label.configure(
                    text_color=theme.TEXT if on else theme.MUTED,
                    font=ctk.CTkFont(
                        family=theme.FONT, size=13, weight="bold" if on else "normal",
                    ),
                )
                rule.configure(fg_color=theme.ACCENT if on else "transparent")
            except Exception:
                pass

    frame.set_active = set_active  # type: ignore[attr-defined]
    set_active(active)
    return frame
