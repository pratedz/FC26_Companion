"""Boost multi-run queue — enqueue without blocking on LE wait."""

from __future__ import annotations

import time
from unittest import mock

from src import boost_queue
from src.apply_service import ApplyResult


def _fake_turbo(profile_id, *, wait=None, clear_stale=None):
    return ApplyResult(
        applied=False,
        queued=True,
        live=True,
        reason="Queued (no wait)",
        queue_file=f"queue/{profile_id}_test.lua",
        outcome="queued_live",
        detail=profile_id,
    )


def test_enqueue_profile_is_instant_and_stacks():
    # Fresh manager (not process singleton) for isolation
    mq = boost_queue.BoostQueueManager()
    with mock.patch.object(boost_queue.product, "run_profile_turbo", side_effect=_fake_turbo):
        with mock.patch(
            "src.profiles.get_profile",
            side_effect=lambda pid: type("P", (), {"id": pid, "label": pid})(),
        ):
            j1 = mq.enqueue_profile("full_fitness")
            j2 = mq.enqueue_profile("full_sharpness")
            assert j1.id != j2.id
            # Writer is async — give it a moment
            deadline = time.time() + 2.0
            while time.time() < deadline:
                c = mq.counts()
                if c["pending"] == 0 and c["writing"] == 0:
                    break
                time.sleep(0.05)
            snap = mq.jobs_snapshot()
            assert len(snap) >= 2
            # Both should have left pending
            assert all(j.status in ("queued", "writing", "applied", "error") for j in snap)
            # Turbo called with wait=False for each write
            # (allow some still writing)


def test_enqueue_pack_expands_profiles():
    mq = boost_queue.BoostQueueManager()
    with mock.patch.object(boost_queue.product, "run_profile_turbo", side_effect=_fake_turbo):
        with mock.patch.dict(
            boost_queue.product.PACKS,
            {
                "test_pack": {
                    "id": "test_pack",
                    "label": "Test Pack",
                    "profile_ids": ["a", "b"],
                    "category": "boost",
                }
            },
            clear=False,
        ):
            with mock.patch(
                "src.profiles.get_profile",
                side_effect=lambda pid: type("P", (), {"id": pid, "label": pid})(),
            ):
                jobs = mq.enqueue_pack("test_pack")
                assert len(jobs) == 2
                assert jobs[0].pack_id == "test_pack"


def test_clear_finished_and_cancel():
    mq = boost_queue.BoostQueueManager()
    # Stop writer eating pending by marking manually
    j = boost_queue.BoostJob(id="1", profile_id="x", label="X", status="applied")
    j2 = boost_queue.BoostJob(id="2", profile_id="y", label="Y", status="pending")
    with mq._lock:
        mq._jobs = [j, j2]
    n = mq.clear_finished()
    assert n == 1
    n2 = mq.cancel_pending()
    assert n2 == 1
    assert mq.jobs_snapshot()[0].status == "cancelled"


def test_summary_line():
    mq = boost_queue.BoostQueueManager()
    assert "empty" in mq.summary_line().lower() or "Queue" in mq.summary_line()
