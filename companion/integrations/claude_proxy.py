"""Claude (proxy) transport: the official ``anthropic`` SDK against a local proxy.

The local CLIProxyAPI for Claude speaks the Anthropic Messages API. This module
converts the app's chat-style request (one system prompt plus user/assistant
messages) into Messages API parameters, streams the answer, checks
``stop_reason`` and maps SDK errors onto ``AiError`` categories.

The key is passed in by ``ai_provider`` and is never logged, stored, or put into
an error message. Every message that could echo server text is redacted.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://127.0.0.1:8318"
DEFAULT_MODEL = "claude-sonnet-5-5"
MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
DEFAULT_MAX_TOKENS = 16000
RETRY_MAX_TOKENS = 32000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Server-side refusal fallbacks ("default" routing) exist for these models only.
_FALLBACK_MODELS = frozenset(
    {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
)
_JSON_HINT = "Respond with JSON only."
_EFFORTS = ("low", "medium", "high")

_lock = threading.Lock()
_clients: dict[tuple[str, str], Any] = {}
# Models for which the proxy rejected ``betas``/``fallbacks`` this session.
_no_fallbacks: set[tuple[str, str]] = set()


def _sdk() -> Any:
    """The ``anthropic`` module. A seam for tests and a clear error when absent."""
    from .ai_provider import AiError

    try:
        import anthropic
    except ImportError:
        raise AiError(
            "The Anthropic SDK is not installed in this Companion build.",
            category="unavailable",
        ) from None
    return anthropic


def _client(base_url: str, api_key: str) -> Any:
    """One SDK client per (proxy, key). The client is thread-safe and pools connections."""
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    slot = (base_url, digest)
    with _lock:
        client = _clients.get(slot)
        if client is None:
            client = _sdk().Anthropic(
                base_url=base_url, api_key=api_key, timeout=180.0, max_retries=2,
            )
            _clients[slot] = client
        return client


def reset_session() -> None:
    """Forget cached clients and fallback memory (key or URL changed)."""
    with _lock:
        _clients.clear()
        _no_fallbacks.clear()


def _with_json_word(text: str) -> str:
    if "json" in (text or "").lower():
        return text
    return f"{(text or '').rstrip()}\n\n{_JSON_HINT}".strip()


def build_params(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    json_object: bool = True,
    effort: str | None = None,
    schema: Mapping[str, Any] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    fallbacks: bool = True,
) -> dict[str, Any]:
    """Messages API keyword arguments for ``client.beta.messages.stream``.

    System text becomes one cached system block (it is the stable part of every
    planner request). Consecutive same-role turns merge; the conversation always
    starts and ends with a user turn because these models reject prefill.
    """
    system_parts: list[str] = []
    convo: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role in {"system", "developer"}:
            if content.strip():
                system_parts.append(content)
            continue
        role = role if role in {"user", "assistant"} else "user"
        if convo and convo[-1]["role"] == role:
            convo[-1]["content"] = f"{convo[-1]['content']}\n\n{content}"
        else:
            convo.append({"role": role, "content": content})
    system = "\n\n".join(system_parts).strip()
    if not convo:
        convo = [{"role": "user", "content": _JSON_HINT if json_object else "Respond with plain text."}]
    if convo[0]["role"] != "user":
        convo.insert(0, {"role": "user", "content": "Continue the conversation below."})
    if convo[-1]["role"] != "user":
        convo.append({"role": "user", "content": "Continue."})
    if json_object and not schema:
        if system:
            system = _with_json_word(system)
        else:
            convo[-1]["content"] = _with_json_word(convo[-1]["content"])
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "messages": convo,
    }
    if system:
        params["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    output_config: dict[str, Any] = {}
    if effort in _EFFORTS:
        output_config["effort"] = effort
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": dict(schema)}
    if output_config:
        params["output_config"] = output_config
    if fallbacks and model in _FALLBACK_MODELS:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def _stream(client: Any, params: Mapping[str, Any]) -> Any:
    with client.beta.messages.stream(**params) as stream:
        return stream.get_final_message()


def _text(message: Any) -> str:
    parts = [
        str(getattr(block, "text", "") or "")
        for block in (getattr(message, "content", None) or ())
        if getattr(block, "type", "") == "text"
    ]
    return "".join(parts)


def complete(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    json_object: bool = True,
    effort: str | None = None,
    schema: Mapping[str, Any] | None = None,
    redact: Callable[[str], str] | None = None,
) -> str:
    """Send one request and return the answer text. Raises ``AiError`` only."""
    from .ai_provider import AiError

    if not api_key:
        raise AiError(
            "Claude proxy key required. Open Settings to add it.", category="authentication",
        )
    scrub = _scrubber(api_key, redact)
    url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    anthropic = _sdk()
    client = _client(url, api_key)
    slot = (url, model)
    use_fallbacks = slot not in _no_fallbacks
    max_tokens = DEFAULT_MAX_TOKENS
    message: Any = None
    dropped_fallbacks = False
    for _attempt in range(3):
        params = build_params(
            messages, model=model, json_object=json_object, effort=effort, schema=schema,
            max_tokens=max_tokens, fallbacks=use_fallbacks,
        )
        try:
            message = _stream(client, params)
        except anthropic.BadRequestError as exc:
            if "fallbacks" in params and not dropped_fallbacks:
                # A proxy that does not forward the fallback beta: retry once
                # without it; remembered for the session only if that works.
                use_fallbacks = False
                dropped_fallbacks = True
                continue
            raise _map_error(anthropic, exc, model=model, base_url=url, scrub=scrub) from None
        except Exception as exc:  # noqa: BLE001 - mapped below, never echoed raw
            raise _map_error(anthropic, exc, model=model, base_url=url, scrub=scrub) from None
        if dropped_fallbacks:
            with _lock:
                _no_fallbacks.add(slot)
        reason = str(getattr(message, "stop_reason", "") or "")
        if reason == "max_tokens" and max_tokens < RETRY_MAX_TOKENS:
            max_tokens = RETRY_MAX_TOKENS
            continue
        break
    reason = str(getattr(message, "stop_reason", "") or "")
    if reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = str(getattr(details, "category", "") or "").strip()
        suffix = f" (category: {category})" if category else ""
        raise AiError(
            f"Claude declined this request{suffix}. Rephrase the request or switch "
            "the provider. Nothing was written to the career.",
            category="refusal",
        )
    if reason == "max_tokens":
        raise AiError(
            f"Claude's answer was cut off at {max_tokens} tokens. Select fewer "
            "players or ask for a smaller change.",
            category="malformed",
        )
    text = _text(message).strip()
    if not text:
        raise AiError("Claude response did not contain text.", category="malformed")
    return text


def _scrubber(api_key: str, redact: Callable[[str], str] | None) -> Callable[[str], str]:
    def scrub(text: str) -> str:
        cleaned = str(text or "")
        if api_key and api_key in cleaned:
            cleaned = cleaned.replace(api_key, "[redacted]")
        if redact is not None:
            cleaned = redact(cleaned)
        return cleaned

    return scrub


def _detail(exc: Exception, scrub: Callable[[str], str]) -> str:
    body = getattr(exc, "body", None)
    text = ""
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            text = str(error.get("message") or "")
        else:
            text = str(body.get("message") or "")
    if not text:
        text = str(getattr(exc, "message", "") or exc)
    return scrub(text).strip()[:200]


def _map_error(
    anthropic: Any, exc: Exception, *, model: str, base_url: str, scrub: Callable[[str], str],
) -> Exception:
    """Most-specific first. Messages never contain the key."""
    from .ai_provider import AiError

    if isinstance(exc, AiError):
        return exc
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return AiError(
            "The Claude proxy rejected the key. Check the key in Settings "
            "(or the CLIProxyAPI-Claude config).",
            category="authentication",
        )
    if isinstance(exc, anthropic.NotFoundError):
        return AiError(
            f"Model {model} is not available on the Claude proxy.", category="model",
        )
    if isinstance(exc, anthropic.RateLimitError):
        return AiError(
            "Claude proxy rate limit or quota was reached. Wait a moment and try again.",
            category="rate_limit",
        )
    if isinstance(exc, anthropic.APIStatusError):
        code = int(getattr(exc, "status_code", 0) or 0)
        detail = _detail(exc, scrub)
        suffix = f": {detail}" if detail else ""
        return AiError(f"Claude proxy request failed ({code}){suffix}", category="network")
    if isinstance(exc, anthropic.APITimeoutError):
        return AiError("Claude proxy request timed out.", category="timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        try:
            port = urlparse(base_url).port or 8318
        except ValueError:
            port = 8318
        return AiError(
            f"Cannot reach the Claude proxy at {base_url}. Start the Claude proxy "
            f"(port {port}), then try again.",
            category="network",
        )
    return AiError(scrub(str(exc))[:200] or "Claude request failed.", category="network")
