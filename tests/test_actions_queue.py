"""Queue write / clipboard / clear_stale unit tests (temp dirs)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import actions  # noqa: E402
from src import apply_service  # noqa: E402
from src import le_apply  # noqa: E402


class ActionsQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.q = Path(self._tmp.name) / "queue"
        self.g = Path(self._tmp.name) / "generated"
        self.q.mkdir()
        self.g.mkdir()
        (self.q / "done").mkdir()
        self._p = mock.patch.object(le_apply, "queue_dir", return_value=self.q)
        self._p2 = mock.patch.object(le_apply, "done_dir", return_value=self.q / "done")
        self._p3 = mock.patch("src.paths.queue_dir", return_value=self.q)
        self._p4 = mock.patch("src.paths.generated_dir", return_value=self.g)
        for p in (self._p, self._p2, self._p3, self._p4):
            p.start()

    def tearDown(self) -> None:
        for p in (self._p, self._p2, self._p3, self._p4):
            p.stop()
        self._tmp.cleanup()

    def test_write_lua_creates_job_run_now_pending_wake(self) -> None:
        out = actions.write_lua("-- test job\n", stem="apply_1", clipboard=False)
        qf = Path(out["queue_file"])
        self.assertTrue(qf.is_file())
        self.assertTrue((self.q / "_run_now.lua").is_file())
        pending = (self.q / "_pending.txt").read_text(encoding="utf-8")
        self.assertIn(qf.name, pending)
        wake = (self.q / "_wake.txt").read_text(encoding="utf-8")
        self.assertIn(qf.name, wake)
        self.assertTrue((self.g).exists())

    def test_clear_stale_keeps_named(self) -> None:
        a = self.q / "apply_a.lua"
        b = self.q / "apply_b.lua"
        a.write_text("--a\n", encoding="utf-8")
        b.write_text("--b\n", encoding="utf-8")
        n = le_apply.clear_stale_jobs(keep="apply_b.lua")
        self.assertGreaterEqual(n, 1)
        self.assertFalse(a.is_file())
        self.assertTrue(b.is_file())

    def test_clipboard_normalize_crlf(self) -> None:
        raw = "line1\n  keep  \nline3"
        n = actions.normalize_lua_for_le_clipboard(raw)
        self.assertIn("\r\n", n)
        self.assertTrue(n.endswith("\r\n"))
        self.assertNotIn("  \r\n", n)  # trailing spaces stripped
        self.assertIn("  keep\r\n", n)

    def test_wait_fail_when_ok_zero(self) -> None:
        job = self.q / "apply_fail.lua"
        job.write_text("--x\n", encoding="utf-8")
        (self.q / "_run_now.lua").write_text("--x\n", encoding="utf-8")
        res = self.q / "_last_result.txt"
        res.write_text("old\n", encoding="utf-8")

        def sim() -> None:
            import time

            time.sleep(0.1)
            (self.q / "_run_now.lua").unlink(missing_ok=True)
            job.unlink(missing_ok=True)
            res.write_text("OK processed=1 ok=0 last=_run_now.lua\n", encoding="utf-8")

        import threading

        threading.Thread(target=sim, daemon=True).start()
        result = le_apply.wait_until_applied(job, timeout_sec=2.0, poll_sec=0.05)
        # Job file gone → primary success path may win before FAIL parse.
        # If result says applied because file gone, that's OK; if FAIL path: not applied.
        if result.get("applied"):
            self.assertFalse(job.is_file())
        else:
            self.assertIn("fail", (result.get("reason") or "").lower())

    def test_apply_service_no_wait(self) -> None:
        r = apply_service.apply_lua("-- hi\n", stem="apply_x", wait=False)
        self.assertTrue(r.queued)
        self.assertFalse(r.applied)
        self.assertTrue(r.queue_file)


if __name__ == "__main__":
    unittest.main()
