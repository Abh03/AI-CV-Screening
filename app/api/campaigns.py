"""Owner-scoped campaign intake and Stage 0 status APIs."""
import hashlib
import json
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CampaignCreateSchema
from app.campaigns.intake import ArchiveLimitError, import_zip
from app.campaigns.persistence import campaign_counts, reserve_campaign
from app.config import settings
from app.core.auth import Principal, can_access, current_principal
from app.models.database import CampaignModel, get_db
from app.workers.tasks import campaign_stage0_task

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
            policy_snapshots=[{"version": "campaign-v1", "stage3_cap": 30} for _ in jobs],
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
    return {"campaign_id": campaign_id, "status": "RUNNING", **report}


@router.get("/{campaign_id}")
async def campaign_status(campaign_id: str, db: AsyncSession = Depends(get_db),
                          principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    counts = await campaign_counts(db, campaign_id=campaign.id, owner_id=campaign.owner_id)
    from sqlalchemy import func, select
    from app.models.database import CampaignCVModel
    stage0 = dict((await db.execute(select(CampaignCVModel.stage0_status, func.count()).where(
        CampaignCVModel.campaign_id == campaign.id).group_by(CampaignCVModel.stage0_status))).all())
    return {"campaign_id": campaign.id, "status": campaign.status,
            "counts": counts, "stage0": stage0,
            "intake_report": campaign.intake_report}
