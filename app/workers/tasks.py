"""One durable run per task. Only run IDs cross the broker."""
import asyncio
import base64
import json
import logging
import math
import time
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_
from sqlalchemy.exc import OperationalError
from redis.asyncio import Redis
import httpx

from app.config import settings
from app.models.database import AsyncSessionLocal, ScreeningRunModel, engine
from app.run_audit import complete_run
from app.api.schemas import ScreeningResponseSchema
from app.orchestrator import run_end_to_end_screening_pipeline
from app.workers.celery_app import celery_app
from app.stage3_evaluation.llm_client import llm_client
from app.core.security import decrypt_payload
from app.core.security import decrypt_bytes
from app.models.database import CampaignCVModel, CampaignJDModel, CampaignModel, CampaignPairModel
from app.campaigns.persistence import finish_stage0
from app.campaigns.coordinator import dispatch_pairs, finalize_ready_jds, dispatch_stage3, finalize_campaign
from app.campaigns.provider_limit import admit, release, retry_delay
from app.stage3_evaluation.llm_client import ProviderRateLimited, evaluate_single_candidate_async
from app.stage3_evaluation.scoring import failed_evaluation
from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters
from app.stage1_rules.contracts import AttributeSource, AuthorizationStatus, resolve_hard_filters
from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence, extract_candidate_category_evidence_postgres)
from app.stage0_extraction.pipeline import ingest_pdf
logger = logging.getLogger("cv_screening")


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
                extraction_started = time.perf_counter()
                pdf = base64.b64decode(decrypt_payload(base64.b64decode(
                    request["encrypted_pdf"])).encode("ascii"))
                view = await asyncio.wait_for(asyncio.to_thread(ingest_pdf, pdf),
                                              timeout=settings.RUN_TIMEOUT_SECONDS)
                logger.info("pdf extraction", extra={"event": "stage_complete", "run_id": run_id,
                                                     "stage": "STAGE0_PDF",
                                                     "latency_ms": round((time.perf_counter() - extraction_started) * 1000, 2),
                                                     "count": 1})
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
            error_code = (str(exc) if isinstance(exc, NonRetryableRunError) else
                          "RUN_TIMEOUT" if isinstance(exc, asyncio.TimeoutError) else "WORKER_ERROR")
            logger.error("worker failed", extra={"event": "run_failed", "run_id": run_id,
                                                 "error_code": error_code})
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    run = (await db.execute(select(ScreeningRunModel).where(
                        ScreeningRunModel.id == run_id).with_for_update())).scalar_one()
                    if run.status != "COMPLETED" and run.lease_owner == owner:
                        run.status = "FAILED"
                        run.failure_code = error_code
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


async def execute_campaign_stage0(cv_id):
    """Claim one document version; completed redaction is reused on redelivery."""
    await engine.dispose()
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                cv = (await db.execute(select(CampaignCVModel).where(
                    CampaignCVModel.id == cv_id).with_for_update())).scalar_one_or_none()
                if cv is None or cv.stage0_status in {"SUCCEEDED", "FAILED"}:
                    return "ALREADY_COMPLETE"
                now = datetime.now(timezone.utc)
                if cv.stage0_status == "RUNNING" and _utc(cv.updated_at) > now - timedelta(seconds=settings.RUN_TIMEOUT_SECONDS + 35):
                    return "ALREADY_RUNNING"
                if cv.encrypted_pdf is None:
                    cv.stage0_status = "FAILED"
                    cv.extraction_error_code = "PDF_MISSING"
                    pairs = (await db.execute(select(CampaignPairModel).where(
                        CampaignPairModel.cv_id == cv.id).with_for_update())).scalars().all()
                    for pair in pairs:
                        if pair.status == "PENDING":
                            pair.status = "EXTRACTION_FAILED"
                            pair.failure_code = "PDF_MISSING"
                    return "PDF_MISSING"
                cv.stage0_status = "RUNNING"
                cv.updated_at = now
                campaign_id = cv.campaign_id
                candidate_id = cv.candidate_id
                encrypted = cv.encrypted_pdf
            owner_id = (await db.get(CampaignModel, campaign_id)).owner_id
        try:
            pdf = decrypt_bytes(encrypted)
            del encrypted
            view = await asyncio.wait_for(asyncio.to_thread(ingest_pdf, pdf),
                                          timeout=settings.RUN_TIMEOUT_SECONDS)
            del pdf
            async with AsyncSessionLocal() as db:
                if view.status == "success":
                    await finish_stage0(db, campaign_id=campaign_id, owner_id=owner_id,
                                        candidate_id=candidate_id, redacted_text=view.redacted_text,
                                        source_locations=view.pages)
                else:
                    await finish_stage0(db, campaign_id=campaign_id, owner_id=owner_id,
                                        candidate_id=candidate_id, error_code=view.code)
            return view.code
        except (asyncio.TimeoutError, TimeoutError, OperationalError):
            raise
        except Exception:
            async with AsyncSessionLocal() as db:
                await finish_stage0(db, campaign_id=campaign_id, owner_id=owner_id,
                                    candidate_id=candidate_id, error_code="PDF_PROCESSING_FAILED")
            return "PDF_PROCESSING_FAILED"
    finally:
        await engine.dispose()


@celery_app.task(name="campaign.stage0", bind=True, max_retries=settings.RUN_MAX_ATTEMPTS - 1)
def campaign_stage0_task(self, cv_id):
    try:
        return asyncio.run(execute_campaign_stage0(cv_id))
    except (asyncio.TimeoutError, TimeoutError, OperationalError) as exc:
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=min(60, 2 ** (self.request.retries + 1)))


@celery_app.task(name="campaign.recover_stage0")
def recover_campaign_stage0():
    async def find():
        await engine.dispose()
        try:
            async with AsyncSessionLocal() as db:
                stale = datetime.now(timezone.utc) - timedelta(seconds=settings.RUN_TIMEOUT_SECONDS + 35)
                rows = (await db.execute(select(CampaignCVModel.id).join(
                    CampaignModel, CampaignModel.id == CampaignCVModel.campaign_id).where(
                    CampaignModel.status == "RUNNING",
                    or_(CampaignCVModel.stage0_status == "PENDING",
                        (CampaignCVModel.stage0_status == "RUNNING") &
                        (CampaignCVModel.updated_at < stale))).limit(100))).scalars().all()
                return rows
        finally:
            await engine.dispose()
    for cv_id in asyncio.run(find()):
        campaign_stage0_task.delay(cv_id)


async def execute_campaign_pair(pair_id, lease_owner):
    """Use the stored redacted document and fence results from expired workers."""
    await engine.dispose()
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(CampaignPairModel, CampaignCVModel, CampaignJDModel).join(
                CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).join(
                CampaignJDModel, CampaignJDModel.id == CampaignPairModel.jd_id).where(
                CampaignPairModel.id == pair_id))).one_or_none()
            if row is None:
                return "NOT_FOUND"
            pair, cv, jd = row
            if pair.status != "RUNNING" or pair.lease_owner != lease_owner:
                return "LEASE_LOST"
            campaign_id = pair.campaign_id
            candidate_id = cv.candidate_id
            redacted_text = cv.redacted_text
            pages = cv.source_locations
            job = jd.job_snapshot
            job_id = jd.id
            document_version = cv.document_version
            await db.rollback()
        decision, details, evidence, error = None, None, None, None
        try:
            rules = resolve_hard_filters(job)
            details = evaluate_stage1_hard_filters(
                candidate_yoe=None, candidate_cv_text=redacted_text,
                work_authorized=AuthorizationStatus.UNKNOWN, jd_profile=rules,
                experience_source=AttributeSource.UNKNOWN,
                authorization_source=AttributeSource.UNKNOWN)
            details["input_provenance"] = {
                "reported_experience_source": "unknown",
                "reported_authorization_source": "unknown",
                "document_version": document_version}
            decision = details["status"]
            if decision in ("PASS", "REVIEW"):
                kwargs = dict(candidate_id=candidate_id, redacted_cv_text=redacted_text,
                              jd_category_queries=job["jd_category_queries"], source_pages=pages)
                if settings.STAGE2_BACKEND == "postgres":
                    evidence = await asyncio.wait_for(
                        extract_candidate_category_evidence_postgres(**kwargs, job_id=job_id),
                        timeout=settings.RUN_TIMEOUT_SECONDS)
                else:
                    evidence = await asyncio.wait_for(asyncio.to_thread(
                        extract_candidate_category_evidence, **kwargs),
                        timeout=settings.RUN_TIMEOUT_SECONDS)
                score = evidence.get("composite_score")
                if (evidence.get("candidate_id") != candidate_id or evidence.get("status") != "SUCCESS"
                        or isinstance(score, bool) or not isinstance(score, (int, float))
                        or not math.isfinite(score)):
                    error = "EVIDENCE_EXTRACTION_FAILED"
        except Exception:
            error = "PAIR_PROCESSING_FAILED"
        async with AsyncSessionLocal() as db:
            async with db.begin():
                pair = (await db.execute(select(CampaignPairModel).where(
                    CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                if pair.status != "RUNNING" or pair.lease_owner != lease_owner:
                    return "LEASE_LOST"
                pair.stage1_decision = decision
                pair.stage1_details = details
                pair.verification_reasons = ([dict(check) for check in details["checks"]
                    if check["status"] == "REVIEW"] if details else [])
                pair.verification_required = bool(pair.verification_reasons)
                if error:
                    # Retries are scheduled by control, preserving the original queue.
                    if pair.attempt_count < settings.RUN_MAX_ATTEMPTS:
                        pair.status = "PENDING"
                    else:
                        pair.status = "PROCESSING_FAILED"
                        pair.failure_code = error
                elif decision == "FAIL":
                    pair.status = "FILTER_REJECTED"
                else:
                    pair.status = "STAGE2_READY"
                    pair.stage2_score = evidence["composite_score"]
                    pair.result_snapshot = {"stage2_evidence": evidence,
                                            "document_version": document_version}
                pair.lease_owner = None
                pair.lease_until = None
                pair.updated_at = datetime.now(timezone.utc)
        try:
            campaign_coordinate_task.delay(campaign_id)
        except Exception:
            pass
        return "RETRY_PENDING" if error and pair.attempt_count < settings.RUN_MAX_ATTEMPTS else pair.status
    finally:
        await engine.dispose()


@celery_app.task(name="campaign.stage2_pair")
def campaign_stage2_pair_task(pair_id, lease_owner):
    return asyncio.run(execute_campaign_pair(pair_id, lease_owner))


async def coordinate_campaigns(campaign_id=None):
    await engine.dispose()
    try:
        async with AsyncSessionLocal() as db:
            if campaign_id is None:
                last_id = ""
                while True:
                    async with db.begin():
                        ids = (await db.execute(select(CampaignModel.id).where(
                            CampaignModel.status == "RUNNING", CampaignModel.id > last_id)
                            .order_by(CampaignModel.id).limit(100))).scalars().all()
                    if not ids:
                        break
                    for item_id in ids:
                        await coordinate_one_campaign(db, item_id)
                    last_id = ids[-1]
            else:
                await coordinate_one_campaign(db, campaign_id)
    finally:
        await engine.dispose()


async def coordinate_one_campaign(db, campaign_id):
    reserved = await dispatch_pairs(db, campaign_id)
    for pair_id, token in reserved:
        try:
            campaign_stage2_pair_task.delay(pair_id, token)
        except Exception:
            async with db.begin():
                pair = (await db.execute(select(CampaignPairModel).where(
                    CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                if pair.status == "RUNNING" and pair.lease_owner == token:
                    pair.status = "PENDING"
                    pair.lease_owner = None
                    pair.lease_until = None
                    pair.attempt_count -= 1
            logger.warning("retrieval enqueue failed", extra={
                "event": "campaign_enqueue_failed", "campaign_id": campaign_id})
    await finalize_ready_jds(db, campaign_id)
    claims = await dispatch_stage3(db, campaign_id)
    for pair_id, token in claims:
        try:
            campaign_stage3_pair_task.delay(pair_id, token)
        except Exception:
            async with db.begin():
                pair = (await db.execute(select(CampaignPairModel).where(
                    CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                if pair.status == "STAGE3_RUNNING" and pair.lease_owner == token:
                    pair.status = "SHORTLISTED"
                    pair.lease_owner = None
                    pair.lease_until = None
            logger.warning("evaluation enqueue failed", extra={
                "event": "campaign_enqueue_failed", "campaign_id": campaign_id})
    await finalize_campaign(db, campaign_id)


async def execute_campaign_stage3(pair_id, lease_owner):
    """Evaluate one persisted shortlist place, fenced against duplicate writes."""
    await engine.dispose()
    redis = Redis.from_url(settings.REDIS_URL)
    admission_token = None
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(CampaignPairModel, CampaignCVModel,
                CampaignJDModel).join(CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id)
                .join(CampaignJDModel, CampaignJDModel.id == CampaignPairModel.jd_id).where(
                CampaignPairModel.id == pair_id))).one_or_none()
            if row is None:
                return "NOT_FOUND"
            pair, cv, jd = row
            if pair.status != "STAGE3_RUNNING" or pair.lease_owner != lease_owner:
                return "LEASE_LOST"
            campaign_id, candidate_id = pair.campaign_id, cv.candidate_id
            evidence = (pair.result_snapshot or {}).get("stage2_evidence")
            job = jd.job_snapshot
            policy = jd.policy_snapshot
            await db.rollback()
        admission_token, wait = await admit(redis)
        if admission_token is None:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    pair = (await db.execute(select(CampaignPairModel).where(
                        CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                    if pair.status == "STAGE3_RUNNING" and pair.lease_owner == lease_owner:
                        pair.status = "SHORTLISTED"
                        pair.lease_owner = None
                        pair.lease_until = datetime.now(timezone.utc) + timedelta(seconds=wait)
            try:
                campaign_coordinate_task.apply_async(args=[campaign_id], countdown=wait)
            except Exception:
                pass  # Beat will recover the delayed claim.
            return "ADMISSION_DELAYED"
        async with AsyncSessionLocal() as db:
            async with db.begin():
                pair = (await db.execute(select(CampaignPairModel).where(
                    CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                if pair.status != "STAGE3_RUNNING" or pair.lease_owner != lease_owner:
                    return "LEASE_LOST"
                pair.stage3_attempt_count += 1
                attempt = pair.stage3_attempt_count
        try:
            if not evidence or evidence.get("candidate_id") != candidate_id:
                result = failed_evaluation(candidate_id, "INVALID_EVIDENCE", "Stored retrieval evidence is unavailable.")
            else:
                result = await evaluate_single_candidate_async(
                    evidence, job, max_retries=0, raise_rate_limits=True,
                    max_provider_attempts=1)
        except ProviderRateLimited as exc:
            delay = retry_delay(attempt, exc.retry_after)
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    pair = (await db.execute(select(CampaignPairModel).where(
                        CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                    if pair.status != "STAGE3_RUNNING" or pair.lease_owner != lease_owner:
                        return "LEASE_LOST"
                    pair.lease_owner = None
                    if attempt >= settings.CAMPAIGN_STAGE3_MAX_ATTEMPTS:
                        pair.status = "EVALUATION_FAILED"
                        pair.stage3_status = "EVALUATION_FAILED"
                        pair.failure_code = "PROVIDER_RATE_LIMIT_EXHAUSTED"
                        pair.lease_until = None
                    else:
                        pair.status = "SHORTLISTED"
                        pair.failure_code = "PROVIDER_RATE_LIMITED"
                        pair.lease_until = datetime.now(timezone.utc) + timedelta(seconds=delay)
            if attempt < settings.CAMPAIGN_STAGE3_MAX_ATTEMPTS:
                try:
                    campaign_coordinate_task.apply_async(args=[campaign_id], countdown=delay)
                except Exception:
                    pass  # Beat will recover the delayed claim.
            return "RATE_LIMITED"
        async with AsyncSessionLocal() as db:
            async with db.begin():
                pair = (await db.execute(select(CampaignPairModel).where(
                    CampaignPairModel.id == pair_id).with_for_update())).scalar_one()
                if pair.status != "STAGE3_RUNNING" or pair.lease_owner != lease_owner:
                    return "LEASE_LOST"
                pair.status = result.evaluation_status.value
                pair.stage3_status = result.evaluation_status.value
                pair.composite_score = result.composite_score
                pair.tier = result.tier.value if result.tier else None
                pair.failure_code = result.error_code
                pair.result_snapshot = {**(pair.result_snapshot or {}),
                    "stage3_evaluation": result.model_dump(mode="json"),
                    "stage3_policy": {"prompt_version": policy.get("prompt_version"),
                                      "scoring_policy_version": result.scoring_policy_version,
                                      "provider": settings.LLM_PROVIDER.lower()}}
                pair.lease_owner = None
                pair.lease_until = None
                pair.updated_at = datetime.now(timezone.utc)
        try:
            campaign_coordinate_task.delay(campaign_id)
        except Exception:
            pass
        return result.evaluation_status.value
    finally:
        if admission_token:
            await release(redis, admission_token)
        await redis.aclose()
        await llm_client.aclose()
        await engine.dispose()


@celery_app.task(name="campaign.stage3_pair")
def campaign_stage3_pair_task(pair_id, lease_owner):
    return asyncio.run(execute_campaign_stage3(pair_id, lease_owner))


@celery_app.task(name="campaign.coordinate")
def campaign_coordinate_task(campaign_id=None):
    return asyncio.run(coordinate_campaigns(campaign_id))
