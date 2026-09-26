"""Seed approved versions for tests unrelated to JD extraction."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.models.database import JDDraftModel, ApprovedJDModel


async def approved_jobs(sessions, jobs, owner="local"):
    async with sessions() as db:
        identifiers = []
        for job in jobs:
            identifier = uuid4().hex
            draft = JDDraftModel(id=identifier, owner_id=owner, pdf_hash=identifier,
                status="APPROVED", pages=[], provenance={}, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
            db.add(draft)
            await db.flush()
            db.add(ApprovedJDModel(id=identifier, owner_id=owner, draft_id=identifier,
                profile=job, provenance={}, approved_at=datetime.now(timezone.utc)))
            identifiers.append(identifier)
        await db.commit()
        return identifiers
