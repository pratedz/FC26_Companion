"""Search UI helpers and card-row projections for Sign."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ... import theme
from ...widgets.primitives import button, eyebrow, muted_label, panel, set_disabled, text_label
from ...widgets.table import Column, TableModel
from .._common import set_status
from .results import CardResults, can_add
from ....domain.catalog import card_key

try:  # pragma: no cover - desktop only
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


def _card_constraints(
    year_text: str, minimum_text: str, maximum_text: str
) -> tuple[int | None, int | None, int | None]:
    year = _optional_int(year_text, "FC year", minimum=1, maximum=99)
    minimum = _optional_int(minimum_text, "Minimum OVR", minimum=1, maximum=99)
    maximum = _optional_int(maximum_text, "Maximum OVR", minimum=1, maximum=99)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Minimum OVR cannot be higher than maximum OVR.")
    return year, minimum, maximum


def _optional_int(text: str, label: str, *, minimum: int, maximum: int) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


SEARCH_LIMIT = 200


def _card_overall(card: Mapping[str, Any]) -> int:
    raw = card.get("overallrating")
    if raw in (None, ""):
        raw = card.get("overall")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _card_year(card: Mapping[str, Any]) -> int:
    raw = card.get("year")
    if raw in (None, ""):
        raw = card.get("game_year")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _person_key(card: Mapping[str, Any]) -> str:
    for field in ("person_id", "playerid", "base_player_id"):
        value = card.get(field)
        if value not in (None, "", 0):
            return f"id:{value}"
    name = str(card.get("name") or card.get("playername") or "").casefold().strip()
    return f"name:{name}"


def _position_tokens(card: Mapping[str, Any]) -> set[str]:
    import re

    text = str(card.get("positions_text") or "")
    return {
        part.strip().upper()
        for part in re.split(r"[,/|]+", text)
        if part.strip()
    }


def shop_cards(
    rows: Sequence[Mapping[str, Any]],
    *,
    verified_only: bool = True,
    best_only: bool = True,
    position: str = "",
) -> tuple[dict[str, Any], ...]:
    """Narrow a Library hit list the way a shopper actually picks a card.

    Verified faces are the only ones Checkout will accept. Best-card keeps the
    highest overall for each person, then the newer year, so one name is not
    twenty nearly identical rows.
    """
    items = [dict(row) for row in rows if isinstance(row, Mapping)]
    if verified_only:
        items = [row for row in items if _card_face_status(row) == "Verified"]
    token = str(position or "").strip().upper()
    if token:
        items = [row for row in items if token in _position_tokens(row)]
    if not best_only:
        return tuple(items)
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for card in items:
        key = _person_key(card)
        current = best.get(key)
        rank = (_card_overall(card), _card_year(card))
        if current is None:
            best[key] = card
            order.append(key)
            continue
        current_rank = (_card_overall(current), _card_year(current))
        if rank > current_rank:
            best[key] = card
    return tuple(best[key] for key in order)


def shop_feedback(
    total: int,
    shown: int,
    *,
    best_only: bool,
    capped: bool,
) -> str:
    """One status line after a search: what is on screen, and the next click."""
    if total <= 0:
        return "No cards match. Try another name, or clear the year and OVR."
    if shown <= 0:
        return (
            "No verified faces in these matches. Switch to All faces to inspect them. "
            "Checkout still refuses a missing face."
        )
    if best_only and shown != total:
        lead = f"{shown} best card{'s' if shown != 1 else ''} from {total} matches"
    else:
        lead = f"{shown} card{'s' if shown != 1 else ''}"
    notes: list[str] = []
    if capped:
        notes.append("Showing the top matches — add a year or OVR to narrow it.")
    if best_only and shown != total:
        notes.append("All versions lists every year and promo.")
    notes.append("Add a card to your bag, then Checkout.")
    return lead + ". " + " ".join(notes)


def _criteria_text(year: int | None, minimum: int | None, maximum: int | None) -> str:
    parts: list[str] = []
    if year is not None:
        parts.append(f"FC {year}")
    if minimum is not None and maximum is not None:
        parts.append(f"OVR {minimum}-{maximum}")
    elif minimum is not None:
        parts.append(f"OVR {minimum}+")
    elif maximum is not None:
        parts.append(f"OVR up to {maximum}")
    return f" ({', '.join(parts)})" if parts else ""


def _request_card_constraints(request: str) -> tuple[int | None, int | None, int | None]:
    """Extract optional prompt constraints; visible controls override them."""
    import re

    text = str(request or "").casefold()
    year_match = re.search(r"(?:year|fc)\s*[:=]?\s*(2[0-9])\b", text)
    year = int(year_match.group(1)) if year_match else None
    range_match = re.search(
        r"(?:ovr|overall|rating)[^0-9]{0,24}(\d{2})\s*(?:-|to)\s*(\d{2})",
        text,
    )
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return year, min(low, high), max(low, high)
    min_match = re.search(
        r"(?:at\s+least|min(?:imum)?(?:\s+(?:ovr|overall|rating))?)\s*(\d{2})\b",
        text,
    )
    max_match = re.search(
        r"(?:max(?:imum)?(?:\s+(?:ovr|overall|rating))?|"
        r"(?:can(?:no)?t|can't)\s+exceed|up\s+to|no\s+more\s+than)\s*"
        r"(?:(?:ovr|overall|rating)\s*)?(\d{2})\b",
        text,
    )
    minimum = int(min_match.group(1)) if min_match else None
    maximum = int(max_match.group(1)) if max_match else None
    if minimum is not None and maximum is not None and minimum > maximum:
        minimum, maximum = maximum, minimum
    return year, minimum, maximum


def _request_player_count(request: str) -> int | None:
    """How many people the prompt asked for, if it said so in plain language."""
    import re

    text = str(request or "").casefold()
    match = re.search(
        r"\b(\d{1,2})\s*(?:defenders?|attackers?|midfielders?|strikers?|"
        r"forwards?|wingers?|keepers?|goalkeepers?|players?|cbs?|gks?)\b",
        text,
    )
    if not match:
        return None
    count = int(match.group(1))
    if 1 <= count <= 11:
        return count
    return None


def _restore_grid_selection(
    display: Sequence[Mapping[str, Any]],
    *,
    checked_keys: Sequence[Any] = (),
    selected_key: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    """Rebuild click + checkbox selection after Sign is reconstructed."""
    present = {str(row.get("_key") or "") for row in display}
    present.discard("")
    wanted = {str(key) for key in checked_keys if str(key) in present}
    selection = [
        dict(row) for row in display if str(row.get("_key") or "") in wanted
    ]
    selected: dict[str, Any] = {}
    key = "" if selected_key is None else str(selected_key)
    if key and key in present:
        for row in display:
            if str(row.get("_key") or "") == key:
                selected = dict(row)
                break
    elif len(selection) == 1:
        selected = dict(selection[0])
    return selected, selection, wanted


def _card_face_status(card: Mapping[str, Any]) -> str:
    """Cheap local CSV lookup: Verified, Missing, or unknown. Never reads FC26."""
    stored = str(card.get("_face_status") or "")
    if stored in {"Verified", "Missing", "—"}:
        return stored
    if card.get("_face_verified") is True:
        return "Verified"
    if card.get("_face_verified") is False:
        return "Missing"
    try:
        from ....platform.base_players import resolve_card_base_profile

        profile = resolve_card_base_profile(card)
    except Exception:
        return "—"
    if profile is None:
        return "—"
    return "Verified" if profile.face_verified else "Missing"


def _annotate_card_face(card: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(card)
    status = _card_face_status(row)
    row["_face_status"] = status
    if status == "Verified":
        row["_face_verified"] = True
    elif status == "Missing":
        row["_face_verified"] = False
    else:
        row.pop("_face_verified", None)
    return row


def _sign_columns() -> tuple[Column, ...]:
    return (
        Column("name", "PLAYER", width=120, stretch=True),
        Column("year", "YR", width=40, numeric=True),
        Column("ovr", "OVR", width=42, numeric=True, kind="ovr"),
        Column("pos", "POS", width=52),
        Column("variant", "CARD", width=110, stretch=True),
        Column("club", "CLUB", width=72),
        Column(
            "face",
            "FACE",
            width=72,
            tone=lambda row: (
                "ok"
                if row.get("face") == "Verified"
                else "warn" if row.get("face") == "Missing" else "muted"
            ),
        ),
        Column("reason", "WHY", width=160),
    )


def _card_row(card: Mapping[str, Any]) -> dict[str, Any]:
    annotated = dict(card) if card.get("_history") else _annotate_card_face(card)
    return {
        "name": annotated.get("name") or annotated.get("playername") or "-",
        "year": annotated.get("year") or annotated.get("game_year") or "-",
        "ovr": annotated.get("overallrating") or annotated.get("overall") or "-",
        "pos": annotated.get("positions_text") or annotated.get("preferredposition1") or "-",
        "variant": annotated.get("variant") or annotated.get("revision") or "Base",
        "club": annotated.get("club") or annotated.get("club_name") or "-",
        "face": annotated.get("_face_status") or "—",
        "reason": annotated.get("_ai_reason") or "",
        "_key": annotated.get("_history_key") or annotated.get("_card_key") or card_key(annotated),
        "_raw": annotated,
    }


def add_to_bag_label(rows: Sequence[Mapping[str, Any]]) -> str:
    """Catalog add label: Add to bag, Add Casillas, or Add 3."""
    picked = [row for row in rows if isinstance(row, Mapping) and row]
    if not picked:
        return "Add to bag"
    if len(picked) == 1:
        name = str(picked[0].get("name") or "player").strip() or "player"
        if len(name) > 16:
            name = name[:15] + "..."
        return f"Add {name}"
    return f"Add {len(picked)}"


def _entry(parent: Any, placeholder: str, width: int, *, value: str = "") -> Any:
    entry = ctk.CTkEntry(
        parent,
        placeholder_text=placeholder,
        width=width,
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
    )
    if value:
        entry.insert(0, str(value))
    return entry


class LibrarySearchPanel:
    """Find-a-card controls, result grid, and async Library search."""

    def __init__(
        self,
        controls: Any,
        root: Any,
        svc: Any,
        view: dict[str, Any],
        *,
        on_selection_change: Callable[[], None] | None = None,
        on_add: Callable[[], None] | None = None,
        on_add_row: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.svc = svc
        self.view = view
        self.root = root
        self.controls = controls
        self.on_selection_change = on_selection_change
        self.on_add = on_add
        self.on_add_row = on_add_row
        self.selected: dict[str, Any] = {}
        self.selection: list[dict[str, Any]] = []
        self._catalog_hits: tuple[Mapping[str, Any], ...] = tuple(view.get("catalog_hits") or ())
        self._showing_catalog = False
        self._search_after: str | None = None
        self._position_after: str | None = None
        self._add_state = None
        self._mount_controls()
        self._mount_results()

    def picked_rows(self) -> list[dict[str, Any]]:
        if self.selection:
            return list(self.selection)
        if self.selected:
            return [dict(self.selected)]
        return []

    def clear_checks(self) -> None:
        self.model.checked = set()
        self.selection.clear()
        self.view["checked_keys"] = ()
        self.cache_current_mode()
        try:
            self.grid.refresh()
        except Exception:
            pass
        self.sync_add_button()
        if self.on_selection_change is not None:
            self.on_selection_change()

    def _mount_controls(self) -> None:
        self.search_row = ctk.CTkFrame(self.controls, fg_color="transparent")
        self.search_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP3, theme.SP2))
        self.query = _entry(self.search_row, "Search players in your local Library…", 120, value=self.view["query"])
        self.query.configure(height=40)
        self.query.pack(side="left", fill="x", expand=True)
        self.search_button = button(self.search_row, "Search", self.search, kind="accent", height=40, width=92)
        self.search_button.pack(side="right", padx=(8, 0))
        self.shop_row = ctk.CTkFrame(self.controls, fg_color="transparent")
        self.shop_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
        self.year = _entry(self.shop_row, "Any year", 70, value=self.view.get("year") or "")
        self.year.pack(side="left", padx=(0, 6))
        self.verified_button = button(self.shop_row, "Verified only", self.toggle_verified,
                                      kind="ghost", height=30, width=100)
        self.verified_button.pack(side="left", padx=(0, 6))
        self.best_button = button(self.shop_row, "Best card", self.toggle_best,
                                  kind="ghost", height=30, width=84)
        self.best_button.pack(side="left", padx=(0, 6))
        self.refine_button = button(self.shop_row, "More filters", self.toggle_refine,
                                    kind="ghost", height=30, width=90)
        self.refine_button.pack(side="left")
        self.refine_row = ctk.CTkFrame(self.controls, fg_color="transparent")
        self.refine_visible = {"value": bool(self.view.get("refine_open") or self.view.get("ovr_min") or
                                              self.view.get("ovr_max") or self.view.get("pos") or self.view.get("source"))}
        self.ovr_min = _entry(self.refine_row, "OVR min", 68, value=self.view.get("ovr_min") or "")
        self.ovr_min.pack(side="left")
        self.ovr_max = _entry(self.refine_row, "OVR max", 68, value=self.view.get("ovr_max") or "")
        self.ovr_max.pack(side="left", padx=(5, 0))
        self.pos = _entry(self.refine_row, "Position", 66, value=self.view.get("pos") or "")
        self.pos.pack(side="left", padx=(5, 0))
        self.source_options = {"Any source": "", "Career": "career", "FUT": "fut", "EA official": "ea_official", "Live Editor": "le_base"}
        label = next((k for k,v in self.source_options.items() if v == self.view.get("source", "")), "Any source")
        self.source = ctk.CTkOptionMenu(self.refine_row, values=list(self.source_options), width=104, height=30,
                                      fg_color=theme.CARD, button_color=theme.BORDER, text_color=theme.TEXT,
                                      dropdown_fg_color=theme.CARD, dropdown_text_color=theme.TEXT,
                                      command=lambda _value: self._filter_changed())
        self.source.set(label)
        self.source.pack(side="left", padx=(5, 0))
        if self.refine_visible["value"]:
            self.refine_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            self.refine_button.configure(text="Less filters")
        self.filter_error = muted_label(self.controls, "", size=10, color=theme.DANGER, wraplength=400, justify="left")
        self.search_feedback = muted_label(self.controls, "", size=10, wraplength=400, justify="left")
        self._sync_shop_toggles()
        for field in (self.query, self.year, self.ovr_min, self.ovr_max, self.pos):
            field.bind("<Return>", lambda _event: self.search())
            field.bind("<KP_Enter>", lambda _event: self.search())
        for field in (self.year, self.ovr_min, self.ovr_max):
            field.bind("<KeyRelease>", lambda _event: self._persist_fields())
        self.pos.bind("<KeyRelease>", lambda _event: self._on_pos_key())
        self.query.bind("<KeyRelease>", lambda _event: self._on_query_key())

    def _filter_changed(self) -> None:
        self._persist_fields()
        self.search()

    def _mount_results(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
        self.result_title = text_label(header, "Search results", size=16, bold=True)
        self.result_title.pack(side="left")
        self.result_count = muted_label(
            header, "Search for a player to see cards here.", size=11
        )
        self.result_count.pack(side="left", padx=(theme.SP2, 0))
        self.add_button = button(header, "Add to bag", self._add_picked, kind="secondary", height=28, width=92,
                                 disabled_reason="Select a verified local card first")
        self.add_button.pack(side="right")
        self.model = TableModel(
            columns=_sign_columns(),
            rows=(),
            key_field="_key",
            multi_select=True,
        )
        self.grid = CardResults(
            self.root,
            self.model, self.svc, self.view,
            show_checkboxes=True,
            visible_rows=8,
            fill_available=True,
            on_row_click=self.select,
            selection_on_click=False,
            on_row_activate=self._activate_row,
            on_selection_change=self._on_selection,
        )
        self.empty_results = ctk.CTkFrame(self.root, fg_color="transparent")
        self._paint_empty(
            "Search Library for a verified card",
            "Type at least 3 letters and press Enter. Double-click a verified card to add it. Click a column heading to sort.",
        )

    def toggle_refine(self) -> None:
        self.refine_visible["value"] = not self.refine_visible["value"]
        self.view["refine_open"] = self.refine_visible["value"]
        if self.refine_visible["value"]:
            self.refine_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP1), after=self.shop_row)
            self.refine_button.configure(text="Less filters")
        else:
            self.refine_row.pack_forget()
            self.refine_button.configure(text="More filters")

    def toggle_verified(self) -> None:
        self.view["verified_only"] = not bool(self.view.get("verified_only", True))
        self._sync_shop_toggles()
        self._reshow_catalog()

    def toggle_best(self) -> None:
        self.view["best_only"] = not bool(self.view.get("best_only", True))
        self._sync_shop_toggles()
        self._reshow_catalog()

    def _sync_shop_toggles(self) -> None:
        verified = bool(self.view.get("verified_only", True))
        best = bool(self.view.get("best_only", True))
        try:
            self.verified_button.configure(
                text="Verified only" if verified else "All faces",
                border_color=theme.SUCCESS if verified else theme.BORDER,
                text_color=theme.SUCCESS if verified else theme.TEXT,
            )
            self.best_button.configure(
                text="Best card" if best else "All versions",
                border_color=theme.ACCENT if best else theme.BORDER,
                text_color=theme.ACCENT if best else theme.TEXT,
            )
        except Exception:
            pass

    def _on_query_key(self) -> None:
        if (self.query.get() or "") == self.view.get("query", ""):
            return
        self._persist_fields()
        # Invalidate at the keystroke, before debounce: an older completion
        # must never paint over the name the user is currently typing.
        self.view["results_generation"] = int(self.view.get("results_generation", 0)) + 1
        self.svc.executor.cancel("add_player.search")
        self.search_button.configure(text="Search")
        self.set_feedback("Type at least 3 letters." if len(self.view["query"].strip()) < 3
                          else "Updating results… You can keep browsing the current cards.")
        try:
            if self._search_after is not None:
                self.query.after_cancel(self._search_after)
            self._search_after = self.query.after(350, self._run_scheduled_search)
        except Exception:
            pass

    def _on_pos_key(self) -> None:
        self._persist_fields()
        if self._position_after is not None:
            self.pos.after_cancel(self._position_after)
        def apply() -> None:
            self._position_after = None
            self.view["result_page"] = 0
            self._reshow_catalog()
        self._position_after = self.pos.after(200, apply)

    def _run_scheduled_search(self) -> None:
        self._search_after = None
        if len((self.query.get() or "").strip()) < 3:
            return
        self.search()

    def _cancel_scheduled_search(self) -> None:
        if self._search_after is None:
            return
        try:
            self.query.after_cancel(self._search_after)
        except Exception:
            pass
        self._search_after = None

    def _reshow_catalog(self) -> None:
        if not self._catalog_hits:
            return
        self._present_catalog(
            self._catalog_hits,
            capped=len(self._catalog_hits) >= SEARCH_LIMIT,
        )

    def _present_catalog(
        self, rows: Sequence[Mapping[str, Any]], *, capped: bool
    ) -> None:
        best_only = bool(self.view.get("best_only", True))
        shown = shop_cards(
            rows,
            verified_only=bool(self.view.get("verified_only", True)),
            best_only=best_only,
            position=(self.pos.get() if hasattr(self, "pos") else "")
            or self.view.get("pos")
            or "",
        )
        # Toast and textbox must use the same list: len(shown) is what replace_rows
        # paints. shop_feedback already refuses a "N cards" lead when shown is 0.
        self.replace_rows(
            shown,
            catalog=True, mode="Search",
            empty_detail=shop_feedback(
                len(rows),
                0,
                best_only=best_only,
                capped=capped,
            ),
        )
        message = shop_feedback(
            len(rows),
            len(shown),
            best_only=best_only,
            capped=capped,
        )
        self.set_feedback(message)

    def select(self, row: Mapping[str, Any]) -> None:
        self.selected.clear()
        self.selected.update(dict(row))
        self.view["selected_key"] = str(row.get("_key") or "")
        self.cache_current_mode()
        if self.on_selection_change is not None:
            self.on_selection_change()
        self.sync_add_button()

    def _on_selection(self, rows: tuple[Mapping[str, Any], ...]) -> None:
        self.selection.clear()
        self.selection.extend(dict(row) for row in rows)
        self.view["checked_keys"] = tuple(self.model.checked)
        self.cache_current_mode()
        self.sync_add_button()
        if self.on_selection_change is not None:
            self.on_selection_change()

    def _add_picked(self) -> None:
        if self.on_add is not None:
            self.on_add()

    def _activate_row(self, row: Mapping[str, Any]) -> None:
        self.select(row)
        if not can_add(row):
            set_status(
                self.svc,
                "That card has no verified face, so Checkout would refuse it.",
            )
            return
        if self.on_add_row is not None:
            self.on_add_row(dict(row))
        elif self.on_add is not None:
            self.on_add()

    def _refuse_unverified(self, rows: Sequence[Mapping[str, Any]]) -> str:
        if any((row.get("_raw") or row).get("_history") for row in rows):
            return "Recently Added is inspection-only. Search the Library for a source card."
        blocked = [
            str(row.get("name") or "card")
            for row in rows
            if str(row.get("face") or "") != "Verified"
        ]
        if not blocked:
            return ""
        shown = ", ".join(blocked[:3])
        return f"{shown} has no verified face, so Checkout would refuse it."

    def sync_add_button(self) -> None:
        picked = self.picked_rows()
        reason = "" if picked else "Select a Library card to add it to your bag."
        blocked = self._refuse_unverified(picked)
        if blocked:
            reason = blocked
        state = (add_to_bag_label(picked), reason)
        if state == self._add_state:
            return
        self._add_state = state
        try:
            self.add_button.configure(text=state[0])
            set_disabled(self.add_button, reason)
        except Exception:
            pass

    def _paint_empty(self, headline: str, detail: str) -> None:
        for child in list(self.empty_results.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        card = panel(self.empty_results, level=1)
        card.pack(fill="x")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
        copy = ctk.CTkFrame(row, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True)
        text_label(copy, headline, size=13, bold=True).pack(anchor="w")
        muted_label(copy, detail, size=11, wraplength=720, justify="left").pack(
            anchor="w", pady=(theme.SP1, 0)
        )
        button(
            row,
            "Focus search",
            lambda: self.query.focus(),
            kind="primary",
            height=theme.BTN_MD,
        ).pack(side="right", padx=(theme.SP3, 0))

    def show_empty_results(self, headline: str, detail: str) -> None:
        self.grid.pack_forget()
        self.result_count.configure(text="No cards shown")
        self._paint_empty(headline, detail)
        self.empty_results.pack(fill="x", padx=theme.SP2, pady=(theme.SP1, 0))

    def show_result_grid(
        self, rows: Sequence[Mapping[str, Any]] | None = None
    ) -> None:
        painted = rows if rows is not None else self.model.visible_rows()
        self.empty_results.pack_forget()
        self.result_count.configure(
            text=f"{len(painted)} {'signings' if self.view.get('discovery_mode') == 'Recently Added' else 'cards'}"
        )
        if not self.grid.widget.winfo_manager():
            self.grid.pack(fill="both", expand=True, padx=theme.SP2, pady=(0, theme.SP2))
        self.grid.refresh()

    def replace_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        select_all: bool = False,
        catalog: bool = False,
        empty_detail: str | None = None,
        mode: str | None = None,
    ) -> None:
        target_mode = mode or self.view.get("discovery_mode", "Search")
        cache = self.view.setdefault("discovery_cache", {}).setdefault(target_mode, {})
        cache["rows"] = tuple(dict(row.get("_raw") or row) for row in rows)
        if select_all:
            cache["checked_keys"] = tuple(str(_card_row(row).get("_key")) for row in rows)
        if target_mode != self.view.get("discovery_mode", "Search"):
            return
        display = tuple(_card_row(row) for row in rows)
        # Stale text/facet filters must not hide rows the toast just counted.
        self.model.filter_text = ""
        self.model.filter_fn = None
        if target_mode == "Recently Added":
            self.model.sort_key = ""
        else:
            label = self.view.get("result_sort", "Overall ↓")
            self.model.sort_key, self.model.sort_desc = {"Overall ↓": ("ovr", True), "Year ↓": ("year", True), "Name A–Z": ("name", False)}.get(label, ("ovr", True))
        self.model.set_rows(display)
        self._show_why_column(any(str(row.get("reason") or "").strip() for row in display))
        self.selected.clear()
        self.selection.clear()
        if select_all:
            self.view["checked_keys"] = tuple(self.model.row_key(row) for row in display)
        selected, selection, wanted = _restore_grid_selection(
            display,
            checked_keys=tuple(self.view.get("checked_keys") or ()),
            selected_key=self.view.get("selected_key"),
        )
        self.model.checked = wanted
        self.selection.extend(selection)
        self.selected.update(selected)
        self.view["selected_key"] = str(selected.get("_key") or "")
        self.view["rows"] = tuple(dict(row.get("_raw") or row) for row in display)
        self.view["checked_keys"] = tuple(self.model.checked)
        self.cache_current_mode()
        self._showing_catalog = catalog
        if display:
            self.show_result_grid(display)
        else:
            self.grid.refresh()
            self.show_empty_results(
                "No signings yet" if target_mode == "Recently Added" else "No cards here yet",
                empty_detail
                or "Try a shorter player name, or clear a year or OVR filter and search again.",
            )
        self.sync_add_button()
        if self.on_selection_change is not None:
            self.on_selection_change()

    def cache_current_mode(self) -> None:
        mode = self.view.get("discovery_mode", "Search")
        cache = self.view.setdefault("discovery_cache", {}).setdefault(mode, {})
        cache.update(checked_keys=tuple(self.model.checked), selected_key=self.view.get("selected_key", ""),
                     page=self.view.get("result_page", 0), sort=self.view.get("result_sort", "Overall ↓"))

    def _show_why_column(self, visible: bool) -> None:
        # Recommendation explanations live beneath the player name.
        pass

    def card_constraints(self) -> tuple[int | None, int | None, int | None]:
        return _card_constraints(self.year.get(), self.ovr_min.get(), self.ovr_max.get())

    def _pack_hint(self, widget: Any, text: str) -> None:
        try:
            if text:
                if not widget.winfo_manager():
                    widget.pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP1))
            else:
                widget.pack_forget()
        except Exception:
            pass

    def set_feedback(self, text: str) -> None:
        try:
            self.search_feedback.configure(text=text)
        except Exception:
            pass
        self._pack_hint(self.search_feedback, text)

    def show_filter_error(self, message: str) -> None:
        try:
            self.filter_error.configure(text=message, text_color=theme.DANGER)
        except Exception:
            pass
        self._pack_hint(self.filter_error, message)
        if message:
            set_status(self.svc, message, tone="warn")

    def clear_filter_error(self) -> None:
        self.show_filter_error("")

    def search(self) -> None:
        self._cancel_scheduled_search()
        self.view["results_generation"] = int(self.view.get("results_generation", 0)) + 1
        generation = int(self.view["results_generation"])
        self.svc.executor.cancel("add_player.search")
        self.search_button.configure(text="Search")
        needle = (self.query.get() or "").strip()
        try:
            selected_year, minimum, maximum = self.card_constraints()
            self.clear_filter_error()
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            self.show_filter_error(message)
            self.set_feedback(message)
            return
        if not needle and selected_year is None and minimum is None and maximum is None and not self.view.get("source"):
            if (self.pos.get() or "").strip():
                self.show_filter_error(
                    "Type a player name first. POS only narrows cards already found."
                )
            else:
                self.show_filter_error(
                    "Enter a player name or choose at least one FC/OVR filter."
                )
            return
        if needle and len(needle) < 3:
            self.show_filter_error("Type at least 3 letters of the player name.")
            return
        if self.svc.catalog is None:
            message = "The local card Library is unavailable."
            self.set_feedback(message)
            set_status(self.svc, message, tone="error")
            return
        self._persist_fields()
        source = self.view.get("source", "")
        self.search_button.configure(text="Searching…")
        self.set_feedback(f"Searching for {needle or 'matching cards'}… Current cards stay available below.")

        def work(_token: Any) -> None:
            try:
                if _token.cancelled:
                    return
                matches = self.svc.catalog.search(
                        needle,
                        year=str(selected_year or ""),
                        ovr_min=minimum,
                        ovr_max=maximum,
                        limit=SEARCH_LIMIT,
                        **({"source": source} if source else {}),
                    )
                prepared = []
                for row in matches:
                    if _token.cancelled:
                        return
                    # Disk-backed face lookups belong in the worker, not Tk.
                    prepared.append(_annotate_card_face(row))
                rows = tuple(prepared)

                def done() -> None:
                    if int(self.view.get("results_generation", 0)) != generation:
                        return
                    self._catalog_hits = rows
                    self.view["catalog_hits"] = rows
                    if not self.root.winfo_exists():
                        self.view.setdefault("discovery_cache", {}).setdefault("Search", {})["rows"] = shop_cards(
                            rows, verified_only=bool(self.view.get("verified_only", True)),
                            best_only=bool(self.view.get("best_only", True)), position=self.view.get("pos", ""))
                        return
                    self.search_button.configure(text="Search")
                    if self.view.get("discovery_mode", "Search") == "Search":
                        self.view["checked_keys"] = ()
                        self.view["result_page"] = 0
                    self._present_catalog(rows, capped=len(rows) >= SEARCH_LIMIT)
                    if self.view.get("discovery_mode", "Search") == "Search":
                        self.grid.scroll.canvas.yview_moveto(0)

                self.svc.executor.on_ui_thread(done)
            except Exception as exc:  # noqa: BLE001
                message = f"Library search failed: {exc}"

                def failed() -> None:
                    if int(self.view.get("results_generation", 0)) != generation:
                        return
                    if not self.root.winfo_exists():
                        return
                    self.search_button.configure(text="Search")
                    self.set_feedback(message)
                    set_status(self.svc, message)

                self.svc.executor.on_ui_thread(failed)

        try:
            self.svc.executor.submit("add_player.search", work)
        except Exception as exc:  # noqa: BLE001
            self.search_button.configure(text="Search")
            message = f"Library search failed: {exc}"
            self.set_feedback(message)
            set_status(self.svc, message)

    def chosen_age(self) -> int | None:
        return chosen_age_from_view(self.view)

    def _persist_fields(self) -> None:
        try:
            self.view["query"] = self.query.get() or ""
            self.view["year"] = self.year.get() or ""
            self.view["ovr_min"] = self.ovr_min.get() or ""
            self.view["ovr_max"] = self.ovr_max.get() or ""
            self.view["pos"] = self.pos.get() or ""
            self.view["source"] = self.source_options.get(self.source.get(), "")
        except Exception:
            pass

    def search_fields(self) -> dict[str, Any]:
        self._persist_fields()
        return {
            "query": self.query.get() or "",
            "year": self.year.get() or "",
            "ovr_min": self.ovr_min.get() or "",
            "ovr_max": self.ovr_max.get() or "",
            "pos": self.pos.get() or "",
            "source": self.view.get("source", ""),
            "verified_only": bool(self.view.get("verified_only", True)),
            "best_only": bool(self.view.get("best_only", True)),
            "force_age": bool(self.view.get("force_age")),
            "age": str(self.view.get("age") or "25"),
            "checked_keys": tuple(self.model.checked),
            "selected_key": self.view.get("selected_key") or (
                str(self.selected.get("_key") or "") if self.selected else ""
            ),
            "rows": tuple(dict(row.get("_raw") or row) for row in self.model.rows),
            "sign_list": tuple(self.view.get("sign_list") or ()),
        }


def chosen_age_from_view(view: Mapping[str, Any]) -> int | None:
    if not bool(view.get("force_age")):
        return None
    try:
        value = int(str(view.get("age") or "").strip())
    except ValueError as exc:
        raise ValueError("New-player age must be a whole number from 16 to 40.") from exc
    if not 16 <= value <= 40:
        raise ValueError("New-player age must be between 16 and 40.")
    return value


