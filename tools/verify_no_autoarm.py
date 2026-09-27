"""Verify companion works with auto-arm disabled."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import actions, le_apply  # noqa: E402


def main() -> int:
    # restore LE core
    import runpy

    runpy.run_path(str(ROOT / "tools" / "restore_live_editor.py"), run_name="__r__")

    info = le_apply.install_bridge()
    print("install", info["ok"], "le_core_clean", info["le_core_clean"])
    print("autoarm", info["autoarm"])
    assert info["ok"], info
    assert info["le_core_clean"], info
    assert not le_apply.autoarm_installed()

    le = le_apply.le_live_editor_lua_path().read_text(encoding="utf-8")
    assert "AUTOARM" not in le
    assert "00_le_companion_bridge" not in le
    assert "function LIVE_EDITOR:Load()" in le

    worker = Path(info["installed"])
    assert worker.is_file()
    wtxt = worker.read_text(encoding="utf-8")
    assert "local QUEUE_DIR =" in wtxt
    assert "LE_Profile_Executor/queue" not in wtxt or "C:/" in wtxt

    lua = 'if Log then Log("[test] ok") end\n'
    out = actions.write_lua(lua, stem="apply_test_no_autoarm", clipboard=False)
    qf = Path(out["queue_file"])
    assert qf.is_file()
    le_apply.rebuild_pending()
    names = [p.name for p in le_apply.list_pending_lua()]
    assert qf.name in names

    # wait should timeout quickly if bridge off (not hang forever)
    result = le_apply.wait_until_applied(qf, timeout_sec=0.35, poll_sec=0.1)
    assert result["applied"] is False
    assert "Timed out" in (result.get("reason") or "")

    # simulate LE processing: move to done
    done = le_apply.done_dir() / qf.name
    done.write_text(qf.read_text(encoding="utf-8"), encoding="utf-8")
    qf.unlink()
    # fresh job for success path
    out2 = actions.write_lua(lua, stem="apply_test_ok", clipboard=False)
    qf2 = Path(out2["queue_file"])

    def mover() -> None:
        import time

        time.sleep(0.08)
        d = le_apply.done_dir() / qf2.name
        d.write_text(qf2.read_text(encoding="utf-8"), encoding="utf-8")
        qf2.unlink()

    import threading

    threading.Thread(target=mover, daemon=True).start()
    result2 = le_apply.wait_until_applied(qf2, timeout_sec=2.0, poll_sec=0.05)
    assert result2["applied"] is True, result2

    # cleanup leftovers
    for p in le_apply.list_pending_lua():
        if "apply_test" in p.name:
            p.unlink(missing_ok=True)
    le_apply.rebuild_pending()

    print("status_line:", le_apply.apply_status_line())
    print("VERIFY OK — auto-arm off, queue apply path works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
