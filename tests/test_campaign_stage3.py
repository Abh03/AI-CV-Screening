from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.campaigns import _pair_view
from app.campaigns.coordinator import dispatch_stage3, finalize_campaign
from app.campaigns.persistence import finish_stage0, reserve_campaign, reserve_cv
from app.campaigns.provider_limit import retry_delay
from app.config import settings
from app.main import app
from app.models.database import Base, CampaignModel, CampaignPairModel, CampaignJDModel, get_db
from app.stage3_evaluation.llm_client import ProviderRateLimited, retry_after_seconds
from app.stage3_evaluation.scoring import failed_evaluation
from app.stage3_evaluation.evaluator import compute_deterministic_tier
from app.stage3_evaluation.llm_client import LLMClientWrapper
from app.stage3_evaluation.schemas import LLMEvaluationOutput
from app.workers import tasks
from app.workers.celery_app import celery_app


@pytest.mark.asyncio
async def test_stage3_dispatch_waits_for_retry_and_completes_each_jd():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="local", request_hash="x",
                job_snapshots=[{"job_id": "ops", "title": "Operations"},
                               {"job_id": "sales", "title": "Sales"}],
                policy_snapshots=[{}, {}])
            for name in ("a", "b"):
                await reserve_cv(db, campaign_id=campaign.id, owner_id="local", candidate_id=name,
                    source_filename=name + ".pdf", content_hash=name)
                await finish_stage0(db, campaign_id=campaign.id, owner_id="local",
                    candidate_id=name, redacted_text="Operations", source_locations=[])
            campaign.status = "RUNNING"
            pairs = (await db.execute(select(CampaignPairModel))).scalars().all()
            for pair in pairs:
                pair.status = "SHORTLISTED"
                pair.stage2_rank = 1
            jds = (await db.execute(select(CampaignJDModel))).scalars().all()
            for jd in jds:
                jd.status = "SHORTLISTED"
            pairs[1].lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            await db.commit()
            claims = await dispatch_stage3(db, campaign.id)
            assert len(claims) == min(3, settings.CAMPAIGN_STAGE3_GLOBAL_INFLIGHT)
            assert pairs[1].id not in {pair_id for pair_id, _ in claims}
            assert await finalize_campaign(db, campaign.id) is False
            for pair in pairs:
                pair.status = "REVIEW_REQUIRED" if pair is pairs[0] else "SUCCESS"
                pair.lease_owner = None
                pair.lease_until = None
            await db.commit()
            assert await finalize_campaign(db, campaign.id) is True
            assert (await db.get(CampaignModel, campaign.id)).status == "COMPLETED"
            assert await dispatch_stage3(db, campaign.id) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stage3_worker_rate_limit_is_durable_and_fenced(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    class NoDispose:
        async def dispose(self):
            pass
    class FakeRedis:
        async def aclose(self):
            pass
    monkeypatch.setattr(tasks, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(tasks, "engine", NoDispose())
    monkeypatch.setattr(tasks, "Redis", type("RedisFactory", (), {"from_url": lambda _: FakeRedis()}))
    monkeypatch.setattr(tasks, "admit", lambda _: _admitted())
    monkeypatch.setattr(tasks, "release", lambda *_: _released())
    monkeypatch.setattr(tasks.campaign_coordinate_task, "apply_async", lambda **_: None)
    monkeypatch.setattr(tasks.campaign_coordinate_task, "delay", lambda *_: None)
    monkeypatch.setattr(tasks.llm_client, "aclose", lambda: _released())
    calls = []
    async def rate_limited(*args, **kwargs):
        calls.append(1)
        raise ProviderRateLimited(120)
    monkeypatch.setattr(tasks, "evaluate_single_candidate_async", rate_limited)
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="local", request_hash="x",
                job_snapshots=[{"job_id": "ops", "title": "Operations"}], policy_snapshots=[{}])
            await reserve_cv(db, campaign_id=campaign.id, owner_id="local", candidate_id="a",
                source_filename="a.pdf", content_hash="x")
            await finish_stage0(db, campaign_id=campaign.id, owner_id="local",
                candidate_id="a", redacted_text="Operations", source_locations=[])
            pair = (await db.execute(select(CampaignPairModel))).scalar_one()
            jd = (await db.execute(select(CampaignJDModel))).scalar_one()
            campaign.status = "RUNNING"
            jd.status = "SHORTLISTED"
            pair.status = "SHORTLISTED"
            pair.stage2_rank = 1
            pair.result_snapshot = {"stage2_evidence": {"candidate_id": "a", "evidence_by_category": {}}}
            await db.commit()
            pair_id, token = (await dispatch_stage3(db, campaign.id))[0]
        assert await tasks.execute_campaign_stage3(pair_id, token) == "RATE_LIMITED"
        assert await tasks.execute_campaign_stage3(pair_id, token) == "LEASE_LOST"
        async with sessions() as db:
            pair = await db.get(CampaignPairModel, pair_id)
            assert pair.status == "SHORTLISTED"
            assert pair.failure_code == "PROVIDER_RATE_LIMITED"
            assert pair.stage3_attempt_count == 1
            assert tasks._utc(pair.lease_until) > datetime.now(timezone.utc) + timedelta(seconds=100)
            pair.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            pair.stage3_attempt_count = settings.CAMPAIGN_STAGE3_MAX_ATTEMPTS - 1
            await db.commit()
            _, token = (await dispatch_stage3(db, campaign.id))[0]
        assert await tasks.execute_campaign_stage3(pair_id, token) == "RATE_LIMITED"
        async with sessions() as db:
            pair = await db.get(CampaignPairModel, pair_id)
            assert pair.status == "EVALUATION_FAILED"
            assert pair.failure_code == "PROVIDER_RATE_LIMIT_EXHAUSTED"
            assert pair.stage3_attempt_count == settings.CAMPAIGN_STAGE3_MAX_ATTEMPTS
            assert len(calls) == 2
    finally:
        await engine.dispose()


async def _admitted():
    return "permit", 0


async def _released():
    return None


@pytest.mark.asyncio
async def test_one_candidate_can_rank_in_two_jds_with_verification(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    class NoDispose:
        async def dispose(self):
            pass
    class FakeRedis:
        async def aclose(self):
            pass
    monkeypatch.setattr(tasks, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(tasks, "engine", NoDispose())
    monkeypatch.setattr(tasks, "Redis", type("RedisFactory", (), {"from_url": lambda _: FakeRedis()}))
    monkeypatch.setattr(tasks, "admit", lambda _: _admitted())
    monkeypatch.setattr(tasks, "release", lambda *_: _released())
    monkeypatch.setattr(tasks.llm_client, "aclose", lambda: _released())
    monkeypatch.setattr(tasks.campaign_coordinate_task, "delay", lambda *_: None)
    async def evaluate(payload, jd, **kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["max_provider_attempts"] == 1
        output = LLMEvaluationOutput.model_validate(LLMClientWrapper._call_mock(payload["candidate_id"]))
        return compute_deterministic_tier(payload["candidate_id"], output, payload)
    monkeypatch.setattr(tasks, "evaluate_single_candidate_async", evaluate)
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="local", request_hash="x",
                job_snapshots=[{"job_id": "ops", "title": "Operations"},
                               {"job_id": "sales", "title": "Sales"}], policy_snapshots=[{}, {}])
            await reserve_cv(db, campaign_id=campaign.id, owner_id="local", candidate_id="a",
                source_filename="a.pdf", content_hash="x")
            await finish_stage0(db, campaign_id=campaign.id, owner_id="local",
                candidate_id="a", redacted_text="Operations", source_locations=[])
            campaign.status = "RUNNING"
            pairs = (await db.execute(select(CampaignPairModel))).scalars().all()
            jds = (await db.execute(select(CampaignJDModel))).scalars().all()
            for jd in jds:
                jd.status = "SHORTLISTED"
            for pair in pairs:
                pair.status = "SHORTLISTED"
                pair.stage2_rank = 1
                pair.stage1_decision = "REVIEW"
                pair.verification_required = True
                pair.verification_reasons = [{"code": "AUTHORIZATION_UNKNOWN", "status": "REVIEW"}]
                pair.result_snapshot = {"stage2_evidence": {"candidate_id": "a",
                    "evidence_by_category": {category: [{"candidate_id": "a", "text": category + " evidence",
                        "source_location": {"page_number": 1}}] for category in
                        ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")}}}
            await db.commit()
            claims = await dispatch_stage3(db, campaign.id)
        assert len(claims) == 2
        for pair_id, token in claims:
            assert await tasks.execute_campaign_stage3(pair_id, token) == "SUCCESS"
            assert await tasks.execute_campaign_stage3(pair_id, token) == "LEASE_LOST"
        async with sessions() as db:
            pairs = (await db.execute(select(CampaignPairModel))).scalars().all()
            assert all(pair.status == "SUCCESS" and pair.verification_required for pair in pairs)
            assert all(pair.stage3_attempt_count == 1 for pair in pairs)
            assert all(pair.result_snapshot["stage3_evaluation"]["tier"] for pair in pairs)
            await db.rollback()
            assert await finalize_campaign(db, campaign.id) is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stage3_result_apis_order_privacy_and_owner_scope(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def override():
        async with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="local", request_hash="x",
                job_snapshots=[{"job_id": "ops", "title": "Operations"}], policy_snapshots=[{}])
            for name in ("a", "b", "c"):
                await reserve_cv(db, campaign_id=campaign.id, owner_id="local", candidate_id=name,
                    source_filename=name + ".pdf", content_hash=name)
            pairs = (await db.execute(select(CampaignPairModel))).scalars().all()
            for pair, score in zip(pairs, (80, 80, 70)):
                pair.status = "SUCCESS" if score == 80 else "REVIEW_REQUIRED"
                pair.composite_score = score
                pair.tier = "TIER_1" if score == 80 else None
                pair.stage1_decision = "REVIEW"
                pair.verification_required = True
                pair.verification_reasons = [{"code": "AUTHORIZATION_UNKNOWN", "status": "REVIEW"}]
                pair.result_snapshot = {"stage3_evaluation": {"review_reasons": ["MISSING_CITATIONS"] if score == 70 else [],
                    "verified_citations": ["SKILLS:1"], "evidence_verification": {"registry": {
                    "SKILLS:1": {"document_id": "d", "chunk_id": "c", "source_location": {"page_number": 1},
                                  "text": "secret CV text"}}}}}
            await db.commit()
            campaign_id = campaign.id
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            jds = await client.get(f"/api/v1/campaigns/{campaign_id}/jds")
            assert jds.status_code == 200
            assert jds.json()["jds"][0]["counts"] == {"SUCCESS": 2, "REVIEW_REQUIRED": 1}
            ranked = await client.get(f"/api/v1/campaigns/{campaign_id}/jds/ops/rankings?limit=1&offset=1")
            assert ranked.status_code == 200
            assert ranked.json()["total"] == 2
            assert ranked.json()["results"][0]["rank"] == 2
            assert ranked.json()["results"][0]["provisional"] is True
            assert ranked.json()["results"][0]["source_filename"]
            assert "secret CV text" not in ranked.text
            review = await client.get(f"/api/v1/campaigns/{campaign_id}/jds/ops/outcomes?status=REVIEW_REQUIRED")
            assert review.json()["results"][0]["score"] == 70
            assert review.json()["results"][0]["tier"] is None
            assert review.json()["results"][0]["source_filename"]
            assert "secret CV text" not in review.text
            assert (await client.get(f"/api/v1/campaigns/{campaign_id}/jds/missing/rankings")).status_code == 404
            monkeypatch.setattr(settings, "ENVIRONMENT", "production")
            monkeypatch.setattr(settings, "API_TOKENS_JSON", '[{"id":"bob","role":"recruiter","token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]')
            denied = await client.get(f"/api/v1/campaigns/{campaign_id}/jds", headers={
                "Authorization": "Bearer " + "b" * 32})
            assert denied.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def test_stage3_retry_after_and_queue_route():
    assert retry_after_seconds("12") == 12
    assert retry_after_seconds("bogus") is None
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0
    assert retry_delay(1, 120) >= 120
    assert retry_delay(1) < retry_delay(3)
    assert celery_app.conf.task_routes["campaign.stage3_pair"]["queue"] == "evaluation"
    assert celery_app.conf.task_routes["campaign.coordinate"]["queue"] == "control"
