"""Bounded pair dispatch and an atomic, per-JD Stage 2 barrier."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update

from app.config import settings
from app.models.database import CampaignCVModel, CampaignJDModel, CampaignModel, CampaignPairModel


DONE_BEFORE_CUTOFF = ("EXTRACTION_FAILED", "FILTER_REJECTED", "PROCESSING_FAILED",
                      "CUTOFF_EXCLUDED", "SHORTLISTED", "SUCCESS", "REVIEW_REQUIRED",
                      "EVALUATION_FAILED")


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
            rank = 0
            cursor = None
            while True:
                query = select(CampaignPairModel.id, CampaignPairModel.stage2_score,
                    CampaignCVModel.candidate_id).join(
                    CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).where(
                    CampaignPairModel.jd_id == jd_id,
                    CampaignPairModel.status == "STAGE2_READY")
                if cursor is not None:
                    score, candidate_id = cursor
                    query = query.where(or_(
                        CampaignPairModel.stage2_score < score,
                        and_(CampaignPairModel.stage2_score == score,
                             CampaignCVModel.candidate_id > candidate_id)))
                batch = (await db.execute(query.order_by(
                    CampaignPairModel.stage2_score.desc(), CampaignCVModel.candidate_id)
                    .limit(100))).all()
                if not batch:
                    break
                now = datetime.now(timezone.utc)
                updates = []
                for pair_id, score, candidate_id in batch:
                    rank += 1
                    updates.append({"id": pair_id, "stage2_rank": rank,
                        "status": "SHORTLISTED" if rank <= jd.stage3_cap else "CUTOFF_EXCLUDED",
                        "updated_at": now})
                await db.execute(update(CampaignPairModel), updates)
                cursor = batch[-1][1:]
            jd.status = "SHORTLISTED"
            jd.updated_at = datetime.now(timezone.utc)
            finalized.append(jd_id)
    return finalized
