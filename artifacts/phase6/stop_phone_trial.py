"""Close only the disposable diagnostic run; retain all evidence and results."""
import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.database import AsyncSessionLocal, CampaignModel, CampaignJDModel, CampaignPairModel, CampaignCVModel

CAMPAIGN = "ec8bbc11-e38c-4d06-99ec-adeecf426c33"
TERMINAL = {"EXTRACTION_FAILED", "FILTER_REJECTED", "PROCESSING_FAILED", "CUTOFF_EXCLUDED",
            "SUCCESS", "REVIEW_REQUIRED", "EVALUATION_FAILED"}


async def main():
    async with AsyncSessionLocal() as db:
        campaign = (await db.execute(select(CampaignModel).where(
            CampaignModel.id == CAMPAIGN).with_for_update())).scalar_one()
        pairs = (await db.execute(select(CampaignPairModel).where(
            CampaignPairModel.campaign_id == CAMPAIGN).with_for_update())).scalars().all()
        before = {}
        for pair in pairs:
            before[pair.status] = before.get(pair.status, 0) + 1
        changed = 0
        for pair in pairs:
            if pair.status in TERMINAL:
                continue
            selected = pair.status in {"SHORTLISTED", "STAGE3_RUNNING"}
            pair.status = "EVALUATION_FAILED" if selected else "PROCESSING_FAILED"
            if selected:
                pair.stage3_status = "EVALUATION_FAILED"
            pair.failure_code = "CAPACITY_DIAGNOSTIC_SUPERSEDED"
            pair.lease_until = pair.lease_owner = None
            pair.updated_at = datetime.now(timezone.utc)
            changed += 1
        for jd in (await db.execute(select(CampaignJDModel).where(
                CampaignJDModel.campaign_id == CAMPAIGN).with_for_update())).scalars():
            if jd.status != "COMPLETED":
                jd.status = "FAILED"
        campaign.status = "FAILED"
        campaign.completed_at = datetime.now(timezone.utc)
        purged = 0
        for cv in (await db.execute(select(CampaignCVModel).where(
                CampaignCVModel.campaign_id == CAMPAIGN).with_for_update())).scalars():
            if cv.stage0_status in {"PENDING", "RUNNING"}:
                cv.stage0_status = "FAILED"
                cv.extraction_error_code = "CAPACITY_DIAGNOSTIC_SUPERSEDED"
                cv.encrypted_pdf = None
                cv.updated_at = datetime.now(timezone.utc)
                purged += 1
        await db.commit()
        print(json.dumps({"input": {"campaign_id": CAMPAIGN,
                         "reason": "Privacy trial superseded after phone-extension leaks were measured"},
                         "output": {"before": before, "accounted_pairs": len(pairs),
                                    "closed_unresolved_pairs": changed, "purged_unprocessed_pdfs": purged,
                                    "retained_existing_results": True}}))


asyncio.run(main())
