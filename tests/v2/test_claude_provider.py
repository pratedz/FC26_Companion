"""Claude (proxy) provider: dispatch, key resolution, errors. No network by default.

The single live test is opt-in: set FC26_LIVE_CLAUDE=1 with the local proxy running.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest

from companion.integrations import ai_provider as ai
from companion.integrations import claude_proxy as cp
from companion.integrations import grok

KEY = "sk-test-claude-0123456789abcdef"


def _defaults() -> None:
    ai.apply_settings(
        provider=ai.PROVIDER_CODEX, model="", claude_model="", claude_base_url="",
        efforts={pid: "" for pid in ai.PROVIDERS}, parallel=0,
    )
    ai.clear_session_key()
    ai.clear_claude_session_key()
    cp.reset_session()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(ai.CLAUDE_KEY_ENV, raising=False)
    monkeypatch.setattr(ai, "_read_claude_stored_key", lambda: "")
    monkeypatch.setattr(ai, "claude_config_path", lambda: tmp_path / "absent" / "config.yaml")
    monkeypatch.setattr(ai, "_claude_sdk_available", lambda: True)
    _defaults()
    yield
    _defaults()


def _msg(text: str = '{"ok": true}', *, stop: str = "end_turn", category: str | None = None):
    details = SimpleNamespace(category=category, explanation="") if stop == "refusal" else None
    return SimpleNamespace(
        stop_reason=stop,
        stop_details=details,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


class _Stream:
    def __init__(self, outcome):
        self.outcome = outcome

    def __enter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self

    def __exit__(self, *_exc):
        return False

    def get_final_message(self):
        return self.outcome


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.calls.append(params)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        return _Stream(outcome)


@pytest.fixture
def fake(monkeypatch):
    made: dict = {}

    def install(*outcomes):
        client = FakeClient(*outcomes)

        def factory(base_url, api_key):
            made["base_url"], made["api_key"] = base_url, api_key
            return client

        monkeypatch.setattr(cp, "_client", factory)
        client.made = made
        return client

    return install


def _claude(monkeypatch, **settings):
    monkeypatch.setenv(ai.CLAUDE_KEY_ENV, KEY)
    ai.apply_settings(provider=ai.PROVIDER_CLAUDE, **settings)


PLAN = [
    {"role": "system", "content": "You design safe FC 26 edits. Return JSON only."},
    {"role": "user", "content": '{"user_request": "lift the squad"}'},
]


# -- request shape ---------------------------------------------------------------


def test_dispatch_converts_messages_and_caches_the_system_prompt(monkeypatch, fake):
    client = fake(_msg('{"players": []}'))
    _claude(monkeypatch)
    body = ai.dispatch_request(PLAN, model=grok.DEFAULT_MODEL)
    assert body == {"output_text": '{"players": []}'}
    assert grok._content(body) == {"players": []}
    params = client.calls[0]
    assert params["model"] == "claude-sonnet-5-5"
    assert params["system"] == [{
        "type": "text", "text": PLAN[0]["content"], "cache_control": {"type": "ephemeral"},
    }]
    assert params["messages"] == [{"role": "user", "content": PLAN[1]["content"]}]
    assert params["output_config"] == {"effort": "low"}
    assert params["betas"] == [cp.FALLBACK_BETA] and params["fallbacks"] == "default"
    assert params["max_tokens"] == cp.DEFAULT_MAX_TOKENS
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in params
    assert client.made == {"base_url": "http://127.0.0.1:8318", "api_key": KEY}


def test_conversion_merges_turns_and_never_ends_on_an_assistant_prefill():
    params = cp.build_params(
        [
            {"role": "assistant", "content": "earlier"},
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "a"},
            {"role": "tool", "content": "b"},
            {"role": "assistant", "content": "draft"},
        ],
        model="claude-haiku-5-5",
        json_object=True,
    )
    roles = [m["role"] for m in params["messages"]]
    assert roles[0] == "user" and roles[-1] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert "a\n\nb" in params["messages"][2]["content"]
    assert "json" in params["system"][0]["text"].lower()
    assert "betas" not in params and "fallbacks" not in params  # no fallbacks for Haiku


def test_json_hint_goes_to_the_user_turn_without_a_system_prompt():
    params = cp.build_params([{"role": "user", "content": "plan"}], model="claude-sonnet-5-5")
    assert "system" not in params
    assert params["messages"][-1]["content"].endswith("Respond with JSON only.")
    plain = cp.build_params([{"role": "user", "content": "hi"}], model="m", json_object=False)
    assert plain["messages"][-1]["content"] == "hi"


def test_schema_is_passed_as_output_format(monkeypatch, fake):
    client = fake(_msg('{"a": 1}'))
    _claude(monkeypatch)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}},
              "required": ["a"], "additionalProperties": False}
    ai.dispatch_request([{"role": "user", "content": "give a"}], model="x", schema=schema)
    params = client.calls[0]
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert "Respond with JSON only" not in params["messages"][0]["content"]


def test_speed_maps_to_claude_effort_and_model_pref(monkeypatch, fake):
    client = fake(_msg())
    _claude(monkeypatch, claude_model="claude-opus-5-5", efforts={ai.PROVIDER_CLAUDE: "high"})
    ai.dispatch_request(PLAN, model=grok.DEFAULT_MODEL)
    assert client.calls[0]["model"] == "claude-opus-5-5"
    assert client.calls[0]["output_config"]["effort"] == "high"


def test_connection_test_uses_claude_with_low_effort(monkeypatch, fake):
    client = fake(_msg("FC26_OK"))
    _claude(monkeypatch)
    result = ai.test_connection()
    assert result.ok and result.provider_id == ai.PROVIDER_CLAUDE
    assert result.model_id == "claude-sonnet-5-5"
    assert client.calls[0]["output_config"] == {"effort": "low"}
    assert "json" not in client.calls[0]["messages"][0]["content"].lower()


# -- stop reasons ------------------------------------------------------------------


def test_refusal_is_a_friendly_error_with_category(monkeypatch, fake):
    fake(_msg("", stop="refusal", category="cyber"))
    _claude(monkeypatch)
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert caught.value.category == "refusal"
    assert "cyber" in str(caught.value) and "declined" in str(caught.value)


def test_max_tokens_retries_once_with_a_bigger_budget(monkeypatch, fake):
    client = fake(_msg('{"cut', stop="max_tokens"), _msg('{"ok": 1}'))
    _claude(monkeypatch)
    assert ai.dispatch_request(PLAN, model="x") == {"output_text": '{"ok": 1}'}
    assert [c["max_tokens"] for c in client.calls] == [cp.DEFAULT_MAX_TOKENS, cp.RETRY_MAX_TOKENS]


def test_max_tokens_twice_reports_truncation(monkeypatch, fake):
    client = fake(_msg('{"cut', stop="max_tokens"))
    _claude(monkeypatch)
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert caught.value.category == "malformed" and "cut off" in str(caught.value)
    assert len(client.calls) == 2


def test_empty_text_is_malformed(monkeypatch, fake):
    fake(SimpleNamespace(stop_reason="end_turn", stop_details=None, content=[]))
    _claude(monkeypatch)
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert caught.value.category == "malformed"


# -- errors ------------------------------------------------------------------------

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")
_REQ = httpx2.Request("POST", "http://127.0.0.1:8318/v1/messages")


def _status(cls, code: int, message: str = "nope"):
    return cls(message, response=httpx2.Response(code, request=_REQ),
               body={"type": "error", "error": {"type": "x", "message": message}})


@pytest.mark.parametrize(
    ("error", "category", "needle"),
    [
        (lambda: _status(anthropic.AuthenticationError, 401), "authentication", "rejected the key"),
        (lambda: _status(anthropic.PermissionDeniedError, 403), "authentication", "rejected the key"),
        (lambda: _status(anthropic.NotFoundError, 404), "model", "claude-sonnet-5-5"),
        (lambda: _status(anthropic.RateLimitError, 429), "rate_limit", "quota"),
        (lambda: _status(anthropic.InternalServerError, 500, "upstream down"), "network", "(500)"),
        (lambda: anthropic.APITimeoutError(request=_REQ), "timeout", "timed out"),
        (lambda: anthropic.APIConnectionError(request=_REQ), "network",
         "Start the Claude proxy (port 8318)"),
    ],
)
def test_errors_map_most_specific_first(monkeypatch, fake, error, category, needle):
    fake(error())
    _claude(monkeypatch)
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert caught.value.category == category
    assert needle in str(caught.value)
    assert caught.value.__cause__ is None


def test_rejected_fallbacks_retry_without_them_and_are_remembered(monkeypatch, fake):
    client = fake(_status(anthropic.BadRequestError, 400, "unknown field fallbacks"),
                  _msg('{"a": 1}'))
    _claude(monkeypatch)
    assert ai.dispatch_request(PLAN, model="x") == {"output_text": '{"a": 1}'}
    assert "fallbacks" in client.calls[0] and "betas" in client.calls[0]
    assert "fallbacks" not in client.calls[1] and "betas" not in client.calls[1]
    ai.dispatch_request(PLAN, model="x")
    assert "fallbacks" not in client.calls[2]


def test_a_real_bad_request_is_reported_and_not_remembered(monkeypatch, fake):
    client = fake(_status(anthropic.BadRequestError, 400, "messages: bad"))
    _claude(monkeypatch)
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert "(400)" in str(caught.value)
    assert len(client.calls) == 2
    assert cp._no_fallbacks == set()


def test_key_never_appears_in_errors_or_logs(monkeypatch, fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake(_status(anthropic.InternalServerError, 500, f"bad key {KEY} echoed"))
    _claude(monkeypatch)
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert KEY not in str(caught.value)
    assert "[redacted]" in str(caught.value)
    assert KEY not in caplog.text
    status = ai.readiness()
    assert KEY not in status.message and KEY not in ai.block_reason()
    result = ai.test_connection()
    assert KEY not in result.message and KEY not in result.text


def test_missing_key_blocks_before_any_request(monkeypatch, fake):
    client = fake(_msg())
    ai.apply_settings(provider=ai.PROVIDER_CLAUDE)
    state = ai.readiness()
    assert state.ready is False and state.category == "authentication"
    with pytest.raises(ai.AiError) as caught:
        ai.dispatch_request(PLAN, model="x")
    assert caught.value.category == "authentication"
    assert client.calls == []


def test_missing_sdk_is_not_ready(monkeypatch):
    monkeypatch.setenv(ai.CLAUDE_KEY_ENV, KEY)
    monkeypatch.setattr(ai, "_claude_sdk_available", lambda: False)
    state = ai.readiness(ai.PROVIDER_CLAUDE)
    assert state.ready is False and state.category == "unavailable"


# -- key resolution --------------------------------------------------------------

CONFIG = """\
port: 8318
remote-management:
  secret-key: "not-the-client-key"
access:
  api-keys:
    - "cfg-key-first"   # comment
    - "cfg-key-second"
routing:
  strategy: "round-robin"
"""


def test_key_resolution_order_env_stored_config_session(monkeypatch, tmp_path):
    config = tmp_path / "CLIProxyAPI-Claude" / "config.yaml"
    config.parent.mkdir()
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(ai, "claude_config_path", lambda: config)
    ai.set_claude_session_key("session-key")
    monkeypatch.setenv(ai.CLAUDE_KEY_ENV, "env-key")
    monkeypatch.setattr(ai, "_read_claude_stored_key", lambda: "stored-key")
    assert ai.resolve_claude_key() == ("env-key", "env")
    monkeypatch.delenv(ai.CLAUDE_KEY_ENV)
    assert ai.resolve_claude_key() == ("stored-key", "stored")
    monkeypatch.setattr(ai, "_read_claude_stored_key", lambda: "")
    assert ai.resolve_claude_key() == ("cfg-key-first", "config")
    assert ai.claude_key_source_label("config") == "Found in CLIProxyAPI-Claude config"
    config.unlink()
    assert ai.resolve_claude_key() == ("session-key", "session")
    ai.clear_claude_session_key()
    assert ai.resolve_claude_key() == ("", "missing")


def test_readiness_shows_the_source_never_the_key(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(ai, "claude_config_path", lambda: config)
    state = ai.readiness(ai.PROVIDER_CLAUDE)
    assert state.ready and state.message == "Found in CLIProxyAPI-Claude config"
    assert "cfg-key-first" not in repr(state)
    assert "cfg-key-first" not in ai.redact("echo cfg-key-first")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CONFIG, "cfg-key-first"),
        ("api-keys:\n  - plain-top\n", "plain-top"),
        ("access:\n  api-keys: ['inline-a', 'inline-b']\n", "inline-a"),
        ("api-keys: [\"top-inline\"]\n", "top-inline"),
        ("api-keys:\n  - top\naccess:\n  api-keys:\n    - nested\n", "nested"),
        ("access:\n  api-keys:\n", ""),
        ("port: 1\n", ""),
        ("", ""),
    ],
)
def test_minimal_config_parser(text, expected):
    assert ai.parse_proxy_config_key(text) == expected


def test_prefs_never_store_a_claude_key():
    clean = ai.scrub_pref_values({"ai_claude_proxy_key": "x", "ai_claude_model_id": "m"})
    assert clean == {"ai_claude_model_id": "m"}


# -- planning speed --------------------------------------------------------------


def test_proposal_groups_run_with_the_provider_parallelism(monkeypatch):
    seen: list[int] = []
    real = grok.ThreadPoolExecutor

    class Recording(real):
        def __init__(self, max_workers=None, **kwargs):
            seen.append(max_workers)
            super().__init__(max_workers=max_workers, **kwargs)

    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def fake_chunk(chunk, **_kwargs):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        return ({int(r["playerid"]): {"overallrating": 80} for r in chunk}, {}, {})

    monkeypatch.setattr(grok, "ThreadPoolExecutor", Recording)
    monkeypatch.setattr(grok, "_propose_squad_chunk", fake_chunk)
    players = [{"playerid": i, "name": f"P{i}"} for i in range(1, 41)]  # 8 groups of 5
    for provider, expected in ((ai.PROVIDER_CODEX, 5), (ai.PROVIDER_CLAUDE, 6)):
        ai.apply_settings(provider=provider)
        seen.clear()
        patches, _r, _h = grok.propose_squad_players(
            players=players, selection_revision="rev", request="lift", recipe=SimpleNamespace(),
        )
        assert len(patches) == 40
        assert seen == [expected]
    assert grok.PROPOSE_CHUNK == 5


def test_planner_hint_lookups_are_deduped_and_concurrent(monkeypatch):
    from companion.ui.surfaces import planner

    rows = [{"playerid": i, "name": "Same" if i < 4 else f"P{i}", "position": "ST",
             "target_ovr": 85} for i in range(1, 9)]
    calls: list[tuple] = []
    threads: set[str] = set()
    lock = threading.Lock()

    def hint(name, position, ovr, catalog, **_kw):
        with lock:
            calls.append((name, position, ovr))
            threads.add(threading.current_thread().name)
        time.sleep(0.02)
        return f"hint:{name}"

    captured: dict = {}

    def propose(**kwargs):
        captured["players"] = kwargs["players"]
        return {}, {}, {}

    monkeypatch.setattr(planner, "propose_payload", lambda plan, session: rows)
    monkeypatch.setattr(planner, "relative_only", lambda recipe: False)
    monkeypatch.setattr(planner, "jersey_only", lambda recipe: False)
    monkeypatch.setattr(planner, "stage_per_player_plan", lambda *a, **k: "staged")
    monkeypatch.setattr(grok, "library_hint_for", hint)
    monkeypatch.setattr(grok, "propose_squad_players", propose)
    done: list = []
    planner.session_propose_worker(
        SimpleNamespace(cancelled=False),
        plan=SimpleNamespace(selection_revision="r"),
        session=SimpleNamespace(recipe=SimpleNamespace(prompt="p", excluded_families=(), named_jerseys=()), warnings=(), targets=()),
        catalog=object(),
        on_success=done.append,
        on_error=lambda exc: (_ for _ in ()).throw(exc),
    )
    assert done == ["staged"]
    assert len(calls) == 6  # "Same" looked up once for three players
    assert all(name.startswith("plan-hints") for name in threads)
    assert [p["library_hint"] for p in captured["players"]][:3] == ["hint:Same"] * 3
    assert [p["playerid"] for p in captured["players"]] == list(range(1, 9))


# -- Codex transport retry -----------------------------------------------------------


class _Resp:
    def read(self):
        return b'{"output_text":"{\\"ok\\":true}"}'

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None


def _codex(monkeypatch, *outcomes):
    calls: list[int] = []
    queue = list(outcomes)

    def urlopen(req, timeout=0.0):
        calls.append(1)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(grok.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(grok, "_session", lambda: {"token": "t", "account_id": "a"})
    monkeypatch.setattr(grok, "_codex_backoff", lambda: None)
    return calls


def _http(code: int):
    import io

    return urllib.error.HTTPError(grok.CODEX_RESPONSES_URL, code, "x", {}, io.BytesIO(b"err"))


@pytest.mark.parametrize("first", [
    lambda: _http(502),
    lambda: urllib.error.URLError(ConnectionResetError("reset by peer")),
    lambda: urllib.error.URLError(TimeoutError("timed out")),
    lambda: ConnectionResetError("reset"),
    lambda: TimeoutError("read timed out"),
])
def test_codex_retries_once_on_transient_errors(monkeypatch, first):
    calls = _codex(monkeypatch, first(), _Resp())
    body = grok._codex_request([{"role": "user", "content": "x"}], model="m")
    assert grok._response_text(body)
    assert len(calls) == 2


@pytest.mark.parametrize("code", [401, 429, 400])
def test_codex_does_not_retry_auth_quota_or_client_errors(monkeypatch, code):
    calls = _codex(monkeypatch, _http(code), _Resp())
    with pytest.raises(RuntimeError):
        grok._codex_request([{"role": "user", "content": "x"}], model="m")
    assert len(calls) == 1


def test_codex_gives_up_after_one_retry(monkeypatch):
    calls = _codex(monkeypatch, _http(503), _http(503), _Resp())
    with pytest.raises(RuntimeError, match="503"):
        grok._codex_request([{"role": "user", "content": "x"}], model="m")
    assert len(calls) == 2


def test_codex_refused_connection_is_not_retried(monkeypatch):
    calls = _codex(monkeypatch, urllib.error.URLError(ConnectionRefusedError("refused")), _Resp())
    with pytest.raises(RuntimeError, match="Cannot reach Codex"):
        grok._codex_request([{"role": "user", "content": "x"}], model="m")
    assert len(calls) == 1


# -- opt-in live test --------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("FC26_LIVE_CLAUDE") != "1",
                    reason="set FC26_LIVE_CLAUDE=1 to call the local Claude proxy")
def test_live_claude_proxy_round_trip(monkeypatch):
    # Use the real key sources (env, Credential Manager, CLIProxyAPI-Claude config).
    monkeypatch.undo()
    _defaults()
    ai.apply_settings(provider=ai.PROVIDER_CLAUDE)
    if not ai.readiness().ready:
        pytest.skip("No Claude proxy key found")
    result = ai.test_connection()
    assert result.ok, result.message
    body = ai.dispatch_request(
        [
            {"role": "system", "content": "You answer tiny FC 26 questions. Return JSON only."},
            {"role": "user", "content": 'Return {"club": "<a famous Spanish club>"}'},
        ],
        model=grok.DEFAULT_MODEL,
    )
    parsed = grok._content(body)
    assert isinstance(parsed.get("club"), str) and parsed["club"]
