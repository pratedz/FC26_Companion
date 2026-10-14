"""Exercise the real Settings dialog and worker button without live services."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.app.store import Store
from companion.ui.shell import Shell


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


@pytest.mark.gui
@pytest.mark.parametrize("outcome", ["installed", "rejected", "exception"])
def test_settings_opens_ai_card_and_installs_with_configured_queue(monkeypatch, outcome):
    import customtkinter as ctk

    from companion.platform import le_install
    from companion.ui.widgets import ai_bar

    # Keep real widgets and the actual AI-card import/mounting, while avoiding
    # credential discovery, worker files, application polling, and game calls.
    monkeypatch.setattr(ai_bar, "_prefs", lambda svc: None)
    monkeypatch.setattr(ai_bar, "readiness", lambda provider=None: SimpleNamespace(
        model_id="test-model", message="Test provider", ready=False, label="Codex Account",
    ))
    monkeypatch.setattr(ai_bar, "model_id", lambda: "test-model")
    monkeypatch.setattr(ai_bar, "provider_id", lambda: ai_bar.PROVIDER_CODEX)
    monkeypatch.setattr(ai_bar, "provider_label", lambda: ai_bar.PROVIDER_LABELS[ai_bar.PROVIDER_CODEX])
    queue = Path("C:/settings-regression/queue")
    paths = SimpleNamespace(root=queue.parent, queue=queue,
                            state_db=queue.parent / "state.sqlite",
                            profiles_file=queue.parent / "profiles.json",
                            universe_db=queue.parent / "universe.sqlite")
    calls, status_calls, messages = [], [], []

    def install(*, queue_dir):
        calls.append(queue_dir)
        if outcome == "exception":
            raise RuntimeError("Mock installation failure")
        return {"ok": outcome == "installed", "errors": [] if outcome == "installed" else ["Mock rejection"]}

    def worker_status():
        status_calls.append(True)
        return {"installed": bool(calls) and outcome == "installed",
                "core_version": "test", "detail": "Mock worker status"}

    monkeypatch.setattr(le_install, "install", install)
    monkeypatch.setattr(le_install, "status", worker_status)
    root = ctk.CTk()
    root.withdraw()
    windows = []
    real_toplevel = ctk.CTkToplevel

    def hidden_toplevel(*args, **kwargs):
        win = real_toplevel(*args, **kwargs)
        win.withdraw()
        windows.append(win)
        return win

    monkeypatch.setattr(ctk, "CTkToplevel", hidden_toplevel)
    shell = object.__new__(Shell)
    shell.root = root
    shell.svc = SimpleNamespace(paths=paths, store=Store())
    shell._status = lambda text, *, tone="info": messages.append((text, tone))
    try:
        shell._open_settings()
        assert len(windows) == 1
        win = windows[0]
        widgets = list(_descendants(win))
        labels = {widget.cget("text") for widget in widgets if isinstance(widget, ctk.CTkLabel)}
        assert {"About", "Display", "AI provider", "Paths", "Worker", "Health"} <= labels
        assert any("test-model" in text for text in labels)
        buttons = [widget for widget in widgets if isinstance(widget, ctk.CTkButton)]
        install_button = next(widget for widget in buttons if widget.cget("text") == "Install / repair worker")
        assert calls == []
        assert len(status_calls) == 1
        install_button.invoke()
        assert calls == [queue]
        assert len(status_calls) == 2
        if outcome == "installed":
            assert messages[-1] == ("Worker installed.", "ok")
        else:
            assert messages[-1][1] == "error"
            assert "Mock" in messages[-1][0]
        worker_labels = [widget for widget in widgets if isinstance(widget, ctk.CTkLabel)
                         and "Mock worker status" in widget.cget("text")]
        assert len(worker_labels) == 1
        assert f"Installed: {outcome == 'installed'}" in worker_labels[0].cget("text")
    finally:
        for win in windows:
            win.destroy()
        root.update_idletasks()
        root.destroy()
