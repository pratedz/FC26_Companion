"""AI provider controls: a compact bar for AI surfaces and the Settings card.

All three providers are always shown with their own status. Selecting one makes
it the app-wide provider; the panel below it holds only that provider's setup.
Every mounted control subscribes to ``ai_provider`` so a change anywhere
(provider, model, speed, key) refreshes all of them.
"""

from __future__ import annotations

import threading
from typing import Any

from .. import theme
from ...integrations.ai_provider import (
    EFFORT_LABELS,
    EFFORTS,
    PREF_CLAUDE_BASE_URL,
    PREF_CLAUDE_MODEL,
    PREF_EFFORT,
    PREF_MODEL,
    PREF_PROVIDER,
    PROVIDER_API,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_LABELS,
    ConnectionTest,
    apply_settings,
    claude_base_url,
    claude_key_source_label,
    claude_model_id,
    clear_claude_session_key,
    clear_claude_stored_key,
    clear_session_key,
    clear_stored_key,
    key_source_label,
    load_from_prefs,
    model_id,
    provider_id,
    provider_label,
    readiness,
    reasoning_effort,
    resolve_api_key,
    resolve_claude_key,
    set_claude_session_key,
    set_session_key,
    shared_model_id,
    store_api_key,
    store_claude_key,
    subscribe,
    test_connection,
)
from ...integrations.claude_proxy import MODELS as CLAUDE_MODELS
from .primitives import button, entry, muted_label, text_label

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]

_ORDER = (PROVIDER_CODEX, PROVIDER_API, PROVIDER_CLAUDE)
_BLURB = {
    PROVIDER_CODEX: "Uses the ChatGPT account signed in to Codex CLI on this Windows user.",
    PROVIDER_API: "Uses your own OpenAI API key. Billed to that key.",
    PROVIDER_CLAUDE: "Uses the local Claude proxy (CLIProxyAPI-Claude) on this PC. Start it first.",
}
_BY_LABEL = {label: pid for pid, label in PROVIDER_LABELS.items()}
_SPEEDS = [EFFORT_LABELS[e] for e in EFFORTS]
_EFFORT_BY_SPEED = {EFFORT_LABELS[e]: e for e in EFFORTS}


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


def _save_choice(
    svc: Any,
    *,
    provider: str | None = None,
    model: str | None = None,
    claude_model: str | None = None,
    base_url: str | None = None,
    effort: tuple[str, str] | None = None,
) -> None:
    """Persist non-secret choices to prefs and apply them app-wide."""
    from ...app import events as E

    if provider is not None:
        svc.store.dispatch(E.PrefChanged(PREF_PROVIDER, provider))
    if model is not None:
        svc.store.dispatch(E.PrefChanged(PREF_MODEL, model))
    if claude_model is not None:
        svc.store.dispatch(E.PrefChanged(PREF_CLAUDE_MODEL, claude_model))
    if base_url is not None:
        svc.store.dispatch(E.PrefChanged(PREF_CLAUDE_BASE_URL, base_url))
    if effort is not None:
        svc.store.dispatch(E.PrefChanged(PREF_EFFORT[effort[0]], effort[1]))
    _prefs(svc)
    apply_settings(
        provider=provider,
        model=model,
        claude_model=claude_model,
        claude_base_url=base_url,
        efforts=None if effort is None else {effort[0]: effort[1]},
    )


def _speed_label(provider: str | None = None) -> str:
    return EFFORT_LABELS.get(reasoning_effort(provider), EFFORT_LABELS["medium"])


def _speed_menu(parent: Any, svc: Any, *, width: int = 104, height: int = 28) -> tuple[Any, Any]:
    """Fast / Balanced / Thorough for the selected provider. Returns (menu, variable)."""
    variable = ctk.StringVar(value=_speed_label())

    def changed(label: str) -> None:
        effort = _EFFORT_BY_SPEED.get(label)
        if effort:
            _save_choice(svc, effort=(provider_id(), effort))

    menu = _provider_menu(parent, values=_SPEEDS, variable=variable, command=changed,
                          width=width, height=height)
    return menu, variable


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
    """One row: provider, speed, model, status. Changing it updates the whole app."""
    _prefs(svc)
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x")
    labels = [PROVIDER_LABELS[pid] for pid in _ORDER]
    variable = ctk.StringVar(value=provider_label())
    status = muted_label(row, "", size=10)
    speed, speed_var = _speed_menu(row, svc)

    def refresh(*_args: Any) -> None:
        state = readiness()
        try:
            variable.set(provider_label())
            speed_var.set(_speed_label())
            status.configure(text=f"{state.model_id}  ·  {state.message}", text_color=_tone(state.ready))
        except Exception:
            pass

    def changed(label: str) -> None:
        _save_choice(svc, provider=_BY_LABEL.get(label, PROVIDER_API))
        refresh()

    menu = _provider_menu(row, values=labels, variable=variable, command=changed, width=168, height=28)
    menu.pack(side="left")
    speed.pack(side="left", padx=(6, 0))
    status.pack(side="left", padx=(8, 0))

    def configure() -> None:
        if not svc.ui.open_settings():
            refresh()

    button(row, "Configure", configure, kind="ghost", height=28, width=88).pack(side="right")
    _watch(row, refresh)
    refresh()
    return row


def mount_ai_settings(host: Any, svc: Any) -> None:
    """Settings card body. Key fields are masked and never written to prefs."""
    _prefs(svc)
    pad = {"padx": theme.SP4}

    active = text_label(host, "", size=theme.T_BODY, bold=True)
    active.pack(anchor="w", pady=(0, theme.SP2), **pad)

    # -- provider cards: every status visible, click one to use it -----------
    cards_row = ctk.CTkFrame(host, fg_color="transparent")
    cards_row.pack(fill="x", pady=(0, theme.SP2), **pad)
    cards_row.grid_columnconfigure(tuple(range(len(_ORDER))), weight=1, uniform="provider")
    tiles: dict[str, dict[str, Any]] = {}

    def choose(pid: str) -> None:
        if pid != provider_id():
            _save_choice(svc, provider=pid)
        sync()

    last = len(_ORDER) - 1
    for col, pid in enumerate(_ORDER):
        tile = ctk.CTkFrame(
            cards_row, fg_color=theme.CARD, border_width=2, border_color=theme.BORDER,
            corner_radius=8, cursor="hand2",
        )
        tile.grid(row=0, column=col, sticky="nsew", padx=(0, theme.SP2) if col < last else 0)
        title = text_label(tile, PROVIDER_LABELS[pid], size=theme.T_BODY, bold=True)
        title.pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
        state_lbl = muted_label(tile, "", size=theme.T_SMALL, wraplength=160, justify="left")
        state_lbl.pack(anchor="w", padx=theme.SP3)
        blurb = muted_label(tile, _BLURB[pid], size=10, wraplength=160, justify="left")
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

    def build_claude() -> None:
        # Shows where the key came from, never the key itself.
        _secret, source = resolve_claude_key()
        have = source != "missing"
        muted_label(
            detail,
            claude_key_source_label(source) if have else (
                "No Claude proxy key found. Start CLIProxyAPI-Claude once, "
                "or paste its client key below."
            ),
            size=theme.T_SMALL,
        ).pack(anchor="w", pady=(0, theme.SP1))
        url_row = ctk.CTkFrame(detail, fg_color="transparent")
        url_row.pack(fill="x", pady=(0, theme.SP1))
        muted_label(url_row, "Proxy URL", size=theme.T_SMALL).pack(side="left")
        url_var = ctk.StringVar(value=claude_base_url())
        entry(url_row, variable=url_var, placeholder="http://127.0.0.1:8318", width=220).pack(
            side="left", padx=(theme.SP2, 0)
        )

        def use_url() -> None:
            text = url_var.get().strip()
            if text and not text.lower().startswith(("http://", "https://")):
                say("The proxy URL must start with http:// or https://.", False)
                return
            _save_choice(svc, base_url=text)
            say(f"Claude proxy URL set to {claude_base_url()}. Run Test connection.", True)

        button(url_row, "Use URL", use_url, kind="ghost", height=theme.BTN_MD, width=80).pack(
            side="left", padx=(theme.SP2, 0)
        )
        key_entry = entry(
            detail,
            placeholder="Paste a key to override it" if have else "Paste Claude proxy key",
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
                    clear_claude_stored_key()
                    clear_claude_session_key()
                    left = resolve_claude_key()[1]
                    if left == "missing":
                        say("Saved and session Claude keys removed.", None)
                    else:
                        say(f"Saved and session keys removed. Still using: "
                            f"{claude_key_source_label(left)}.", None)
                    return
                if not secret:
                    say("Paste the Claude proxy key first.", False)
                    return
                if mode == "session":
                    set_claude_session_key(secret)
                    if resolve_claude_key()[1] != "session":
                        say("Session key kept, but a saved/config key takes priority.", None)
                    else:
                        say("Key will be used until Companion closes. Run Test connection.", True)
                else:
                    store_claude_key(secret)
                    say("Key saved to Windows Credential Manager. Run Test connection.", True)
            except Exception as exc:  # noqa: BLE001
                say(str(exc), False)

        button(actions, "Test connection", lambda: run_test(), kind="secondary",
               height=theme.BTN_MD).pack(side="left")
        button(actions, "Save key", lambda: remember("store"), kind="ghost", icon="lock",
               height=theme.BTN_MD).pack(side="left", padx=(theme.SP2, 0))
        button(actions, "Use for this session", lambda: remember("session"), kind="ghost",
               height=theme.BTN_MD).pack(side="left", padx=(theme.SP2, 0))
        button(actions, "Forget key", lambda: remember("clear"), kind="ghost",
               height=theme.BTN_MD).pack(side="left", padx=(theme.SP2, 0))
        if source == "env":
            muted_label(
                detail,
                "FC26_CLAUDE_PROXY_KEY in the environment takes priority over a saved key.",
                size=10,
            ).pack(anchor="w", pady=(theme.SP1, 0))

    # -- model + speed + test (rebuilt when the provider changes) -------------
    model_row = ctk.CTkFrame(host, fg_color="transparent")
    model_row.pack(fill="x", pady=(0, theme.SP2), **pad)
    speed_holder: dict[str, Any] = {}

    def build_model_row(chosen: str) -> None:
        for child in model_row.winfo_children():
            child.destroy()
        muted_label(model_row, "Model", size=theme.T_SMALL).pack(side="left")
        if chosen == PROVIDER_CLAUDE:
            model_var = ctk.StringVar(value=claude_model_id())
            field = ctk.CTkComboBox(
                model_row, values=list(CLAUDE_MODELS), variable=model_var, width=200,
                fg_color=theme.CARD, border_color=theme.BORDER, button_color=theme.BORDER,
                button_hover_color=theme.CARD_HOVER, dropdown_fg_color=theme.PANEL,
                dropdown_hover_color=theme.CARD_HOVER, dropdown_text_color=theme.TEXT,
                text_color=theme.TEXT,
            )
        else:
            model_var = ctk.StringVar(value=shared_model_id())
            field = entry(model_row, variable=model_var, placeholder="Model ID", width=200)
        field.pack(side="left", padx=(theme.SP2, 0))

        def save_model() -> None:
            text = model_var.get().strip()
            if chosen == PROVIDER_CLAUDE:
                _save_choice(svc, claude_model=text)
                say(f"Claude model set to {claude_model_id()}.", True)
            else:
                _save_choice(svc, model=text)
                say(f"Model set to {shared_model_id()} for Codex Account and OpenAI API Key.", True)

        button(model_row, "Use model", save_model, kind="secondary", height=theme.BTN_MD,
               width=96).pack(side="left", padx=(theme.SP2, 0))
        muted_label(model_row, "Speed", size=theme.T_SMALL).pack(side="left", padx=(theme.SP3, 0))
        menu, variable = _speed_menu(model_row, svc, height=theme.BTN_MD)
        menu.pack(side="left", padx=(theme.SP2, 0))
        speed_holder["var"] = variable
        button(model_row, "Test connection", lambda: run_test(), kind="secondary",
               height=theme.BTN_MD).pack(side="right")

    shown = {"provider": "", "source": "", "model_provider": ""}

    def sync() -> None:
        """Repaint cards, headline, and the setup panels from current state."""
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
                text=f"Using {now.label} · {now.model_id} · {_speed_label(chosen)} · "
                     f"{'Ready' if now.ready else 'Not ready'}",
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
            if speed_holder.get("var") is not None:
                speed_holder["var"].set(_speed_label(chosen))
        except Exception:
            return
        if chosen == PROVIDER_API:
            source = resolve_api_key()[1]
        elif chosen == PROVIDER_CLAUDE:
            source = resolve_claude_key()[1]
        else:
            source = ""
        if (shown["provider"], shown["source"]) != (chosen, source):
            shown["provider"], shown["source"] = chosen, source
            for child in detail.winfo_children():
                child.destroy()
            {PROVIDER_API: build_api, PROVIDER_CLAUDE: build_claude}.get(chosen, build_codex)()
        if shown["model_provider"] != chosen:
            shown["model_provider"] = chosen
            build_model_row(chosen)

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

    muted_label(
        host,
        "AI suggestions are staged or reviewed before game changes. The provider does not bypass those checks.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP3))

    _watch(host, sync)
    sync()
