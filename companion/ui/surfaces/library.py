"""Library - multi-year card search, variants, matching and favorites."""

from __future__ import annotations

from typing import Any, Mapping

from ...app.presenters import search_view
from .. import theme
from ..widgets import states
from ..widgets.primitives import button, ensure_ctk, eyebrow, muted_label, panel, set_disabled, text_label
from ..widgets.table import Column, DataGrid, TableModel
from ._common import identity_row, monogram, section_header, set_status, surface_root

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def _target_route_copy() -> str:
    """One shared prerequisite route for every unselected-target state."""
    return "Lock a Club player to stage a card build, or Send to Sign with no lock."


def _compact_prompt(
    parent: Any,
    *,
    headline: str,
    detail: str,
    action_label: str,
    action: Any,
) -> None:
    """A purposeful first-use prompt, rather than a screen-sized empty grid."""
    card = panel(parent, level=1)
    card.pack(fill="x", pady=(theme.SP2, 0))
    row = ctk.CTkFrame(card, fg_color="transparent")
    row.pack(fill="x", padx=theme.SP3, pady=theme.SP3)
    copy = ctk.CTkFrame(row, fg_color="transparent")
    copy.pack(side="left", fill="x", expand=True)
    text_label(copy, headline, size=13, bold=True).pack(anchor="w")
    muted_label(copy, detail, size=11, wraplength=900, justify="left").pack(
        anchor="w", pady=(theme.SP1, 0)
    )
    button(row, action_label, action, kind="primary", height=theme.BTN_MD).pack(
        side="right", padx=(theme.SP3, 0)
    )


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    root = surface_root(parent)
    view_model = vm if isinstance(vm, dict) else {}
    state = svc.store.snapshot()
    search = search_view(state)
    section_header(
        root, "Library",
        subtitle="Find a card, inspect it, then choose one safe next step.",
    )
    _target_bar(root, svc, state)

    search_card = panel(root, level=1)
    search_card.pack(fill="x", pady=(0, theme.SP2))
    eyebrow(search_card, "Find a card").pack(
        anchor="w", padx=theme.SP3, pady=(theme.SP2, 0)
    )
    bar = ctk.CTkFrame(search_card, fg_color="transparent")
    bar.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP2))
    entry = _entry(bar, "Player or variant", 220)
    entry.pack(side="left", fill="x", expand=True)
    if search["query"] and not search["query"].startswith(("Best:", "Variants:")):
        entry.insert(0, search["query"])
    filters_row = ctk.CTkFrame(search_card, fg_color="transparent")
    filters_visible = {"value": bool(search.get("year") or dict(search.get("filters") or {}))}
    year = _entry(filters_row, "Year", 60)
    year.pack(side="left")
    if search.get("year"):
        year.insert(0, search["year"])
    filters = dict(search.get("filters") or {})
    minimum = _entry(filters_row, "Min OVR", 72)
    minimum.pack(side="left", padx=(theme.SP2, 0))
    if filters.get("ovr_min") not in (None, ""):
        minimum.insert(0, str(filters["ovr_min"]))
    maximum = _entry(filters_row, "Max OVR", 72)
    maximum.pack(side="left", padx=(theme.SP2, 0))
    if filters.get("ovr_max") not in (None, ""):
        maximum.insert(0, str(filters["ovr_max"]))

    def do_search() -> None:
        _search(
            svc, entry.get(), year=year.get(),
            filters={"ovr_min": minimum.get(), "ovr_max": maximum.get()},
        )

    entry.bind("<Return>", lambda _event: do_search())
    button(bar, "Search", do_search, kind="primary", height=theme.BTN_LG, width=76).pack(
        side="left", padx=(theme.SP2, 0)
    )
    button(
        bar, "Favorites", lambda: _show_favorites(svc),
        kind="secondary", height=theme.BTN_LG, width=82,
    ).pack(side="left", padx=(theme.SP2, 0))
    filter_button = button(bar, "Filters", lambda: None, kind="ghost", height=theme.BTN_LG, width=72)
    filter_button.pack(side="left", padx=(theme.SP2, 0))

    def toggle_filters() -> None:
        filters_visible["value"] = not filters_visible["value"]
        if filters_visible["value"]:
            filters_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            filter_button.configure(text="Hide filters")
        else:
            filters_row.pack_forget()
            filter_button.configure(text="Filters")

    filter_button.configure(command=toggle_filters)
    if filters_visible["value"]:
        filters_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
        filter_button.configure(text="Hide filters")
    if state.target.locked:
        button(
            bar, "Best match", lambda: _best_matches(svc),
            kind="secondary", height=theme.BTN_LG, width=92,
        ).pack(side="left", padx=(theme.SP2, 0))

    body = ctk.CTkFrame(root, fg_color="transparent")
    body.pack(fill="both", expand=True)
    if search["busy"]:
        states.loading_row(body, "Searching...").pack(anchor="w", pady=theme.SP3)
        return root
    if search["error"]:
        states.error_state(
            body, message=search["error"], action_label="Retry", action=do_search
        ).pack(fill="x", pady=theme.SP2)
        return root
    if search["empty"]:
        _compact_prompt(
            body, headline=f'No results for "{search["query"]}"',
            detail="Try a shorter name, another year, or a wider OVR range.",
            action_label="Clear search", action=lambda: _search(svc, ""),
        )
        return root
    if not search["results"]:
        missing_target = not state.target.locked
        _compact_prompt(
            body, headline="Search the local card library",
            detail=(
                "Search 238,000+ card observations here. " + _target_route_copy()
                if missing_target
                else "Search is local. Select a card to inspect it or stage its card build."
            ),
            action_label="Open Club" if missing_target else "Search Neymar",
            action=(
                (lambda: svc.ui.navigate("club"))
                if missing_target
                else (lambda: _search(svc, "Neymar"))
            ),
        )
        return root

    muted_label(
        body,
        f"{search['count']} card variant(s). "
        + (
            "Double-click to stage the card build."
            if state.target.locked
            else "Double-click to send the card to Sign."
        ),
        size=11,
    ).pack(anchor="w")
    rows = [_card_row(svc, result) for result in search["results"]]
    model = TableModel(
        columns=(
            Column("favorite", "FAV", width=38),
            Column("name", "NAME", width=175),
            Column("year", "YR", width=38),
            Column("ovr", "OVR", width=44, numeric=True, kind="ovr"),
            Column("pos", "POS", width=42),
            Column("variant", "VARIANT", width=175),
            Column("club", "CLUB", width=110),
        ),
        rows=rows, key_field="_key", multi_select=True,
    )
    selected: dict[str, Any] = {}
    selection: list[dict[str, Any]] = []
    inspector = panel(body, level=1)
    inspector.pack(fill="x", pady=(theme.SP2, 0))
    identity_host = ctk.CTkFrame(inspector, fg_color="transparent")
    identity_host.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, 0))
    identity_row(
        identity_host,
        letters="?",
        title="Select a card to inspect it",
        caption="Library stages the card build (ratings, skills, body, run style). Kit, name, age and face stay off here.",
    ).pack(anchor="w")
    inspector_actions = ctk.CTkFrame(inspector, fg_color="transparent")
    inspector_actions.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP2))
    stage_button = button(
        inspector_actions, "Stage card build for selected player",
        lambda: _stage_card(svc, selected),
        kind="primary", height=theme.BTN_MD,
        disabled_reason=(
            _target_route_copy()
            if not state.target.locked else "Select a card first."
        ),
    )
    stage_button.pack(side="left")
    send_button = button(
        inspector_actions, "Send to Sign", lambda: _add_to_team(svc, selected),
        kind="secondary", height=theme.BTN_MD,
        disabled_reason="Select a card first.",
    )
    send_button.pack(side="left", padx=(theme.SP2, 0))

    def select(row: dict) -> None:
        selected.clear()
        selected.update(row)
        name = row.get("name") or "card"
        target = state.target.name or state.target.playerid
        caption = (
            f"Will affect: {target}. Will copy: card build (ratings, skills, body, run style)."
            if state.target.locked
            else _target_route_copy()
        )
        for child in list(identity_host.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        identity_row(
            identity_host,
            letters=monogram(str(name)),
            title=str(name),
            caption=caption,
            ovr=row.get("ovr"),
        ).pack(anchor="w")
        set_disabled(
            stage_button,
            "" if state.target.locked else _target_route_copy(),
        )
        set_disabled(send_button, "")
        if state.target.locked:
            stage_button.configure(text=f"Stage card build for {target}")
            send_button.configure(text="Send to Sign")
        else:
            stage_button.configure(text="Choose a player to stage")
            send_button.configure(text="Send to Sign")
        set_status(
            svc,
            f"Inspecting {name}. "
            + (
                "Stage the card build or send the card to Sign."
                if state.target.locked
                else "Send the card to Sign."
            ),
        )

    def on_sel(rows: tuple) -> None:
        selection.clear()
        selection.extend(dict(r) for r in rows)
        if selection:
            select(selection[-1])

    def open_card(row: dict) -> None:
        select(row)
        if state.target.locked:
            _stage_card(svc, row)
        else:
            _add_to_team(svc, row)

    grid = DataGrid(
        body, model, show_checkboxes=True, on_row_click=select,
        on_row_activate=open_card,
        on_selection_change=on_sel,
    )
    grid.pack(fill="both", expand=True, pady=(theme.SP2, 0))
    more = ctk.CTkFrame(body, fg_color="transparent")
    more_visible = {"value": False}
    more_button = button(body, "More card tools", lambda: None, kind="ghost", height=theme.BTN_MD)
    more_button.pack(anchor="w", pady=(theme.SP2, 0))

    def toggle_more() -> None:
        more_visible["value"] = not more_visible["value"]
        if more_visible["value"]:
            more.pack(fill="x", pady=(theme.SP1, 0))
            more_button.configure(text="Hide card tools")
        else:
            more.pack_forget()
            more_button.configure(text="More card tools")

    more_button.configure(command=toggle_more)
    button(
        more, "Compare selected", lambda: _compare_selection(svc, selection, selected, state),
        kind="secondary", height=theme.BTN_MD,
    ).pack(side="left")
    button(
        more, "Favorite", lambda: _toggle_favorite(svc, selected),
        kind="secondary", height=theme.BTN_MD,
    ).pack(side="left", padx=(theme.SP2, 0))
    button(
        more, "Show variants", lambda: _show_variants(svc, selected),
        kind="secondary", height=theme.BTN_MD,
    ).pack(side="left", padx=(theme.SP2, 0))
    _sources_panel(root, svc, view_model)
    return root


def _card_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a library row (or its raw card) for LocalCatalog.compare."""
    raw = dict(row.get("_raw") or row)
    attrs = raw.get("attrs")
    if isinstance(attrs, str):
        try:
            import json

            attrs = json.loads(attrs)
        except Exception:
            attrs = {}
    if isinstance(attrs, Mapping):
        raw.update(attrs)
    return raw


def _compare_selection(
    svc: Any,
    selection: list[dict[str, Any]],
    selected: dict[str, Any],
    state: Any,
) -> None:
    """Compare two checked cards, or one card vs the locked live player base."""
    from ...domain.catalog import LocalCatalog

    left: Mapping[str, Any] | None = None
    right: Mapping[str, Any] | None = None
    title = "Compare"

    picks = list(selection) if selection else ([selected] if selected else [])
    if len(picks) >= 2:
        left = _card_fields(picks[0])
        right = _card_fields(picks[1])
        title = (
            f"{picks[0].get('name') or 'A'}  vs  {picks[1].get('name') or 'B'}"
        )
    elif len(picks) == 1 and state.target.locked and state.editor.base:
        left = dict(state.editor.base)
        right = _card_fields(picks[0])
        title = f"Live {state.target.name or state.target.playerid}  vs  {picks[0].get('name')}"
    else:
        set_status(
            svc,
            "Compare needs two checked cards, or one card plus a locked player with live values.",
        )
        return

    rows = LocalCatalog.compare(left, right)
    changed = [r for r in rows if r.get("changed")]
    _show_compare_dialog(title, changed or list(rows)[:40])
    set_status(
        svc,
        f"Compare: {len(changed)} field(s) differ "
        f"({len(rows)} comparable)." if rows else "Compare: no comparable fields.",
    )


def _show_compare_dialog(title: str, rows: list[Mapping[str, Any]] | tuple) -> None:
    ensure_ctk()
    win = ctk.CTkToplevel()
    win.title(f"Compare — {title}")
    win.geometry("560x420")
    win.configure(fg_color=theme.BG)
    muted_label(win, title, size=13).pack(anchor="w", padx=theme.SP3, pady=theme.SP2)
    if not rows:
        muted_label(
            win, "No field differences in the comparable set.", size=12,
        ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP3))
        return
    display = []
    for index, row in enumerate(rows[:80]):
        delta = row.get("delta")
        display.append({
            "field": row.get("field") or "—",
            "before": row.get("before"),
            "after": row.get("after"),
            "delta": f"{delta:+d}" if isinstance(delta, int) else "—",
            "_key": f"{row.get('field')}-{index}",
        })
    model = TableModel(
        columns=(
            Column("field", "FIELD", width=140, stretch=True),
            Column("before", "BEFORE", width=90),
            Column("after", "AFTER", width=90),
            Column("delta", "Δ", width=56, numeric=True),
        ),
        rows=display,
        key_field="_key",
    )
    grid = DataGrid(win, model, show_checkboxes=False, visible_rows=12)
    grid.pack(fill="both", expand=True, padx=theme.SP3, pady=(0, theme.SP3))


def _count_jsonl_lines(path: Any) -> int:
    lines = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lines, _ in enumerate(fh, 1):
                pass
    except OSError:
        return 0
    return lines


def _sources_panel(root: Any, svc: Any, vm: dict[str, Any] | None = None) -> None:
    """Catalog maintenance — status + FUT.GG multi-year sync / index rebuild."""
    from pathlib import Path

    view = vm if isinstance(vm, dict) else {}
    card = panel(root, level=1)
    card.pack(fill="x", pady=(0, theme.SP2))
    header = ctk.CTkFrame(card, fg_color="transparent")
    header.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    eyebrow(header, "Catalog tools").pack(side="left")
    muted_label(header, "Sync and diagnostics - not needed to search cards.", size=10).pack(
        side="left", padx=theme.SP2
    )
    inner = ctk.CTkFrame(card, fg_color="transparent")
    shown = {"value": False}

    def toggle() -> None:
        shown["value"] = not shown["value"]
        if shown["value"]:
            inner.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            reveal.configure(text="Hide catalog tools")
        else:
            inner.pack_forget()
            reveal.configure(text="Catalog tools")

    reveal = button(header, "Catalog tools", toggle, kind="ghost", height=theme.BTN_MD, width=112)
    reveal.pack(side="right")
    eyebrow(inner, "Sources").pack(anchor="w")
    root_path = Path(svc.paths.root)
    futgg_dir = root_path / "card_db" / "futgg"
    rows: list[tuple[str, Path]] = [
        ("Universe", root_path / "card_db" / "universe.sqlite"),
        ("Catalog", Path(svc.paths.universe_db)),
        ("Teams", root_path / "card_db" / "teams.sqlite"),
        ("Profiles", Path(svc.paths.profiles_file)),
    ]
    for label, path in rows:
        if path.is_file():
            mb = path.stat().st_size / 1e6
            muted_label(inner, f"{label}: OK · {mb:.1f} MB · {path.name}", size=11).pack(
                anchor="w"
            )
        else:
            muted_label(
                inner, f"{label}: missing · {path}", size=11, color=theme.WARNING,
            ).pack(anchor="w")

    # Per-year FUT.GG dumps (promos / Icons / Heroes) — line counts off UI thread.
    dumps_host = ctk.CTkFrame(inner, fg_color="transparent")
    dumps_host.pack(fill="x")
    if not futgg_dir.is_dir():
        muted_label(dumps_host, "FUT.GG folder missing", size=11, color=theme.WARNING).pack(
            anchor="w"
        )
    else:
        dumps = sorted(futgg_dir.glob("futgg_*.jsonl"))
        if not dumps:
            muted_label(
                dumps_host,
                "FUT.GG dumps: none yet (sync years 23–27)",
                size=11,
                color=theme.WARNING,
            ).pack(anchor="w")
        else:
            cache = view.setdefault("futgg_dump_stats", {})
            pending = muted_label(dumps_host, "FUT.GG dumps: counting…", size=11)
            pending.pack(anchor="w")

            def paint(stats: list[tuple[str, int, float]]) -> None:
                for child in list(dumps_host.winfo_children()):
                    try:
                        child.destroy()
                    except Exception:
                        pass
                for stem, lines, mb in stats:
                    muted_label(
                        dumps_host,
                        f"FUT.GG {stem}: {lines:,} cards · {mb:.1f} MB",
                        size=11,
                    ).pack(anchor="w")

            cached_stats = cache.get("rows")
            cache_key = tuple((str(path), path.stat().st_mtime) for path in dumps)
            if cached_stats is not None and cache.get("key") == cache_key:
                paint(list(cached_stats))
            else:
                paths = list(dumps)

                def work(_token: Any) -> None:
                    stats: list[tuple[str, int, float]] = []
                    key: list[tuple[str, float]] = []
                    for path in paths:
                        try:
                            mtime = path.stat().st_mtime
                            mb = path.stat().st_size / 1e6
                        except OSError:
                            continue
                        lines = _count_jsonl_lines(path)
                        stats.append((path.stem, lines, mb))
                        key.append((str(path), mtime))

                    def done() -> None:
                        cache["key"] = tuple(key)
                        cache["rows"] = tuple(stats)
                        paint(stats)

                    svc.executor.on_ui_thread(done)

                try:
                    svc.executor.submit("library.futgg_dump_counts", work)
                except Exception:
                    stats = []
                    for path in paths:
                        try:
                            mb = path.stat().st_size / 1e6
                        except OSError:
                            continue
                        stats.append((path.stem, _count_jsonl_lines(path), mb))
                    paint(stats)

    actions = ctk.CTkFrame(inner, fg_color="transparent")
    actions.pack(fill="x", pady=(theme.SP2, 0))
    button(
        actions, "Sync FUT.GG 23–27",
        lambda: _sync_futgg_years(svc, "23,24,25,26,27"),
        kind="secondary", height=28,
    ).pack(side="left")
    button(
        actions, "Sync FUT.GG 27 only",
        lambda: _sync_futgg_years(svc, "27"),
        kind="ghost", height=28,
    ).pack(side="left", padx=(theme.SP2, 0))
    button(
        actions, "Rebuild catalog index",
        lambda: _rebuild_catalog_index(svc),
        kind="ghost", height=28,
    ).pack(side="left", padx=(theme.SP2, 0))
    muted_label(
        inner,
        "Sync downloads promos/Icons/Heroes from FUT.GG (not Futbin). "
        "Rebuild index after sync so Library search sees new years.",
        size=10,
    ).pack(anchor="w", pady=(2, 0))


def _sync_futgg_years(svc: Any, years_csv: str) -> None:
    """Off-thread FUT.GG bulk sync using the real v1 client."""
    years = [y.strip() for y in years_csv.split(",") if y.strip()]
    set_status(svc, f"FUT.GG sync starting for {', '.join(years)}…", tone="info")

    def work() -> None:
        try:
            # Prefer repo src.futgg_client (same as main.py --sync-futgg)
            import sys
            from pathlib import Path as P

            root = P(svc.paths.root)
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from src import futgg_client  # type: ignore

            def prog(msg: str) -> None:
                if getattr(svc, "executor", None) is not None:
                    svc.executor.on_ui_thread(
                        lambda m=msg: set_status(svc, f"FUT.GG: {m}", tone="info")
                    )

            results = futgg_client.sync_years(years, progress=prog)
            total = sum(int(r.get("written") or 0) for r in results)
            def done() -> None:
                set_status(
                    svc,
                    f"FUT.GG sync done — {total:,} cards written. Rebuild catalog index next.",
                    tone="ok",
                )
            if getattr(svc, "executor", None) is not None:
                svc.executor.on_ui_thread(done)
            else:
                done()
        except Exception as exc:  # noqa: BLE001
            def fail() -> None:
                set_status(svc, f"FUT.GG sync failed: {exc}", tone="error")
            if getattr(svc, "executor", None) is not None:
                svc.executor.on_ui_thread(fail)
            else:
                fail()

    if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "submit"):
        svc.executor.submit("library.sync_futgg", work)
    else:
        work()


def _rebuild_catalog_index(svc: Any) -> None:
    set_status(svc, "Rebuilding catalog.sqlite…", tone="info")

    def work() -> None:
        try:
            import sys
            from pathlib import Path as P

            root = P(svc.paths.root)
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from src import product  # type: ignore

            report = product.rebuild_catalog(
                progress=lambda f, m: None,
            )
            def done() -> None:
                set_status(
                    svc,
                    f"Catalog rebuild: {report.get('rows') or report.get('count') or report}",
                    tone="ok",
                )
            if getattr(svc, "executor", None) is not None:
                svc.executor.on_ui_thread(done)
            else:
                done()
        except Exception as exc:  # noqa: BLE001
            def fail() -> None:
                set_status(svc, f"Catalog rebuild failed: {exc}", tone="error")
            if getattr(svc, "executor", None) is not None:
                svc.executor.on_ui_thread(fail)
            else:
                fail()

    if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "submit"):
        svc.executor.submit("library.rebuild_catalog", work)
    else:
        work()


def _target_bar(root: Any, svc: Any, state: Any) -> None:
    bar = ctk.CTkFrame(
        root, fg_color=theme.PANEL, border_color=theme.BORDER,
        border_width=1, corner_radius=theme.R_MD,
    )
    bar.pack(fill="x", pady=(0, theme.SP2))
    inner = ctk.CTkFrame(bar, fg_color="transparent")
    inner.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
    current = (
        f"Staging onto {state.target.name or state.target.playerid}"
        if state.target.locked else _target_route_copy()
    )
    muted_label(inner, current, size=11).pack(side="left")
    live = state.bridge.liveness
    verified = (
        live.armed
        and bool(live.session_id)
        and state.squad.session_id == live.session_id
        and not state.squad.stale
    )
    if verified and state.squad.players:
        labels: list[str] = []
        mapping: dict[str, Mapping[str, Any]] = {}
        for raw in state.squad.players:
            pid = raw.get("playerid") or raw.get("id")
            name = raw.get("name") or raw.get("playername") or f"Player {pid}"
            label = f"{name} · {raw.get('overallrating') or raw.get('ovr') or '—'}"
            if label in mapping:
                label = f"{label} · {pid}"
            labels.append(label)
            mapping[label] = raw
        chooser = ctk.CTkComboBox(
            inner,
            values=labels,
            width=230,
            height=theme.BTN_MD,
            fg_color=theme.CARD,
            border_color=theme.BORDER,
            button_color=theme.CARD_HOVER,
            text_color=theme.TEXT,
            command=lambda label: _choose_library_target(svc, mapping.get(label)),
        )
        chooser.set("Change player..." if state.target.locked else "Choose player...")
        chooser.pack(side="right")
def _choose_library_target(svc: Any, raw: Mapping[str, Any] | None) -> None:
    if not raw:
        return
    from ...app.commands.player import lock_player, reload_player

    try:
        selected = lock_player(
            svc,
            raw.get("playerid", raw.get("id")),
            record=raw,
            name=str(raw.get("name") or raw.get("playername") or ""),
            source="squad",
            teamid=svc.store.snapshot().squad.teamid,
        )
        if svc.store.snapshot().bridge.armed:
            job_id = reload_player(svc)
            set_status(
                svc,
                f"Selected {selected.name}; loading full live values ({job_id[:8]}…).",
            )
        else:
            set_status(svc, f"Selected {selected.name}.")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))


def _entry(parent: Any, placeholder: str, width: int) -> Any:
    return ctk.CTkEntry(
        parent, placeholder_text=placeholder, width=width, height=theme.BTN_LG,
        fg_color=theme.CARD, border_color=theme.BORDER, text_color=theme.TEXT,
    )


def _card_row(svc: Any, card: dict) -> dict:
    favorite = False
    try:
        favorite = bool(svc.catalog.is_favorite(card))
    except Exception:  # noqa: BLE001
        pass
    return {
        "favorite": "★" if favorite else "",
        "name": card.get("name") or card.get("playername") or "—",
        "year": card.get("year") or card.get("game_year") or "—",
        "ovr": card.get("overallrating") or card.get("ovr") or "—",
        "pos": card.get("positions_text") or card.get("preferredposition1") or "—",
        "variant": card.get("variant") or card.get("revision") or "Base",
        "club": card.get("club") or card.get("teamname") or "—",
        "_key": card.get("_card_key") or card.get("obs_id") or id(card),
        "_raw": card,
    }


def _search(
    svc: Any, query: str, *, year: str = "", filters: dict | None = None
) -> None:
    from ...app import events as E

    q, y = (query or "").strip(), (year or "").strip()
    if q and len(q) < 3:
        set_status(svc, "Type at least 3 letters of the player name.", tone="warn")
        return
    clean = {
        key: value for key, value in dict(filters or {}).items()
        if value not in (None, "")
    }
    generation = int(svc.store.snapshot().search.generation) + 1
    svc.store.dispatch(
        E.SearchStarted(query=q, year=y, filters=clean, generation=generation)
    )
    if not q and not y and not clean:
        svc.store.dispatch(E.SearchSucceeded(results=(), generation=generation))
        set_status(svc, "Search cleared.")
        return

    def work(_token: Any) -> None:
        try:
            rows = _catalog_query(svc, q, year=y, filters=clean)
            svc.executor.on_ui_thread(
                lambda: svc.store.dispatch(
                    E.SearchSucceeded(results=rows, generation=generation)
                )
            )
            svc.executor.on_ui_thread(
                lambda: set_status(svc, f"{len(rows)} card variant(s) found.")
            )
        except Exception as exc:  # noqa: BLE001
            svc.executor.on_ui_thread(
                lambda: svc.store.dispatch(
                    E.SearchFailed(error=str(exc), generation=generation)
                )
            )

    try:
        svc.executor.submit(f"library.search.{generation}", work)
    except Exception as exc:  # noqa: BLE001
        svc.store.dispatch(E.SearchFailed(error=str(exc), generation=generation))


def _catalog_query(
    svc: Any, query: str, *, year: str = "", filters: dict | None = None
) -> tuple[dict, ...]:
    values = dict(filters or {})
    hits = svc.catalog.search(
        query, year=year, ovr_min=values.get("ovr_min"),
        ovr_max=values.get("ovr_max"), limit=100,
    )
    return tuple(dict(hit) for hit in hits)


def _stage_card(svc: Any, row: dict) -> None:
    """Stage Library cards through the Player card-build command (body included)."""
    if not row:
        set_status(svc, "Select a card first.")
        return
    snapshot = getattr(getattr(svc, "store", None), "snapshot", None)
    state = snapshot() if callable(snapshot) else None
    if state is not None and not state.target.locked:
        set_status(svc, "Choose a verified live squad player before staging a card build.")
        return
    raw = row.get("_raw") or row
    try:
        from ...app.commands.card_import import CardImportTopics, stage_card

        def complete(_result: Any) -> None:
            handed_off = False
            open_card = getattr(getattr(svc, "ui", None), "open_player_card", None)
            if callable(open_card):
                handed_off = bool(open_card(raw))
            elif getattr(svc, "ui", None) is not None:
                handed_off = bool(svc.ui.navigate("player"))
            target_label = (
                state.target.name or state.target.playerid
                if state is not None
                else "the selected player"
            )
            set_status(
                svc,
                f"Card build staged for {target_label} (ratings, skills, body). "
                "Kit stays off. Review changes before applying.",
            )

        stage_card(
            svc,
            raw,
            CardImportTopics(stats=True),
            on_staged=complete,
        )
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))

# Kept as a narrow compatibility entry point for callers from the previous
# Library surface. Its semantics are now safe staging, not variant-ID locking.
def _pick(svc: Any, row: dict) -> None:
    _stage_card(svc, row)


def _toggle_favorite(svc: Any, row: dict) -> None:
    if not row:
        set_status(svc, "Select a card first.")
        return
    try:
        active = svc.catalog.toggle_favorite(row.get("_raw") or row)
        set_status(svc, "Added to favorites." if active else "Removed from favorites.")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))


def _add_to_team(svc: Any, row: dict) -> None:
    if not row:
        set_status(svc, "Select a card first.")
        return
    # Adding a card is destructive.  The receiving signing surface still owns
    # face, current-squad, safe-slot and confirmation safeguards.  This only
    # keeps an already chosen card from being lost while navigating.
    raw = dict(row.get("_raw") or row)
    if getattr(svc, "ui", None) is not None and svc.ui.open_signing(raw):
        set_status(
            svc,
            "Card sent to Sign. Review its real face, age and safe slot before adding it to your team.",
        )
    else:
        set_status(svc, "Signing handoff is unavailable. Open Sign and search this card before adding it to your team.")


def _replace_results(svc: Any, rows: Any, *, query: str) -> None:
    from ...app import events as E

    generation = int(svc.store.snapshot().search.generation) + 1
    svc.store.dispatch(E.SearchStarted(query=query, generation=generation))
    svc.store.dispatch(E.SearchSucceeded(results=tuple(rows), generation=generation))


def _show_favorites(svc: Any) -> None:
    try:
        rows = svc.catalog.favorites()
        _replace_results(svc, rows, query="Favorites")
        set_status(svc, f"{len(rows)} favorite card(s).")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))


def _show_variants(svc: Any, row: dict) -> None:
    if not row:
        set_status(svc, "Select a card first.")
        return
    try:
        rows = svc.catalog.variants(row.get("_raw") or row)
        _replace_results(svc, rows, query=f"Variants: {row.get('name', '')}")
        set_status(svc, f"{len(rows)} variant(s).")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))


def _best_matches(svc: Any) -> None:
    state = svc.store.snapshot()
    if not state.target.locked:
        set_status(svc, "Pick an FC26 player first.")
        return
    target = dict(state.editor.base)
    target.setdefault("playerid", state.target.playerid)
    target.setdefault("name", state.target.name)
    try:
        rows = svc.catalog.best_matches(target, limit=20)
        _replace_results(svc, rows, query=f"Best: {state.target.name}")
        set_status(svc, f"{len(rows)} ranked match(es) for {state.target.name}.")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, str(exc))
