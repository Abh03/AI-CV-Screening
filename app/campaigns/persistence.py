"""Campaign reservations and owner-scoped reads.

Raw PDFs may exist only as encrypted_pdf while Stage 0 is pending. Both Stage 0
terminal paths clear it. Redacted text, source locations and decisions are
retained as sensitive data and must be served only through owner-scoped APIs.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.security import encrypt_payload
from app.models.database import CampaignModel, CampaignJDModel, CampaignCVModel, CampaignPairModel


TERMINAL_PAIR_STATUSES = frozenset({
    "EXTRACTION_FAILED", "FILTER_REJECTED", "PROCESSING_FAILED", "CUTOFF_EXCLUDED",
    "SUCCESS", "REVIEW_REQUIRED", "EVALUATION_FAILED",
})


def _now():
    return datetime.now(timezone.utc)


async def get_campaign(db, campaign_id: str, owner_id: str):
    campaign = await db.get(CampaignModel, campaign_id)
    if campaign is None or campaign.owner_id != owner_id:
        raise PermissionError("Campaign not found or access denied")
    return campaign


async def reserve_campaign(db, *, owner_id: str, request_hash: str, job_snapshots: list[dict],
                           policy_snapshots: list[dict], idempotency_key: str | None = None,
                           stage3_cap: int = 30):
    """Reserve an immutable JD set. Returns (created, campaign)."""
    if not job_snapshots or len(job_snapshots) != len(policy_snapshots):
        raise ValueError("A campaign needs at least one JD and one policy per JD")
    keys = [item["job_id"] for item in job_snapshots]
    if len(set(keys)) != len(keys) or not 1 <= stage3_cap <= 30:
        raise ValueError("JD IDs must be distinct and Stage 3 cap must be between 1 and 30")
    if idempotency_key:
        existing = (await db.execute(select(CampaignModel).where(
            CampaignModel.owner_id == owner_id,
            CampaignModel.idempotency_key == idempotency_key))).scalar_one_or_none()
        if existing:
            if existing.request_hash != request_hash:
                raise ValueError("Idempotency key belongs to a different campaign request")
            return False, existing
    campaign = CampaignModel(id=str(uuid4()), owner_id=owner_id, request_hash=request_hash,
                             idempotency_key=idempotency_key, status="INTAKE")
    db.add(campaign)
    try:
        await db.flush()
        for job, policy in zip(job_snapshots, policy_snapshots):
            db.add(CampaignJDModel(id=str(uuid4()), campaign_id=campaign.id, jd_key=job["job_id"],
                                   job_snapshot=job, policy_snapshot=policy, stage3_cap=stage3_cap,
                                   status="PENDING"))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing = (await db.execute(select(CampaignModel).where(
                CampaignModel.owner_id == owner_id,
                CampaignModel.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing and existing.request_hash == request_hash:
                return False, existing
        raise
    return True, campaign


async def reserve_cv(db, *, campaign_id: str, owner_id: str, candidate_id: str,
                     source_filename: str, content_hash: str, pdf_bytes: bytes | None = None):
    """Reserve one named candidate and all JD pairs in a single transaction.

    Matching hashes do not merge candidates. A retry of the same candidate ID
    must present the same filename and hash to reuse the reservation.
    """
    campaign = await get_campaign(db, campaign_id, owner_id)
    if campaign.status != "INTAKE":
        raise ValueError("Campaign intake is closed")
    existing = (await db.execute(select(CampaignCVModel).where(
        CampaignCVModel.campaign_id == campaign_id,
        CampaignCVModel.candidate_id == candidate_id))).scalar_one_or_none()
    if existing:
        if existing.content_hash != content_hash or existing.source_filename != source_filename:
            raise ValueError("Candidate ID belongs to a different document")
        return False, existing
    cvs = CampaignCVModel(id=str(uuid4()), campaign_id=campaign_id, candidate_id=candidate_id,
                          source_filename=source_filename, content_hash=content_hash,
                          document_version=1, stage0_status="PENDING",
                          encrypted_pdf=encrypt_payload(pdf_bytes) if pdf_bytes is not None else None)
    db.add(cvs)
    try:
        await db.flush()
        jds = (await db.execute(select(CampaignJDModel.id).where(
            CampaignJDModel.campaign_id == campaign_id))).scalars().all()
        for jd_id in jds:
            db.add(CampaignPairModel(id=str(uuid4()), campaign_id=campaign_id,
                                     jd_id=jd_id, cv_id=cvs.id, status="PENDING",
                                     verification_required=False, verification_reasons=[], attempt_count=0))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(CampaignCVModel).where(
            CampaignCVModel.campaign_id == campaign_id,
            CampaignCVModel.candidate_id == candidate_id))).scalar_one_or_none()
        if existing and existing.content_hash == content_hash and existing.source_filename == source_filename:
            return False, existing
        raise
    return True, cvs


async def stage_pdf(db, *, campaign_id: str, owner_id: str, candidate_id: str, pdf_bytes: bytes):
    """Encrypt pending raw bytes; a completed Stage 0 document is never restaged."""
    await get_campaign(db, campaign_id, owner_id)
    cv = (await db.execute(select(CampaignCVModel).where(
        CampaignCVModel.campaign_id == campaign_id,
        CampaignCVModel.candidate_id == candidate_id).with_for_update())).scalar_one()
    if cv.stage0_status != "PENDING":
        return False
    cv.encrypted_pdf = encrypt_payload(pdf_bytes)
    cv.updated_at = _now()
    await db.commit()
    return True


async def finish_stage0(db, *, campaign_id: str, owner_id: str, candidate_id: str,
                        redacted_text: str | None = None, source_locations: list | None = None,
                        error_code: str | None = None):
    """Publish Stage 0 once and erase the pending PDF in the same transaction."""
    await get_campaign(db, campaign_id, owner_id)
    cv = (await db.execute(select(CampaignCVModel).where(
        CampaignCVModel.campaign_id == campaign_id,
        CampaignCVModel.candidate_id == candidate_id).with_for_update())).scalar_one()
    if cv.stage0_status in {"SUCCEEDED", "FAILED"}:
        return False
    if (error_code is None and (redacted_text is None or source_locations is None)) or (
        error_code is not None and (redacted_text is not None or source_locations is not None)
    ):
        raise ValueError("Provide redacted text and locations, or an error code")
    cv.stage0_status = "FAILED" if error_code else "SUCCEEDED"
    cv.redacted_text = None if error_code else redacted_text
    cv.source_locations = None if error_code else source_locations
    cv.extraction_error_code = error_code
    cv.encrypted_pdf = None
    cv.updated_at = _now()
    if error_code:
        pairs = (await db.execute(select(CampaignPairModel).where(
            CampaignPairModel.campaign_id == campaign_id,
            CampaignPairModel.cv_id == cv.id).with_for_update())).scalars().all()
        for pair in pairs:
            if pair.status not in TERMINAL_PAIR_STATUSES:
                pair.status = "EXTRACTION_FAILED"
                pair.failure_code = error_code
                pair.updated_at = _now()
    await db.commit()
    return True


async def finish_pair(db, *, campaign_id: str, owner_id: str, jd_key: str,
                      candidate_id: str, status: str, stage1_decision: str | None = None,
                      stage1_details: dict | None = None, stage2_score: float | None = None,
                      stage2_rank: int | None = None, stage3_status: str | None = None,
                      composite_score: float | None = None, tier: str | None = None,
                      verification_reasons: list | None = None,
                      result_snapshot: dict | None = None, failure_code: str | None = None):
    """Store one terminal pair result. Exact retries are no-ops; conflicts fail."""
    await get_campaign(db, campaign_id, owner_id)
    row = (await db.execute(select(CampaignPairModel).join(
        CampaignJDModel, CampaignJDModel.id == CampaignPairModel.jd_id).join(
        CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).where(
        CampaignPairModel.campaign_id == campaign_id,
        CampaignJDModel.jd_key == jd_key,
        CampaignCVModel.candidate_id == candidate_id).with_for_update())).scalar_one()
    if status not in TERMINAL_PAIR_STATUSES:
        raise ValueError("A terminal pair status is required")
    if row.status in TERMINAL_PAIR_STATUSES:
        if any((row.status != status,
                row.stage1_decision != stage1_decision,
                row.stage1_details != stage1_details,
                row.stage2_score != stage2_score,
                row.stage2_rank != stage2_rank,
                row.stage3_status != stage3_status,
                row.composite_score != composite_score,
                row.tier != tier,
                row.verification_reasons != (verification_reasons or []),
                row.result_snapshot != result_snapshot,
                row.failure_code != failure_code)):
            raise ValueError("Pair already has a different terminal outcome")
        return False
    if status == "SUCCESS" and (stage3_status != "SUCCESS" or composite_score is None):
        raise ValueError("A ranked success needs a valid Stage 3 score")
    row.status = status
    row.stage1_decision = stage1_decision
    row.stage1_details = stage1_details
    row.stage2_score = stage2_score
    row.stage2_rank = stage2_rank
    row.stage3_status = stage3_status
    row.composite_score = composite_score
    row.tier = tier
    row.verification_reasons = verification_reasons or []
    row.verification_required = bool(row.verification_reasons)
    row.result_snapshot = result_snapshot
    row.failure_code = failure_code
    row.updated_at = _now()
    await db.commit()
    return True


async def list_campaign_records(db, *, campaign_id: str, owner_id: str):
    """Return JDs, CVs and pairs without loading redacted CV text or raw bytes."""
    await get_campaign(db, campaign_id, owner_id)
    jds = (await db.execute(select(CampaignJDModel).where(
        CampaignJDModel.campaign_id == campaign_id).order_by(CampaignJDModel.jd_key))).scalars().all()
    cvs = (await db.execute(select(CampaignCVModel.id, CampaignCVModel.candidate_id,
                                   CampaignCVModel.source_filename, CampaignCVModel.content_hash,
                                   CampaignCVModel.stage0_status, CampaignCVModel.extraction_error_code).where(
        CampaignCVModel.campaign_id == campaign_id).order_by(CampaignCVModel.candidate_id))).all()
    pairs = (await db.execute(select(CampaignPairModel).where(
        CampaignPairModel.campaign_id == campaign_id))).scalars().all()
    return {"jds": jds, "cvs": cvs, "pairs": pairs}


async def jd_ranked_successes(db, *, campaign_id: str, owner_id: str, jd_key: str,
                              limit: int = 50, offset: int = 0):
    await get_campaign(db, campaign_id, owner_id)
    if limit < 1 or limit > 100 or offset < 0:
        raise ValueError("Invalid pagination")
    jd = (await db.execute(select(CampaignJDModel).where(
        CampaignJDModel.campaign_id == campaign_id,
        CampaignJDModel.jd_key == jd_key))).scalar_one()
    return (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id).join(
        CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).where(
        CampaignPairModel.jd_id == jd.id, CampaignPairModel.status == "SUCCESS"
    ).order_by(CampaignPairModel.composite_score.desc(), CampaignCVModel.candidate_id)
        .limit(limit).offset(offset))).all()


async def campaign_counts(db, *, campaign_id: str, owner_id: str):
    await get_campaign(db, campaign_id, owner_id)
    jd_count = (await db.execute(select(func.count()).select_from(CampaignJDModel).where(
        CampaignJDModel.campaign_id == campaign_id))).scalar_one()
    cv_count = (await db.execute(select(func.count()).select_from(CampaignCVModel).where(
        CampaignCVModel.campaign_id == campaign_id))).scalar_one()
    rows = (await db.execute(select(CampaignPairModel.status, func.count()).where(
        CampaignPairModel.campaign_id == campaign_id).group_by(CampaignPairModel.status))).all()
    counts = dict(rows)
    if sum(counts.values()) != jd_count * cv_count:
        raise RuntimeError("Campaign pair accounting is incomplete")
    return {"jds": jd_count, "cvs": cv_count, "pairs": counts,
            "terminal_pairs": sum(count for status, count in rows if status in TERMINAL_PAIR_STATUSES)}


async def jd_counts(db, *, campaign_id: str, owner_id: str, jd_key: str):
    await get_campaign(db, campaign_id, owner_id)
    jd = (await db.execute(select(CampaignJDModel).where(
        CampaignJDModel.campaign_id == campaign_id,
        CampaignJDModel.jd_key == jd_key))).scalar_one()
    cv_count = (await db.execute(select(func.count()).select_from(CampaignCVModel).where(
        CampaignCVModel.campaign_id == campaign_id))).scalar_one()
    rows = (await db.execute(select(CampaignPairModel.status, func.count()).where(
        CampaignPairModel.jd_id == jd.id).group_by(CampaignPairModel.status))).all()
    counts = dict(rows)
    if sum(counts.values()) != cv_count:
        raise RuntimeError("JD pair accounting is incomplete")
    return {"cvs": cv_count, "pairs": counts,
            "terminal_pairs": sum(count for status, count in rows if status in TERMINAL_PAIR_STATUSES)}
