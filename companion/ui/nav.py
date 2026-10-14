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
    #: ``assets/icons/<icon>.png``, tinted by state in the sidebar.
    icon: str = ""


#: §2.2 — the primary surfaces, in sidebar order. Ctrl+1…6 switch between them.
SURFACES: tuple[Surface, ...] = (
    Surface("club", "Club", "companion.ui.surfaces.club", order=1, hotkey="Ctrl+1",
            icon="squad", purpose="Your squad, read from the open Career save."),
    Surface("player", "Player", "companion.ui.surfaces.player", order=2, hotkey="Ctrl+2",
            icon="player", purpose="Change one player with a card or with AI."),
    Surface("add_player", "Sign", "companion.ui.surfaces.add_player", order=3,
            hotkey="Ctrl+3", icon="contract",
            purpose="Search cards, fill the signing bag, check out."),
    Surface("injury", "Injuries", "companion.ui.surfaces.injury", order=4, hotkey="Ctrl+4",
            icon="medical", purpose="Scan the squad and heal injuries."),
    Surface("automations", "Matchday", "companion.ui.surfaces.automations", order=5,
            hotkey="Ctrl+5", icon="whistle", purpose="Prepare the squad before a match."),
    Surface("library", "Library", "companion.ui.surfaces.library", order=6, hotkey="Ctrl+6",
            icon="card", purpose="Find any card from FIFA 18 to FC 26."),
)

#: The Activity drawer (§3.8) is registered the same way so the shell does not
#: import it either — a broken drawer must not cost the user their window.
DRAWER = Surface(
    "activity", "Activity", "companion.ui.surfaces.activity",
    hotkey="Ctrl+J", order=99, icon="queue",
    purpose="Queued, running and finished jobs, plans, health and assets.",
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
            font=ctk.CTkFont(family=theme.FONT, size=theme.T_BODY),
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
                    font=ctk.CTkFont(family=theme.FONT, size=theme.T_BODY,
                                     weight="bold" if on else "normal"),
                )
                rule.configure(fg_color=theme.ACCENT if on else "transparent")
            except Exception:
                pass

    frame.set_active = set_active  # type: ignore[attr-defined]
    set_active(active)
    return frame


SIDEBAR_WIDTH = 200


def _logo_image(size: int) -> Any:
    """The brand mark for the sidebar, or None if the asset is missing."""
    try:
        from PIL import Image

        from .icons import icons_dir

        path = icons_dir().parent / "logo.png"
        if not path.is_file():
            return None
        return ctk.CTkImage(light_image=Image.open(path), size=(size, size))
    except Exception:
        return None


def _nav_row(
    parent: Any,
    *,
    icon: str,
    title: str,
    command: Callable[[], None],
    tip: str = "",
) -> dict[str, Any]:
    """One sidebar destination: tinted icon + label, whole row clickable."""
    from .icons import ctk_icon
    from .widgets.primitives import tooltip

    button = ctk.CTkButton(
        parent,
        text=title,
        image=ctk_icon(icon, 18, tint=theme.MUTED),
        compound="left",
        anchor="w",
        command=command,
        fg_color="transparent",
        hover_color=theme.CARD_HOVER,
        text_color=theme.MUTED,
        font=ctk.CTkFont(family=theme.FONT, size=theme.T_BODY),
        corner_radius=theme.R_SM,
        border_width=0,
        height=38,
    )
    if tip:
        tooltip(button, tip)
    return {"button": button, "icon": icon}


def _paint_nav_row(item: dict[str, Any], on: bool) -> None:
    from .icons import ctk_icon

    try:
        item["button"].configure(
            fg_color=theme.CARD if on else "transparent",
            text_color=theme.TEXT if on else theme.MUTED,
            image=ctk_icon(item["icon"], 18, tint=theme.ACCENT if on else theme.MUTED),
            font=ctk.CTkFont(
                family=theme.FONT, size=theme.T_BODY, weight="bold" if on else "normal",
            ),
        )
    except Exception:
        pass


def side_nav(
    parent: Any,
    registry: SurfaceRegistry,
    *,
    active: str,
    on_select: Callable[[str], None],
    include: Sequence[str] = (),
    on_settings: Callable[[], None] | None = None,
    version: str = "",
) -> Any:
    """Vertical page list with Settings pinned to the bottom.

    Same ``set_active`` contract as :func:`tab_bar`. The active page is marked by
    its filled row, accent icon and weight, not by a coloured edge bar.
    """
    ensure_ctk()
    from .widgets.primitives import display_font

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
    brand.pack(fill="x", padx=theme.SP4, pady=(theme.SP4, theme.SP5))
    mark = _logo_image(40)
    if mark is not None:
        ctk.CTkLabel(brand, text="", image=mark).pack(side="left")
    words = ctk.CTkFrame(brand, fg_color="transparent")
    words.pack(side="left", padx=(theme.SP2, 0))
    ctk.CTkLabel(
        words, text="FC26", font=display_font(theme.T_TITLE),
        text_color=theme.TEXT, anchor="w", height=24,
    ).pack(anchor="w")
    ctk.CTkLabel(
        words, text="COMPANION",
        font=ctk.CTkFont(family=theme.FONT, size=10, weight="bold"),
        text_color=theme.ACCENT, anchor="w", height=14,
    ).pack(anchor="w")

    tabs: dict[str, dict[str, Any]] = {}
    for key in keys:
        surface = registry.get(key)
        if surface is None:
            continue
        tip = surface.purpose
        if surface.hotkey:
            tip = f"{tip}  ({surface.hotkey})" if tip else surface.hotkey
        item = _nav_row(
            frame, icon=surface.icon, title=surface.title,
            command=lambda k=key: on_select(k), tip=tip,
        )
        item["button"].pack(fill="x", padx=theme.SP2, pady=1)
        tabs[key] = item

    foot = ctk.CTkFrame(frame, fg_color="transparent")
    foot.pack(side="bottom", fill="x", padx=theme.SP2, pady=(0, theme.SP3))
    if on_settings is not None:
        settings = _nav_row(
            foot, icon="settings", title="Settings", command=on_settings,
            tip="Display, AI, worker, paths and health  (Ctrl+,)",
        )
        settings["button"].pack(fill="x")
        frame.settings_button = settings["button"]  # type: ignore[attr-defined]
    if version:
        ctk.CTkLabel(
            foot, text=version, anchor="w",
            font=ctk.CTkFont(family=theme.FONT, size=theme.T_CAPTION),
            text_color=theme.MUTED_DIM,
        ).pack(fill="x", padx=theme.SP3, pady=(theme.SP2, 0))

    def set_active(key: str) -> None:
        for k, item in tabs.items():
            _paint_nav_row(item, k == key)

    frame.set_active = set_active  # type: ignore[attr-defined]
    frame.nav_buttons = {k: item["button"] for k, item in tabs.items()}  # type: ignore[attr-defined]
    set_active(active)
    return frame


def segmented_rail(
    parent: Any,
    items: Sequence[tuple[str, str]],
    *,
    active: str,
    on_select: Callable[[str], None],
    compact: bool = False,
) -> Any:
    """The one segmented control: a track with equal segments, the active one filled.

    Used for every in-page mode switch (Sign tabs, Player source, Activity
    sections) so a switch looks and behaves the same everywhere.
    """
    ensure_ctk()
    height = theme.BTN_MD + 6 if not compact else theme.BTN_SM + 8
    frame = ctk.CTkFrame(
        parent,
        fg_color=theme.PANEL,
        corner_radius=theme.R_SM + 2,
        border_width=1,
        border_color=theme.BORDER,
        height=height,
    )
    frame.grid_propagate(False)
    frame.grid_rowconfigure(0, weight=1)
    tabs: dict[str, Any] = {}
    for index, (key, title) in enumerate(items):
        frame.grid_columnconfigure(index, weight=1, uniform="segments")
        widget = ctk.CTkButton(
            frame,
            text=title,
            width=1,
            height=height - 8,
            font=ctk.CTkFont(family=theme.FONT, size=theme.T_CAPTION if compact else theme.T_BODY),
            fg_color="transparent",
            hover_color=theme.CARD,
            text_color=theme.MUTED,
            # CTk pads text by the corner radius; a compact five-way switch in
            # the 360 px drawer needs that width for the words.
            corner_radius=2 if compact else theme.R_SM,
            border_width=0,
            command=lambda k=key: on_select(k),
        )
        widget.grid(row=0, column=index, sticky="nsew", padx=2 if compact else 3, pady=3)
        tabs[key] = widget

    def set_active(key: str) -> None:
        for name, widget in tabs.items():
            on = name == key
            try:
                widget.configure(
                    fg_color=theme.CARD_HOVER if on else "transparent",
                    text_color=theme.TEXT if on else theme.MUTED,
                    font=ctk.CTkFont(
                        family=theme.FONT,
                        size=theme.T_CAPTION if compact else theme.T_BODY,
                        weight="bold" if on else "normal",
                    ),
                )
            except Exception:
                pass

    frame.set_active = set_active  # type: ignore[attr-defined]
    frame.segments = tabs  # type: ignore[attr-defined]
    set_active(active)
    return frame
