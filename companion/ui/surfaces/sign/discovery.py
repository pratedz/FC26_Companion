"""Discovery modes share one results list and the existing Signing Bag."""
from __future__ import annotations

from typing import Any
import customtkinter as ctk

from ....domain.outcome import ApplyOutcome
from ....domain.job import is_sign_job_label
from ... import theme
from ...widgets.primitives import button

MODES = ("Search", "Recommendations", "Favourites", "Recently Added")


def signing_history(svc: Any, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
    """Successful, verified club additions, scoped to the current save/team.

    Job payload fields are actual applied values, not catalog-card provenance.
    Historical rows are inspection-only and can never be added or favourited.
    """
    state = svc.store.snapshot()
    db = getattr(svc, "db", None)
    if db is None or not state.squad.save_uid or not state.squad.teamid:
        return ()
    records = db.job_records(limit=200, save_uid=state.squad.save_uid,
                             outcome=ApplyOutcome.APPLIED)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for record in sorted(records, key=lambda r: r.finished_utc or r.created_utc, reverse=True):
        results = {str(op.get("id")): op for op in (record.result_json or {}).get("ops", ())}
        for op in (record.job_json or {}).get("ops", ()):
            if op.get("op") != "add_to_team" or op.get("dry_run"):
                continue
            if (record.job_json or {}).get("dry_run") or int(op.get("teamid") or 0) != state.squad.teamid:
                continue
            result = results.get(str(op.get("id")), {})
            data = result.get("data") or {}
            if not result.get("ok") or data.get("verified") is not True:
                continue
            members = op.get("batch") or (op,)
            ids = data.get("playerids") or (data.get("playerid"),)
            for index, member in enumerate(members):
                identity = (record.job_id, index)
                if identity in seen:
                    continue
                seen.add(identity)
                player = member.get("player") or {}
                names = player.get("names") or {}
                name = names.get("commonname") or " ".join(
                    str(names.get(key) or "").strip() for key in ("firstname", "surname")).strip()
                if not name:
                    name = data.get("host_player_name") if len(members) == 1 else ""
                if not name:
                    continue
                card = dict(player.get("fields") or {})
                card.update(name=name, _history=True,
                            _signed_at=record.finished_utc or record.created_utc,
                            _history_key=f"signed:{record.job_id}:{index}",
                            source="Companion signing")
                if index < len(ids):
                    card["playerid"] = ids[index]
                # No invented FC year, source card, card variant or face status.
                rows.append(card)
                if len(rows) >= limit:
                    return tuple(rows)
    return tuple(rows)


def squad_context(svc: Any) -> str:
    """Small real squad context for the existing name-only AI recommender."""
    state = svc.store.snapshot()
    entries = []
    for player in state.squad.players[:60]:
        name = player.get("name") or player.get("playername")
        if not name:
            continue
        values = [str(name)]
        for label, keys in (("OVR", ("overallrating", "overall")),
                            ("positions", ("positions_text", "preferredposition1"))):
            value = next((player.get(k) for k in keys if player.get(k) is not None), None)
            if value is not None:
                values.append(f"{label} {value}")
        entries.append(" / ".join(values))
    if not entries:
        return ""
    return "\n\nCurrent Career squad (actual available values):\n" + "\n".join(entries)


class DiscoveryPanel:
    def __init__(self, parent: Any, svc: Any, view: dict[str, Any], search: Any,
                 *, search_host: Any, assistant_host: Any, assistant: Any) -> None:
        self.svc, self.view, self.search = svc, view, search
        self.search_host, self.assistant_host, self.assistant = search_host, assistant_host, assistant
        self.tabs = ctk.CTkFrame(parent, fg_color="transparent")
        self.tabs.pack(fill="x", before=search_host, pady=(0, theme.SP2))
        self.buttons = {}
        for index, mode in enumerate(MODES):
            self.tabs.grid_columnconfigure(index, weight=1, uniform="discovery")
            self.buttons[mode] = button(self.tabs, mode, lambda m=mode: self.select(m),
                                        kind="ghost", width=1, height=36)
            self.buttons[mode].grid(row=0, column=index, sticky="ew", padx=(0, 4))
        mode = view.get("discovery_mode", "Search")
        self.select(mode if mode in MODES else "Search", initial=True)

    def receive_recommendations(self, rows: Any, **kwargs: Any) -> None:
        self.search.replace_rows(rows, mode="Recommendations", **kwargs)

    def refresh_favourites(self) -> None:
        self.search.grid._signature = None
        for item in self.search.grid._pool:
            item["presentation"] = None
        if self.view.get("discovery_mode") == "Favourites":
            self._load_favourites()
        else:
            self.search.grid.refresh()

    def _load_favourites(self) -> None:
        try:
            rows = self.svc.catalog.favorites() if self.svc.catalog is not None else ()
            self.search.replace_rows(rows, mode="Favourites", empty_detail=
                                     "Save a card with ☆ in Search, Recommendations or Library. Your favourites are shared.")
        except Exception as exc:
            self.search.show_empty_results("Could not load favourites", str(exc))

    def refresh_recent(self) -> None:
        if self.view.get("discovery_mode") != "Recently Added":
            return
        try:
            state = self.svc.store.snapshot()
            signature = (state.squad.save_uid, state.squad.teamid,
                         tuple((job.job_id, job.outcome) for job in
                               (*state.jobs.active.values(), *state.jobs.history)
                               if is_sign_job_label(job.label)))
            if getattr(self, "_history_state", None) == signature:
                return
            rows = signing_history(self.svc)
            self._history_state = signature
            # Job progress changes frequently; an unchanged history must not
            # replace the model, selection, or scroll position.
            cached = self.view.get("discovery_cache", {}).get("Recently Added", {}).get("rows")
            if cached is not None and tuple(cached) == rows:
                return
            self.search.replace_rows(rows, mode="Recently Added", empty_detail=
                                     "Successful Companion signings in this Career save appear here. Queued, failed and preview jobs are excluded.")
        except Exception as exc:
            self.search.show_empty_results("Could not load signing history", str(exc))

    def select(self, mode: str, *, initial: bool = False) -> None:
        if not initial and mode == self.view.get("discovery_mode"):
            return
        if not initial:
            self.search._persist_fields()
            self.view.update(self.assistant.assistant_fields())
            self.search.cache_current_mode()
        self.view["discovery_mode"] = mode
        for name, control in self.buttons.items():
            control.configure(fg_color=theme.ACCENT if name == mode else theme.CARD,
                              text_color=theme.BG if name == mode else theme.MUTED,
                              border_color=theme.ACCENT if name == mode else theme.BORDER)
        self.search_host.pack_forget()
        self.assistant_host.pack_forget()
        if mode == "Search":
            self.search_host.pack(fill="x", after=self.tabs, pady=(0, theme.SP2))
        elif mode == "Recommendations":
            self.assistant_host.pack(fill="x", after=self.tabs, pady=(0, theme.SP2))
        cache = self.view.get("discovery_cache", {}).get(mode, {})
        self.view["selected_key"] = cache.get("selected_key", "")
        self.view["checked_keys"] = cache.get("checked_keys", ())
        self.view["result_page"] = cache.get("page", 0)
        self.view["result_sort"] = cache.get("sort", "Overall ↓")
        self.search.replace_rows(cache.get("rows", ()), mode=mode)
        self.search.result_title.configure(text={"Search": "Search results", "Recommendations": "Resolved Library cards",
                                                  "Favourites": "Your favourites", "Recently Added": "Recently added to your club"}[mode])
        if mode == "Favourites":
            self._load_favourites()
        elif mode == "Recently Added":
            self.refresh_recent()
        elif mode == "Search" and not cache.get("rows"):
            self.search.show_empty_results("Find your next signing", "Search your local Library by player name. Verified cards can enter the bag; Checkout reviews the exact safe targets.")
        elif mode == "Recommendations" and not cache.get("rows"):
            self.search.show_empty_results("Tell Codex what your squad needs", "Try a young backup striker, an experienced CM, or three realistic signings. Only cards resolved in your local Library can enter the bag.")
        self.search.sync_add_button()
