"""Import a server-local folder of PDFs using the campaign intake service."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.schemas import CampaignCreateSchema
from app.campaigns.intake import accept_pdf
from app.campaigns.persistence import reserve_campaign
from app.config import settings
from app.models.database import AsyncSessionLocal, CampaignModel
from app.workers.tasks import campaign_stage0_task


async def import_folder(folder: Path, jobs_path: Path, owner: str, key: str | None):
    payload = CampaignCreateSchema(job_profiles=json.loads(jobs_path.read_text(encoding="utf-8")),
                                   idempotency_key=key)
    if len(payload.job_profiles) > settings.CAMPAIGN_MAX_JDS:
        raise ValueError("Too many JDs")
    jobs = [job.model_dump(mode="json") for job in payload.job_profiles]
    digest = hashlib.sha256(json.dumps(jobs, sort_keys=True).encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        _, campaign = await reserve_campaign(
            db, owner_id=owner, request_hash=digest, job_snapshots=jobs,
            policy_snapshots=[{"version": "campaign-v1", "stage3_cap": 30} for _ in jobs],
            idempotency_key=key)
        if campaign.status != "INTAKE":
            return {"campaign_id": campaign.id, "status": campaign.status}
        files = sorted(path for path in folder.iterdir() if path.is_file())
        if len(files) > settings.CAMPAIGN_MAX_MEMBERS:
            raise ValueError("Folder file count exceeds limit")
        accepted, rejected, queued = 0, [], []
        for ordinal, path in enumerate(files):
            if path.suffix.lower() != ".pdf":
                rejected.append({"name": path.name, "code": "NOT_PDF"})
                continue
            if path.stat().st_size > settings.PDF_MAX_BYTES:
                rejected.append({"name": path.name, "code": "PDF_TOO_LARGE"})
                continue
            error, cv_id = await accept_pdf(
                db, campaign_id=campaign.id, owner_id=owner, source_name=path.name,
                data=path.read_bytes(), ordinal=ordinal)
            if error:
                rejected.append(error)
            else:
                accepted += 1
                if cv_id:
                    queued.append(cv_id)
        if accepted:
            campaign.status = "RUNNING"
            await db.commit()
        result = {"campaign_id": campaign.id, "status": campaign.status,
                  "accepted_count": accepted, "rejected_count": len(rejected), "rejected": rejected}
    for cv_id in queued:
        try:
            campaign_stage0_task.delay(cv_id)
        except Exception:
            pass  # Control queue recovery republishes pending work.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("jobs_json", type=Path, help="JSON array of structured JDs")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(import_folder(args.folder, args.jobs_json,
                                               args.owner, args.idempotency_key))))


if __name__ == "__main__":
    main()
