r"""Windows DPAPI — the only place v2 stores a secret.

The problem this closes: ``xai_credentials.json`` sits at the repo root in
**plaintext**, holding a live OAuth ``access_token`` and ``refresh_token`` next
to the ``.exe``, in a directory users routinely zip up and post when asking for
help. §5 of ``docs/V2_ARCHITECTURE.md`` is explicit that it does *not* move into
``state.sqlite`` — a world-readable SQLite file beside the exe is the same bug
with extra steps — it moves into DPAPI, user-scoped.

DPAPI (``CryptProtectData`` / ``CryptUnprotectData``) ties the ciphertext to the
Windows user account: copying the file to another machine, or another user on
this one, yields nothing. No key material for us to store, lose, or hardcode.
``CRYPTPROTECT_UI_FORBIDDEN`` is set on both directions so a background poll can
never block on a credential prompt nobody is looking at.

**Wire-compatible with v1 on purpose.** The envelope is byte-for-byte what
``src/grok_client.py::_write_hidden_json`` produces::

    {"format": "dpapi-v1", "payload_b64": "<base64 of the DPAPI blob>"}

and the default location is v1's preferred one
(``%LOCALAPPDATA%\LE_Profile_Executor\xai_credentials.json``), which
``_load_local_creds`` already reads *before* the repo-root copy. So migrating
does not break v1 — it silently fixes it. No optional entropy by default for
the same reason: v1 decrypts with ``pOptionalEntropy = NULL`` and would fail on
a blob sealed with any.

Migration is deliberately paranoid: seal, write, **re-read from disk, decrypt,
and compare against the original** — and only then shred the plaintext. A
migration that deletes the only copy of a refresh token because the write
half-failed is worse than one that leaves the plaintext behind.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as _stdlib_secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_IS_WINDOWS = os.name == "nt"
_AVAILABLE_CACHE: bool | None = None

#: Shown by Windows in credential-audit UIs. Matches v1 exactly.
DESCRIPTION = "LE Companion credentials"

ENVELOPE_FORMAT = "dpapi-v1"
_FORMAT_KEY = "format"
_PAYLOAD_KEY = "payload_b64"

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_NORMAL = 0x80

#: The file v1 wrote in the clear, and the sealed file v1 already prefers.
CREDENTIALS_FILENAME = "xai_credentials.json"


class SecretsUnavailable(RuntimeError):
    """DPAPI could not be used. Never raised as a side effect of reading."""


# --- DPAPI ------------------------------------------------------------------


def available() -> bool:
    """True only when a user-scoped DPAPI round-trip actually succeeds."""
    global _AVAILABLE_CACHE
    if not _IS_WINDOWS:
        return False
    if _AVAILABLE_CACHE is not None:
        return _AVAILABLE_CACHE
    try:
        probe = b"le-companion-dpapi-probe"
        _AVAILABLE_CACHE = unprotect(protect(probe)) == probe
    except Exception:  # noqa: BLE001
        _AVAILABLE_CACHE = False
    return _AVAILABLE_CACHE


def _crypt32():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    blob_p = ctypes.POINTER(DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [
        blob_p, wintypes.LPCWSTR, blob_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_p,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_p, ctypes.POINTER(wintypes.LPWSTR), blob_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_p,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32, DATA_BLOB


def _to_blob(blob_type, data: bytes):
    import ctypes

    buffer = ctypes.create_string_buffer(bytes(data), len(data))
    return blob_type(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def protect(data: bytes, *, description: str = DESCRIPTION, entropy: bytes | None = None) -> bytes:
    """Encrypt ``data`` for the current Windows user. Raises on failure.

    ``entropy`` is an extra secret that must be supplied again to decrypt.
    Leave it ``None`` for anything v1 also reads (see the module docstring).
    """
    if not _IS_WINDOWS:
        raise SecretsUnavailable("DPAPI is Windows-only")
    import ctypes

    crypt32, kernel32, blob_type = _crypt32()
    blob_in, _keep_in = _to_blob(blob_type, data)
    blob_out = blob_type()
    entropy_ptr = None
    _keep_entropy = None
    if entropy:
        entropy_blob, _keep_entropy = _to_blob(blob_type, entropy)
        entropy_ptr = ctypes.byref(entropy_blob)

    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        description,
        entropy_ptr,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise SecretsUnavailable(f"CryptProtectData failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def unprotect(blob: bytes, *, entropy: bytes | None = None) -> bytes:
    """Decrypt a DPAPI blob sealed by this user. Raises on failure."""
    if not _IS_WINDOWS:
        raise SecretsUnavailable("DPAPI is Windows-only")
    import ctypes

    crypt32, kernel32, blob_type = _crypt32()
    blob_in, _keep_in = _to_blob(blob_type, blob)
    blob_out = blob_type()
    entropy_ptr = None
    _keep_entropy = None
    if entropy:
        entropy_blob, _keep_entropy = _to_blob(blob_type, entropy)
        entropy_ptr = ctypes.byref(entropy_blob)

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        entropy_ptr,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise SecretsUnavailable(f"CryptUnprotectData failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# --- the JSON envelope ------------------------------------------------------


def is_envelope(data: object) -> bool:
    """True for a sealed envelope; False for a plaintext payload."""
    return (
        isinstance(data, dict)
        and data.get(_FORMAT_KEY) == ENVELOPE_FORMAT
        and bool(data.get(_PAYLOAD_KEY))
    )


def seal(payload: dict[str, Any], *, entropy: bytes | None = None) -> dict[str, str]:
    """JSON -> sealed envelope dict (the thing that goes on disk)."""
    raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    return {
        _FORMAT_KEY: ENVELOPE_FORMAT,
        _PAYLOAD_KEY: base64.b64encode(protect(raw, entropy=entropy)).decode("ascii"),
    }


def unseal(envelope: dict[str, Any], *, entropy: bytes | None = None) -> dict[str, Any]:
    """Sealed envelope dict -> the original JSON payload."""
    if not is_envelope(envelope):
        raise ValueError("not a dpapi-v1 envelope")
    blob = base64.b64decode(str(envelope[_PAYLOAD_KEY]))
    data = json.loads(unprotect(blob, entropy=entropy).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sealed payload is not a JSON object")
    return data


# --- files ------------------------------------------------------------------


def user_data_dir() -> Path:
    r"""``%LOCALAPPDATA%\LE_Profile_Executor`` — writable without admin.

    The app can be installed on the Desktop or under Program Files; only the
    per-user directory is reliably writable, which is why v1 moved credentials
    here and why the sealed file follows.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LE_Profile_Executor"


def default_credentials_path() -> Path:
    """Where the sealed credentials live. v1's ``credentials_path()``."""
    return user_data_dir() / CREDENTIALS_FILENAME


def legacy_credentials_path(root: Path | None = None) -> Path:
    """The plaintext file next to the exe — what we are migrating *off*."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    return base / CREDENTIALS_FILENAME


def _clear_attrs(path: Path) -> None:
    """Drop Hidden/System so the file can be rewritten and deleted."""
    if not _IS_WINDOWS or not path.exists():
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), _FILE_ATTRIBUTE_NORMAL)
    except Exception:  # noqa: BLE001
        pass


def _hide(path: Path) -> None:
    """Best effort Hidden — never SYSTEM, never icacls (v1 learned that one:
    shelling out flashed a console window on every save)."""
    try:
        if _IS_WINDOWS:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), _FILE_ATTRIBUTE_HIDDEN)
        else:  # pragma: no cover
            os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        pass


def read_secret_file(path: Path, *, entropy: bytes | None = None) -> dict[str, Any]:
    """Read a credentials file, sealed or not. ``{}`` when absent/unreadable.

    Tolerates plaintext so a half-finished migration still works; use
    :func:`is_plaintext_secret_file` to *detect* that case rather than
    inferring it from a successful read.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if is_envelope(data):
        try:
            return unseal(data, entropy=entropy)
        except Exception:  # noqa: BLE001 - sealed by another user, or corrupt
            return {}
    return data


def write_secret_file(
    path: Path,
    payload: dict[str, Any],
    *,
    entropy: bytes | None = None,
) -> Path:
    """Seal ``payload`` and write it atomically. Raises if DPAPI is unavailable.

    Deliberately has **no** plaintext fallback: v1 quietly degraded to writing
    the token in the clear when ``CryptProtectData`` failed, which is how a
    "secure" store ends up not being one. A caller that genuinely wants plain
    JSON can write it itself and own that decision.
    """
    envelope = seal(payload, entropy=entropy)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _clear_attrs(target)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    _hide(target)
    return target


def is_plaintext_secret_file(path: Path) -> bool:
    """True when ``path`` exists and holds an *unsealed* JSON object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data) and not is_envelope(data)


# --- shredding --------------------------------------------------------------


def shred_file(path: Path, *, passes: int = 1) -> bool:
    """Overwrite ``path`` with random bytes, truncate, then delete it.

    Honest about what this is: on an SSD, a copy-on-write filesystem, a
    snapshotted volume or a synced folder, overwriting in place does not
    guarantee the old blocks are gone. What it *does* guarantee is that the
    file at this path no longer contains the token and no longer exists, which
    is the threat that matters here (a user zipping their app folder). Callers
    should treat a rotated token as the real remedy.
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return False
    _clear_attrs(target)
    try:
        with open(target, "r+b") as handle:
            for _ in range(max(1, passes)):
                handle.seek(0)
                handle.write(_stdlib_secrets.token_bytes(size) if size else b"")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            handle.truncate(0)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass   # keep going: deleting still helps
    try:
        target.unlink()
        return True
    except OSError:
        return False


# --- migration --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Outcome of :func:`migrate_plaintext_credentials`. Carries no secrets.

    ``keys`` lists the *names* of the migrated fields (``access_token``, …) so
    the UI can say what moved without ever rendering a value.
    """

    status: str            # migrated | already-sealed | nothing-to-do | unavailable | failed
    detail: str = ""
    source: Path | None = None
    sealed_path: Path | None = None
    shredded: bool = False
    keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status in ("migrated", "already-sealed", "nothing-to-do")

    @property
    def changed(self) -> bool:
        return self.status == "migrated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "source": str(self.source) if self.source else "",
            "sealed_path": str(self.sealed_path) if self.sealed_path else "",
            "shredded": self.shredded,
            "keys": list(self.keys),
            "ok": self.ok,
        }


def has_plaintext_credentials(root: Path | None = None) -> bool:
    """Doctor check: is there still a plaintext token on disk?"""
    return is_plaintext_secret_file(legacy_credentials_path(root))


def migrate_plaintext_credentials(
    plaintext_path: Path | None = None,
    sealed_path: Path | None = None,
    *,
    root: Path | None = None,
    shred: bool = True,
    entropy: bytes | None = None,
) -> MigrationResult:
    """Re-encrypt the plaintext credentials file, then shred the original.

    Order of operations is the whole point:

    1. read and parse the plaintext (bail if absent, empty or already sealed);
    2. refuse outright if DPAPI is unavailable — never delete what we cannot
       replace;
    3. seal and write the new file atomically;
    4. **read it back from disk and decrypt it, and compare to the original**;
    5. only if step 4 matches exactly, shred the plaintext.

    Any failure leaves the plaintext untouched and reports ``failed``. Safe to
    run on every startup: a second run reports ``nothing-to-do``.
    """
    source = Path(plaintext_path) if plaintext_path else legacy_credentials_path(root)
    target = Path(sealed_path) if sealed_path else default_credentials_path()

    if not source.is_file():
        return MigrationResult("nothing-to-do", "no plaintext credentials file", source, target)

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return MigrationResult("failed", f"cannot read {source.name}: {exc}", source, target)

    if not isinstance(raw, dict) or not raw:
        return MigrationResult("nothing-to-do", "file holds no credentials", source, target)

    if is_envelope(raw):
        return MigrationResult(
            "already-sealed", "file is already a dpapi-v1 envelope", source, source
        )

    if not available():
        return MigrationResult(
            "unavailable",
            "DPAPI is not available; plaintext left in place rather than destroyed",
            source,
            target,
        )

    keys = tuple(sorted(str(k) for k in raw))

    try:
        write_secret_file(target, raw, entropy=entropy)
    except Exception as exc:  # noqa: BLE001
        return MigrationResult("failed", f"could not write {target}: {exc}", source, target, keys=keys)

    # Verified read-back: from disk, through DPAPI, compared to the original.
    try:
        restored = json.loads(target.read_text(encoding="utf-8"))
        if not is_envelope(restored):
            raise ValueError("written file is not a sealed envelope")
        if unseal(restored, entropy=entropy) != raw:
            raise ValueError("decrypted payload does not match the original")
    except Exception as exc:  # noqa: BLE001
        return MigrationResult(
            "failed",
            f"read-back verification failed ({exc}); plaintext left in place",
            source,
            target,
            keys=keys,
        )

    shredded = False
    if shred and source.resolve() != target.resolve():
        shredded = shred_file(source)
        if not shredded:
            return MigrationResult(
                "migrated",
                f"sealed to {target}, but {source} could not be removed — delete it by hand",
                source,
                target,
                False,
                keys,
            )

    return MigrationResult(
        "migrated",
        f"{len(keys)} field(s) sealed to {target}",
        source,
        target,
        shredded,
        keys,
    )


def import_v1_credentials(
    path: Path,
    *,
    sealed_path: Path | None = None,
    shred: bool = True,
) -> MigrationResult:
    """Entry point for ``app/commands/migrate.py``: seal one v1 credentials file.

    Same pipeline as :func:`migrate_plaintext_credentials` (seal, verified
    read-back, then shred) but **raises** instead of returning a failure, because
    the migrate command reports errors from exceptions and would otherwise record
    a silent failure as a success. ``nothing-to-do`` and ``already-sealed`` are
    not failures and return normally.
    """
    result = migrate_plaintext_credentials(path, sealed_path, shred=shred)
    if not result.ok:
        raise SecretsUnavailable(result.detail or result.status)
    return result


#: v1-era alias; ``migrate.py`` probes for either name.
migrate_from_file = import_v1_credentials


__all__ = [
    "CREDENTIALS_FILENAME",
    "DESCRIPTION",
    "ENVELOPE_FORMAT",
    "MigrationResult",
    "SecretsUnavailable",
    "available",
    "default_credentials_path",
    "has_plaintext_credentials",
    "import_v1_credentials",
    "is_envelope",
    "migrate_from_file",
    "is_plaintext_secret_file",
    "legacy_credentials_path",
    "migrate_plaintext_credentials",
    "protect",
    "read_secret_file",
    "seal",
    "shred_file",
    "unprotect",
    "unseal",
    "user_data_dir",
    "write_secret_file",
]
