"""Codex assistant helpers for the Player surface."""

from __future__ import annotations

from typing import Any

from ....domain.builds import revision
from ... import theme
from ...widgets.primitives import button, muted_label, text_label
from .._common import set_status

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

# AI / Codex accent — shared theme token (Integration Agent).
CODEX_PURPLE = theme.CODEX

EXAMPLE_PROMPTS: tuple[str, ...] = (
    "Make him faster but keep him realistic",
    "Improve passing for a deep-lying playmaker",
    "Turn him into a box-to-box midfielder",
    "Boost defending without killing pace",
)


def _grok_readiness_copy(result: Any) -> tuple[str, str]:
    """UI wording for the selected AI provider's readiness; never exposes credentials."""
    label = str(getattr(result, "label", "") or "AI")
    if bool(getattr(result, "ready", False)):
        return f"{label}: ready — proposals still require Review → Apply.", theme.SUCCESS
    from ....integrations.ai_provider import block_reason

    return f"{label}: {block_reason() or getattr(result, 'message', 'not ready.')}", theme.WARNING


def _set_grok_feedback(widget: Any, text: str, *, color: str | None = None) -> None:
    """Update local player-page feedback without making it a hard UI dependency."""
    if widget is None:
        return
    try:
        kwargs: dict[str, Any] = {"text": text}
        if color is not None:
            kwargs["text_color"] = color
        widget.configure(**kwargs)
    except Exception:
        pass


def _watch_ai_changes(widget: Any, refresh: Any) -> None:
    """Re-run ``refresh`` whenever the provider, model, or key changes, until ``widget`` dies."""
    from ...widgets.ai_bar import _watch

    _watch(widget, refresh)


def _connect_grok(svc: Any, feedback: Any = None) -> None:
    try:
        from ....integrations.ai_provider import readiness

        result = readiness()
        message, color = _grok_readiness_copy(result)
        _set_grok_feedback(feedback, message, color=color)
        set_status(svc, message)
    except Exception as exc:  # noqa: BLE001
        message = f"AI status check failed: {exc}"
        _set_grok_feedback(feedback, message, color=theme.WARNING)
        set_status(svc, message)


def grok_player_worker(
    token: Any,
    *,
    playerid: int,
    player_name: str,
    current: dict[str, Any],
    request: str,
    on_success: Any,
    on_error: Any,
) -> None:
    """Executor-compatible single-player Codex worker with no UI side effects."""
    try:
        if getattr(token, "cancelled", False):
            return
        from ....integrations.grok import propose_changes

        result = propose_changes(
            playerid=playerid,
            player_name=player_name,
            current=current,
            request=request,
        )
        if not getattr(token, "cancelled", False):
            on_success(result)
    except Exception as exc:  # noqa: BLE001
        if not getattr(token, "cancelled", False):
            on_error(exc)


def _confirm_draft_replacement(svc: Any, incoming: str, proceed: Any) -> None:
    """Require an explicit choice before a source replaces a useful draft."""
    state = svc.store.snapshot()
    if not state.editor.has_changes:
        proceed()
        return
    try:
        win = ctk.CTkToplevel()
        win.title("Replace staged changes?")
        win.geometry("440x210")
        win.configure(fg_color=theme.BG)
        win.transient(win.master)
        win.grab_set()
    except Exception:
        # A headless/UI-less caller must never silently replace a draft.
        set_status(svc, f"Your staged changes are protected. Discard them before using {incoming}.")
        return
    text_label(win, "Replace staged changes?", size=theme.T_HEADLINE, bold=True).pack(
        anchor="w", padx=theme.SP4, pady=(theme.SP4, theme.SP1)
    )
    muted_label(
        win,
        f"Using {incoming} replaces the current draft. Review it first if you want to keep it.",
        size=12,
        wraplength=380,
    ).pack(anchor="w", padx=theme.SP4)
    actions = ctk.CTkFrame(win, fg_color="transparent")
    actions.pack(fill="x", padx=theme.SP4, pady=theme.SP4)

    def replace() -> None:
        from ....app import events as E

        svc.store.dispatch(E.EditorReset())
        try:
            win.destroy()
        except Exception:
            pass
        proceed()

    button(actions, "Cancel", lambda: win.destroy(), kind="ghost", height=32).pack(side="right")
    button(actions, "Replace draft", replace, kind="danger", height=32).pack(
        side="right", padx=(0, theme.SP2)
    )


def _ask_grok(
    svc: Any,
    prompt: str,
    *,
    feedback: Any = None,
    propose_button: Any = None,
    ai_state: dict[str, Any] | None = None,
) -> None:
    state = svc.store.snapshot()
    if state.editor.has_changes:
        _confirm_draft_replacement(
            svc,
            "this AI proposal",
            lambda: _ask_grok(
                svc, prompt, feedback=feedback, propose_button=propose_button, ai_state=ai_state
            ),
        )
        return
    if not state.target.locked or state.target.playerid is None:
        _set_grok_feedback(feedback, "Codex: choose a live squad player first.", color=theme.WARNING)
        set_status(svc, "Select a player from your live squad first.")
        return
    request = (prompt or "").strip()
    if not request:
        _set_grok_feedback(feedback, "Codex: describe the build you want first.", color=theme.WARNING)
        set_status(svc, "Describe the build or changes you want.")
        return
    from ....integrations.ai_provider import block_reason

    blocked = block_reason()
    if blocked:
        _set_grok_feedback(feedback, blocked, color=theme.WARNING)
        set_status(svc, blocked)
        return
    ai_state = ai_state if ai_state is not None else {}
    if ai_state.get("busy"):
        _set_grok_feedback(feedback, "AI is already preparing this proposal…", color=theme.WARNING)
        set_status(svc, "AI is already preparing a proposal.")
        return
    playerid = int(state.target.playerid)
    current = dict(state.editor.merged())
    base_revision = revision(playerid, current)
    player_name = state.target.name
    ai_state["busy"] = True
    _set_grok_feedback(
        feedback, "AI is preparing a review-only proposal (up to ~2 minutes)…", color=theme.ACCENT
    )
    try:
        if propose_button is not None:
            propose_button.configure(state="disabled", text="Proposing…")
    except Exception:
        pass
    set_status(svc, "AI is preparing a proposal. Nothing is being written.")

    def finish_button() -> None:
        ai_state["busy"] = False
        try:
            if propose_button is not None:
                propose_button.configure(state="normal", text="Propose changes")
        except Exception:
            pass

    def on_success(result: Any) -> None:
        def complete() -> None:
            finish_button()
            latest = svc.store.snapshot()
            if (
                latest.target.playerid != playerid
                or revision(playerid, latest.editor.merged()) != base_revision
            ):
                message = "Player or draft changed while Codex worked — proposal discarded."
                _set_grok_feedback(feedback, message, color=theme.WARNING)
                set_status(svc, message)
                return
            from ....app.commands.builds import stage_proposal
            count = stage_proposal(svc, result)
            suffix = f" {result.summary}" if result.summary else ""
            message = f"Codex staged {count} change(s). Review every value before Apply.{suffix}"
            _set_grok_feedback(feedback, message, color=theme.SUCCESS)
            set_status(svc, message)
            from .workstation import _open_review

            _open_review(svc)

        if getattr(svc, "executor", None) is not None:
            svc.executor.on_ui_thread(complete)
        else:
            complete()

    def on_error(exc: BaseException) -> None:
        message = f"Codex could not create a proposal: {exc}"

        def failed() -> None:
            finish_button()
            _set_grok_feedback(feedback, message, color=theme.WARNING)
            set_status(svc, message)

        if getattr(svc, "executor", None) is not None:
            svc.executor.on_ui_thread(failed)
        else:
            failed()

    def work(token: Any) -> None:
        grok_player_worker(
            token,
            playerid=playerid,
            player_name=player_name,
            current=current,
            request=request,
            on_success=on_success,
            on_error=on_error,
        )

    try:
        svc.executor.submit(f"grok.proposal.{playerid}", work)
    except Exception as exc:  # noqa: BLE001
        finish_button()
        message = f"Could not start Codex: {exc}"
        _set_grok_feedback(feedback, message, color=theme.WARNING)
        set_status(svc, message)

