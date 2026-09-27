"""companion/platform/registry.py — LE location discovery.

Tests that need a real ``HKLM\\SOFTWARE\\Live Editor\\FC 26`` key skip when it
is not there, so this file passes on a machine with no Live Editor installed.
Everything else is driven through the documented env overrides or by faking
``read_value``, which is the module's only registry seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.platform import registry as reg


# --- shape ------------------------------------------------------------------


def test_resolution_reports_provenance() -> None:
    found = reg.Resolution(Path("C:/x"), reg.Source.HKLM, "Data Dir")
    assert found.found and found.trusted
    assert found.to_dict()["source"] == "registry:HKLM"

    guess = reg.Resolution(Path("C:/x"), reg.Source.LEGACY_DEFAULT)
    assert guess.found and not guess.trusted   # a guess is never "trusted"

    missing = reg.Resolution(None)
    assert not missing.found and not missing.trusted
    assert missing.to_dict()["path"] == ""


def test_legacy_default_is_named_not_buried() -> None:
    # v1 hid this literal inside the resolver (src/paths.py:143).
    assert reg.LEGACY_DEFAULT_DATA_DIR == Path(r"C:\FC 26 Live Editor")


# --- directory shape detection ----------------------------------------------


def test_looks_like_install_root(tmp_path: Path) -> None:
    assert not reg.looks_like_install_root(tmp_path)
    assert not reg.looks_like_install_root(None)
    (tmp_path / "FCLiveEditor.DLL").write_bytes(b"MZ")
    assert reg.looks_like_install_root(tmp_path)

    other = tmp_path / "other"
    (other / "lua" / "scripts").mkdir(parents=True)
    assert reg.looks_like_install_root(other)


def test_looks_like_data_dir(tmp_path: Path) -> None:
    assert not reg.looks_like_data_dir(tmp_path)
    (tmp_path / "le_config.json").write_text("{}", encoding="utf-8")
    assert reg.looks_like_data_dir(tmp_path)

    autorun = tmp_path / "b"
    (autorun / "lua" / "autorun").mkdir(parents=True)
    assert reg.looks_like_data_dir(autorun)


def test_install_root_and_data_dir_are_different_questions(tmp_path: Path) -> None:
    """The install root holds binaries; the data dir holds config. Not the same
    directory, which is exactly why v1's single ``le_root()`` mislocated one."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "Launcher.exe").write_bytes(b"MZ")
    data = tmp_path / "data"
    data.mkdir()
    (data / "le_config.json").write_text("{}", encoding="utf-8")

    assert reg.looks_like_install_root(install) and not reg.looks_like_data_dir(install)
    assert reg.looks_like_data_dir(data) and not reg.looks_like_install_root(data)


# --- env overrides ----------------------------------------------------------


def test_env_override_wins_over_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(reg.ENV_DATA_DIR, str(tmp_path))
    resolved = reg.resolve_data_dir()
    assert resolved.path == tmp_path
    assert resolved.source is reg.Source.ENV
    assert reg.le_data_dir() == tmp_path


def test_env_override_pointing_nowhere_is_not_silently_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(reg.ENV_DATA_DIR, str(tmp_path / "gone"))
    resolved = reg.resolve_data_dir()
    assert resolved.path is None
    assert "not a directory" in resolved.detail


def test_install_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(reg.ENV_INSTALL_DIR, str(tmp_path))
    assert reg.le_install_root() == tmp_path


# --- registry seam ----------------------------------------------------------


def test_stale_registry_value_does_not_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Data Dir that no longer exists must not be returned: v1 handed the
    autorun installer a deleted directory and reported success."""
    monkeypatch.delenv(reg.ENV_DATA_DIR, raising=False)
    monkeypatch.setattr(
        reg, "read_value", lambda name, key=reg.LE_KEY: (str(tmp_path / "gone"), reg.Source.HKLM)
    )
    resolved = reg.resolve_data_dir(allow_fallback=False)
    assert resolved.path is None
    assert resolved.source is reg.Source.UNSET


def test_probe_finds_install_root_when_registry_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(reg.ENV_INSTALL_DIR, raising=False)
    monkeypatch.setattr(reg, "read_value", lambda name, key=reg.LE_KEY: None)

    root = tmp_path / "FC 26 LE"
    nested = root / "LE_Profile_Executor" / "companion"
    nested.mkdir(parents=True)
    (root / "FCLiveEditor.DLL").write_bytes(b"MZ")

    resolved = reg.resolve_install_root(nested)
    assert resolved.path == root.resolve()
    assert resolved.source is reg.Source.PROBE


def test_probe_gives_up_instead_of_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(reg.ENV_INSTALL_DIR, raising=False)
    monkeypatch.setattr(reg, "read_value", lambda name, key=reg.LE_KEY: None)
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    resolved = reg.resolve_install_root(deep)
    assert resolved.path is None          # v1 returned start.parent regardless
    assert resolved.source is reg.Source.UNSET


def test_read_value_is_none_for_unknown_name() -> None:
    assert reg.read_value("Definitely Not A Value Name") is None


# --- live registry (skipped without LE) -------------------------------------

_live = pytest.mark.skipif(
    not reg.available() or reg.read_value(reg.VALUE_DATA_DIR) is None,
    reason="no HKLM\\SOFTWARE\\Live Editor\\FC 26 key on this machine",
)


@_live
def test_live_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(reg.ENV_DATA_DIR, raising=False)
    resolved = reg.resolve_data_dir()
    assert resolved.found and resolved.trusted
    assert resolved.path is not None and resolved.path.is_dir()
    assert reg.le_data_dir() == resolved.path


@_live
def test_live_install_root_holds_the_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(reg.ENV_INSTALL_DIR, raising=False)
    root = reg.le_install_root()
    assert root is not None
    assert reg.looks_like_install_root(root)


@_live
def test_describe_is_plain_data() -> None:
    described = reg.describe()
    assert described["registry_available"] is True
    assert described["key"] == reg.LE_KEY
    assert isinstance(described["values"], dict)
    assert set(described["data_dir"]) >= {"path", "source", "found", "trusted"}
