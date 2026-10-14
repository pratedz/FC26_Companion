"""AI provider controls: a compact bar for AI surfaces and the Settings card.

Both providers are always shown with their own status. Selecting one makes it
the app-wide provider; the panel below it holds only that provider's setup.
Every mounted control subscribes to ``ai_provider`` so a change anywhere
(provider, model, key) refreshes all of them.
"""

from __future__ import annotations

import threading
from typing import Any

from .. import theme
from ...integrations.ai_provider import (
    PREF_MODEL,
    PREF_PROVIDER,
    PROVIDER_API,
    PROVIDER_CODEX,
    PROVIDER_LABELS,
    ConnectionTest,
    apply_settings,
    clear_session_key,
    clear_stored_key,
    key_source_label,
    load_from_prefs,
    model_id,
    provider_id,
    provider_label,
    readiness,
    resolve_api_key,
    set_session_key,
    store_api_key,
    subscribe,
    test_connection,
)
from .primitives import button, entry, muted_label, text_label

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

_ORDER = (PROVIDER_CODEX, PROVIDER_API)
_BLURB = {
    PROVIDER_CODEX: "Uses the ChatGPT account signed in to Codex CLI on this Windows user.",
    PROVIDER_API: "Uses your own OpenAI API key. Billed to that key.",
}


def _provider_menu(parent: Any, **kwargs: Any) -> Any:
    """Dark menu. The stock dropdown uses light-mode black text on our dark panel."""
    menu = ctk.CTkOptionMenu(
        parent,
        fg_color=theme.CARD,
        button_color=theme.BORDER,
        button_hover_color=theme.CARD_HOVER,
        dropdown_fg_color=theme.PANEL,
        dropdown_hover_color=theme.CARD_HOVER,
        dropdown_text_color=theme.TEXT,
        text_color=theme.TEXT,
        text_color_disabled=theme.MUTED,
        **kwargs,
    )
    try:
        menu._text_label.configure(fg=theme.TEXT, bg=theme.CARD)
    except Exception:
        pass
    return menu


def _prefs(svc: Any) -> None:
    load_from_prefs(svc.store.snapshot().prefs.values)


def _save_choice(svc: Any, *, provider: str | None = None, model: str | None = None) -> None:
    from ...app import events as E

    if provider is not None:
        svc.store.dispatch(E.PrefChanged(PREF_PROVIDER, provider))
    if model is not None:
        svc.store.dispatch(E.PrefChanged(PREF_MODEL, model))
    _prefs(svc)
    apply_settings(provider=provider_id(), model=model_id())


def _watch(widget: Any, refresh: Any) -> None:
    """Refresh ``widget`` on any AI change until it is destroyed."""
    unsubscribe: list[Any] = []

    def guarded() -> None:
        try:
            alive = bool(widget.winfo_exists())
        except Exception:
            alive = False
        if not alive:
            unsubscribe[0]()
            return
        refresh()

    unsubscribe.append(subscribe(guarded))


def _tone(ready: bool) -> str:
    return theme.SUCCESS if ready else theme.WARNING


def mount_ai_provider_bar(parent: Any, svc: Any) -> Any:
    """One row: provider, model, status. Changing it updates the whole app."""
    _prefs(svc)
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x")
    labels = [PROVIDER_LABELS[pid] for pid in _ORDER]
    variable = ctk.StringVar(value=provider_label())
    status = muted_label(row, "", size=10)

    def refresh(*_args: Any) -> None:
        state = readiness()
        try:
            variable.set(provider_label())
            status.configure(text=f"{state.model_id}  ·  {state.message}", text_color=_tone(state.ready))
        except Exception:
            pass

    def changed(label: str) -> None:
        provider = PROVIDER_API if label == PROVIDER_LABELS[PROVIDER_API] else PROVIDER_CODEX
        _save_choice(svc, provider=provider)
        refresh()

    menu = _provider_menu(row, values=labels, variable=variable, command=changed, width=168, height=28)
    menu.pack(side="left")
    status.pack(side="left", padx=(8, 0))

    def configure() -> None:
        if not svc.ui.open_settings():
            refresh()

    button(row, "Configure", configure, kind="ghost", height=28, width=88).pack(side="right")
    _watch(row, refresh)
    refresh()
    return row


def mount_ai_settings(host: Any, svc: Any) -> None:
    """Settings card body. The key field is masked and is never written to prefs."""
    _prefs(svc)
    pad = {"padx": theme.SP4}

    active = text_label(host, "", size=theme.T_BODY, bold=True)
    active.pack(anchor="w", pady=(0, theme.SP2), **pad)

    # -- provider cards: both statuses visible, click one to use it ----------
    cards_row = ctk.CTkFrame(host, fg_color="transparent")
    cards_row.pack(fill="x", pady=(0, theme.SP2), **pad)
    cards_row.grid_columnconfigure((0, 1), weight=1, uniform="provider")
    tiles: dict[str, dict[str, Any]] = {}

    def choose(pid: str) -> None:
        if pid != provider_id():
            _save_choice(svc, provider=pid)
        sync()

    for col, pid in enumerate(_ORDER):
        tile = ctk.CTkFrame(
            cards_row, fg_color=theme.CARD, border_width=2, border_color=theme.BORDER,
            corner_radius=8, cursor="hand2",
        )
        tile.grid(row=0, column=col, sticky="nsew", padx=(0, theme.SP2) if col == 0 else 0)
        title = text_label(tile, PROVIDER_LABELS[pid], size=theme.T_BODY, bold=True)
        title.pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
        state_lbl = muted_label(tile, "", size=theme.T_SMALL, wraplength=230, justify="left")
        state_lbl.pack(anchor="w", padx=theme.SP3)
        blurb = muted_label(tile, _BLURB[pid], size=10, wraplength=230, justify="left")
        blurb.pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))
        for widget in (tile, title, state_lbl, blurb):
            widget.bind("<Button-1>", lambda _e, p=pid: choose(p))
        tiles[pid] = {"tile": tile, "state": state_lbl}

    # -- setup panel for the selected provider -------------------------------
    detail = ctk.CTkFrame(host, fg_color="transparent")
    detail.pack(fill="x", pady=(0, theme.SP2), **pad)
    notice = muted_label(host, "", size=theme.T_SMALL, wraplength=520, justify="left")
    notice.pack(anchor="w", pady=(0, theme.SP2), **pad)

    def say(text: str, ok: bool | None = None) -> None:
        color = theme.MUTED if ok is None else _tone(ok)
        try:
            notice.configure(text=text, text_color=color)
        except Exception:
            pass

    def build_codex() -> None:
        def sign_in() -> None:
            from ...integrations.grok import start_codex_login

            try:
                say(start_codex_login(), None)
            except Exception as exc:  # noqa: BLE001
                say(str(exc), False)

        def check() -> None:
            sync()
            state = readiness(PROVIDER_CODEX)
            say(state.message, state.ready)

        actions = ctk.CTkFrame(detail, fg_color="transparent")
        actions.pack(fill="x")
        button(actions, "Sign in with Codex", sign_in, kind="secondary", height=theme.BTN_MD).pack(side="left")
        button(actions, "Check again", check, kind="ghost", height=theme.BTN_MD).pack(
            side="left", padx=(theme.SP2, 0)
        )
        muted_label(
            detail,
            "To use a different account, run `codex logout` then Sign in again. "
            "Companion only reads the Codex login; it never copies it.",
            size=10, wraplength=520, justify="left",
        ).pack(anchor="w", pady=(theme.SP1, 0))

    def build_api() -> None:
        _secret, source = resolve_api_key()
        have = source != "missing"
        muted_label(
            detail,
            key_source_label(source) if have else "No API key yet. Paste one below.",
            size=theme.T_SMALL,
        ).pack(anchor="w", pady=(0, theme.SP1))
        key_entry = entry(
            detail,
            placeholder="Paste a new key to replace it" if have else "Paste API key (sk-…)",
            show="•",
        )
        key_entry.pack(fill="x")
        actions = ctk.CTkFrame(detail, fg_color="transparent")
        actions.pack(fill="x", pady=(theme.SP2, 0))

        def remember(mode: str) -> None:
            try:
                secret = key_entry.get().strip()
            except Exception:
                secret = ""
            try:
                if mode == "clear":
                    clear_stored_key()
                    clear_session_key()
                    say("Saved and session keys removed.", None)
                    return
                if not secret:
                    say("Paste an API key first.", False)
                    return
                if mode == "session":
                    set_session_key(secret)
                    say("Key will be used until Companion closes. Run Test connection.", True)
                else:
                    store_api_key(secret)
                    say("Key saved to Windows Credential Manager. Run Test connection.", True)
            except Exception as exc:  # noqa: BLE001
                say(str(exc), False)

        button(actions, "Save securely", lambda: remember("store"), kind="secondary", icon="lock",
               height=theme.BTN_MD).pack(side="left")
        button(actions, "Use for this session", lambda: remember("session"), kind="ghost",
               height=theme.BTN_MD).pack(side="left", padx=(theme.SP2, 0))
        if have:
            button(actions, "Forget key", lambda: remember("clear"), kind="ghost",
                   height=theme.BTN_MD).pack(side="left", padx=(theme.SP2, 0))
        if source == "env":
            muted_label(
                detail,
                "OPENAI_API_KEY in the environment takes priority over a saved key.",
                size=10,
            ).pack(anchor="w", pady=(theme.SP1, 0))

    shown = {"provider": "", "source": ""}

    def sync() -> None:
        """Repaint cards, headline, and the setup panel from current state."""
        try:
            if not host.winfo_exists():
                return
        except Exception:
            return
        chosen = provider_id()
        states = {pid: readiness(pid) for pid in _ORDER}
        now = states[chosen]
        try:
            active.configure(
                text=f"Using {now.label} · {now.model_id} · {'Ready' if now.ready else 'Not ready'}",
                text_color=_tone(now.ready),
            )
            for pid in _ORDER:
                st = states[pid]
                tiles[pid]["tile"].configure(
                    border_color=theme.ACCENT if pid == chosen else theme.BORDER
                )
                tiles[pid]["state"].configure(
                    text=("Selected · " if pid == chosen else "") + st.message,
                    text_color=_tone(st.ready),
                )
        except Exception:
            return
        source = resolve_api_key()[1] if chosen == PROVIDER_API else ""
        if (shown["provider"], shown["source"]) != (chosen, source):
            shown["provider"], shown["source"] = chosen, source
            for child in detail.winfo_children():
                child.destroy()
            (build_api if chosen == PROVIDER_API else build_codex)()

    # -- model + test ----------------------------------------------------------
    model_row = ctk.CTkFrame(host, fg_color="transparent")
    model_row.pack(fill="x", pady=(0, theme.SP2), **pad)
    muted_label(model_row, "Model", size=theme.T_SMALL).pack(side="left")
    model_var = ctk.StringVar(value=model_id())
    entry(model_row, variable=model_var, placeholder="Model ID", width=200).pack(
        side="left", padx=(theme.SP2, 0)
    )

    def save_model() -> None:
        _save_choice(svc, model=model_var.get().strip())
        say(f"Model set to {model_id()} for both providers.", True)

    button(model_row, "Use model", save_model, kind="secondary", height=theme.BTN_MD, width=96).pack(
        side="left", padx=(theme.SP2, 0)
    )

    def on_test_done(result: ConnectionTest) -> None:
        detail_text = result.message or result.text or "Test finished"
        say(
            f"{provider_label(result.provider_id)} · {result.model_id} · "
            f"{detail_text} ({result.latency_ms} ms)",
            result.ok,
        )

    def run_test() -> None:
        say(f"Testing {provider_label()} with {model_id()}…", None)

        def work() -> None:
            result = test_connection()
            try:
                host.after(0, lambda: on_test_done(result))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    button(model_row, "Test connection", run_test, kind="secondary", height=theme.BTN_MD).pack(
        side="right"
    )
    muted_label(
        host,
        "AI suggestions are staged or reviewed before game changes. The provider does not bypass those checks.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP3))

    _watch(host, sync)
    sync()
