"""Codex library assistant for Sign."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ....domain.add_player import (
    INDIVIDUAL_PLAYERS,
    formation_names,
    formation_slots,
    resolve_recommendation_report,
    review_lineup,
)
from ... import theme
from ...widgets.primitives import button, muted_label, panel
from .._common import set_status

try:  # pragma: no cover - desktop only
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

from .discovery import squad_context

from .search import (
    _criteria_text,
    _entry,
    _request_card_constraints,
    _request_player_count,
)


@dataclass(frozen=True, slots=True)
class CodexLibraryDraft:
    """Codex names after the local Library has accepted or rejected each one."""

    summary: str
    resolved: tuple[Any, ...]
    missing: tuple[str, ...] = ()
    rejected_out_of_band: tuple[str, ...] = ()
    requested_count: int = 0
    year: int | None = None
    ovr_min: int | None = None
    ovr_max: int | None = None


def grok_library_worker(
    *,
    request: str,
    count: int,
    catalog: Any,
    year: int | None = None,
    ovr_min: int | None = None,
    ovr_max: int | None = None,
    require_exact: bool = False,
    exclude_people: Sequence[str] = (),
) -> CodexLibraryDraft:
    """Executor boundary: Codex names become only exact local-card matches."""
    from ....integrations.grok import recommend_library_players

    summary, recommendations = recommend_library_players(request=request, max_results=count)
    inferred_year, inferred_min, inferred_max = _request_card_constraints(request)
    selected_year = year if year is not None else inferred_year
    minimum = ovr_min if ovr_min is not None else inferred_min
    maximum = ovr_max if ovr_max is not None else inferred_max
    report = resolve_recommendation_report(
        catalog,
        recommendations,
        limit=count,
        year=selected_year,
        ovr_min=minimum,
        ovr_max=maximum,
        exclude_people=exclude_people,
    )
    if not report.resolved and require_exact:
        detail = "None of Codex's player names were found in the local Library."
        if report.rejected_out_of_band:
            detail = " ".join(report.rejected_out_of_band)
        raise RuntimeError(detail)
    if require_exact and len(report.resolved) != count:
        raise RuntimeError(
            f"Only {len(report.resolved)}/{count} names could be verified as distinct local cards. "
            "No incomplete draft was selected."
        )
    return CodexLibraryDraft(
        summary=summary,
        resolved=report.resolved,
        missing=report.missing,
        rejected_out_of_band=report.rejected_out_of_band,
        requested_count=count,
        year=selected_year,
        ovr_min=minimum,
        ovr_max=maximum,
    )


class SquadAssistantPanel:
    """Optional formation + Codex suggestion strip on the Sign surface."""

    def __init__(
        self,
        controls: Any,
        svc: Any,
        view: dict[str, Any],
        *,
        card_constraints: Callable[[], tuple[int | None, int | None, int | None]],
        replace_rows: Callable[..., None],
        save_view: Callable[[], None],
        toggle_parent: Any | None = None,
        discovery: bool = False,
    ) -> None:
        self.discovery = discovery
        self.svc = svc
        self.view = view
        self.card_constraints = card_constraints
        self.replace_rows = replace_rows
        self.save_view = save_view
        self._kept_cards: list[dict[str, Any]] = list(view.get("recommendation_cards") or ())
        self._last_prompt = ""
        self._last_missing: tuple[str, ...] = tuple(view.get("recommendation_missing") or ())
        self._mount(controls, toggle_parent if toggle_parent is not None else controls)
        if discovery:
            self.toggle_button.pack_forget()
            self.visible["value"] = True
            self.panel.pack(fill="x")
            self.grok_feedback.configure(text=view.get("recommendation_feedback") or "Tell Codex what your squad needs. Only local Library cards can be added.")
            self._show_retry(bool(self._last_missing))

    def _mount(self, controls: Any, toggle_parent: Any) -> None:
        self.panel = ctk.CTkFrame(controls, fg_color="transparent")
        self.visible = {
            "value": bool(self.view["grok_prompt"] or self.view["formation"] != INDIVIDUAL_PLAYERS)
        }
        self.toggle_button = button(
            toggle_parent, "Ask Codex", self.toggle, kind="accent", height=theme.BTN_MD, width=96,
        )
        self.toggle_button.pack(side="left", padx=(theme.SP2, 0))
        if self.visible["value"]:
            self.panel.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            self.toggle_button.configure(text="Hide Codex")

        shell = panel(self.panel, level=1)
        shell.pack(fill="x", pady=(0, theme.SP1))
        try:
            shell.configure(border_width=1, border_color=theme.ACCENT)
        except Exception:
            pass
        inner = ctk.CTkFrame(shell, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SP3, pady=theme.SP2)
        from ...widgets.ai_bar import mount_ai_provider_bar

        provider_row = mount_ai_provider_bar(inner, self.svc)
        if self.discovery:
            for child in provider_row.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(width=160, wraplength=160, justify="left")

        formation_row = ctk.CTkFrame(inner, fg_color="transparent")
        formation_row.pack(fill="x", pady=(0, theme.SP1))
        muted_label(formation_row, "Draft formation", size=11).pack(side="left")
        self.formation = ctk.CTkOptionMenu(
            formation_row,
            values=list(formation_names()),
            variable=ctk.StringVar(value=str(self.view["formation"])),
            command=self.formation_changed,
            width=150,
            height=theme.BTN_MD,
            fg_color=theme.CARD,
            button_color=theme.CARD_HOVER,
            button_hover_color=theme.ACCENT,
            text_color=theme.TEXT,
        )
        self.formation.pack(side="left", padx=(theme.SP2, theme.SP3))
        muted_label(
            formation_row,
            "Exact roles checked locally.",
            size=10,
        ).pack(side="left", padx=(theme.SP2, 0))

        grok_row = ctk.CTkFrame(inner, fg_color="transparent")
        grok_row.pack(fill="x", pady=(theme.SP1, theme.SP1))
        self.grok_prompt = _entry(
            grok_row,
            "Need a young backup striker, or 3 realistic signings…",
            100,
            value=self.view["grok_prompt"],
        )
        self.grok_prompt.pack(side="left", fill="x", expand=True)
        self.grok_count = _entry(grok_row, "Max", 54, value=self.view["grok_count"])
        self.grok_count.pack(side="left", padx=(theme.SP2, 0))
        self.grok_count_label = muted_label(grok_row, "suggestions", size=10)
        self.grok_count_label.pack(side="left", padx=(theme.SP1, 0))
        self.grok_feedback = muted_label(
            inner,
            "Codex only suggests names. The local Library must verify every FC26 card before it can enter a draft.",
            size=10, wraplength=400, justify="left",
        )
        self.ask_button = button(
            grok_row, "Ask Codex", self.ask_grok, kind="accent", height=theme.BTN_LG, width=92,
        )
        self.ask_button.pack(side="left", padx=(theme.SP2, 0))
        self.retry_button = button(
            inner,
            "Ask for missing",
            self.retry_missing,
            kind="ghost",
            height=theme.BTN_LG,
            width=118,
        )
        self.grok_feedback.pack(anchor="w", pady=(0, theme.SP1))
        self._show_retry(False)
        self._apply_formation(self.formation.get(), persist=False)

    def toggle(self) -> None:
        self.visible["value"] = not self.visible["value"]
        if self.visible["value"]:
            self.panel.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            self.toggle_button.configure(text="Hide Codex")
        else:
            self.panel.pack_forget()
            self.toggle_button.configure(text="Ask Codex")

    def current_formation(self) -> str:
        if not self.visible["value"]:
            return INDIVIDUAL_PLAYERS
        return str(self.formation.get() or INDIVIDUAL_PLAYERS)

    def formation_changed(self, value: str) -> None:
        self._apply_formation(value, persist=True)

    def _apply_formation(self, value: str, *, persist: bool) -> None:
        self.view["formation"] = value
        if value != INDIVIDUAL_PLAYERS:
            self.grok_count.configure(state="normal")
            self.grok_count.delete(0, "end")
            self.grok_count.insert(0, str(len(formation_slots(value))))
            self.grok_count.configure(state="disabled")
            self.grok_count_label.configure(text="roles")
        else:
            self.grok_count.configure(state="normal")
            self.grok_count_label.configure(text="suggestions")
        if persist:
            self.save_view()

    def ask_grok(self) -> None:
        from ....integrations.ai_provider import block_reason

        blocked = block_reason()
        if blocked:
            set_status(self.svc, blocked)
            return
        prompt = (self.grok_prompt.get() or "").strip()
        if not prompt:
            set_status(self.svc, "Describe the players you want Codex to find.")
            return
        try:
            count = max(1, min(int((self.grok_count.get() or "3").strip()), 11))
            selected_year, minimum, maximum = self.card_constraints()
        except ValueError as exc:
            set_status(self.svc, str(exc))
            return
        selected_formation = self.current_formation()
        prompt_count = _request_player_count(prompt)
        if selected_formation != INDIVIDUAL_PLAYERS:
            slots = formation_slots(selected_formation)
            count = len(slots)
            roles = ", ".join(slot.label for slot in slots)
            request = (
                f"{prompt}\n\nBuild exactly {count} distinct FC players for formation "
                f"{selected_formation}. Return one player for each role in this exact order: {roles}."
            )
        elif prompt_count is not None:
            count = min(count, prompt_count)
            request = prompt
        else:
            request = prompt
        request += squad_context(self.svc)
        try:
            from ....integrations.grok import status as grok_status

            readiness = grok_status()
            if not readiness.connected:
                set_status(self.svc, readiness.message)
                return
        except Exception as exc:  # noqa: BLE001
            set_status(self.svc, f"Codex status check failed: {exc}")
            return
        self.save_view()
        self._show_retry(False)
        self.view["grok_generation"] = int(self.view.get("grok_generation", 0)) + 1
        request_generation = int(self.view["grok_generation"])
        results_generation = int(self.view.get("results_generation", 0))
        try:
            self.ask_button.configure(state="disabled")
        except Exception:
            pass
        self.grok_feedback.configure(
            text="Codex is choosing names; the Library will verify the exact FC26 cards..."
        )
        set_status(self.svc, "Codex is preparing suggestions. Nothing is being added.")

        def work(token: Any) -> None:
            try:
                draft = grok_library_worker(
                    request=request,
                    count=count,
                    catalog=self.svc.catalog,
                    year=selected_year,
                    ovr_min=minimum,
                    ovr_max=maximum,
                    require_exact=selected_formation != INDIVIDUAL_PLAYERS,
                )
                if token.cancelled:
                    return

                def complete() -> None:
                    if (
                        request_generation != int(self.view.get("grok_generation", 0))
                    ):
                        try:
                            self.ask_button.configure(state="normal")
                        except Exception:
                            pass
                        self.grok_feedback.configure(
                            text="Suggestions are ready, but your newer results were kept. Ask again to replace them."
                        )
                        return
                    cards = [dict(item.card, _ai_reason=item.reason) for item in draft.resolved]
                    self._kept_cards = list(cards)
                    self._last_prompt = prompt
                    self._last_missing = draft.missing
                    self.view["recommendation_cards"] = tuple(cards)
                    self.view["recommendation_missing"] = draft.missing
                    try:
                        self.ask_button.configure(state="normal")
                    except Exception:
                        pass
                    self._show_retry(bool(draft.missing) and selected_formation == INDIVIDUAL_PLAYERS)
                    constraints = _criteria_text(draft.year, draft.ovr_min, draft.ovr_max)
                    select_all = True
                    invalid_message = ""
                    if selected_formation != INDIVIDUAL_PLAYERS:
                        lineup = review_lineup(cards, formation=selected_formation)
                        if not lineup.valid:
                            select_all = False
                            missing = ", ".join(slot.label for slot in lineup.missing_slots)
                            extra = ", ".join(
                                str(card.get("name") or card.get("playername") or "Player")
                                for card in lineup.extra_cards
                            )
                            detail = f"Missing compatible cards for {missing}." if missing else ""
                            if extra:
                                detail = f"{detail} Extra or wrong-role cards: {extra}.".strip()
                            invalid_message = (
                                f"{lineup.filled_count}/{len(lineup.slots)} formation roles verified. "
                                f"{detail} Shown for inspection. None checked."
                            )
                    self.replace_rows(cards, select_all=select_all)
                    if invalid_message:
                        self.grok_feedback.configure(text=invalid_message)
                        set_status(self.svc, invalid_message)
                        return
                    if selected_formation != INDIVIDUAL_PLAYERS:
                        message = (
                            f"{lineup.filled_count}/{len(lineup.slots)} {selected_formation} roles covered"
                            f"{constraints}. Check the cards you want, then add them to your bag."
                        )
                    elif len(draft.resolved) < draft.requested_count:
                        missing = ", ".join(draft.missing)
                        extras = " ".join(draft.rejected_out_of_band)
                        message = (
                            f"Found {len(draft.resolved)} of {draft.requested_count} in Library"
                            f"{constraints}. Add those {len(draft.resolved)} to your bag"
                            + (f", or search the missing name ({missing})" if missing else "")
                            + "."
                        )
                        if extras:
                            message = f"{message} {extras}".strip()
                    else:
                        message = (
                            f"Codex suggested {len(draft.resolved)} verified player(s){constraints}. "
                            "Add them to your bag, then checkout once."
                        )
                    feedback = message
                    if draft.missing:
                        feedback += " Not found in local Library: " + ", ".join(draft.missing) + "."
                    if draft.rejected_out_of_band:
                        feedback += " " + " ".join(draft.rejected_out_of_band)
                    self.view["recommendation_feedback"] = feedback
                    self.grok_feedback.configure(text=feedback)
                    set_status(self.svc, message)

                self.svc.executor.on_ui_thread(complete)
            except Exception as exc:  # noqa: BLE001
                message = f"Codex recommendation failed: {exc}"

                def failed() -> None:
                    try:
                        self.ask_button.configure(state="normal")
                    except Exception:
                        pass
                    self.view["recommendation_feedback"] = message
                    self.grok_feedback.configure(text=message)
                    set_status(self.svc, message)

                self.svc.executor.on_ui_thread(failed)

        try:
            self.svc.executor.submit("grok.add_player.recommend", work)
        except Exception as exc:  # noqa: BLE001
            try:
                self.ask_button.configure(state="normal")
            except Exception:
                pass
            set_status(self.svc, f"Could not start Codex: {exc}")

    def _show_retry(self, visible: bool) -> None:
        try:
            if visible:
                if not self.retry_button.winfo_manager():
                    self.retry_button.pack(anchor="w", pady=(theme.SP1, 0))
            else:
                self.retry_button.pack_forget()
        except Exception:
            pass

    def retry_missing(self) -> None:
        missing = tuple(self._last_missing)
        if not missing:
            set_status(self.svc, "There are no missing Codex names to retry.")
            return
        kept = list(self._kept_cards)
        exclude = tuple(
            str(card.get("person_id") or card.get("base_player_id") or card.get("name") or "")
            for card in kept
        )
        prompt = (self.grok_prompt.get() or self._last_prompt or "").strip()
        request = (
            f"{prompt}\n\nThe Library already has: "
            + ", ".join(str(card.get("name") or "Player") for card in kept)
            + f". Recommend {len(missing)} different players instead of: "
            + ", ".join(missing)
            + "."
        )
        try:
            selected_year, minimum, maximum = self.card_constraints()
        except ValueError as exc:
            set_status(self.svc, str(exc))
            return
        self.view["grok_generation"] = int(self.view.get("grok_generation", 0)) + 1
        retry_generation = self.view["grok_generation"]
        self._show_retry(False)
        try:
            self.retry_button.configure(state="disabled")
            self.ask_button.configure(state="disabled")
        except Exception:
            pass
        self.grok_feedback.configure(text="Asking Codex for the missing names. Kept cards stay selected.")
        set_status(self.svc, "Retrying missing Library names. Nothing is being added.")

        def work(token: Any) -> None:
            try:
                draft = grok_library_worker(
                    request=request,
                    count=len(missing),
                    catalog=self.svc.catalog,
                    year=selected_year,
                    ovr_min=minimum,
                    ovr_max=maximum,
                    require_exact=False,
                    exclude_people=exclude,
                )
                if token.cancelled:
                    return

                def complete() -> None:
                    if retry_generation != self.view.get("grok_generation"):
                        return
                    extra = [dict(item.card, _ai_reason=item.reason) for item in draft.resolved]
                    merged = kept + extra
                    self._kept_cards = list(merged)
                    self._last_missing = draft.missing
                    self.view["recommendation_cards"] = tuple(merged)
                    self.view["recommendation_missing"] = draft.missing
                    self.replace_rows(merged, select_all=True)
                    try:
                        self.ask_button.configure(state="normal")
                        self.retry_button.configure(state="normal")
                    except Exception:
                        pass
                    self._show_retry(bool(draft.missing))
                    found = len(extra)
                    message = (
                        f"Added {found} more Library card(s). "
                        f"{len(merged)} ready to add to your bag."
                    )
                    if draft.missing:
                        message += " Still missing: " + ", ".join(draft.missing) + "."
                    self.view["recommendation_feedback"] = message
                    self.grok_feedback.configure(text=message)
                    set_status(self.svc, message)

                self.svc.executor.on_ui_thread(complete)
            except Exception as exc:  # noqa: BLE001
                message = f"Codex retry failed: {exc}"

                def failed() -> None:
                    try:
                        self.ask_button.configure(state="normal")
                        self.retry_button.configure(state="normal")
                    except Exception:
                        pass
                    self._show_retry(True)
                    self.view["recommendation_feedback"] = message
                    self.grok_feedback.configure(text=message)
                    set_status(self.svc, message)

                self.svc.executor.on_ui_thread(failed)

        try:
            self.svc.executor.submit("grok.add_player.retry", work)
        except Exception as exc:  # noqa: BLE001
            try:
                self.ask_button.configure(state="normal")
                self.retry_button.configure(state="normal")
            except Exception:
                pass
            self._show_retry(True)
            set_status(self.svc, f"Could not retry Codex: {exc}")

    def assistant_fields(self) -> dict[str, Any]:
        return {
            "formation": self.formation.get() or INDIVIDUAL_PLAYERS,
            "grok_prompt": self.grok_prompt.get() or "",
            "grok_count": self.grok_count.get() or "",
        }
