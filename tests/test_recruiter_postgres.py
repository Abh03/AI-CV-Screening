"""Validate real row-lock behavior, using a fresh database destroyed by the fixture."""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_postgres_retrieval import migrated_database
from app.core.auth import Principal, current_principal
from app.main import app
from app.models.database import CampaignModel, CampaignCVModel, CampaignJDModel, CampaignPairModel, get_db


@pytest.mark.infrastructure
@pytest.mark.asyncio
async def test_simultaneous_reviewers_cannot_overwrite_each_other(migrated_database):
    engine = create_async_engine(migrated_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(CampaignModel(id="scope", owner_id="alice", request_hash="hash"))
        await db.flush()
        db.add(CampaignJDModel(id="role", campaign_id="scope", jd_key="engineer", job_snapshot={"title": "Engineer"}, policy_snapshot={}))
        db.add(CampaignCVModel(id="cv", campaign_id="scope", candidate_id="candidate", source_filename="candidate.pdf", content_hash="hash"))
        await db.flush()
        db.add(CampaignPairModel(id="pair", campaign_id="scope", jd_id="role", cv_id="cv"))
        await db.commit()
    async def database():
        async with sessions() as db:
            yield db
    async def principal():
        return Principal("alice", "recruiter")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_principal] = principal
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = await asyncio.gather(*[client.patch("/api/v1/recruiter/reviews", json={
                "pair_versions": {"pair": 0}, "decision": decision, "reason": decision}) for decision in ("SHORTLIST", "HOLD")])
            assert sorted(response.status_code for response in responses) == [200, 409]
            detail = (await client.get("/api/v1/recruiter/pairs/pair")).json()
            assert detail["review"]["version"] == 1 and len(detail["history"]) == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_principal, None)
        await engine.dispose()
