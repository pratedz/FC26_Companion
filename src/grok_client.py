"""Codex CLI session client for AI player creation.

Reuses ``~/.codex/auth.json`` from `codex login` (ChatGPT plan).
No platform API key. Uses GPT-6 Terra at high reasoning.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import paths
from . import player_schema

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "high"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_JSON_OBJECT_HINT = "Respond with JSON only."


def _with_json_word(text: str) -> str:
    if "json" in (text or "").lower():
        return text
    return f"{text.rstrip()}\n\n{_JSON_OBJECT_HINT}".strip()

# Same public OIDC client as Grok Build / Grok CLI (browser subscription login)
OIDC_ISSUER = "https://auth.x.ai"
OIDC_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_SCOPES = (
    "openid profile email offline_access "
    "grok-cli:access api:access "
    "conversations:read conversations:write "
    "workspaces:read workspaces:write"
)
OIDC_DISCOVERY = f"{OIDC_ISSUER}/.well-known/openid-configuration"

CONSOLE_KEYS_URL = "https://platform.openai.com/api-keys"
ACCOUNTS_URL = "https://chatgpt.com"
GROK_COM_URL = "https://chatgpt.com"


# ── credential storage ───────────────────────────────────────────────


def _creds_dir() -> Path:
    """Writable per-user dir (avoids Permission denied on Desktop/Program Files)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        d = Path(base) / "LE_Profile_Executor"
    else:
        d = Path.home() / ".config" / "le_profile_executor"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = paths.app_root()
    return d


def credentials_path() -> Path:
    """Prefer %LOCALAPPDATA%\\LE_Profile_Executor (writable); legacy next to exe."""
    return _creds_dir() / "xai_credentials.json"


def legacy_credentials_path() -> Path:
    return paths.app_root() / "xai_credentials.json"


def grok_build_auth_path() -> Path:
    home = (os.environ.get("CODEX_HOME") or "").strip()
    if home:
        return Path(home) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        t = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _dpapi_protect(plain: bytes) -> bytes:
    """Windows DPAPI: encrypt for current user. Raises OSError on failure."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    # ctypes' untyped ``windll`` call path can truncate pointers on 64-bit
    # Python 3.14.  It made encryption silently fail and the caller then wrote
    # an API key in plaintext.  Use the fully typed DPAPI signature and retain
    # the input buffer until the native call has returned.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_p = ctypes.POINTER(DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [
        blob_p, wintypes.LPCWSTR, blob_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_p,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    keep_in = ctypes.create_string_buffer(bytes(plain), len(plain))
    blob_in = DATA_BLOB(len(plain), ctypes.cast(keep_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "LE Companion credentials",
        None,
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    """Windows DPAPI: decrypt current-user blob."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_p = ctypes.POINTER(DATA_BLOB)
    crypt32.CryptUnprotectData.argtypes = [
        blob_p, ctypes.POINTER(wintypes.LPWSTR), blob_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_p,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    keep_in = ctypes.create_string_buffer(bytes(blob), len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(keep_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _clear_file_attrs(path: Path) -> None:
    """Clear Hidden/System so rewrite/unlink works (no console tools)."""
    if os.name != "nt" or not path.exists():
        return
    try:
        import ctypes

        # FILE_ATTRIBUTE_NORMAL
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)
    except Exception:
        pass


def _soft_hide(path: Path) -> None:
    """Best-effort Hidden only — never SYSTEM, never icacls (no PowerShell flash)."""
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # HIDDEN
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def _write_hidden_json(path: Path, payload: dict) -> None:
    """Persist credentials under a writable path; soft-fail never blocks Grok Build use."""
    import base64

    path.parent.mkdir(parents=True, exist_ok=True)
    _clear_file_attrs(path)
    raw = json.dumps(payload, indent=2).encode("utf-8")
    text: str
    if os.name == "nt":
        try:
            sealed = _dpapi_protect(raw)
            envelope = {
                "format": "dpapi-v1",
                "payload_b64": base64.b64encode(sealed).decode("ascii"),
            }
            text = json.dumps(envelope, indent=2)
        except Exception:
            text = json.dumps(payload, indent=2)
    else:
        text = json.dumps(payload, indent=2)
    # Atomic-ish write
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            # Surface a clear error for callers that care; Grok Build path can ignore
            raise PermissionError(
                f"Cannot write credentials to {path} ({e}). "
                f"Grok Build session at {grok_build_auth_path()} still works without a local copy."
            ) from e
    _soft_hide(path)


def save_oauth_session(
    *,
    access_token: str,
    refresh_token: str = "",
    expires_in: Optional[int] = None,
    expires_at: Optional[str] = None,
    email: str = "",
    source: str = "oauth",
) -> Path:
    exp = expires_at
    if not exp and expires_in:
        exp = datetime.fromtimestamp(
            time.time() + int(expires_in), tz=timezone.utc
        ).isoformat()
    payload = {
        "auth_mode": "oauth",
        "source": source,
        "access_token": access_token.strip(),
        "refresh_token": (refresh_token or "").strip(),
        "expires_at": exp or "",
        "email": email or "",
        "model": DEFAULT_MODEL,
        "oidc_issuer": OIDC_ISSUER,
        "oidc_client_id": OIDC_CLIENT_ID,
        "provider": "xai-subscription",
    }
    p = credentials_path()
    _write_hidden_json(p, payload)
    return p


def save_api_key(api_key: str) -> Path:
    p = credentials_path()
    payload = {
        "auth_mode": "api_key",
        "api_key": api_key.strip(),
        "model": DEFAULT_MODEL,
        "provider": "xai",
    }
    _write_hidden_json(p, payload)
    return p


def clear_credentials() -> None:
    for p in (credentials_path(), legacy_credentials_path()):
        if not p.is_file():
            continue
        try:
            _clear_file_attrs(p)
            p.unlink()
        except OSError:
            pass


# Back-compat names
clear_api_key = clear_credentials


def _parse_creds_file(p: Path) -> Dict[str, Any]:
    import base64

    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("format") == "dpapi-v1" and data.get("payload_b64"):
        try:
            sealed = base64.b64decode(str(data["payload_b64"]))
            plain = _dpapi_unprotect(sealed)
            inner = json.loads(plain.decode("utf-8"))
            return inner if isinstance(inner, dict) else {}
        except Exception:
            return {}
    return data


def _load_local_creds() -> Dict[str, Any]:
    # New path first, then legacy next-to-exe (may be locked/unreadable)
    for p in (credentials_path(), legacy_credentials_path()):
        data = _parse_creds_file(p)
        if data:
            return data
    return {}


def _load_grok_build_session() -> Optional[Dict[str, Any]]:
    """Parse ~/.codex/auth.json (Codex CLI ChatGPT login). Ignores any API key field."""
    p = grok_build_auth_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access = str(tokens.get("access_token") or "").strip()
    if not access:
        return None
    return {
        "auth_mode": "chatgpt",
        "source": "codex_cli",
        "access_token": access,
        "refresh_token": str(tokens.get("refresh_token") or "").strip(),
        "account_id": str(tokens.get("account_id") or data.get("account_id") or "").strip(),
        "expires_at": str(data.get("last_refresh") or ""),
        "email": "",
    }


def load_api_key() -> Optional[str]:
    """Legacy: return API key string if configured (not OAuth)."""
    env = (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or "").strip()
    if env:
        return env
    data = _load_local_creds()
    if data.get("auth_mode") == "api_key" or data.get("api_key"):
        key = str(data.get("api_key") or "").strip()
        return key or None
    return None


def get_access_token(*, force_refresh: bool = False) -> Optional[str]:
    """Resolve the Codex CLI ChatGPT access token. Never uses a platform API key."""
    gb = _load_grok_build_session()
    if gb and gb.get("access_token"):
        return str(gb["access_token"]).strip()
    return None


def _token_expiring_soon(expires_at: Any, skew_secs: int = 120) -> bool:
    dt = _parse_iso(str(expires_at or ""))
    if not dt:
        return False
    return (_now_utc().timestamp() + skew_secs) >= dt.timestamp()


def is_connected() -> bool:
    return bool(get_access_token())


def _mask_email(email: str) -> str:
    """Privacy: never show full email in UI status lines."""
    e = (email or "").strip()
    if not e or "@" not in e:
        return ""
    user, _, domain = e.partition("@")
    if len(user) <= 2:
        masked_user = user[0] + "*" if user else "*"
    else:
        masked_user = user[0] + "***" + user[-1]
    return f"{masked_user}@{domain}"


def auth_status(*, show_email: bool = False) -> str:
    gb = _load_grok_build_session()
    if gb and gb.get("access_token"):
        return "Using Codex CLI login · signed in"
    return "Not connected — run `codex login`"


def auth_detail() -> Dict[str, Any]:
    """Structured status for the Connect popup."""
    tok = get_access_token()
    local = _load_local_creds()
    gb = _load_grok_build_session()
    return {
        "connected": bool(tok),
        "status": auth_status(),
        "has_grok_build": bool(gb and gb.get("access_token")),
        "grok_build_email": (gb or {}).get("email") or "",
        "grok_build_path": str(grok_build_auth_path()),
        "local_mode": local.get("auth_mode") or "",
        "local_email": local.get("email") or "",
    }


# ── OIDC helpers ─────────────────────────────────────────────────────


def _http_form(
    url: str,
    fields: Dict[str, str],
    *,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "LE-Profile-Executor/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"OAuth HTTP {e.code}: {err[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"OAuth network error: {e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OAuth non-JSON: {raw[:400]}") from e


def discover_oidc() -> Dict[str, Any]:
    req = urllib.request.Request(
        OIDC_DISCOVERY,
        headers={"Accept": "application/json", "User-Agent": "LE-Profile-Executor/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _try_refresh(session: Dict[str, Any], *, persist_local: bool = True) -> Optional[str]:
    rt = str(session.get("refresh_token") or "").strip()
    if not rt:
        return None
    try:
        conf = discover_oidc()
        token_url = conf.get("token_endpoint") or f"{OIDC_ISSUER}/oauth2/token"
        client_id = str(session.get("oidc_client_id") or OIDC_CLIENT_ID)
        data = _http_form(
            token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": client_id,
            },
        )
        access = str(data.get("access_token") or "").strip()
        if not access:
            return None
        new_rt = str(data.get("refresh_token") or rt)
        if persist_local:
            try:
                save_oauth_session(
                    access_token=access,
                    refresh_token=new_rt,
                    expires_in=data.get("expires_in"),
                    email=str(session.get("email") or ""),
                    source=str(session.get("source") or "oauth_refresh"),
                )
            except OSError:
                pass  # token still usable; local cache optional
        return access
    except Exception:
        return None


def start_device_login() -> Dict[str, Any]:
    """
    Start OAuth 2.0 device-code login (SuperGrok subscription).

    Returns dict with: device_code, user_code, verification_uri, verification_uri_complete,
    interval, expires_in, code_verifier (for PKCE if required).
    """
    conf = discover_oidc()
    device_url = conf.get("device_authorization_endpoint") or f"{OIDC_ISSUER}/oauth2/device/code"
    verifier, challenge = _pkce_pair()
    fields = {
        "client_id": OIDC_CLIENT_ID,
        "scope": OIDC_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # Some AS ignore PKCE on device flow; retry without if needed
    try:
        data = _http_form(device_url, fields)
    except RuntimeError:
        data = _http_form(
            device_url,
            {"client_id": OIDC_CLIENT_ID, "scope": OIDC_SCOPES},
        )
        verifier = ""
    if not data.get("device_code"):
        raise RuntimeError(f"Device login failed: {data}")
    data["code_verifier"] = verifier
    data["token_endpoint"] = conf.get("token_endpoint") or f"{OIDC_ISSUER}/oauth2/token"
    return data


def poll_device_login(
    device: Dict[str, Any],
    *,
    cancel_event: Optional[threading.Event] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Poll until user approves device login. Saves OAuth session on success.
    Returns saved credential info.
    """
    token_url = device.get("token_endpoint") or f"{OIDC_ISSUER}/oauth2/token"
    interval = int(device.get("interval") or 5)
    expires_in = int(device.get("expires_in") or 600)
    deadline = time.time() + expires_in
    verifier = str(device.get("code_verifier") or "")

    while time.time() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Login cancelled")
        fields = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": str(device["device_code"]),
            "client_id": OIDC_CLIENT_ID,
        }
        if verifier:
            fields["code_verifier"] = verifier
        try:
            data = _http_form(token_url, fields, timeout=30.0)
        except RuntimeError as e:
            msg = str(e)
            if "authorization_pending" in msg or "slow_down" in msg:
                if "slow_down" in msg:
                    interval = min(interval + 2, 15)
                if on_status:
                    on_status("Waiting for you to approve in the browser…")
                time.sleep(interval)
                continue
            if "access_denied" in msg or "expired_token" in msg:
                raise RuntimeError("Login denied or code expired. Try again.") from e
            # Other errors: may be pending in non-json form
            if "400" in msg and ("pending" in msg.lower() or "authorization" in msg.lower()):
                if on_status:
                    on_status("Waiting for approval…")
                time.sleep(interval)
                continue
            raise

        access = str(data.get("access_token") or "").strip()
        if access:
            email = ""
            # optional userinfo
            try:
                conf = discover_oidc()
                ui = conf.get("userinfo_endpoint")
                if ui:
                    req = urllib.request.Request(
                        ui,
                        headers={
                            "Authorization": f"Bearer {access}",
                            "Accept": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        info = json.loads(resp.read().decode("utf-8"))
                        email = str(info.get("email") or info.get("preferred_username") or "")
            except Exception:
                pass
            save_oauth_session(
                access_token=access,
                refresh_token=str(data.get("refresh_token") or ""),
                expires_in=data.get("expires_in"),
                email=email,
                source="device_oauth",
            )
            if on_status:
                on_status("Signed in!")
            return {
                "email": email,
                "expires_in": data.get("expires_in"),
                "status": auth_status(),
            }
        # error field in body
        err = str(data.get("error") or "")
        if err in ("authorization_pending", "slow_down"):
            if err == "slow_down":
                interval = min(interval + 2, 15)
            if on_status:
                on_status("Waiting for you to approve in the browser…")
            time.sleep(interval)
            continue
        if err:
            raise RuntimeError(f"Login error: {err} {data.get('error_description') or ''}")
        time.sleep(interval)

    raise RuntimeError("Login timed out — run Connect Grok again.")


def import_grok_build_session() -> Dict[str, Any]:
    """Confirm the local Codex CLI login is readable. Does not copy tokens."""
    gb = _load_grok_build_session()
    if not gb or not gb.get("access_token"):
        raise RuntimeError(
            "No Codex CLI login found.\n"
            "Run `codex login` first, then try again."
        )
    if not get_access_token():
        raise RuntimeError("Codex session loaded but no access token is available.")
    return {
        "status": auth_status(),
        "email": "",
        "source": "codex_cli",
        "cached_locally": False,
    }


# ── HTTP API ─────────────────────────────────────────────────────────


def _codex_headers(token: str) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
        "originator": "LE-Profile-Executor",
        "session_id": str(uuid.uuid4()),
        "User-Agent": "LE-Profile-Executor/1.0",
    }
    gb = _load_grok_build_session() or {}
    account = str(gb.get("account_id") or "").strip()
    if account:
        headers["chatgpt-account-id"] = account
    return headers


def _responses_body(
    messages: List[Dict[str, str]],
    *,
    model: str,
) -> dict:
    instructions = ""
    items: List[Dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system" and not instructions:
            instructions = content
            continue
        items.append({"role": role if role in {"user", "assistant"} else "user", "content": content})
    if not items:
        items = [{"role": "user", "content": instructions or "Respond with JSON only."}]
        instructions = ""
    items = [
        {"role": item["role"], "content": _with_json_word(item["content"])}
        for item in items
    ]
    return {
        "model": model,
        "instructions": instructions,
        "input": items,
        "reasoning": {"effort": DEFAULT_REASONING_EFFORT},
        "store": False,
        "stream": True,
        "text": {"format": {"type": "json_object"}},
    }


def _sse_payloads(raw: str) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[5:].lstrip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        blob = "\n".join(data_lines).strip()
        if not blob or blob == "[DONE]":
            continue
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _assemble_codex_stream(raw: str) -> Dict[str, Any]:
    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    events = _sse_payloads(raw)
    if not events:
        raise RuntimeError("Codex stream was empty.")
    for event in reversed(events):
        kind = str(event.get("type") or "")
        if kind in {"response.failed", "error"}:
            raise RuntimeError(f"Codex stream failed: {event.get('error') or event}")
        response = event.get("response")
        if kind in {"response.completed", "response.done"} and isinstance(response, dict):
            return response
        if isinstance(event.get("output"), list):
            return event
    deltas: List[str] = []
    for event in events:
        kind = str(event.get("type") or "")
        if "output_text.delta" in kind or kind.endswith("text.delta"):
            piece = event.get("delta") or event.get("text") or ""
            if piece:
                deltas.append(str(piece))
    if deltas:
        return {"output_text": "".join(deltas)}
    raise RuntimeError("Codex stream ended without a proposal.")


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    body: Optional[dict] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    data = None
    headers = _codex_headers(token)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"Codex HTTP {e.code}: {err_body[:800]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Codex network error: {e}") from e
    try:
        return _assemble_codex_stream(raw)
    except RuntimeError as e:
        raise RuntimeError(f"Codex non-JSON response: {raw[:400]}") from e


def _extract_output_text(resp: Dict[str, Any]) -> str:
    if isinstance(resp.get("output_text"), str) and resp["output_text"].strip():
        return resp["output_text"]
    out = resp.get("output")
    if isinstance(out, list):
        chunks: List[str] = []
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                        t = c.get("text") or c.get("output_text") or ""
                        if t:
                            chunks.append(str(t))
            elif isinstance(item.get("text"), str):
                chunks.append(item["text"])
        if chunks:
            return "\n".join(chunks)
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(p.get("text") or "") for p in content if isinstance(p, dict)
            )
    raise RuntimeError(f"Could not parse model output: {json.dumps(resp)[:500]}")


def _parse_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(text[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise RuntimeError(f"Model did not return JSON object:\n{text[:600]}")


SYSTEM_PROMPT = """You are an expert EA FC / FIFA Live Editor player designer.
Given a natural-language request (era, player, overall caps, style), return ONLY one JSON object
for the players table. No markdown fences, no commentary.

Include whenever relevant:
- Attributes 1-99: acceleration,sprintspeed,positioning,finishing,shotpower,longshots,volleys,penalties,
  vision,crossing,freekickaccuracy,shortpassing,longpassing,curve,agility,balance,reactions,ballcontrol,
  dribbling,composure,interceptions,headingaccuracy,defensiveawareness,standingtackle,slidingtackle,
  jumping,stamina,strength,aggression,gkdiving,gkhandling,gkkicking,gkpositioning,gkreflexes
- Ratings: overallrating, potential, modifier (0), internationalrep (1-5)
- Skills: skillmoves (0-4, where 0 = 1 star and 4 = 5 stars), weakfootabilitytypecode (1-5), preferredfoot ("Right"|"Left"|1|2)
- Positions: preferredposition1 as "ST"/"CAM"/… (also preferredposition2 if useful)
- Body: height (cm), weight (kg), bodytypecode (int if known), muscularitycode
- Movement: runstylecode, runningcode1, runningcode2, personality, emotion (ints; 0 ok if unknown)
- Playstyles arrays by name:
  playstyles: [...], playstyles_plus: [...]
  Names from: Finesse Shot, Chip Shot, Power Shot, Dead Ball, Precision Header, Acrobatic,
  Low Driven Shot, Game Changer, Incisive Pass, Pinged Pass, Long Ball Pass, Tiki Taka,
  Whipped Pass, Inventive, Jockey, Block, Intercept, Anticipate, Slide Tackle, Aerial Fortress,
  Technical, Rapid, First Touch, Trickster, Press Proven, Quick Step, Relentless, Long Throw,
  Bruiser, Enforcer, GK Far Throw, GK Footwork, GK Cross Claimer, GK Rush Out, GK Far Reach, GK Deflector
- Optional face if requested: headassetid, hairtypecode, haircolorcode, skintonecode, eyecolorcode
- name: short label e.g. "CR7 2008"

Rules:
- Never exceed overall/potential caps from the user.
- Coherent era/position build (not all 99). Outfield GK attrs 1-20; GK inverted.
- 2-6 playstyles_plus, up to ~10 regular playstyles for stars.
- Valid JSON only.
"""


def test_connection(token: Optional[str] = None) -> Tuple[bool, str]:
    tok = (token or get_access_token() or "").strip()
    if not tok:
        return False, "Not signed in — run `codex login`"
    try:
        resp = _http_json(
            "POST",
            CODEX_RESPONSES_URL,
            token=tok,
            body=_responses_body(
                [{"role": "user", "content": "Reply with exactly: ok"}],
                model=DEFAULT_MODEL,
            ),
            timeout=60.0,
        )
        text = _extract_output_text(resp)
        return True, f"OK · {DEFAULT_MODEL}: {text.strip()[:80]}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def generate_player_from_prompt(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_ovr: Optional[int] = None,
) -> Dict[str, Any]:
    """Call GPT-5.6 Terra via the Codex CLI ChatGPT session."""
    token = (get_access_token() or "").strip()
    if not token:
        raise RuntimeError(
            "Codex is not connected.\n\n"
            "Run `codex login` in Codex CLI, then use Connect Codex here."
        )
    user = (prompt or "").strip()
    if not user:
        raise ValueError("Empty prompt")
    if max_ovr is not None:
        user = f"{user}\n\nHard cap: overallrating and potential must be <= {int(max_ovr)}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    resp = _http_json(
        "POST",
        CODEX_RESPONSES_URL,
        token=token,
        body=_responses_body(messages, model=model),
        timeout=180.0,
    )
    text = _extract_output_text(resp)

    assert text is not None
    raw = _parse_json_object(text)
    if max_ovr is not None:
        for f in ("overallrating", "potential"):
            try:
                if int(raw.get(f, 0)) > int(max_ovr):
                    raw[f] = int(max_ovr)
            except (TypeError, ValueError):
                pass
    return player_schema.normalize_player_card(raw)


def chat_edit_card(
    card: Dict[str, Any],
    user_message: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Chat-style edit: user asks changes; return updated player card JSON.

    history: optional prior [{role, content}] for multi-turn.
    """
    token = (get_access_token() or "").strip()
    if not token:
        raise RuntimeError("Codex is not connected. Run `codex login` first.")
    msg = (user_message or "").strip()
    if not msg:
        raise ValueError("Empty message")

    import json as _json

    base = player_schema.normalize_player_card(dict(card or {}))
    # slim payload for context
    slim = {k: base.get(k) for k in list(base.keys()) if base.get(k) not in (None, "", [])}
    system = (
        SYSTEM_PROMPT
        + "\nYou are editing an existing FC 26 player card based on chat.\n"
        "Start from the JSON card provided. Apply the user's requested edits.\n"
        "Return ONLY the full updated player JSON object (same schema)."
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        for h in history[-8:]:
            if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": str(h["role"]), "content": str(h["content"])})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Current card JSON:\n{_json.dumps(slim, ensure_ascii=False)[:6000]}\n\n"
                f"User request:\n{msg}"
            ),
        }
    )
    resp = _http_json(
        "POST",
        CODEX_RESPONSES_URL,
        token=token,
        body=_responses_body(messages, model=model),
        timeout=180.0,
    )
    text = _extract_output_text(resp)
    raw = _parse_json_object(text)
    return player_schema.normalize_player_card(raw)
