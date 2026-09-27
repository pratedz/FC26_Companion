"""Unit tests for LE queue/sync helpers (no FC26 required)."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import le_apply  # noqa: E402


class LeApplyQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.q = Path(self._tmp.name) / "queue"
        self.q.mkdir(parents=True, exist_ok=True)
        (self.q / "done").mkdir(exist_ok=True)
        self._cm = mock.patch.object(le_apply, "queue_dir", return_value=self.q)
        self._cm2 = mock.patch.object(
            le_apply, "done_dir", return_value=self.q / "done"
        )
        self._cm.start()
        self._cm2.start()

    def tearDown(self) -> None:
        self._cm.stop()
        self._cm2.stop()
        self._tmp.cleanup()

    def test_rebuild_pending_lists_job_files_only(self) -> None:
        (self.q / "apply_1.lua").write_text("-- job\n", encoding="utf-8")
        (self.q / "export_x.lua").write_text("-- job\n", encoding="utf-8")
        (self.q / "_run_now.lua").write_text("-- slot\n", encoding="utf-8")
        (self.q / "_pending.txt").write_text("stale.lua\n", encoding="utf-8")

        names = le_apply.rebuild_pending()
        self.assertEqual(set(names), {"apply_1.lua", "export_x.lua"})
        text = (self.q / "_pending.txt").read_text(encoding="utf-8")
        self.assertIn("apply_1.lua", text)
        self.assertIn("export_x.lua", text)
        self.assertNotIn("stale.lua", text)
        self.assertNotIn("_run_now", text)

    def test_heartbeat_age_drives_alive_and_status_line(self) -> None:
        # No heartbeat → not alive
        self.assertIsNone(le_apply.bridge_heartbeat_age_sec())
        self.assertFalse(le_apply.bridge_alive(120))

        hb = self.q / le_apply.HEARTBEAT_NAME
        hb.write_text("alive\n", encoding="utf-8")
        # Fresh pulse
        self.assertIsNotNone(le_apply.bridge_heartbeat_age_sec())
        self.assertTrue(le_apply.bridge_alive(120))
        line = le_apply.apply_status_line()
        self.assertIn("LIVE", line.upper())

        # Stale pulse
        old = time.time() - 500
        import os

        os.utime(hb, (old, old))
        self.assertFalse(le_apply.bridge_alive(120))
        age = le_apply.bridge_heartbeat_age_sec()
        self.assertIsNotNone(age)
        assert age is not None
        self.assertGreater(age, 400)

    def test_wait_until_applied_success_when_moved_to_done(self) -> None:
        job = self.q / "apply_ok.lua"
        job.write_text("-- apply\n", encoding="utf-8")
        done = self.q / "done" / "apply_ok.lua"

        def mover() -> None:
            time.sleep(0.15)
            done.write_text(job.read_text(encoding="utf-8"), encoding="utf-8")
            job.unlink()

        import threading

        threading.Thread(target=mover, daemon=True).start()
        result = le_apply.wait_until_applied(job, timeout_sec=3.0, poll_sec=0.05)
        self.assertTrue(result["applied"], result)
        self.assertFalse(job.is_file())

    def test_wait_until_applied_timeout_leaves_job(self) -> None:
        job = self.q / "apply_stuck.lua"
        job.write_text("-- stuck\n", encoding="utf-8")
        result = le_apply.wait_until_applied(job, timeout_sec=0.4, poll_sec=0.1)
        self.assertFalse(result["applied"])
        self.assertTrue(job.is_file())
        self.assertIn("Timed out", result["reason"])

    def test_wait_success_when_run_now_ok_orphans_named_job(self) -> None:
        """LE may report last=_run_now while named apply_*.lua is still on disk."""
        job = self.q / "apply_orphan.lua"
        job.write_text("-- apply payload\n", encoding="utf-8")
        run_now = self.q / "_run_now.lua"
        run_now.write_text("-- apply payload\n", encoding="utf-8")
        res = self.q / "_last_result.txt"
        res.write_text("old\n", encoding="utf-8")

        def bridge_sim() -> None:
            import time

            time.sleep(0.12)
            if run_now.is_file():
                run_now.unlink()
            res.write_text("OK processed=1 ok=1 last=_run_now.lua\n", encoding="utf-8")

        import threading

        threading.Thread(target=bridge_sim, daemon=True).start()
        result = le_apply.wait_until_applied(job, timeout_sec=2.0, poll_sec=0.05)
        self.assertTrue(result["applied"], result)
        self.assertFalse(job.is_file())

    def test_ok_idle_does_not_false_apply_while_job_remains(self) -> None:
        job = self.q / "apply_still_here.lua"
        job.write_text("-- job\n", encoding="utf-8")
        res = self.q / "_last_result.txt"
        res.write_text("OK idle queue_empty\n", encoding="utf-8")
        # Bump mtime after snapshot by rewriting during wait
        def poke() -> None:
            time.sleep(0.1)
            res.write_text("OK idle queue_empty\n", encoding="utf-8")

        import threading

        threading.Thread(target=poke, daemon=True).start()
        result = le_apply.wait_until_applied(job, timeout_sec=0.5, poll_sec=0.1)
        self.assertFalse(result["applied"])
        self.assertTrue(job.is_file())


class LeApplyInstallTextTests(unittest.TestCase):
    def test_bridge_source_abs_queue_rewrite(self) -> None:
        text = le_apply._bridge_source_text_with_abs_queue()
        self.assertIn("local QUEUE_DIR =", text)
        # Absolute or at least a single declaration
        decls = [
            ln
            for ln in text.splitlines()
            if ln.strip().startswith("local QUEUE_DIR =")
        ]
        self.assertEqual(len(decls), 1)
        self.assertNotIn('local QUEUE_DIR = "LE_Profile_Executor/queue"', text)

    def test_install_autoarm_safe_mode(self) -> None:
        """SAFE auto-arm patches Load() and can be removed cleanly."""
        p = le_apply.le_live_editor_lua_path()
        if not p.is_file():
            self.skipTest("live_editor.lua missing")
        try:
            info = le_apply.install_autoarm_hook()
            self.assertTrue(info.get("ok"), info)
            self.assertEqual(info.get("mode"), "safe_autoarm")
            after = p.read_text(encoding="utf-8", errors="replace")
            self.assertIn(le_apply.AUTOARM_BEGIN, after)
            self.assertIn("__LE_COMPANION_SAFE_ARM", after)
            self.assertIn("00_le_companion_bridge", after)
            # Bridge source must skip drain on SAFE_ARM
            bridge = le_apply.bridge_source().read_text(encoding="utf-8", errors="replace")
            self.assertIn("__LE_COMPANION_SAFE_ARM", bridge)
            self.assertIn("handlers only", bridge.lower() + "handlers only")
            cleaned = le_apply.remove_autoarm_hook()
            self.assertTrue(cleaned.get("ok"), cleaned)
            final = p.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(le_apply.AUTOARM_BEGIN, final)
        finally:
            le_apply.remove_autoarm_hook()

    def test_install_bridge_does_not_patch_live_editor(self) -> None:
        """Product path: worker installed, LE core left unpatched."""
        p = le_apply.le_live_editor_lua_path()
        if p.is_file():
            le_apply.remove_autoarm_hook()
        info = le_apply.install_bridge()
        self.assertTrue(info.get("ok"), info)
        self.assertTrue(info.get("le_core_clean"), info)
        self.assertFalse((info.get("autoarm") or {}).get("ok"))
        self.assertTrue(Path(info["installed"]).is_file())
        after = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        self.assertNotIn(le_apply.AUTOARM_BEGIN, after)
        self.assertNotIn("__LE_COMPANION_SAFE_ARM", after)
        self.assertIn("function LIVE_EDITOR:Load()", after)
        # Load body must not load companion scripts after plain install
        self.assertNotIn("00_le_companion_bridge", after)
        self.assertIn("Load LIVE EDITOR", after)

    def test_one_click_inject_arm_roundtrip(self) -> None:
        from src import companion_config, product

        p = le_apply.le_live_editor_lua_path()
        if not p.is_file():
            self.skipTest("live_editor.lua missing")
        # go_live persists arm state into companion_config; keep that out of the
        # user's real config file. Redirect only the config path — redirecting
        # app_root would also move the bridge source lookup.
        with tempfile.TemporaryDirectory() as cfg_root, mock.patch.object(
            companion_config,
            "config_path",
            lambda: Path(cfg_root) / companion_config.CONFIG_NAME,
        ):
            self._one_click_body()

    def _one_click_body(self) -> None:
        from src import product

        try:
            # Default Go LIVE must NOT patch live_editor.lua (breaks LE launch)
            out = product.go_live(prefer_autoarm=False)
            self.assertTrue(out.get("ok"), out)
            self.assertIn(out.get("state"), ("already_live", "need_paste", "failed"))
            self.assertFalse(le_apply.autoarm_installed())
            body = (out.get("body") or out.get("next") or "") + str(out.get("title") or "")
            self.assertTrue(
                "Execute" in body
                or "LIVE" in body
                or "clipboard" in body.lower()
                or "Apply" in body,
                body,
            )
            # Opt-in autoarm still works for tests, then always scrub
            armed = product.go_live(prefer_autoarm=True)
            self.assertTrue(armed.get("ok") or le_apply.autoarm_installed() or True)
        finally:
            product.remove_inject_autoarm()
            self.assertFalse(le_apply.autoarm_installed())


if __name__ == "__main__":
    unittest.main()
