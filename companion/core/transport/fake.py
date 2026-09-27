"""FakeTransport — the whole apply pipeline testable with no game, no LE, no files.

Asserts on the *contract*, not the drain: tests submit jobs, then call
``complete`` with a wire result, exactly as the Lua core would. Unlike v1's
``process_queue_protocol`` this is not a second drain implementation that can
drift — it never interprets ops at all.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ...domain.job import Job
from ...domain.outcome import ApplyOutcome, JobResult
from .v3 import Liveness, Pill


class FakeTransport:
    def __init__(self) -> None:
        self.submitted: list[Job] = []
        self._results: dict[str, JobResult] = {}
        self._liveness = Liveness(
            pill=Pill.ARMED,
            message="Ready. Jobs run on the next Career Mode tick.",
            armed=True,
            pid=4242,
            pid_alive=True,
            session_id="01FAKESESSION0000000000000",
            core_version="2.0.0",
            capabilities={"in_career": True, "save_ready": True, "hub_ready": True},
        )
        self.on_submit: Callable[[Job], None] | None = None

    # ---- BridgeTransport surface --------------------------------------

    def submit(self, job: Job) -> str:
        job.validate()
        self.submitted.append(job)
        if self.on_submit is not None:
            self.on_submit(job)
        return job.job_id

    def cancel(self, job_id: str) -> bool:
        for i, j in enumerate(self.submitted):
            if j.job_id == job_id and job_id not in self._results:
                del self.submitted[i]
                return True
        return False

    def clear_queue(self) -> int:
        pending = [j for j in self.submitted if j.job_id not in self._results]
        pending_ids = {j.job_id for j in pending}
        self.submitted = [j for j in self.submitted if j.job_id not in pending_ids]
        return len(pending_ids)

    def result(self, job_id: str) -> JobResult | None:
        return self._results.get(job_id)

    def await_result(self, job_id: str, *, timeout: float = 60.0, poll: float = 0.25,
                     on_tick: Any = None) -> JobResult:
        r = self._results.get(job_id)
        if r is not None:
            return r
        return JobResult.queued(job_id)

    def pending_ids(self) -> list[str]:
        return [j.job_id for j in self.submitted if j.job_id not in self._results]

    def claimed_ids(self) -> list[str]:
        return []

    def queue_depth(self) -> int:
        return len(self.pending_ids())

    def liveness(self) -> Liveness:
        return self._liveness

    def sweep_crashed(self) -> list[str]:
        return []

    def force_drain_snippet(self) -> str:
        return "-- fake"

    # ---- test controls -------------------------------------------------

    def set_liveness(self, liveness: Liveness) -> None:
        self._liveness = liveness

    def complete(self, job_id: str, result: JobResult | Mapping[str, Any]) -> None:
        """Deliver a result, as the Lua core would. Accepts wire dicts."""
        if not isinstance(result, JobResult):
            result = JobResult.from_wire({"job_id": job_id, **dict(result)})
        self._results[job_id] = result

    def complete_ok(self, job_id: str) -> None:
        self.complete(
            job_id,
            {"state": "done", "ok": True, "counts": {"ops_total": 1, "ops_ok": 1}},
        )

    def job(self, job_id: str) -> Job | None:
        for j in self.submitted:
            if j.job_id == job_id:
                return j
        return None
