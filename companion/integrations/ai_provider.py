"""App-wide AI provider: Codex account, OpenAI API key, or the local Claude proxy.

Codex and the OpenAI API key share one model id; Claude (proxy) has its own.
Each provider has its own reasoning effort ("Speed").

Keys never live in preferences, logs, or source. OpenAI: the environment
variable, then Windows Credential Manager, then a session value. Claude proxy:
``FC26_CLAUDE_PROXY_KEY``, then Credential Manager, then the local
CLIProxyAPI-Claude ``config.yaml``, then a session value.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PROVIDER_CODEX = "codex_account"
PROVIDER_API = "openai_api"
PROVIDER_CLAUDE = "claude_proxy"
PROVIDERS = (PROVIDER_CODEX, PROVIDER_API, PROVIDER_CLAUDE)
PROVIDER_LABELS = {
    PROVIDER_CODEX: "Codex Account",
    PROVIDER_API: "OpenAI API Key",
    PROVIDER_CLAUDE: "Claude (proxy)",
}
PREF_PROVIDER = "ai_provider"
PREF_MODEL = "ai_model_id"
PREF_CLAUDE_MODEL = "ai_claude_model_id"
PREF_CLAUDE_BASE_URL = "ai_claude_base_url"
PREF_PARALLEL = "ai_parallel_groups"
PREF_EFFORT = {
    PROVIDER_CODEX: "ai_effort_codex",
    PROVIDER_API: "ai_effort_openai",
    PROVIDER_CLAUDE: "ai_effort_claude",
}
EFFORTS = ("low", "medium", "high")
EFFORT_LABELS = {"low": "Fast", "medium": "Balanced", "high": "Thorough"}
DEFAULT_EFFORT = {
    PROVIDER_CODEX: "medium",
    PROVIDER_API: "medium",
    PROVIDER_CLAUDE: "low",
}
# Concurrent proposal groups. The proxy rotates accounts, so Claude gets one more.
DEFAULT_PARALLEL = {PROVIDER_CODEX: 5, PROVIDER_API: 5, PROVIDER_CLAUDE: 6}
MAX_PARALLEL = 8
CLAUDE_KEY_ENV = "FC26_CLAUDE_PROXY_KEY"
_CRED_TARGET = "FC26Companion/OpenAI"
_CLAUDE_CRED_TARGET = "FC26Companion/ClaudeProxy"
_SESSION_KEY = ""
_CLAUDE_SESSION_KEY = ""
_PROVIDER = PROVIDER_API
_MODEL = ""
_CLAUDE_MODEL = ""
_CLAUDE_BASE_URL = ""
_EFFORT: dict[str, str] = {}
_PARALLEL = 0


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


_LISTENERS: list[Any] = []


def subscribe(callback: Any) -> Any:
    """Call ``callback()`` after provider, model, or key changes. Returns an unsubscribe."""
    _LISTENERS.append(callback)

    def unsubscribe() -> None:
        try:
            _LISTENERS.remove(callback)
        except ValueError:
            pass

    return unsubscribe


def notify_changed() -> None:
    for callback in list(_LISTENERS):
        try:
            callback()
        except Exception:  # noqa: BLE001 - a dead widget must not block the others
            try:
                _LISTENERS.remove(callback)
            except ValueError:
                pass


def provider_id() -> str:
    return _PROVIDER if _PROVIDER in PROVIDERS else PROVIDER_API


def provider_label(provider: str | None = None) -> str:
    return PROVIDER_LABELS.get(provider or provider_id(), PROVIDER_LABELS[PROVIDER_API])


def model_id(provider: str | None = None) -> str:
    """Model of ``provider`` (default: the selected one)."""
    chosen = provider if provider in PROVIDERS else provider_id()
    if chosen == PROVIDER_CLAUDE:
        return claude_model_id()
    return shared_model_id()


def shared_model_id() -> str:
    """The one model id Codex Account and the OpenAI API key share."""
    from .grok import DEFAULT_MODEL

    text = (_MODEL or "").strip()
    return text or DEFAULT_MODEL


def claude_model_id() -> str:
    from .claude_proxy import DEFAULT_MODEL as CLAUDE_DEFAULT

    text = (_CLAUDE_MODEL or "").strip()
    return text or CLAUDE_DEFAULT


def claude_base_url() -> str:
    from .claude_proxy import DEFAULT_BASE_URL

    text = (_CLAUDE_BASE_URL or "").strip().rstrip("/")
    return text or DEFAULT_BASE_URL


def reasoning_effort(provider: str | None = None) -> str:
    """low/medium/high for ``provider`` (default: the selected one)."""
    chosen = provider if provider in PROVIDERS else provider_id()
    value = _EFFORT.get(chosen, "")
    return value if value in EFFORTS else DEFAULT_EFFORT[chosen]


def parallel_groups(provider: str | None = None) -> int:
    """How many proposal groups run at once for ``provider``."""
    chosen = provider if provider in PROVIDERS else provider_id()
    if _PARALLEL > 0:
        return max(1, min(MAX_PARALLEL, _PARALLEL))
    return DEFAULT_PARALLEL[chosen]


def apply_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    claude_model: str | None = None,
    claude_base_url: str | None = None,
    efforts: Mapping[str, Any] | None = None,
    parallel: Any = None,
) -> None:
    """Update in-memory settings. ``model`` is the Codex/OpenAI shared model."""
    global _PROVIDER, _MODEL, _CLAUDE_MODEL, _CLAUDE_BASE_URL, _PARALLEL
    before = (_PROVIDER, _MODEL, _CLAUDE_MODEL, _CLAUDE_BASE_URL, dict(_EFFORT), _PARALLEL)
    if provider is not None:
        chosen = str(provider or "").strip()
        _PROVIDER = chosen if chosen in PROVIDERS else PROVIDER_API
    if model is not None:
        _MODEL = str(model or "").strip()
    if claude_model is not None:
        _CLAUDE_MODEL = str(claude_model or "").strip()
    if claude_base_url is not None:
        _CLAUDE_BASE_URL = str(claude_base_url or "").strip()
    if efforts is not None:
        for pid, value in dict(efforts).items():
            if pid not in PROVIDERS:
                continue
            text = str(value or "").strip().lower()
            if text in EFFORTS:
                _EFFORT[pid] = text
            else:
                _EFFORT.pop(pid, None)
    if parallel is not None:
        try:
            _PARALLEL = max(0, min(MAX_PARALLEL, int(parallel)))
        except (TypeError, ValueError):
            _PARALLEL = 0
    after = (_PROVIDER, _MODEL, _CLAUDE_MODEL, _CLAUDE_BASE_URL, dict(_EFFORT), _PARALLEL)
    if after != before:
        notify_changed()


def load_from_prefs(values: Mapping[str, Any] | None) -> None:
    raw = dict(values or {})
    apply_settings(
        provider=str(raw.get(PREF_PROVIDER) or PROVIDER_API),
        model=str(raw.get(PREF_MODEL) or ""),
        claude_model=str(raw.get(PREF_CLAUDE_MODEL) or ""),
        claude_base_url=str(raw.get(PREF_CLAUDE_BASE_URL) or ""),
        efforts={pid: raw.get(name) or "" for pid, name in PREF_EFFORT.items()},
        parallel=raw.get(PREF_PARALLEL) or 0,
    )


def effective_model(requested: str) -> str:
    """Use the app-wide model unless a caller passed an explicit non-default id.

    An explicit OpenAI id is never sent to Claude (and vice versa).
    """
    from .grok import DEFAULT_MODEL

    text = str(requested or "").strip()
    is_claude = provider_id() == PROVIDER_CLAUDE
    if text and text != DEFAULT_MODEL and text.startswith("claude-") == is_claude:
        return text
    return model_id()


def scrub_pref_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop anything that looks like an API secret before it can be saved."""
    clean: dict[str, Any] = {}
    for key, value in dict(values or {}).items():
        name = str(key).lower()
        if "api_key" in name or "proxy_key" in name or name in {"openai_key", "openai_api_key"}:
            continue
        clean[str(key)] = value
    return clean


def set_session_key(secret: str) -> None:
    global _SESSION_KEY
    _SESSION_KEY = str(secret or "").strip()
    notify_changed()


def clear_session_key() -> None:
    global _SESSION_KEY
    _SESSION_KEY = ""
    notify_changed()


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
    notify_changed()


def clear_stored_key() -> None:
    _delete_stored_key()
    notify_changed()


# -- Claude (proxy) key ---------------------------------------------------------


def claude_config_path() -> Path:
    """CLIProxyAPI-Claude's config, the last automatic place a key can come from."""
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "CLIProxyAPI-Claude" / "config.yaml"


_CONFIG_CACHE: dict[str, Any] = {"stamp": None, "key": ""}


def _unquote(text: str) -> str:
    value = text.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.strip()


def parse_proxy_config_key(text: str) -> str:
    """First ``access: api-keys:`` item (or a top-level ``api-keys:``). Minimal YAML."""
    found: dict[str, str] = {}
    section = ""
    pending = ""  # "access" or "top" while reading an api-keys list
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            pending = ""
            section = stripped.split(":", 1)[0].strip()
            if section == "api-keys" and ":" in stripped:
                rest = stripped.split(":", 1)[1].strip()
                if rest.startswith("[") and rest.endswith("]"):
                    first = rest[1:-1].split(",")[0]
                    if _unquote(first):
                        found.setdefault("top", _unquote(first))
                else:
                    pending = "top"
            continue
        if pending and stripped.startswith("- "):
            value = _unquote(stripped[2:])
            if value:
                found.setdefault(pending, value)
                pending = ""
            continue
        if section == "access" and stripped.split(":", 1)[0].strip() == "api-keys":
            rest = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if rest.startswith("[") and rest.endswith("]"):
                first = rest[1:-1].split(",")[0]
                if _unquote(first):
                    found.setdefault("access", _unquote(first))
            else:
                pending = "access"
            continue
        if pending and not stripped.startswith("- "):
            pending = ""
    return found.get("access") or found.get("top") or ""


def _read_proxy_config_key() -> str:
    path = claude_config_path()
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ""
    if _CONFIG_CACHE["stamp"] == stamp:
        return str(_CONFIG_CACHE["key"] or "")
    try:
        key = parse_proxy_config_key(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        key = ""
    _CONFIG_CACHE["stamp"], _CONFIG_CACHE["key"] = stamp, key
    return key


def resolve_claude_key() -> tuple[str, str]:
    """Return ``(secret, source)``: env, stored, config, session, or missing."""
    env = str(os.environ.get(CLAUDE_KEY_ENV) or "").strip()
    if env:
        return env, "env"
    stored = _read_claude_stored_key()
    if stored:
        return stored, "stored"
    found = _read_proxy_config_key()
    if found:
        return found, "config"
    if _CLAUDE_SESSION_KEY:
        return _CLAUDE_SESSION_KEY, "session"
    return "", "missing"


def claude_key_source_label(source: str) -> str:
    return {
        "env": f"{CLAUDE_KEY_ENV} is set",
        "stored": "Saved in Windows Credential Manager",
        "config": "Found in CLIProxyAPI-Claude config",
        "session": "Entered for this session only",
        "missing": "Claude proxy key required",
    }.get(source, "Claude proxy key required")


def set_claude_session_key(secret: str) -> None:
    global _CLAUDE_SESSION_KEY
    _CLAUDE_SESSION_KEY = str(secret or "").strip()
    _reset_claude_clients()
    notify_changed()


def clear_claude_session_key() -> None:
    global _CLAUDE_SESSION_KEY
    _CLAUDE_SESSION_KEY = ""
    _reset_claude_clients()
    notify_changed()


def store_claude_key(secret: str) -> None:
    text = str(secret or "").strip()
    if not text:
        raise AiError("Paste the Claude proxy key first.", category="authentication")
    _write_credential(
        _CLAUDE_CRED_TARGET, text, user="claude-proxy",
        comment="FC26 Companion Claude proxy key",
        env_hint=f"Set {CLAUDE_KEY_ENV} or use a session key.",
    )
    _reset_claude_clients()
    notify_changed()


def clear_claude_stored_key() -> None:
    _delete_credential(_CLAUDE_CRED_TARGET)
    _reset_claude_clients()
    notify_changed()


def _read_claude_stored_key() -> str:
    return _read_credential(_CLAUDE_CRED_TARGET)


def _reset_claude_clients() -> None:
    try:
        from .claude_proxy import reset_session
    except Exception:  # noqa: BLE001
        return
    reset_session()


def _claude_sdk_available() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("anthropic") is not None
    except (ImportError, ValueError):
        return False


def redact(text: str) -> str:
    cleaned = str(text or "")
    secrets = {
        resolve_api_key()[0], _SESSION_KEY, resolve_claude_key()[0], _CLAUDE_SESSION_KEY,
    }
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        if secret in cleaned:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def readiness_all() -> dict[str, ProviderStatus]:
    """Status of every provider, so Settings can show where a switch would work."""
    return {pid: readiness(pid) for pid in PROVIDERS}


def readiness(provider: str | None = None) -> ProviderStatus:
    """Status of ``provider`` (default: the selected one). The only readiness gate."""
    chosen = provider if provider in PROVIDERS else provider_id()
    model = model_id(chosen)
    label = provider_label(chosen)
    if chosen == PROVIDER_CLAUDE:
        if not _claude_sdk_available():
            return ProviderStatus(
                chosen, label, model, False,
                "The Anthropic SDK is not installed in this Companion build.", "unavailable",
            )
        _key, source = resolve_claude_key()
        if source == "missing":
            return ProviderStatus(
                chosen, label, model, False,
                "Claude proxy key required. Open Settings to add one.", "authentication",
            )
        return ProviderStatus(chosen, label, model, True, claude_key_source_label(source))
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
    message = (
        state.message.rstrip(".") if state.state == "expired" else "Codex sign-in required"
    )
    if hint:
        message = f"{message}. {hint}"
    return ProviderStatus(chosen, label, model, False, message, "authentication")


def block_reason() -> str:
    state = readiness()
    if state.ready:
        return ""
    ready = [
        status.label
        for pid in PROVIDERS
        if pid != state.provider_id and (status := readiness(pid)).ready
    ]
    if ready:
        return f"{state.message} {' or '.join(ready)} is ready: switch the provider in Settings."
    return state.message


def dispatch_request(
    messages: list[dict[str, str]],
    *,
    model: str,
    schema: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Normalized Responses result. Callers still validate the JSON themselves.

    ``schema`` (optional) is a strict JSON schema; only Claude enforces it.
    """
    chosen = effective_model(model)
    provider = provider_id()
    if provider == PROVIDER_CLAUDE:
        text = claude_text(messages, model=chosen, json_object=True, schema=schema)
        return {"output_text": text}
    if provider == PROVIDER_API:
        text = openai_text(messages, model=chosen, json_object=True)
        return {"output_text": text}
    from .grok import _codex_request

    return _codex_request(messages, model=chosen)


def claude_text(
    messages: list[dict[str, str]],
    *,
    model: str,
    json_object: bool = True,
    reasoning: bool = True,
    schema: Mapping[str, Any] | None = None,
) -> str:
    """One Claude (proxy) call through the official SDK. Raises ``AiError``."""
    secret, source = resolve_claude_key()
    if source == "missing" or not secret:
        raise AiError(
            "Claude proxy key required. Open Settings to add one.", category="authentication",
        )
    from .claude_proxy import complete

    return complete(
        messages,
        model=model,
        api_key=secret,
        base_url=claude_base_url(),
        json_object=json_object,
        effort=reasoning_effort(PROVIDER_CLAUDE) if reasoning else "low",
        schema=schema,
        redact=redact,
    )


def test_connection() -> ConnectionTest:
    """Ask the selected provider and model to return exactly FC26_OK."""
    chosen = provider_id()
    model = model_id(chosen)
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": "Reply with plain text only."},
        {"role": "user", "content": "Return exactly FC26_OK"},
    ]
    try:
        if chosen == PROVIDER_CLAUDE:
            text = claude_text(messages, model=model, json_object=False, reasoning=False)
        elif chosen == PROVIDER_API:
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
    _write_credential(
        _CRED_TARGET, secret, user="openai", comment="FC26 Companion OpenAI API key",
        env_hint="Set OPENAI_API_KEY or use a session key.",
    )


def _read_stored_key() -> str:
    return _read_credential(_CRED_TARGET)


def _delete_stored_key() -> None:
    _delete_credential(_CRED_TARGET)


def _write_credential(target: str, secret: str, *, user: str, comment: str, env_hint: str) -> None:
    if sys.platform != "win32":
        raise AiError(
            f"Secure key storage needs Windows. {env_hint}",
            category="unavailable",
        )
    import ctypes

    blob = secret.encode("utf-8")
    buffer = ctypes.create_string_buffer(blob)
    cred = _CREDENTIALW()
    cred.Type = 1
    cred.TargetName = target
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    cred.Persist = 2
    cred.UserName = user
    cred.Comment = comment
    if not _advapi32().CredWriteW(ctypes.byref(cred), 0):
        raise AiError("Windows could not save the key.", category="unavailable")


def _read_credential(target: str) -> str:
    if sys.platform != "win32":
        return ""
    import ctypes

    try:
        adv = _advapi32()
        slot = ctypes.POINTER(_CREDENTIALW)()
        ok = adv.CredReadW(target, 1, 0, ctypes.byref(slot))
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


def _delete_credential(target: str) -> None:
    if sys.platform != "win32":
        return
    try:
        _advapi32().CredDeleteW(target, 1, 0)
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
