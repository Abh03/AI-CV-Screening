import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.campaigns.coordinator import dispatch_pairs, finalize_ready_jds
from app.campaigns.persistence import (campaign_counts, finish_stage0, jd_counts,
                                       reserve_campaign, reserve_cv)
from app.models.database import Base, CampaignCVModel, CampaignJDModel, CampaignPairModel
from app.workers import tasks
from app.config import settings
from app.workers.celery_app import celery_app


def test_campaign_stage2_queue_routes():
    routes = celery_app.conf.task_routes
    assert routes["campaign.stage2_pair"]["queue"] == "retrieval"
    assert routes["campaign.coordinate"]["queue"] == "control"


@pytest.mark.asyncio
async def test_global_cutoff_waits_for_every_pair_and_is_idempotent(monkeypatch):
    from app.campaigns import persistence
    monkeypatch.setattr(persistence, "encrypt_payload", lambda data: data)
    monkeypatch.setattr(settings, "CAMPAIGN_RETRIEVAL_INFLIGHT", 4)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="owner", request_hash="x",
                job_snapshots=[{"job_id": "a", "title": "A"}, {"job_id": "b", "title": "B"}],
                policy_snapshots=[{}, {}], stage3_cap=2)
            for name in ("a", "b", "c", "d"):
                await reserve_cv(db, campaign_id=campaign.id, owner_id="owner",
                    candidate_id=name, source_filename=name + ".pdf", content_hash=name)
                await finish_stage0(db, campaign_id=campaign.id, owner_id="owner",
                    candidate_id=name, redacted_text="skills", source_locations=[])
            campaign.status = "RUNNING"
            await db.commit()
            assert len(await dispatch_pairs(db, campaign.id)) == 4
            assert await finalize_ready_jds(db, campaign.id) == []
            pairs = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id,
                CampaignJDModel.jd_key).join(CampaignCVModel).join(CampaignJDModel))).all()
            for pair, candidate, jd in reversed(pairs):
                pair.status = ("PROCESSING_FAILED" if candidate == "d" and jd == "a"
                               else "STAGE2_READY")
                pair.stage1_decision = "REVIEW" if candidate == "c" else "PASS"
                if pair.status == "STAGE2_READY":
                    pair.stage2_score = {"a": 0.8, "b": 0.9, "c": 0.9, "d": 0.1}[candidate]
                else:
                    pair.stage2_score = 999.0
            await db.commit()
            assert len(await finalize_ready_jds(db, campaign.id)) == 2
            assert await finalize_ready_jds(db, campaign.id) == []
            rows = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id,
                CampaignJDModel.jd_key).join(CampaignCVModel).join(CampaignJDModel))).all()
            for jd in ("a", "b"):
                ordered = sorted(((pair.stage2_rank, candidate, pair.status)
                    for pair, candidate, key in rows if key == jd and pair.stage2_rank is not None))
                expected = [(1, "b", "SHORTLISTED"), (2, "c", "SHORTLISTED"),
                            (3, "a", "CUTOFF_EXCLUDED")]
                if jd == "b":
                    expected.append((4, "d", "CUTOFF_EXCLUDED"))
                assert ordered == expected
            assert next(pair for pair, candidate, key in rows
                        if key == "a" and candidate == "d").status == "PROCESSING_FAILED"
            counts = await jd_counts(db, campaign_id=campaign.id, owner_id="owner", jd_key="a")
            assert counts["cvs"] == sum(counts["pairs"].values()) == 4
            assert counts["pairs"]["PROCESSING_FAILED"] == 1
            assert counts["terminal_pairs"] == 2
            assert (await campaign_counts(db, campaign_id=campaign.id,
                                          owner_id="owner"))["pairs"]["PROCESSING_FAILED"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pair_worker_reuses_redaction_and_fences_old_lease(monkeypatch):
    from app.campaigns import persistence
    monkeypatch.setattr(persistence, "encrypt_payload", lambda data: data)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    class NoDispose:
        async def dispose(self):
            pass
    monkeypatch.setattr(tasks, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(tasks, "engine", NoDispose())
    monkeypatch.setattr(tasks.campaign_coordinate_task, "delay", lambda *args: None)
    monkeypatch.setattr(settings, "STAGE2_BACKEND", "memory")
    calls = []
    def extractor(**kwargs):
        calls.append(kwargs)
        return {"candidate_id": kwargs["candidate_id"], "status": "SUCCESS",
                "composite_score": 0.75, "evidence_by_category": {}}
    monkeypatch.setattr(tasks, "extract_candidate_category_evidence", extractor)
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="owner", request_hash="x",
                job_snapshots=[{"job_id": "ops", "title": "Operations",
                    "jd_category_queries": {"EXPERIENCE": "operations"}}],
                policy_snapshots=[{}])
            await reserve_cv(db, campaign_id=campaign.id, owner_id="owner",
                candidate_id="candidate", source_filename="c.pdf", content_hash="x")
            await finish_stage0(db, campaign_id=campaign.id, owner_id="owner",
                candidate_id="candidate", redacted_text="Operations experience", source_locations=[])
            campaign.status = "RUNNING"
            await db.commit()
            pair_id, token = (await dispatch_pairs(db, campaign.id))[0]
            pair = await db.get(CampaignPairModel, pair_id)
            pair.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
            recovered_id, recovered_token = (await dispatch_pairs(db, campaign.id))[0]
            assert recovered_id == pair_id and recovered_token != token
        assert await tasks.execute_campaign_pair(pair_id, token) == "LEASE_LOST"
        assert await tasks.execute_campaign_pair(pair_id, recovered_token) == "STAGE2_READY"
        assert await tasks.execute_campaign_pair(pair_id, recovered_token) == "LEASE_LOST"
        assert len(calls) == 1
        async with sessions() as db:
            pair = await db.get(CampaignPairModel, pair_id)
            assert pair.stage1_decision == "REVIEW"
            assert pair.verification_required
            assert pair.stage2_score == 0.75
            assert pair.result_snapshot["stage2_evidence"]["composite_score"] == 0.75
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 30, 31, 101])
async def test_default_cap_applies_to_entire_jd_pool(count, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            _, campaign = await reserve_campaign(db, owner_id="owner", request_hash="x",
                job_snapshots=[{"job_id": "ops", "title": "Operations"}], policy_snapshots=[{}])
            for index in range(count):
                candidate = f"candidate-{index:03}"
                await reserve_cv(db, campaign_id=campaign.id, owner_id="owner",
                    candidate_id=candidate, source_filename=candidate + ".pdf", content_hash=candidate)
                await finish_stage0(db, campaign_id=campaign.id, owner_id="owner",
                    candidate_id=candidate, redacted_text="Operations", source_locations=[])
            campaign.status = "RUNNING"
            await db.commit()
            rows = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id)
                .join(CampaignCVModel))).all()
            for pair, candidate in rows:
                pair.status = "STAGE2_READY"
                pair.stage1_decision = "REVIEW"
                pair.stage2_score = 0.5
            await db.commit()
            assert len(await finalize_ready_jds(db, campaign.id)) == 1
            rows = (await db.execute(select(CampaignPairModel, CampaignCVModel.candidate_id)
                .join(CampaignCVModel))).all()
            assert sum(pair.status == "SHORTLISTED" for pair, _ in rows) == min(count, 30)
            assert sum(pair.status == "CUTOFF_EXCLUDED" for pair, _ in rows) == max(0, count - 30)
            assert [(candidate, pair.stage2_rank) for pair, candidate in sorted(
                rows, key=lambda item: item[1])] == [(f"candidate-{index:03}", index + 1)
                                                for index in range(count)]
    finally:
        await engine.dispose()
