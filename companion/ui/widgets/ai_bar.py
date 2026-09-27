"""Compact provider control shared by Settings and every AI surface."""

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
    load_from_prefs,
    model_id,
    provider_id,
    provider_label,
    readiness,
    resolve_api_key,
    set_session_key,
    store_api_key,
    test_connection,
)
from .primitives import button, muted_label

try:  # pragma: no cover
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore[assignment]


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


def mount_ai_provider_bar(parent: Any, svc: Any) -> Any:
    """One row: provider, model, status. Changing it updates the whole app."""
    _prefs(svc)
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x")
    labels = [PROVIDER_LABELS[PROVIDER_CODEX], PROVIDER_LABELS[PROVIDER_API]]
    current = provider_label()
    variable = ctk.StringVar(value=current)
    status = muted_label(row, "", size=10)

    def refresh(*_args: Any) -> None:
        state = readiness()
        color = theme.SUCCESS if state.ready else theme.WARNING
        try:
            status.configure(
                text=f"{state.model_id}  ·  {state.message}",
                text_color=color,
            )
        except Exception:
            pass

    def changed(label: str) -> None:
        provider = PROVIDER_API if label == PROVIDER_LABELS[PROVIDER_API] else PROVIDER_CODEX
        _save_choice(svc, provider=provider)
        refresh()

    menu = _provider_menu(
        row,
        values=labels,
        variable=variable,
        command=changed,
        width=168,
        height=28,
    )
    menu.pack(side="left")
    status.pack(side="left", padx=(8, 0))

    def configure() -> None:
        if not svc.ui.open_settings():
            refresh()

    button(row, "Configure", configure, kind="ghost", height=28, width=88).pack(side="right")
    refresh()
    return row


def mount_ai_settings(host: Any, svc: Any) -> None:
    """Settings card body. The key field is masked and is never written to prefs."""
    _prefs(svc)
    state = readiness()
    status = muted_label(host, f"{state.model_id}  ·  {state.message}", size=11)
    status.pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP2))

    row = ctk.CTkFrame(host, fg_color="transparent")
    row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    labels = [PROVIDER_LABELS[PROVIDER_CODEX], PROVIDER_LABELS[PROVIDER_API]]
    variable = ctk.StringVar(value=provider_label())

    def refresh() -> None:
        now = readiness()
        color = theme.SUCCESS if now.ready else theme.WARNING
        try:
            status.configure(text=f"{now.model_id}  ·  {now.message}", text_color=color)
        except Exception:
            pass

    def changed(label: str) -> None:
        provider = PROVIDER_API if label == PROVIDER_LABELS[PROVIDER_API] else PROVIDER_CODEX
        _save_choice(svc, provider=provider)
        _show_key_row()
        refresh()

    _provider_menu(
        row,
        values=labels,
        variable=variable,
        command=changed,
        width=190,
        height=theme.BTN_MD,
    ).pack(side="left")

    model_var = ctk.StringVar(value=model_id())
    model_entry = ctk.CTkEntry(
        row,
        textvariable=model_var,
        width=160,
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
        placeholder_text_color=theme.MUTED,
    )
    model_entry.pack(side="left", padx=(theme.SP2, 0))

    def save_model() -> None:
        _save_choice(svc, model=model_var.get().strip())
        refresh()

    button(row, "Use model", save_model, kind="secondary", height=theme.BTN_MD, width=96).pack(
        side="left", padx=(theme.SP2, 0)
    )

    key_host = ctk.CTkFrame(host, fg_color="transparent")
    key_entry = ctk.CTkEntry(
        key_host,
        show="•",
        placeholder_text="Paste API key",
        height=theme.BTN_MD,
        fg_color=theme.CARD,
        border_color=theme.BORDER,
        text_color=theme.TEXT,
        placeholder_text_color=theme.MUTED,
    )

    key_entry.pack(side="left", fill="x", expand=True)

    def _remember(mode: str) -> None:
        secret = ""
        try:
            secret = key_entry.get().strip()
        except Exception:
            secret = ""
        try:
            if mode == "clear":
                clear_stored_key()
                clear_session_key()
            elif not secret:
                status.configure(text="Paste an API key first.", text_color=theme.WARNING)
                return
            elif mode == "session":
                set_session_key(secret)
            else:
                store_api_key(secret)
            try:
                key_entry.delete(0, "end")
            except Exception:
                pass
            refresh()
        except Exception as exc:  # noqa: BLE001
            status.configure(text=str(exc), text_color=theme.WARNING)

    button(key_host, "Save securely", lambda: _remember("store"), kind="secondary", height=theme.BTN_MD).pack(
        side="left", padx=(theme.SP2, 0)
    )
    button(key_host, "This session", lambda: _remember("session"), kind="ghost", height=theme.BTN_MD).pack(
        side="left", padx=(theme.SP2, 0)
    )
    button(key_host, "Clear", lambda: _remember("clear"), kind="ghost", height=theme.BTN_MD, width=64).pack(
        side="left", padx=(theme.SP2, 0)
    )

    def _show_key_row() -> None:
        if provider_id() == PROVIDER_API:
            if not key_host.winfo_manager():
                key_host.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
            _key, source = resolve_api_key()
            if source != "missing":
                key_entry.configure(placeholder_text="Key saved — paste only to replace it")
        elif key_host.winfo_manager():
            key_host.pack_forget()

    _show_key_row()

    actions = ctk.CTkFrame(host, fg_color="transparent")
    actions.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))

    def check_codex() -> None:
        from ...integrations.grok import use_build_session

        try:
            use_build_session()
            refresh()
        except Exception as exc:  # noqa: BLE001
            status.configure(text=str(exc), text_color=theme.WARNING)

    button(actions, "Check Codex login", check_codex, kind="ghost", height=theme.BTN_MD).pack(side="left")

    def on_test_done(result: ConnectionTest) -> None:
        color = theme.SUCCESS if result.ok else theme.WARNING
        detail = result.message or result.text or "Test finished"
        try:
            status.configure(
                text=f"{result.provider_id} · {result.model_id} · {detail}",
                text_color=color,
            )
        except Exception:
            pass

    def run_test() -> None:
        status.configure(
            text=f"Testing {provider_label()} with {model_id()}…",
            text_color=theme.MUTED,
        )

        def work() -> None:
            result = test_connection()
            try:
                host.after(0, lambda: on_test_done(result))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    button(actions, "Test connection", run_test, kind="secondary", height=theme.BTN_MD).pack(
        side="left", padx=(theme.SP2, 0)
    )
    muted_label(
        host,
        "AI suggestions are staged or reviewed before game changes. The provider does not bypass those checks.",
        size=10,
    ).pack(anchor="w", padx=theme.SP3, pady=(0, theme.SP3))
    refresh()
