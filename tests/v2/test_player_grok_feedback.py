"""Regression coverage for Player-page Grok readiness and executor behavior."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.integrations import grok
from companion.ui.surfaces import player


def test_grok_status_distinguishes_missing_and_usable_build_login(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    monkeypatch.setattr(grok, "auth_path", lambda: auth)
    monkeypatch.setattr(grok.shutil, "which", lambda _name: None)

    missing = grok.status()
    assert missing.connected is False
    assert missing.state == "needs_login"
    assert "codex login" in missing.login_hint

    auth.write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "not-exposed", "account_id": "acct"}}),
        encoding="utf-8",
    )
    connected = grok.status()
    assert connected.connected is True
    assert connected.state == "connected"
    assert "not-exposed" not in connected.message


def test_grok_system_prompt_requires_height_weight_for_era_physique(monkeypatch) -> None:
    from companion.domain.builds import revision

    current = {"overallrating": 91, "height": 187, "weight": 84, "bodytypecode": 1}
    captured: list[list] = []
    content = {
        "target_playerid": 123,
        "base_revision": revision(123, current),
        "summary": "08/09 physique",
        "changes": [{"field": "height", "value": 185, "reason": "era"}],
    }

    def fake_request(messages, model):
        captured.append(messages)
        return {"choices": [{"message": {"content": json.dumps(content)}}]}

    monkeypatch.setattr(grok, "_request", fake_request)
    grok.propose_changes(
        playerid=123,
        player_name="Ronaldo",
        current=current,
        request="make him feel like CR7 08/09",
    )
    system = captured[0][0]["content"]
    user = json.loads(captured[0][1]["content"])
    assert "height" in system and "weight" in system
    assert "bodytypecode" in system
    assert "runstylecode" in system
    assert "physique" in system.lower() or "era" in system.lower()
    assert "leave body unchanged" in system.lower()
    assert user["requirements"]["era_physique_requires_height_weight"] is True


def test_grok_build_login_button_action_rechecks_without_leaking_auth_path(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    monkeypatch.setattr(grok, "auth_path", lambda: auth)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.setattr(grok.shutil, "which", lambda _name: "C:/tools/codex.exe")

    with pytest.raises(RuntimeError) as excinfo:
        grok.use_build_session()
    message = str(excinfo.value)
    assert "not signed in" in message
    assert "codex login" in message
    assert str(auth) not in message


def test_player_grok_worker_reports_success_and_respects_cancellation(monkeypatch) -> None:
    returned = SimpleNamespace(fields={"acceleration": 93})
    monkeypatch.setattr(
        grok,
        "propose_changes",
        lambda **_kwargs: returned,
    )
    received: list[object] = []
    errors: list[BaseException] = []
    player.grok_player_worker(
        SimpleNamespace(cancelled=False),
        playerid=10,
        player_name="Target",
        current={"overallrating": 80},
        request="make him faster",
        on_success=received.append,
        on_error=errors.append,
    )
    assert received == [returned]
    assert errors == []

    player.grok_player_worker(
        SimpleNamespace(cancelled=True),
        playerid=10,
        player_name="Target",
        current={"overallrating": 80},
        request="make him faster",
        on_success=received.append,
        on_error=errors.append,
    )
    assert received == [returned]


def test_player_surface_has_visible_grok_feedback_and_safe_card_activation() -> None:
    package_dir = Path(player.__file__).resolve().parent
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py"))
    )
    for marker in (
        "AI assistant",
        "Check Codex login",
        "Codex is preparing a review-only proposal",
        "grok_player_worker",
        "stage_selected()",
        "Review every value before Apply",
        "era/physique",
    ):
        assert marker in source


def test_codex_stream_assembles_completed_response_and_text_deltas() -> None:
    completed = grok._assemble_codex_stream(
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"output_text":"{\\"ok\\":true}"}}\n\n'
    )
    assert completed["output_text"] == '{"ok":true}'
    deltas = grok._assemble_codex_stream(
        'data: {"type":"response.output_text.delta","delta":"{\\"a\\":"}\n\n'
        'data: {"type":"response.output_text.delta","delta":"1}"}\n\n'
        "data: [DONE]\n\n"
    )
    assert deltas["output_text"] == '{"a":1}'
    body = grok._responses_body([{"role": "user", "content": "hi"}], model="gpt-5.6-terra")
    assert body["stream"] is True

