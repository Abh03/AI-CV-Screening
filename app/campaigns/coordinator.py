"""Bounded pair dispatch and an atomic, per-JD Stage 2 barrier."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.config import settings
from app.models.database import CampaignCVModel, CampaignJDModel, CampaignModel, CampaignPairModel
from app.stage2_retrieval.coverage import candidate_sort_key, eligible, MAX_CANDIDATES_PER_JD


DONE_BEFORE_CUTOFF = ("EXTRACTION_FAILED", "FILTER_REJECTED", "PROCESSING_FAILED",
                      "CUTOFF_EXCLUDED", "SHORTLISTED", "SUCCESS", "REVIEW_REQUIRED",
                      "EVALUATION_FAILED")
TERMINAL = ("EXTRACTION_FAILED", "FILTER_REJECTED", "PROCESSING_FAILED",
            "CUTOFF_EXCLUDED", "SUCCESS", "REVIEW_REQUIRED", "EVALUATION_FAILED")


async def dispatch_pairs(db, campaign_id: str):
    """Reserve at most the available retrieval slots; return (pair ID, lease token)."""
    async with db.begin():
        # Serialize admission across control workers on PostgreSQL.
        if db.bind.dialect.name == "postgresql":
            await db.execute(select(func.pg_advisory_xact_lock(410274, 4)))
        campaign = (await db.execute(select(CampaignModel).where(
            CampaignModel.id == campaign_id).with_for_update())).scalar_one_or_none()
        if campaign is None or campaign.status != "RUNNING":
            return []
        now = datetime.now(timezone.utc)
        expired = (await db.execute(select(CampaignPairModel).where(
            CampaignPairModel.campaign_id == campaign_id,
            CampaignPairModel.status == "RUNNING",
            CampaignPairModel.lease_until < now).with_for_update())).scalars().all()
        for pair in expired:
            pair.lease_owner = None
            pair.lease_until = None
            if pair.attempt_count >= settings.RUN_MAX_ATTEMPTS:
                pair.status = "PROCESSING_FAILED"
                pair.failure_code = "RETRIEVAL_ATTEMPTS_EXHAUSTED"
            else:
                pair.status = "PENDING"
        running = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
            CampaignPairModel.campaign_id == campaign_id,
            CampaignPairModel.status == "RUNNING"))).scalar_one()
        global_running = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
            CampaignPairModel.status == "RUNNING"))).scalar_one()
        slots = min(settings.CAMPAIGN_RETRIEVAL_INFLIGHT - running,
                    settings.CAMPAIGN_RETRIEVAL_GLOBAL_INFLIGHT - global_running,
                    settings.CAMPAIGN_DISPATCH_BATCH)
        if slots <= 0:
            return []
        rows = (await db.execute(select(CampaignPairModel).join(
            CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).join(
            CampaignJDModel, CampaignJDModel.id == CampaignPairModel.jd_id).where(
            CampaignPairModel.campaign_id == campaign_id,
            CampaignPairModel.status == "PENDING",
            CampaignCVModel.stage0_status == "SUCCEEDED",
            CampaignJDModel.status.in_(("PENDING", "PROCESSING")))
            .order_by(CampaignJDModel.jd_key, CampaignCVModel.candidate_id)
            .limit(slots).with_for_update(skip_locked=True))).scalars().all()
        reserved = []
        for pair in rows:
            token = str(uuid4())
            pair.status = "RUNNING"
            pair.lease_owner = token
            pair.lease_until = now + timedelta(seconds=settings.RUN_TIMEOUT_SECONDS + 35)
            pair.attempt_count += 1
            pair.updated_at = now
            reserved.append((pair.id, token))
        return reserved


async def finalize_ready_jds(db, campaign_id: str):
    """Commit all ranks for a JD together after its complete pool is terminal."""
    finalized = []
    async with db.begin():
        jd_ids = (await db.execute(select(CampaignJDModel.id).where(
            CampaignJDModel.campaign_id == campaign_id,
            CampaignJDModel.status.in_(("PENDING", "PROCESSING"))))).scalars().all()
    for jd_id in jd_ids:
        async with db.begin():
            jd = (await db.execute(select(CampaignJDModel).where(
                CampaignJDModel.id == jd_id).with_for_update())).scalar_one()
            if jd.status not in ("PENDING", "PROCESSING"):
                continue
            unresolved = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
                CampaignPairModel.jd_id == jd_id,
                CampaignPairModel.status.not_in(DONE_BEFORE_CUTOFF + ("STAGE2_READY",))))).scalar_one()
            if unresolved:
                if jd.status == "PENDING":
                    jd.status = "PROCESSING"
                continue
            # Pool size is bounded by archive intake. Use the same evidence-aware
            # ordering and eligibility policy as the synchronous pipeline.
            rows = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id).join(
                CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).where(
                CampaignPairModel.jd_id == jd_id,
                CampaignPairModel.status == "STAGE2_READY"))).all()
            def payload(row):
                pair, identifier = row
                return {**(pair.result_snapshot or {}).get("stage2_evidence", {}),
                        "candidate_id": identifier, "composite_score": pair.stage2_score}
            rows = sorted(rows, key=lambda row: candidate_sort_key(payload(row)))
            selected = 0
            now = datetime.now(timezone.utc)
            for rank, row in enumerate(rows, 1):
                pair, identifier = row
                evidence = payload(row)
                pair.stage2_rank = rank
                if eligible(evidence) and selected < min(jd.stage3_cap, MAX_CANDIDATES_PER_JD):
                    pair.status = "SHORTLISTED"
                    selected += 1
                elif not eligible(evidence):
                    pair.status = "REVIEW_REQUIRED"
                    pair.verification_required = True
                    pair.failure_code = evidence.get("relevance_reason") or "STAGE2_SCORING_REPLAY_REQUIRED"
                    pair.result_snapshot = {**(pair.result_snapshot or {}),
                        "candidate_id": identifier, "evaluation_status": "REVIEW_REQUIRED",
                        "stage": "STAGE2", "reason": pair.failure_code}
                else:
                    pair.status = "CUTOFF_EXCLUDED"
                pair.updated_at = now
            jd.status = "SHORTLISTED"
            jd.updated_at = datetime.now(timezone.utc)
            finalized.append(jd_id)
    return finalized


async def dispatch_stage3(db, campaign_id: str):
    """Claim shortlisted pairs with fenced leases; delayed retries stay parked."""
    async with db.begin():
        if db.bind.dialect.name == "postgresql":
            await db.execute(select(func.pg_advisory_xact_lock(410274, 5)))
        campaign = (await db.execute(select(CampaignModel).where(
            CampaignModel.id == campaign_id).with_for_update())).scalar_one_or_none()
        if campaign is None or campaign.status != "RUNNING":
            return []
        now = datetime.now(timezone.utc)
        expired = (await db.execute(select(CampaignPairModel).where(
            CampaignPairModel.campaign_id == campaign_id,
            CampaignPairModel.status == "STAGE3_RUNNING",
            CampaignPairModel.lease_until < now).with_for_update())).scalars().all()
        for pair in expired:
            pair.lease_owner = None
            pair.lease_until = None
            if pair.stage3_attempt_count >= settings.CAMPAIGN_STAGE3_MAX_ATTEMPTS:
                pair.status = "EVALUATION_FAILED"
                pair.stage3_status = "EVALUATION_FAILED"
                pair.failure_code = "STAGE3_ATTEMPTS_EXHAUSTED"
            else:
                pair.status = "SHORTLISTED"
        running = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
            CampaignPairModel.status == "STAGE3_RUNNING"))).scalar_one()
        slots = min(settings.CAMPAIGN_STAGE3_GLOBAL_INFLIGHT - running,
                    settings.CAMPAIGN_DISPATCH_BATCH)
        if slots <= 0:
            return []
        rows = (await db.execute(select(CampaignPairModel).join(
            CampaignJDModel, CampaignJDModel.id == CampaignPairModel.jd_id).where(
            CampaignPairModel.campaign_id == campaign_id,
            CampaignPairModel.status == "SHORTLISTED",
            or_(CampaignPairModel.lease_until.is_(None), CampaignPairModel.lease_until <= now),
            CampaignJDModel.status == "SHORTLISTED")
            .order_by(CampaignJDModel.jd_key, CampaignPairModel.stage2_rank)
            .limit(slots).with_for_update(skip_locked=True))).scalars().all()
        claims = []
        for pair in rows:
            token = str(uuid4())
            pair.status = "STAGE3_RUNNING"
            pair.lease_owner = token
            pair.lease_until = now + timedelta(seconds=settings.RUN_TIMEOUT_SECONDS + 35)
            pair.updated_at = now
            claims.append((pair.id, token))
        return claims


async def finalize_campaign(db, campaign_id: str):
    """Complete each JD and the campaign only after every pair is terminal."""
    async with db.begin():
        campaign = (await db.execute(select(CampaignModel).where(
            CampaignModel.id == campaign_id).with_for_update())).scalar_one_or_none()
        if campaign is None or campaign.status != "RUNNING":
            return False
        jds = (await db.execute(select(CampaignJDModel).where(
            CampaignJDModel.campaign_id == campaign_id).with_for_update())).scalars().all()
        for jd in jds:
            if jd.status != "SHORTLISTED":
                continue
            unresolved = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
                CampaignPairModel.jd_id == jd.id,
                CampaignPairModel.status.not_in(TERMINAL)))).scalar_one()
            if not unresolved:
                jd.status = "COMPLETED"
                jd.updated_at = datetime.now(timezone.utc)
        await db.flush()
        remaining = (await db.execute(select(func.count()).select_from(CampaignJDModel).where(
            CampaignJDModel.campaign_id == campaign_id,
            CampaignJDModel.status != "COMPLETED"))).scalar_one()
        if remaining == 0 and jds:
            campaign.status = "COMPLETED"
            campaign.completed_at = datetime.now(timezone.utc)
            campaign.updated_at = campaign.completed_at
            return True
        return False
