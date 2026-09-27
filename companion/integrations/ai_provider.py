"""App-wide AI provider: Codex account or OpenAI API key, one model id.

The key never lives in preferences, logs, or source. Resolution order is the
environment variable, then Windows Credential Manager, then a session value
that disappears when Companion closes.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping

PROVIDER_CODEX = "codex_account"
PROVIDER_API = "openai_api"
PROVIDERS = (PROVIDER_CODEX, PROVIDER_API)
PROVIDER_LABELS = {
    PROVIDER_CODEX: "Codex Account",
    PROVIDER_API: "OpenAI API Key",
}
PREF_PROVIDER = "ai_provider"
PREF_MODEL = "ai_model_id"
_CRED_TARGET = "FC26Companion/OpenAI"
_SESSION_KEY = ""
_PROVIDER = PROVIDER_API
_MODEL = ""


class AiError(RuntimeError):
    """A provider failure with a category the UI can show without a generic 'Codex failed'."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider_id: str
    label: str
    model_id: str
    ready: bool
    message: str
    category: str = ""


@dataclass(frozen=True, slots=True)
class ConnectionTest:
    provider_id: str
    model_id: str
    ok: bool
    latency_ms: int
    text: str
    category: str = ""
    message: str = ""


def provider_id() -> str:
    return _PROVIDER if _PROVIDER in PROVIDERS else PROVIDER_CODEX


def provider_label(provider: str | None = None) -> str:
    return PROVIDER_LABELS.get(provider or provider_id(), PROVIDER_LABELS[PROVIDER_CODEX])


def model_id() -> str:
    from .grok import DEFAULT_MODEL

    text = (_MODEL or "").strip()
    return text or DEFAULT_MODEL


def apply_settings(*, provider: str | None = None, model: str | None = None) -> None:
    global _PROVIDER, _MODEL
    if provider is not None:
        chosen = str(provider or "").strip()
        _PROVIDER = chosen if chosen in PROVIDERS else PROVIDER_CODEX
    if model is not None:
        _MODEL = str(model or "").strip()


def load_from_prefs(values: Mapping[str, Any] | None) -> None:
    raw = dict(values or {})
    apply_settings(
        provider=str(raw.get(PREF_PROVIDER) or PROVIDER_API),
        model=str(raw.get(PREF_MODEL) or ""),
    )


def effective_model(requested: str) -> str:
    """Use the app-wide model unless a caller passed an explicit non-default id."""
    from .grok import DEFAULT_MODEL

    text = str(requested or "").strip()
    if text and text != DEFAULT_MODEL:
        return text
    return model_id()


def scrub_pref_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop anything that looks like an API secret before it can be saved."""
    clean: dict[str, Any] = {}
    for key, value in dict(values or {}).items():
        name = str(key).lower()
        if "api_key" in name or name in {"openai_key", "openai_api_key"}:
            continue
        clean[str(key)] = value
    return clean


def set_session_key(secret: str) -> None:
    global _SESSION_KEY
    _SESSION_KEY = str(secret or "").strip()


def clear_session_key() -> None:
    global _SESSION_KEY
    _SESSION_KEY = ""


def resolve_api_key() -> tuple[str, str]:
    """Return ``(secret, source)``. Source is env, stored, session, or missing."""
    env = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    if env:
        return env, "env"
    stored = _read_stored_key()
    if stored:
        return stored, "stored"
    if _SESSION_KEY:
        return _SESSION_KEY, "session"
    return "", "missing"


def key_source_label(source: str) -> str:
    return {
        "env": "OPENAI_API_KEY is set",
        "stored": "Saved in Windows Credential Manager",
        "session": "Entered for this session only",
        "missing": "API key required",
    }.get(source, "API key required")


def store_api_key(secret: str) -> None:
    text = str(secret or "").strip()
    if not text:
        raise AiError("Paste an API key first.", category="authentication")
    _write_stored_key(text)


def clear_stored_key() -> None:
    _delete_stored_key()


def redact(text: str) -> str:
    secret, _source = resolve_api_key()
    cleaned = str(text or "")
    if secret and secret in cleaned:
        cleaned = cleaned.replace(secret, "[redacted]")
    if _SESSION_KEY and _SESSION_KEY in cleaned:
        cleaned = cleaned.replace(_SESSION_KEY, "[redacted]")
    return cleaned


def readiness() -> ProviderStatus:
    chosen = provider_id()
    model = model_id()
    label = provider_label(chosen)
    if chosen == PROVIDER_API:
        _key, source = resolve_api_key()
        if source == "missing":
            return ProviderStatus(
                chosen, label, model, False, "API key required. Open Settings to add one.",
                "authentication",
            )
        return ProviderStatus(chosen, label, model, True, key_source_label(source))
    from .grok import status as codex_status

    state = codex_status()
    if state.connected:
        return ProviderStatus(chosen, label, model, True, "Codex account ready")
    hint = str(state.login_hint or "").strip()
    message = "Codex sign-in required"
    if hint:
        message = f"{message}. {hint}"
    return ProviderStatus(chosen, label, model, False, message, "authentication")


def block_reason() -> str:
    state = readiness()
    if state.ready:
        return ""
    return state.message


def dispatch_request(messages: list[dict[str, str]], *, model: str) -> Mapping[str, Any]:
    """Normalized Responses result. Callers still validate the JSON themselves."""
    chosen = effective_model(model)
    if provider_id() == PROVIDER_API:
        text = openai_text(messages, model=chosen, json_object=True)
        return {"output_text": text}
    from .grok import _codex_request

    return _codex_request(messages, model=chosen)


def test_connection() -> ConnectionTest:
    """Ask the selected provider and model to return exactly FC26_OK."""
    chosen = provider_id()
    model = model_id()
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": "Reply with plain text only."},
        {"role": "user", "content": "Return exactly FC26_OK"},
    ]
    try:
        if chosen == PROVIDER_API:
            text = openai_text(messages, model=model, json_object=False, reasoning=False)
        else:
            from .grok import _codex_request, _response_text

            body = _codex_request(messages, model=model, json_object=False, reasoning=False)
            text = _response_text(body).strip()
    except AiError as exc:
        return ConnectionTest(
            chosen, model, False, _elapsed(started), "", exc.category, redact(str(exc)),
        )
    except Exception as exc:  # noqa: BLE001
        return ConnectionTest(
            chosen, model, False, _elapsed(started), "", "network", redact(str(exc)),
        )
    shown = redact(text).strip()
    ok = shown == "FC26_OK"
    message = "Connected" if ok else f"Model replied {shown[:80]!r}, not FC26_OK"
    return ConnectionTest(
        chosen,
        model,
        ok,
        _elapsed(started),
        shown[:80],
        "" if ok else "malformed",
        message,
    )


def openai_text(
    messages: list[dict[str, str]],
    *,
    model: str,
    json_object: bool = True,
    reasoning: bool = True,
) -> str:
    secret, source = resolve_api_key()
    if source == "missing" or not secret:
        raise AiError("API key required. Open Settings to add one.", category="authentication")
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            OpenAI,
            RateLimitError,
        )
    except ImportError as exc:
        raise AiError(
            "The OpenAI SDK is not installed in this Companion build.",
            category="unavailable",
        ) from exc
    from .grok import _response_text, _responses_body

    payload = _responses_body(messages, model=model, json_object=json_object, reasoning=reasoning)
    kwargs: dict[str, Any] = {
        "model": payload["model"],
        "input": payload["input"],
        "store": False,
    }
    instructions = str(payload.get("instructions") or "").strip()
    if instructions:
        kwargs["instructions"] = instructions
    if json_object:
        kwargs["text"] = payload["text"]
    if reasoning and payload.get("reasoning"):
        kwargs["reasoning"] = payload["reasoning"]
    client = OpenAI(api_key=secret, timeout=180.0)
    try:
        response = client.responses.create(**kwargs)
    except AuthenticationError as exc:
        raise AiError("API key was rejected.", category="authentication") from exc
    except RateLimitError as exc:
        raise AiError("OpenAI rate limit or quota was reached.", category="rate_limit") from exc
    except APITimeoutError as exc:
        raise AiError("OpenAI request timed out.", category="timeout") from exc
    except APIConnectionError as exc:
        raise AiError("Cannot reach OpenAI.", category="network") from exc
    except APIStatusError as exc:
        raise _status_error(exc, model) from exc
    except Exception as exc:  # noqa: BLE001
        raise AiError(redact(str(exc)) or "OpenAI request failed.", category="network") from exc
    returned = str(getattr(response, "model", "") or "")
    if returned and returned != model and not returned.startswith(model):
        raise AiError(
            f"OpenAI answered with model {returned}, not {model}.",
            category="model",
        )
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    try:
        dumped = response.model_dump()
    except Exception:
        dumped = {}
    if isinstance(dumped, Mapping):
        return _response_text(dumped).strip()
    raise AiError("OpenAI response did not contain text.", category="malformed")


def _status_error(exc: Exception, model: str) -> AiError:
    code = int(getattr(exc, "status_code", 0) or 0)
    detail = redact(str(exc))[:240]
    if code in {401, 403}:
        return AiError("API key was rejected.", category="authentication")
    if code == 429:
        return AiError("OpenAI rate limit or quota was reached.", category="rate_limit")
    if code == 404 or "model" in detail.lower():
        return AiError(
            f"Model {model} is not available for this API key.",
            category="model",
        )
    return AiError(f"OpenAI request failed ({code}).", category="network")


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _write_stored_key(secret: str) -> None:
    if sys.platform != "win32":
        raise AiError(
            "Secure key storage needs Windows. Set OPENAI_API_KEY or use a session key.",
            category="unavailable",
        )
    import ctypes

    blob = secret.encode("utf-8")
    buffer = ctypes.create_string_buffer(blob)
    cred = _CREDENTIALW()
    cred.Type = 1
    cred.TargetName = _CRED_TARGET
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    cred.Persist = 2
    cred.UserName = "openai"
    cred.Comment = "FC26 Companion OpenAI API key"
    if not _advapi32().CredWriteW(ctypes.byref(cred), 0):
        raise AiError("Windows could not save the API key.", category="unavailable")


def _read_stored_key() -> str:
    if sys.platform != "win32":
        return ""
    import ctypes

    try:
        adv = _advapi32()
        slot = ctypes.POINTER(_CREDENTIALW)()
        ok = adv.CredReadW(_CRED_TARGET, 1, 0, ctypes.byref(slot))
        if not ok or not slot:
            return ""
        try:
            size = int(slot.contents.CredentialBlobSize or 0)
            if size <= 0 or not slot.contents.CredentialBlob:
                return ""
            raw = ctypes.string_at(slot.contents.CredentialBlob, size)
            return raw.decode("utf-8").strip()
        finally:
            adv.CredFree(slot)
    except Exception:
        return ""


def _delete_stored_key() -> None:
    if sys.platform != "win32":
        return
    try:
        _advapi32().CredDeleteW(_CRED_TARGET, 1, 0)
    except Exception:
        return


def _advapi32() -> Any:
    import ctypes

    lib = ctypes.windll.advapi32
    lib.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), ctypes.c_uint32]
    lib.CredWriteW.restype = ctypes.c_int
    lib.CredReadW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    lib.CredReadW.restype = ctypes.c_int
    lib.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    lib.CredDeleteW.restype = ctypes.c_int
    lib.CredFree.argtypes = [ctypes.c_void_p]
    return lib


def _credential_type() -> Any:
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    return CREDENTIALW


_CREDENTIALW = _credential_type()
