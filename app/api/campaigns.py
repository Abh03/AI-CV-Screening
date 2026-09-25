"""Owner-scoped campaign intake and Stage 0 status APIs."""
import hashlib
import json
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CampaignCreateSchema
from app.campaigns.intake import ArchiveLimitError, import_zip
from app.campaigns.persistence import campaign_counts, reserve_campaign
from app.config import settings
from app.core.auth import Principal, can_access, current_principal
from app.models.database import CampaignModel, CampaignJDModel, CampaignPairModel, CampaignCVModel, get_db
from app.run_audit import policy_snapshot
from app.workers.tasks import campaign_stage0_task, campaign_coordinate_task

router = APIRouter(prefix="/api/v1/campaigns", tags=["Campaigns"])


async def _owned(db, campaign_id, principal):
    campaign = await db.get(CampaignModel, campaign_id)
    if campaign is None or not can_access(campaign.owner_id, principal):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("", status_code=201)
async def create_campaign(payload: CampaignCreateSchema, db: AsyncSession = Depends(get_db),
                          principal: Principal = Depends(current_principal)):
    if len(payload.job_profiles) > settings.CAMPAIGN_MAX_JDS:
        raise HTTPException(status_code=413, detail="Too many JDs")
    jobs = [job.model_dump(mode="json") for job in payload.job_profiles]
    digest = hashlib.sha256(json.dumps(jobs, sort_keys=True).encode()).hexdigest()
    try:
        created, campaign = await reserve_campaign(
            db, owner_id=principal.id, request_hash=digest, job_snapshots=jobs,
            policy_snapshots=[{"version": "campaign-v1", "stage3_cap": 30,
                               **policy_snapshot(30)} for _ in jobs],
            idempotency_key=payload.idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"campaign_id": campaign.id, "status": campaign.status, "created": created,
            "upload_url": f"/api/v1/campaigns/{campaign.id}/archive"}


@router.post("/{campaign_id}/archive", status_code=202)
async def upload_archive(campaign_id: str, request: Request, db: AsyncSession = Depends(get_db),
                         principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/zip":
        raise HTTPException(status_code=415, detail="Expected application/zip")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.CAMPAIGN_ARCHIVE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="ZIP archive exceeds limit")
    total = 0
    digest = hashlib.sha256()
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
        async for chunk in request.stream():
            total += len(chunk)
            if total > settings.CAMPAIGN_ARCHIVE_MAX_BYTES:
                raise HTTPException(status_code=413, detail="ZIP archive exceeds limit")
            staged.write(chunk)
            digest.update(chunk)
        staged.seek(0)
        archive_hash = digest.hexdigest()
        if campaign.status != "INTAKE":
            if campaign.archive_hash == archive_hash and campaign.intake_report:
                return {"campaign_id": campaign_id, "status": campaign.status, **campaign.intake_report}
            raise HTTPException(status_code=409, detail="Campaign intake is closed")
        try:
            report, queued = await import_zip(
                db, campaign_id=campaign_id, owner_id=campaign.owner_id, archive=staged)
        except ArchiveLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if report["accepted_count"] == 0:
        campaign.intake_report = report
        await db.commit()
        return {"campaign_id": campaign_id, "status": "INTAKE", **report}
    campaign.status = "RUNNING"
    campaign.archive_hash = archive_hash
    campaign.intake_report = report
    await db.commit()
    for cv_id in queued:
        try:
            campaign_stage0_task.delay(cv_id)
        except Exception:
            # Recovery on the control queue republishes pending rows.
            pass
    try:
        campaign_coordinate_task.delay(campaign_id)
    except Exception:
        pass
    return {"campaign_id": campaign_id, "status": "RUNNING", **report}


@router.get("")
async def list_campaigns(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                         db: AsyncSession = Depends(get_db),
                         principal: Principal = Depends(current_principal)):
    # Even administrators see their own list; opening another owner's ID remains an explicit action.
    scope = CampaignModel.owner_id == principal.id
    total = (await db.execute(select(func.count()).select_from(CampaignModel).where(scope))).scalar_one()
    jd_count = (select(func.count()).select_from(CampaignJDModel)
                .where(CampaignJDModel.campaign_id == CampaignModel.id).correlate(CampaignModel).scalar_subquery())
    cv_count = (select(func.count()).select_from(CampaignCVModel)
                .where(CampaignCVModel.campaign_id == CampaignModel.id).correlate(CampaignModel).scalar_subquery())
    rows = (await db.execute(select(CampaignModel, jd_count, cv_count).where(scope)
            .order_by(CampaignModel.created_at.desc(), CampaignModel.id.desc())
            .limit(limit).offset(offset))).all()
    return {"total": total, "limit": limit, "offset": offset, "campaigns": [
        {"campaign_id": campaign.id, "status": campaign.status,
         "created_at": campaign.created_at, "updated_at": campaign.updated_at,
         "completed_at": campaign.completed_at, "jd_count": jds, "accepted_count": cvs}
        for campaign, jds, cvs in rows]}


@router.get("/{campaign_id}")
async def campaign_status(campaign_id: str, db: AsyncSession = Depends(get_db),
                          principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    counts = await campaign_counts(db, campaign_id=campaign.id, owner_id=campaign.owner_id)
    from sqlalchemy import func, select
    from app.models.database import CampaignCVModel
    stage0 = dict((await db.execute(select(CampaignCVModel.stage0_status, func.count()).where(
        CampaignCVModel.campaign_id == campaign.id).group_by(CampaignCVModel.stage0_status))).all())
    retry_waiting = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
        CampaignPairModel.campaign_id == campaign.id,
        CampaignPairModel.status == "SHORTLISTED",
        CampaignPairModel.lease_until > datetime.now(timezone.utc)))).scalar_one()
    return {"campaign_id": campaign.id, "status": campaign.status,
            "counts": counts, "stage0": stage0, "stage3_retry_waiting": retry_waiting,
            "intake_report": campaign.intake_report}


async def _jd(db, campaign_id, jd_key):
    jd = (await db.execute(select(CampaignJDModel).where(
        CampaignJDModel.campaign_id == campaign_id,
        CampaignJDModel.jd_key == jd_key))).scalar_one_or_none()
    if jd is None:
        raise HTTPException(status_code=404, detail="JD not found")
    return jd


@router.get("/{campaign_id}/jds/{jd_key}/definition")
async def jd_definition(campaign_id: str, jd_key: str, db: AsyncSession = Depends(get_db),
                        principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    jd = await _jd(db, campaign.id, jd_key)
    return {"campaign_id": campaign.id, "jd_key": jd.jd_key,
            "job_profile": jd.job_snapshot, "stage3_cap": jd.stage3_cap}


def _pair_view(pair, candidate_id, source_filename=None, *, rank=None):
    evaluation = (pair.result_snapshot or {}).get("stage3_evaluation") or {}
    registry = (evaluation.get("evidence_verification") or {}).get("registry") or {}
    citations = evaluation.get("verified_citations") or []
    return {
        "candidate_id": candidate_id, "source_filename": source_filename, "status": pair.status,
        "rank": rank, "stage2_rank": pair.stage2_rank, "stage2_score": pair.stage2_score,
        "score": pair.composite_score, "tier": pair.tier,
        "category_scores": evaluation.get("category_scores", {}),
        "provisional": pair.status == "SUCCESS" and pair.verification_required,
        "verification_required": pair.verification_required,
        "verification_reasons": pair.verification_reasons,
        "stage1_decision": pair.stage1_decision,
        "stage1_checks": (pair.stage1_details or {}).get("checks", []),
        "review_reasons": evaluation.get("review_reasons", []),
        "failure_code": pair.failure_code,
        "error_message": evaluation.get("error_message"),
        "is_mock": evaluation.get("is_mock", False),
        "evidence": [{"citation": citation,
                      "document_id": registry[citation].get("document_id"),
                      "chunk_id": registry[citation].get("chunk_id"),
                      "source_location": registry[citation].get("source_location")}
                     for citation in citations if citation in registry],
    }


@router.get("/{campaign_id}/jds")
async def campaign_jds(campaign_id: str, db: AsyncSession = Depends(get_db),
                       principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    jds = (await db.execute(select(CampaignJDModel).where(
        CampaignJDModel.campaign_id == campaign.id).order_by(CampaignJDModel.jd_key))).scalars().all()
    result = []
    for jd in jds:
        counts = dict((await db.execute(select(CampaignPairModel.status, func.count()).where(
            CampaignPairModel.jd_id == jd.id).group_by(CampaignPairModel.status))).all())
        retry_waiting = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
            CampaignPairModel.jd_id == jd.id,
            CampaignPairModel.status == "SHORTLISTED",
            CampaignPairModel.lease_until > datetime.now(timezone.utc)))).scalar_one()
        result.append({"jd_key": jd.jd_key, "title": jd.job_snapshot.get("title"),
                       "status": jd.status, "stage3_cap": jd.stage3_cap,
                       "counts": counts, "stage3_retry_waiting": retry_waiting})
    return {"campaign_id": campaign.id, "jds": result}


@router.get("/{campaign_id}/jds/{jd_key}/rankings")
async def jd_rankings(campaign_id: str, jd_key: str, limit: int = Query(30, ge=1, le=100),
                      offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db),
                      principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    jd = await _jd(db, campaign.id, jd_key)
    total = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
        CampaignPairModel.jd_id == jd.id, CampaignPairModel.status == "SUCCESS"))).scalar_one()
    rows = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id, CampaignCVModel.source_filename).join(
        CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).where(
        CampaignPairModel.jd_id == jd.id, CampaignPairModel.status == "SUCCESS")
        .order_by(CampaignPairModel.composite_score.desc(), CampaignCVModel.candidate_id)
        .limit(limit).offset(offset))).all()
    return {"campaign_id": campaign.id, "jd_key": jd_key, "jd_status": jd.status,
            "total": total, "limit": limit, "offset": offset,
            "results": [_pair_view(pair, candidate, filename, rank=offset + index + 1)
                        for index, (pair, candidate, filename) in enumerate(rows)]}


@router.get("/{campaign_id}/jds/{jd_key}/outcomes")
async def jd_outcomes(campaign_id: str, jd_key: str,
                      status: str = Query(..., pattern="^(REVIEW_REQUIRED|EVALUATION_FAILED|FILTER_REJECTED|PROCESSING_FAILED|CUTOFF_EXCLUDED|EXTRACTION_FAILED)$"),
                      limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                      db: AsyncSession = Depends(get_db),
                      principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    jd = await _jd(db, campaign.id, jd_key)
    total = (await db.execute(select(func.count()).select_from(CampaignPairModel).where(
        CampaignPairModel.jd_id == jd.id, CampaignPairModel.status == status))).scalar_one()
    rows = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id, CampaignCVModel.source_filename).join(
        CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id).where(
        CampaignPairModel.jd_id == jd.id, CampaignPairModel.status == status)
        .order_by(CampaignPairModel.stage2_rank, CampaignCVModel.candidate_id)
        .limit(limit).offset(offset))).all()
    return {"campaign_id": campaign.id, "jd_key": jd_key, "status": status,
            "total": total, "limit": limit, "offset": offset,
            "results": [_pair_view(pair, candidate, filename) for pair, candidate, filename in rows]}
