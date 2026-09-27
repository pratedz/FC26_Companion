"""companion/platform/le_install.py — SI-3 install path, dry-run, no live_editor touch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from companion.platform import le_install


@pytest.fixture()
def fake_le(tmp_path: Path) -> Path:
    """Minimal LE data dir shape: lua/autorun + lua/scripts."""
    data = tmp_path / "le_data"
    (data / "lua" / "autorun").mkdir(parents=True)
    (data / "lua" / "scripts").mkdir(parents=True)
    (data / "le_config.json").write_text("{}", encoding="utf-8")
    # A live_editor.lua that must never be touched.
    core = data / "lua" / "libs" / "v2" / "imports" / "core"
    core.mkdir(parents=True)
    (core / "live_editor.lua").write_text(
        "function LIVE_EDITOR:Load()\nend\n", encoding="utf-8"
    )
    return data


def test_core_source_dir_finds_shipped_tree() -> None:
    src = le_install.core_source_dir()
    assert src is not None
    assert (src / "version.lua").is_file()
    assert (src / "util.lua").is_file()
    assert (src / "json.lua").is_file()


def test_install_dry_run_plans_without_writing(fake_le: Path, tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    live = (
        fake_le / "lua" / "libs" / "v2" / "imports" / "core" / "live_editor.lua"
    )
    before = live.read_bytes()

    report = le_install.install(
        queue_dir=queue, dry_run=True, data_dir=fake_le
    )
    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["patches_live_editor"] is False
    assert report["files_planned"]
    assert any("le_companion" in p for p in report["files_planned"])
    assert any(le_install.AUTORUN_STUB_NAME in p for p in report["files_planned"])
    assert report["files_written"] == []
    assert not (fake_le / "lua" / "scripts" / "le_companion").exists()
    assert not (fake_le / "lua" / "autorun" / le_install.AUTORUN_STUB_NAME).exists()
    assert live.read_bytes() == before
    assert "live_editor.lua" not in "\n".join(report["files_planned"])


def test_install_writes_core_config_and_autorun(fake_le: Path, tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    live = (
        fake_le / "lua" / "libs" / "v2" / "imports" / "core" / "live_editor.lua"
    )
    before = live.read_bytes()
    mtime = live.stat().st_mtime_ns

    report = le_install.install(
        queue_dir=queue, dry_run=False, data_dir=fake_le
    )
    assert report["ok"] is True
    assert report["patches_live_editor"] is False
    assert report["files_written"]

    core = fake_le / "lua" / "scripts" / "le_companion"
    assert (core / "version.lua").is_file()
    assert (core / "util.lua").is_file()
    assert (core / "json.lua").is_file()
    cfg = json.loads((core / "config.json").read_text(encoding="utf-8"))
    assert cfg["v"] == 3
    assert cfg["queue_dir"].replace("\\", "/").endswith("/queue")
    assert cfg["patches_live_editor"] is False
    assert cfg["core_version"]

    ar = fake_le / "lua" / "autorun" / le_install.AUTORUN_STUB_NAME
    assert ar.is_file()
    text = ar.read_text(encoding="utf-8")
    assert "SAFE_ARM" in text or "__LE_COMPANION_SAFE_ARM" in text
    # Comment may name live_editor.lua as the thing we never patch; code must not load it.
    assert "dofile" not in text or "live_editor" not in text.split("dofile")[-1][:80].lower()
    assert "require" not in text or 'require("live_editor' not in text.replace("'", '"')
    assert "ForceDrain" not in text
    assert str(queue.resolve()).replace("\\", "/") in text.replace("\\", "/")

    assert live.read_bytes() == before
    assert live.stat().st_mtime_ns == mtime


def test_v2_uses_distinct_autorun_and_preserves_v1(
    fake_le: Path, tmp_path: Path
) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    v1 = fake_le / "lua" / "autorun" / le_install.LEGACY_V1_AUTORUN_STUB_NAME
    v1.write_text("-- existing v1 loader\n", encoding="utf-8")

    report = le_install.install(queue_dir=queue, data_dir=fake_le)

    assert report["ok"] is True
    assert le_install.AUTORUN_STUB_NAME != le_install.LEGACY_V1_AUTORUN_STUB_NAME
    assert v1.read_text(encoding="utf-8") == "-- existing v1 loader\n"
    v2 = fake_le / "lua" / "autorun" / le_install.AUTORUN_STUB_NAME
    text = v2.read_text(encoding="utf-8")
    assert "__LE_COMPANION_V2_AUTORUN_DONE" in text
    assert "__LE_COMPANION_AUTORUN_DONE then return" not in text


def test_upgrade_repairs_shared_slot_when_v1_bridge_exists(
    fake_le: Path, tmp_path: Path
) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    scripts = fake_le / "lua" / "scripts"
    bridge = scripts / "00_le_companion_bridge.lua"
    bridge.write_text("-- v1 bridge\n", encoding="utf-8")
    shared = fake_le / "lua" / "autorun" / le_install.LEGACY_V1_AUTORUN_STUB_NAME
    shared.write_text(
        "-- LE Companion v2 auto-arm loader (generated — do not edit by hand)\n",
        encoding="utf-8",
    )

    report = le_install.install(queue_dir=queue, data_dir=fake_le)

    assert report["ok"] is True
    repaired = shared.read_text(encoding="utf-8")
    assert "v1 auto-arm loader" in repaired
    assert str(bridge.resolve()).replace("\\", "/") in repaired.replace("\\", "/")
    assert (fake_le / "lua" / "autorun" / le_install.AUTORUN_STUB_NAME).is_file()


def test_install_never_targets_live_editor_lua(fake_le: Path, tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    report = le_install.install(queue_dir=queue, data_dir=fake_le)
    all_paths = report["files_planned"] + report["files_written"]
    for p in all_paths:
        assert "live_editor.lua" not in p.replace("\\", "/").lower()
        assert "fakeeaac" not in p.replace("\\", "/").lower()


def test_status_reflects_install(fake_le: Path, tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    before = le_install.status(data_dir=fake_le)
    assert before["core_installed"] is False
    assert before["autorun_installed"] is False

    le_install.install(queue_dir=queue, data_dir=fake_le)
    after = le_install.status(data_dir=fake_le)
    assert after["core_installed"] is True
    assert after["autorun_installed"] is True
    assert after["core_valid"] is True
    assert after["autorun_valid"] is True
    assert after["config_valid"] is True
    assert after["installed"] is True
    assert after["config"]["queue_dir"]


def test_repair_queue_config_repoints_config_and_autorun(
    fake_le: Path, tmp_path: Path
) -> None:
    first_queue = tmp_path / "first_queue"
    second_queue = tmp_path / "second_queue"
    first_queue.mkdir()
    second_queue.mkdir()
    le_install.install(queue_dir=first_queue, data_dir=fake_le)
    core = fake_le / "lua" / "scripts" / "le_companion"
    config = core / "config.json"
    init_before = (core / "init.lua").read_bytes()
    autorun = fake_le / "lua" / "autorun" / le_install.AUTORUN_STUB_NAME
    first = str(first_queue.resolve()).replace("\\", "/")
    second = str(second_queue.resolve()).replace("\\", "/")
    # Simulate a visual-test install that only left the autorun on a temp path.
    poisoned = autorun.read_text(encoding="utf-8").replace(
        f"local QUEUE_DIR = [==[{first}]==]",
        "local QUEUE_DIR = [==[C:/Users/prated/AppData/Local/Temp/le-companion-vqa-fake/queue]==]",
        1,
    )
    autorun.write_text(poisoned, encoding="utf-8", newline="\n")

    report = le_install.repair_queue_config(
        queue_dir=second_queue, data_dir=fake_le
    )

    assert report["changed"] is True
    assert report["config_changed"] is True
    assert report["autorun_changed"] is True
    assert report["rearm_required"] is True
    assert json.loads(config.read_text(encoding="utf-8"))["queue_dir"] == second
    assert (core / "init.lua").read_bytes() == init_before
    autorun_text = autorun.read_text(encoding="utf-8")
    assert f"local QUEUE_DIR = [==[{second}]==]" in autorun_text
    assert "le-companion-vqa-fake" not in autorun_text
    assert not config.with_name(config.name + ".repair.tmp").exists()
    assert not autorun.with_name(autorun.name + ".repair.tmp").exists()

    # Idempotent second pass: both surfaces already correct.
    again = le_install.repair_queue_config(queue_dir=second_queue, data_dir=fake_le)
    assert again["changed"] is False
    assert again["config_changed"] is False
    assert again["autorun_changed"] is False


def test_reconfigure_snippet_reloads_config_without_draining() -> None:
    script = le_install.reconfigure_current_worker_snippet()
    assert "c.configure({})" in script
    assert "write_session" in script
    assert "force_drain" not in script.lower()
    assert "runner.pump" not in script.lower()

    forced = le_install.reconfigure_current_worker_snippet(
        queue_dir=r"C:\Users\prated\Desktop\FC 26 LE v26.3.5\LE_Profile_Executor\queue"
    )
    assert "queue_dir = [==[" in forced
    assert "LE_Profile_Executor/queue" in forced.replace("\\", "/")
    assert "force_drain" not in forced.lower()


def test_status_rejects_core_that_no_longer_matches_shipped_source(
    fake_le: Path, tmp_path: Path
) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    le_install.install(queue_dir=queue, data_dir=fake_le)
    core = fake_le / "lua" / "scripts" / "le_companion"
    (core / "ops.lua").write_text("-- stale worker\n", encoding="utf-8")

    state = le_install.status(data_dir=fake_le)

    assert state["core_content_matches"] is False
    assert state["core_valid"] is False
    assert state["installed"] is False


def test_status_does_not_call_unrelated_autorun_installed(fake_le: Path) -> None:
    legacy = fake_le / "lua" / "autorun" / le_install.LEGACY_V1_AUTORUN_STUB_NAME
    legacy.write_text("-- v1 only\n", encoding="utf-8")
    state = le_install.status(data_dir=fake_le)
    assert state["v1_autorun_present"] is True
    assert state["autorun_installed"] is False
    assert state["installed"] is False


def test_missing_data_dir_reports_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from companion.platform.registry import Resolution, Source

    monkeypatch.delenv("FC26_LE_DATA_DIR", raising=False)
    monkeypatch.setattr(
        "companion.platform.registry.resolve_data_dir",
        lambda **_k: Resolution(None, Source.UNSET, "nothing"),
    )
    report = le_install.install(queue_dir=tmp_path / "q")
    assert report["ok"] is False
    assert report["errors"]
    assert "not resolved" in report["errors"][0].lower() or "data dir" in report["errors"][0].lower()


def test_real_install_refuses_to_swap_the_worker_while_hosts_run(
    fake_le: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real install target must never be replaced under an active FC/LE."""
    from companion.platform.registry import Resolution, Source

    monkeypatch.setattr(
        "companion.platform.registry.resolve_data_dir",
        lambda **_k: Resolution(fake_le, Source.ENV, "test worker"),
    )
    monkeypatch.setattr(
        le_install,
        "_assert_game_and_live_editor_stopped",
        lambda: (_ for _ in ()).throw(RuntimeError("Close FC 26 and Live Editor first.")),
    )

    report = le_install.install(queue_dir=tmp_path / "queue")

    assert report["ok"] is False
    assert "Close FC 26 and Live Editor first." in report["errors"]
    assert report["files_written"] == []


def test_cli_install_dry_run_imports_and_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from companion.cli import main

    data = tmp_path / "le_data"
    (data / "lua" / "autorun").mkdir(parents=True)
    (data / "lua" / "scripts").mkdir(parents=True)
    queue_root = tmp_path / "app"
    queue_root.mkdir()

    # Import path must work (was the original failure: missing le_install).
    import companion.platform.le_install as _mod  # noqa: F401

    monkeypatch.setenv("FC26_LE_DATA_DIR", str(data))
    code = main(["--root", str(queue_root), "install", "--dry-run"])
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "cannot import name 'le_install'" not in combined
    assert "installer unavailable" not in combined
    body = json.loads(out.out)
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["patches_live_editor"] is False
    assert code == 0


def test_verify_le_integrity_missing_root() -> None:
    report = le_install.verify_le_integrity(install_root=Path("C:/definitely/not/an/le/root"))
    assert report.ok is False
