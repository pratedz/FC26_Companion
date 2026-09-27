"""Apply a SquadPlan through the normal submit_job path (never from Grok)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from ...domain.job import Job
from ...domain.outcome import ApplyOutcome, JobResult
from ...domain.squad_plan import SquadPlan, build_apply_jobs
from .. import events as E
from ..services import Services
from .apply import FOLLOW_SECONDS, ApplyError, submit_job


def apply_squad_plan(
    svc: Services,
    plan: SquadPlan,
    *,
    dry_run: bool = False,
    follow: bool = True,
    on_result: Callable[[JobResult], None] | None = None,
    confirm_token: str = "",
) -> str:
    """Queue one multi-player job. Requires confirm_token == APPLY when N>1."""
    if plan.player_count < 1:
        raise ApplyError("Select players first.")
    if plan.player_count > 1 and (confirm_token or "").strip() != "APPLY":
        raise ApplyError(
            f"Type APPLY to confirm a plan for {plan.player_count} players "
            f"({plan.field_count} fields)."
        )
    state = svc.store.snapshot()
    growth = "auto" if state.prefs.growth_mirror else "off"
    try:
        jobs = build_apply_jobs(
            plan,
            growth_mirror=growth,
            dry_run=dry_run,
            label=f"Squad plan · {plan.blast_summary()}",
        )
    except Exception as exc:  # noqa: BLE001
        raise ApplyError(str(exc)) from exc
    if state.squad.save_uid:
        jobs = tuple(replace(job, requires={**job.requires, "save_uid": state.squad.save_uid})
                     for job in jobs)
    if len(jobs) == 1:
        job = jobs[0]
        if not dry_run:
            snapshot_dir = svc.paths.root / "snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            job = replace(
                job,
                snapshot_to=(snapshot_dir / f"{job.job_id}.json").resolve().as_posix(),
            )
        return submit_job(svc, job, follow=follow, on_result=on_result)
    return _submit_plan_parts(
        svc, jobs, dry_run=dry_run, follow=follow, on_result=on_result
    )


def confirm_squad_apply(typed: str) -> bool:
    return (typed or "").strip() == "APPLY"


def _submit_plan_parts(
    svc: Services,
    jobs: tuple[Job, ...] | list[Job],
    *,
    dry_run: bool,
    follow: bool,
    on_result: Callable[[JobResult], None] | None,
) -> str:
    """Run plan parts in order. Part 2 starts only after part 1 is applied."""
    parts = list(jobs)
    if not dry_run:
        snapshot_dir = svc.paths.root / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        parts[0] = replace(
            parts[0],
            snapshot_to=(snapshot_dir / f"{parts[0].job_id}.json").resolve().as_posix(),
        )

    progress: dict[str, Any] = {"part": 1, "job": parts[0]}

    def run_parts(token: Any) -> None:
        from .apply import _is_write_job, _persist_result, _status_line

        total = len(parts)
        for index, job in enumerate(parts, start=1):
            progress.update(part=index, job=job)
            if getattr(token, "cancelled", False):
                return
            job.validate()
            job_id = svc.transport.submit(job)
            if svc.db is not None:
                svc.db.record_submitted(
                    job_id=job_id,
                    job=job.to_wire(),
                    origin=job.origin,
                    label=job.label,
                    created_utc=svc.clock.stamp(),
                    submitted_utc=svc.clock.stamp(),
                )
            svc.store.dispatch(
                E.JobSubmitted(job_id=job_id, label=job.label, at=svc.clock.now())
            )
            if svc.log is not None:
                svc.log.info("job submitted", extra={"job_id": job_id, "label": job.label})
            svc.store.dispatch(
                E.StatusSet(f"Applying squad plan, part {index} of {total}.")
            )
            if not follow:
                return
            while True:
                if getattr(token, "cancelled", False):
                    return
                result = svc.transport.await_result(job_id, timeout=FOLLOW_SECONDS)
                if result.outcome.is_terminal:
                    break
                # Queue clear / missing-file reconciliation can finish a job
                # locally without writing a worker result file.
                recorded = next((v.result for v in svc.store.snapshot().jobs.history
                                 if v.job_id == job_id), None)
                if recorded is not None and recorded.outcome is ApplyOutcome.EXPIRED:
                    result = recorded
                    break
                # The 45-second follow is a progress boundary, not a failed
                # part. Keep the chain alive until this part really finishes.
                def waiting(result: JobResult = result, job_id: str = job_id,
                            part: int = index) -> None:
                    if job_id not in svc.store.snapshot().jobs.active:
                        return
                    svc.store.dispatch(E.JobProgressed(job_id=job_id, result=result))
                    svc.store.dispatch(E.StatusSet(
                        f"Squad plan part {part} of {total} is waiting for Career Mode. Open a Career menu to continue."
                    ))
                    if on_result is not None:
                        on_result(result)
                if getattr(svc, "executor", None) is not None:
                    svc.executor.on_ui_thread(waiting)
                else:
                    waiting()
            captured = result
            finished_id = job_id
            part_job = job

            def publish(
                outcome: ApplyOutcome = captured.outcome,
                result: JobResult = captured,
                job_id: str = finished_id,
                job: Job = part_job,
                part: int = index,
            ) -> None:
                if outcome.is_terminal:
                    svc.store.dispatch(E.JobFinished(job_id=job_id, result=result))
                    _persist_result(svc, result)
                else:
                    svc.store.dispatch(E.JobProgressed(job_id=job_id, result=result))
                if outcome.is_success and part < total:
                    svc.store.dispatch(
                        E.StatusSet(f"Squad plan part {part} of {total} applied.")
                    )
                else:
                    svc.store.dispatch(E.StatusSet(_status_line(result)))
                if outcome is ApplyOutcome.APPLIED and _is_write_job(job):
                    from .squad import invalidate

                    invalidate(svc, reason=f"{job.label or job_id} applied")
                if on_result is not None and (not outcome.is_success or part == total):
                    on_result(result)

            if getattr(svc, "executor", None) is not None and hasattr(svc.executor, "on_ui_thread"):
                svc.executor.on_ui_thread(publish)
            else:
                publish()
            if not captured.outcome.is_success:
                return

    def work(token: Any) -> None:
        try:
            run_parts(token)
        except Exception as exc:  # a worker Future must not swallow UI completion
            job = progress["job"]
            message = f"Squad plan stopped at part {progress['part']} of {len(parts)}: {exc}"
            result = JobResult.from_wire({
                "job_id": job.job_id, "label": job.label, "state": "failed", "ok": False,
                "diagnostic": message, "error": {"message": message, "phase": "companion"},
            })
            def failed() -> None:
                from .apply import _persist_result
                svc.store.dispatch(E.JobFinished(job_id=job.job_id, result=result))
                _persist_result(svc, result)
                svc.store.dispatch(E.StatusSet(message))
                if on_result is not None:
                    on_result(result)
            if getattr(svc, "executor", None) is not None:
                svc.executor.on_ui_thread(failed)
            else:
                failed()

    if follow and getattr(svc, "executor", None) is not None and hasattr(svc.executor, "submit"):
        svc.executor.submit(f"squad.plan.parts:{parts[0].job_id}", work)
    else:
        work(type("T", (), {"cancelled": False})())
    return parts[0].job_id
