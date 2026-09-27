"""companion/platform/secrets.py — DPAPI and the plaintext-credentials migration.

The migration is the reason this module exists, and the property that matters
most is negative: **nothing is ever shredded unless a verified read-back
succeeded first.** Several tests here deliberately break a step of the pipeline
and assert the plaintext survived.

No test touches the real ``xai_credentials.json``; every path is a tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from companion.platform import secrets as sec

_dpapi = pytest.mark.skipif(not sec.available(), reason="DPAPI unavailable (not Windows)")

_CREDS = {
    "auth_mode": "oauth",
    "access_token": "SECRET-ACCESS-TOKEN-VALUE",
    "refresh_token": "SECRET-REFRESH-TOKEN-VALUE",
    "email": "user@example.com",
}


# --- primitives -------------------------------------------------------------


@_dpapi
def test_protect_unprotect_roundtrip() -> None:
    blob = sec.protect(b"hunter2")
    assert blob != b"hunter2"
    assert b"hunter2" not in blob
    assert sec.unprotect(blob) == b"hunter2"


@_dpapi
def test_protect_handles_empty_and_binary() -> None:
    assert sec.unprotect(sec.protect(b"")) == b""
    payload = bytes(range(256))
    assert sec.unprotect(sec.protect(payload)) == payload


@_dpapi
def test_entropy_is_required_to_open_what_it_sealed() -> None:
    blob = sec.protect(b"token", entropy=b"pepper")
    assert sec.unprotect(blob, entropy=b"pepper") == b"token"
    with pytest.raises(sec.SecretsUnavailable):
        sec.unprotect(blob)                       # no entropy
    with pytest.raises(sec.SecretsUnavailable):
        sec.unprotect(blob, entropy=b"wrong")


@_dpapi
def test_garbage_does_not_decrypt() -> None:
    with pytest.raises(sec.SecretsUnavailable):
        sec.unprotect(b"not a dpapi blob at all")


# --- envelope ---------------------------------------------------------------


@_dpapi
def test_seal_unseal_roundtrip() -> None:
    envelope = sec.seal(_CREDS)
    assert envelope["format"] == sec.ENVELOPE_FORMAT
    assert sec.is_envelope(envelope)
    assert sec.unseal(envelope) == _CREDS


def test_is_envelope_rejects_plaintext() -> None:
    assert not sec.is_envelope(_CREDS)
    assert not sec.is_envelope({"format": "dpapi-v1"})      # no payload
    assert not sec.is_envelope({"payload_b64": "x"})        # no format
    assert not sec.is_envelope("string")
    assert not sec.is_envelope(None)


def test_unseal_refuses_non_envelopes() -> None:
    with pytest.raises(ValueError):
        sec.unseal(_CREDS)


@_dpapi
def test_envelope_is_v1_wire_compatible() -> None:
    """v1's ``grok_client._parse_creds_file`` reads exactly these two keys.

    Breaking this silently logs the user out of x.ai on the next launch.
    """
    envelope = sec.seal(_CREDS)
    assert sorted(envelope) == ["format", "payload_b64"]
    assert envelope["format"] == "dpapi-v1"
    import base64

    assert base64.b64decode(envelope["payload_b64"])


# --- files ------------------------------------------------------------------


@_dpapi
def test_written_file_contains_no_plaintext(tmp_path: Path) -> None:
    target = sec.write_secret_file(tmp_path / "c.json", _CREDS)
    blob = target.read_bytes()
    assert b"SECRET-ACCESS-TOKEN-VALUE" not in blob
    assert b"SECRET-REFRESH-TOKEN-VALUE" not in blob
    assert b"user@example.com" not in blob
    assert sec.read_secret_file(target) == _CREDS


@_dpapi
def test_write_creates_parent_and_leaves_no_tmp(tmp_path: Path) -> None:
    target = sec.write_secret_file(tmp_path / "deep" / "nested" / "c.json", _CREDS)
    assert target.is_file()
    assert list(target.parent.glob("*.tmp")) == []


@_dpapi
def test_write_refuses_to_degrade_to_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1 fell back to writing the token in the clear when DPAPI failed."""
    def boom(*_a: object, **_k: object) -> bytes:
        raise sec.SecretsUnavailable("nope")

    monkeypatch.setattr(sec, "protect", boom)
    target = tmp_path / "c.json"
    with pytest.raises(sec.SecretsUnavailable):
        sec.write_secret_file(target, _CREDS)
    assert not target.exists()


def test_read_secret_file_tolerates_plaintext_and_junk(tmp_path: Path) -> None:
    plain = tmp_path / "p.json"
    plain.write_text(json.dumps(_CREDS), encoding="utf-8")
    assert sec.read_secret_file(plain) == _CREDS

    assert sec.read_secret_file(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert sec.read_secret_file(broken) == {}
    array = tmp_path / "array.json"
    array.write_text("[1,2]", encoding="utf-8")
    assert sec.read_secret_file(array) == {}


def test_is_plaintext_secret_file(tmp_path: Path) -> None:
    plain = tmp_path / "p.json"
    plain.write_text(json.dumps(_CREDS), encoding="utf-8")
    assert sec.is_plaintext_secret_file(plain)

    sealed = tmp_path / "s.json"
    sealed.write_text(json.dumps({"format": "dpapi-v1", "payload_b64": "AAA="}), encoding="utf-8")
    assert not sec.is_plaintext_secret_file(sealed)
    assert not sec.is_plaintext_secret_file(tmp_path / "missing.json")

    empty = tmp_path / "e.json"
    empty.write_text("{}", encoding="utf-8")
    assert not sec.is_plaintext_secret_file(empty)


def test_default_paths_match_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert sec.default_credentials_path() == tmp_path / "LE_Profile_Executor" / "xai_credentials.json"
    assert sec.legacy_credentials_path(tmp_path) == tmp_path / "xai_credentials.json"


# --- shredding --------------------------------------------------------------


def test_shred_removes_file_and_content(tmp_path: Path) -> None:
    victim = tmp_path / "v.json"
    victim.write_text("SECRET-ACCESS-TOKEN-VALUE" * 10, encoding="utf-8")
    assert sec.shred_file(victim) is True
    assert not victim.exists()


def test_shred_of_missing_file_is_false(tmp_path: Path) -> None:
    assert sec.shred_file(tmp_path / "nothing.json") is False


# --- migration --------------------------------------------------------------


def _plaintext(tmp_path: Path) -> Path:
    path = tmp_path / "xai_credentials.json"
    path.write_text(json.dumps(_CREDS), encoding="utf-8")
    return path


@_dpapi
def test_migration_seals_verifies_and_shreds(tmp_path: Path) -> None:
    source = _plaintext(tmp_path)
    target = tmp_path / "local" / "xai_credentials.json"

    result = sec.migrate_plaintext_credentials(source, target)

    assert result.status == "migrated" and result.ok and result.changed
    assert result.shredded is True
    assert not source.exists()                       # plaintext gone
    assert sec.is_envelope(json.loads(target.read_text(encoding="utf-8")))
    assert sec.read_secret_file(target) == _CREDS    # and still readable


@_dpapi
def test_migration_result_carries_names_not_values(tmp_path: Path) -> None:
    result = sec.migrate_plaintext_credentials(_plaintext(tmp_path), tmp_path / "s.json")
    assert result.keys == ("access_token", "auth_mode", "email", "refresh_token")
    rendered = json.dumps(result.to_dict())
    assert "SECRET-ACCESS-TOKEN-VALUE" not in rendered
    assert "user@example.com" not in rendered


@_dpapi
def test_migration_is_idempotent(tmp_path: Path) -> None:
    source = _plaintext(tmp_path)
    target = tmp_path / "s.json"
    assert sec.migrate_plaintext_credentials(source, target).changed
    again = sec.migrate_plaintext_credentials(source, target)
    assert again.status == "nothing-to-do" and again.ok and not again.changed


def test_migration_with_nothing_to_migrate(tmp_path: Path) -> None:
    result = sec.migrate_plaintext_credentials(tmp_path / "absent.json", tmp_path / "s.json")
    assert result.status == "nothing-to-do" and result.ok


def test_migration_of_an_already_sealed_file_is_a_no_op(tmp_path: Path) -> None:
    source = tmp_path / "xai_credentials.json"
    source.write_text(
        json.dumps({"format": "dpapi-v1", "payload_b64": "AAA="}), encoding="utf-8"
    )
    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json")
    assert result.status == "already-sealed"
    assert source.exists()


def test_migration_of_empty_file_keeps_it(tmp_path: Path) -> None:
    source = tmp_path / "xai_credentials.json"
    source.write_text("{}", encoding="utf-8")
    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json")
    assert result.status == "nothing-to-do"
    assert source.exists()


def test_migration_never_shreds_without_dpapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _plaintext(tmp_path)
    monkeypatch.setattr(sec, "available", lambda: False)
    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json")
    assert result.status == "unavailable" and not result.ok
    assert source.exists()
    assert json.loads(source.read_text(encoding="utf-8")) == _CREDS


@_dpapi
def test_migration_never_shreds_when_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _plaintext(tmp_path)

    def boom(*_a: object, **_k: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(sec, "write_secret_file", boom)
    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json")
    assert result.status == "failed" and not result.ok
    assert source.exists() and json.loads(source.read_text(encoding="utf-8")) == _CREDS


@_dpapi
def test_migration_never_shreds_when_read_back_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the verified read-back: a wrong round-trip must not
    cost the user their only copy of a refresh token."""
    source = _plaintext(tmp_path)
    monkeypatch.setattr(sec, "unseal", lambda *_a, **_k: {"access_token": "WRONG"})

    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json")

    assert result.status == "failed"
    assert "verification" in result.detail
    assert source.exists() and json.loads(source.read_text(encoding="utf-8")) == _CREDS


@_dpapi
def test_migration_never_shreds_when_read_back_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _plaintext(tmp_path)

    def boom(*_a: object, **_k: object) -> dict[str, object]:
        raise sec.SecretsUnavailable("sealed by another user")

    monkeypatch.setattr(sec, "unseal", boom)
    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json")
    assert result.status == "failed"
    assert source.exists()


@_dpapi
def test_migration_can_keep_the_plaintext_on_request(tmp_path: Path) -> None:
    source = _plaintext(tmp_path)
    result = sec.migrate_plaintext_credentials(source, tmp_path / "s.json", shred=False)
    assert result.status == "migrated" and result.shredded is False
    assert source.exists()


@_dpapi
def test_migration_in_place_does_not_shred_its_own_output(tmp_path: Path) -> None:
    source = _plaintext(tmp_path)
    result = sec.migrate_plaintext_credentials(source, source)
    assert result.status == "migrated"
    assert source.exists()                            # sealed, not deleted
    assert sec.read_secret_file(source) == _CREDS


@_dpapi
def test_migrate_command_entry_point(tmp_path: Path) -> None:
    """``app/commands/migrate.py`` probes for these names and expects raises."""
    assert sec.migrate_from_file is sec.import_v1_credentials

    source = _plaintext(tmp_path)
    result = sec.import_v1_credentials(source, sealed_path=tmp_path / "s.json")
    assert result.status == "migrated"
    assert not source.exists()

    # A no-op is not an error.
    assert sec.import_v1_credentials(source, sealed_path=tmp_path / "s.json").ok


def test_migrate_command_entry_point_raises_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _plaintext(tmp_path)
    monkeypatch.setattr(sec, "available", lambda: False)
    with pytest.raises(sec.SecretsUnavailable):
        sec.import_v1_credentials(source)
    assert source.exists()


def test_has_plaintext_credentials_uses_the_legacy_location(tmp_path: Path) -> None:
    assert sec.has_plaintext_credentials(tmp_path) is False
    _plaintext(tmp_path)
    assert sec.has_plaintext_credentials(tmp_path) is True


def test_module_reads_no_credentials_on_import() -> None:
    """Importing the module must not touch the real credentials file."""
    source = Path(sec.__file__).read_text(encoding="utf-8")
    body = source.split("__all__")[0]
    assert "migrate_plaintext_credentials()" not in body   # no import-time call
    assert os.path.isabs(str(sec.default_credentials_path()))
