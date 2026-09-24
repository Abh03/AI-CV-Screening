"""One durable run per task. Only run IDs cross the broker."""
import asyncio
import base64
import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_
from sqlalchemy.exc import OperationalError
import httpx

from app.config import settings
from app.models.database import AsyncSessionLocal, ScreeningRunModel, engine
from app.run_audit import complete_run
from app.api.schemas import ScreeningResponseSchema
from app.orchestrator import run_end_to_end_screening_pipeline
from app.workers.celery_app import celery_app
from app.stage3_evaluation.llm_client import llm_client
from app.core.security import decrypt_payload
from app.stage0_extraction.pipeline import ingest_pdf


class NonRetryableRunError(Exception):
    pass


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


async def execute_run(run_id):
    """Claim a queued or expired run, then finalize it atomically."""
    await engine.dispose()
    owner = str(uuid4())
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                run = (await db.execute(select(ScreeningRunModel).where(
                    ScreeningRunModel.id == run_id).with_for_update())).scalar_one_or_none()
                if run is None or run.status == "COMPLETED":
                    return "ALREADY_COMPLETE"
                if run.status == "RUNNING" and _utc(run.lease_until) and _utc(run.lease_until) > datetime.now(timezone.utc):
                    return "ALREADY_RUNNING"
                if run.status not in {"QUEUED", "RUNNING", "FAILED"} or not run.request_snapshot:
                    return "NOT_RUNNABLE"
                if run.attempt_count >= settings.RUN_MAX_ATTEMPTS:
                    run.status = "FAILED"
                    run.failure_code = "ATTEMPTS_EXHAUSTED"
                    return "ATTEMPTS_EXHAUSTED"
                run.status = "RUNNING"
                run.attempt_count += 1
                run.lease_until = datetime.now(timezone.utc) + timedelta(seconds=settings.RUN_TIMEOUT_SECONDS + 35)
                run.lease_owner = owner
                run.failure_code = None
                request = run.request_snapshot
                job = run.job_snapshot
                cutoff = run.policy_snapshot["stage2_cutoff"]
        try:
            deadline = asyncio.get_running_loop().time() + settings.RUN_TIMEOUT_SECONDS
            stage0_views = None
            if request.get("kind") == "text":
                request = {"candidates": json.loads(decrypt_payload(base64.b64decode(
                    request["encrypted_candidates"])))}
            if request.get("kind") == "pdf":
                pdf = base64.b64decode(decrypt_payload(base64.b64decode(
                    request["encrypted_pdf"])).encode("ascii"))
                view = await asyncio.wait_for(asyncio.to_thread(ingest_pdf, pdf),
                                              timeout=settings.RUN_TIMEOUT_SECONDS)
                del pdf
                if view.status != "success":
                    raise NonRetryableRunError(view.code)
                candidate = dict(request["attributes"], raw_cv_text=view.redacted_text)
                request = {"candidates": [candidate]}
                stage0_views = {candidate["candidate_id"]: view}
            result = await asyncio.wait_for(run_end_to_end_screening_pipeline(
                raw_candidates=request["candidates"], jd_profile=job,
                hard_filter_rules=job.get("hard_filter_rules"), top_n_stage2_cutoff=cutoff,
                llm_concurrency_limit=settings.LLM_CONCURRENCY_LIMIT,
                stage0_views=stage0_views),
                timeout=max(0.001, deadline - asyncio.get_running_loop().time()))
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    run = (await db.execute(select(ScreeningRunModel).where(
                        ScreeningRunModel.id == run_id).with_for_update())).scalar_one()
                    if run.status == "COMPLETED":
                        return "ALREADY_COMPLETE"
                    if run.status != "RUNNING" or run.lease_owner != owner:
                        return "LEASE_LOST"
                    response = ScreeningResponseSchema(
                        run_id=run.id, idempotency_key=run.idempotency_key, job_id=run.job_id,
                        metrics=result["metrics"], leaderboard=result["leaderboard"],
                        rejected_candidates=result["rejected_candidates"],
                        review_candidates=result["review_candidates"], failed_candidates=result["failed_candidates"])
                    run.request_snapshot = None
                    run.lease_until = None
                    run.lease_owner = None
                    await complete_run(db, run, result,
                                       [c["candidate_id"] for c in request["candidates"]],
                                       response.model_dump(mode="json"))
            return "COMPLETED"
        except Exception as exc:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    run = (await db.execute(select(ScreeningRunModel).where(
                        ScreeningRunModel.id == run_id).with_for_update())).scalar_one()
                    if run.status != "COMPLETED" and run.lease_owner == owner:
                        run.status = "FAILED"
                        run.failure_code = (str(exc) if isinstance(exc, NonRetryableRunError) else
                                            "RUN_TIMEOUT" if isinstance(exc, asyncio.TimeoutError) else "WORKER_ERROR")
                        if isinstance(exc, NonRetryableRunError):
                            run.request_snapshot = None
                        run.lease_until = None
                        run.lease_owner = None
            raise
    finally:
        await llm_client.aclose()
        await engine.dispose()


@celery_app.task(name="screening.execute_run", bind=True, max_retries=settings.RUN_MAX_ATTEMPTS - 1)
def screening_task(self, run_id):
    try:
        return asyncio.run(execute_run(run_id))
    except Exception as exc:
        retryable = isinstance(exc, (asyncio.TimeoutError, TimeoutError, OperationalError,
                                     httpx.TimeoutException, httpx.TransportError, ConnectionError))
        if not retryable or self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=min(60, 2 ** (self.request.retries + 1)))


async def find_recoverable_runs():
    await engine.dispose()
    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            exhausted = (await db.execute(select(ScreeningRunModel).where(
                ScreeningRunModel.status == "RUNNING",
                ScreeningRunModel.lease_until < now,
                ScreeningRunModel.attempt_count >= settings.RUN_MAX_ATTEMPTS))).scalars().all()
            for run in exhausted:
                run.status = "FAILED"
                run.failure_code = "WORKER_INTERRUPTED"
                run.lease_owner = None
                run.lease_until = None
            await db.commit()
            rows = (await db.execute(select(ScreeningRunModel.id).where(
                ScreeningRunModel.request_snapshot.is_not(None),
                ScreeningRunModel.attempt_count < settings.RUN_MAX_ATTEMPTS,
                or_((ScreeningRunModel.status == "QUEUED") &
                    (ScreeningRunModel.lease_until < now),
                    (ScreeningRunModel.status == "RUNNING") &
                    (ScreeningRunModel.lease_until < now))))).scalars().all()
            for run_id in rows:
                run = await db.get(ScreeningRunModel, run_id)
                if run.status == "QUEUED":
                    run.lease_until = now + timedelta(seconds=settings.QUEUE_RECOVERY_SECONDS)
            await db.commit()
            return rows
    finally:
        await engine.dispose()


@celery_app.task(name="screening.recover_runs")
def recover_runs():
    for run_id in asyncio.run(find_recoverable_runs()):
        screening_task.delay(run_id)
