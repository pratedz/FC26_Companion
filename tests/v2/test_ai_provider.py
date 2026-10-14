"""Provider routing, model id, and secret handling. No network and no real key."""

from __future__ import annotations

import pytest

from companion.app import events as E
from companion.app.reducers import reduce
from companion.app.state import AppState
from companion.integrations import ai_provider as ai
from companion.integrations import grok


@pytest.fixture(autouse=True)
def _reset_provider():
    ai.apply_settings(provider=ai.PROVIDER_CODEX, model="")
    ai.clear_session_key()
    yield
    ai.apply_settings(provider=ai.PROVIDER_CODEX, model="")
    ai.clear_session_key()


def test_both_providers_are_asked_for_the_same_model(monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_codex(messages, *, model, json_object=True, reasoning=True):
        seen.append(("codex", model))
        return {"output_text": "{}"}

    def fake_api(messages, *, model, json_object=True, reasoning=True):
        seen.append(("api", model))
        return "{}"

    monkeypatch.setattr(grok, "_codex_request", fake_codex)
    monkeypatch.setattr(ai, "openai_text", fake_api)
    ai.apply_settings(provider=ai.PROVIDER_CODEX, model="gpt-6-terra")
    ai.dispatch_request([{"role": "user", "content": "plan"}], model=grok.DEFAULT_MODEL)
    ai.apply_settings(provider=ai.PROVIDER_API, model="gpt-6-terra")
    ai.dispatch_request([{"role": "user", "content": "plan"}], model=grok.DEFAULT_MODEL)
    assert seen == [("codex", "gpt-6-terra"), ("api", "gpt-6-terra")]


def test_model_error_is_not_replaced_with_another_model(monkeypatch):
    def fake_api(messages, *, model, json_object=True, reasoning=True):
        assert model == "gpt-6-terra"
        raise ai.AiError("Model gpt-6-terra is not available for this API key.", category="model")

    monkeypatch.setattr(ai, "openai_text", fake_api)
    ai.apply_settings(provider=ai.PROVIDER_API, model="gpt-6-terra")
    try:
        ai.dispatch_request([{"role": "user", "content": "plan"}], model=grok.DEFAULT_MODEL)
    except ai.AiError as exc:
        assert exc.category == "model"
        assert "gpt-6-terra" in str(exc)
    else:
        raise AssertionError("model failure was swallowed")


def test_missing_api_key_blocks_before_a_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ai, "_read_stored_key", lambda: "")
    ai.clear_session_key()
    ai.apply_settings(provider=ai.PROVIDER_API, model="gpt-6-terra")
    state = ai.readiness()
    assert state.ready is False
    assert state.category == "authentication"
    assert "key" in state.message.lower()


def test_key_resolution_prefers_env_then_stored_then_session(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(ai, "_read_stored_key", lambda: "stored-key")
    ai.set_session_key("session-key")
    assert ai.resolve_api_key() == ("env-key", "env")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert ai.resolve_api_key() == ("stored-key", "stored")
    monkeypatch.setattr(ai, "_read_stored_key", lambda: "")
    assert ai.resolve_api_key() == ("session-key", "session")
    ai.clear_session_key()
    assert ai.resolve_api_key()[1] == "missing"


def test_secrets_are_scrubbed_from_prefs_and_errors():
    clean = ai.scrub_pref_values(
        {"ai_provider": "openai_api", "openai_api_key": "secret-value", "ui_scale": 1}
    )
    assert clean == {"ai_provider": "openai_api", "ui_scale": 1}
    state = reduce(AppState(), E.PrefChanged("openai_api_key", "secret-value"))
    assert "openai_api_key" not in state.prefs.values
    ai.set_session_key("secret-value")
    try:
        assert "secret-value" not in ai.redact("rejected secret-value")
    finally:
        ai.clear_session_key()


def test_missing_pref_uses_the_api_key_and_shared_model():
    ai.load_from_prefs({})
    assert ai.provider_id() == ai.PROVIDER_API
    assert ai.model_id() == grok.DEFAULT_MODEL == "gpt-6-luna"


def _signed_out_codex(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text('{"auth_mode": "none"}', encoding="utf-8")
    monkeypatch.setattr(grok, "auth_path", lambda: auth)
    monkeypatch.setattr(grok.shutil, "which", lambda _name: None)


def test_api_provider_is_not_blocked_by_a_signed_out_codex(monkeypatch, tmp_path):
    _signed_out_codex(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    ai.apply_settings(provider=ai.PROVIDER_API)
    assert ai.block_reason() == ""
    assert ai.readiness(ai.PROVIDER_CODEX).ready is False


def test_blocked_provider_points_at_the_ready_one(monkeypatch, tmp_path):
    _signed_out_codex(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    ai.apply_settings(provider=ai.PROVIDER_CODEX)
    reason = ai.block_reason()
    assert "sign-in required" in reason.lower()
    assert "OpenAI API Key is ready" in reason


def test_signed_out_codex_stub_reads_as_not_signed_in(monkeypatch, tmp_path):
    _signed_out_codex(monkeypatch, tmp_path)
    state = grok.status()
    assert state.connected is False
    assert "not signed in" in state.message


def test_expired_codex_token_is_not_ready(monkeypatch, tmp_path):
    import base64
    import json

    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    token = f"{part({'alg': 'none'})}.{part({'exp': 1})}.sig"
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": token}}), encoding="utf-8")
    monkeypatch.setattr(grok, "auth_path", lambda: auth)
    state = grok.status()
    assert state.connected is False and state.state == "expired"
    assert "expired" in ai.readiness(ai.PROVIDER_CODEX).message.lower()


def test_listeners_fire_on_provider_model_and_key_changes():
    calls: list[int] = []
    unsubscribe = ai.subscribe(lambda: calls.append(1))
    ai.apply_settings(provider=ai.PROVIDER_API)
    ai.apply_settings(model="gpt-test")
    ai.set_session_key("k")
    ai.apply_settings(provider=ai.PROVIDER_API)  # unchanged: no event
    unsubscribe()
    ai.apply_settings(provider=ai.PROVIDER_CODEX)
    assert len(calls) == 3
