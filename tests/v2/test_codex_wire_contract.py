"""Codex wire contract: stream/store flags, json_object input, live HTTP."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from companion.integrations import grok

REAL_PLAYER_PROMPT = "gerrard martin overall 84 like this season form"


def test_codex_json_body_sets_stream_true_and_store_false() -> None:
    body = grok._responses_body(
        [{"role": "user", "content": REAL_PLAYER_PROMPT}],
        model=grok.DEFAULT_MODEL,
    )
    dumped = json.dumps(body)
    assert body["stream"] is True
    assert body["store"] is False
    assert '"stream": true' in dumped
    assert '"store": false' in dumped
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"]["effort"] == "high"


def test_codex_json_object_input_contains_json_even_for_plain_player_prompts() -> None:
    assert "json" not in REAL_PLAYER_PROMPT.lower()
    body = grok._responses_body(
        [
            {"role": "system", "content": "You design safe FC 26 edits."},
            {"role": "user", "content": json.dumps({"user_request": REAL_PLAYER_PROMPT})},
        ],
        model=grok.DEFAULT_MODEL,
    )
    input_text = " ".join(str(item.get("content") or "") for item in body["input"])
    assert "json" in input_text.lower()
    assert "json_object" in json.dumps(body["text"])


def test_codex_http_post_body_contains_stream_true(monkeypatch) -> None:
    captured: dict[str, bytes] = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"output_text":"{\\"ok\\":true}"}'

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def fake_urlopen(req: object, timeout: float = 0.0) -> _Resp:
        captured["data"] = bytes(getattr(req, "data") or b"")
        return _Resp()

    monkeypatch.setattr(grok.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(grok, "_session", lambda: {"token": "t", "account_id": "a"})
    grok._request([{"role": "user", "content": REAL_PLAYER_PROMPT}], model=grok.DEFAULT_MODEL)
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload["stream"] is True
    assert payload["store"] is False
    input_text = " ".join(str(item.get("content") or "") for item in payload["input"])
    assert "json" in input_text.lower()


def test_empty_completed_event_uses_streamed_text_deltas() -> None:
    raw = (
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"{\\"target_playerid\\":232223,"}\n\n'
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"\\"summary\\":\\"ok\\"}"}\n\n'
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"output":[],"output_text":null}}\n\n'
    )
    body = grok._assemble_codex_stream(raw)
    parsed = grok._content(body)
    assert parsed["target_playerid"] == 232223
    assert parsed["summary"] == "ok"


def test_live_codex_accepts_real_player_prompt_without_the_word_json() -> None:
    if not grok.status().connected:
        pytest.skip("Codex CLI is not signed in")
    assert "json" not in REAL_PLAYER_PROMPT.lower()
    try:
        body = grok._request(
            [
                {"role": "system", "content": "You design safe FC 26 Career Mode edits."},
                {"role": "user", "content": json.dumps({"user_request": REAL_PLAYER_PROMPT})},
            ],
            model=grok.DEFAULT_MODEL,
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "Stream must be set to true" not in message
        assert "text.format" not in message
        assert "json_object" not in message
        raise
    text = grok._response_text(body)
    assert text.strip()


def test_live_propose_changes_for_tsimikas() -> None:
    if not grok.status().connected:
        pytest.skip("Codex CLI is not signed in")
    result = grok.propose_changes(
        playerid=232223,
        player_name="Kostas Tsimikas",
        current={
            "overallrating": 77,
            "potential": 80,
            "acceleration": 80,
            "sprintspeed": 78,
            "height": 178,
            "weight": 70,
        },
        request="create tsimikas overall 84",
    )
    assert result.fields, result
    assert "overallrating" in result.fields or len(result.fields) >= 5
