import runpy
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.campaigns.persistence import (
    campaign_counts, finish_pair, finish_stage0, get_campaign, jd_counts, jd_ranked_successes,
    list_campaign_records, reserve_campaign, reserve_cv,
)
from app.models.database import Base, CampaignCVModel


@pytest.mark.asyncio
async def test_multi_jd_pairs_retry_owner_rank_and_extraction_failure(monkeypatch):
    from app.campaigns import persistence
    monkeypatch.setattr(persistence, "encrypt_payload", lambda data: b"encrypted:" + data)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            jobs = [{"job_id": "engineering", "title": "Engineer"},
                    {"job_id": "operations", "title": "Operations"}]
            created, campaign = await reserve_campaign(
                db, owner_id="owner-a", request_hash="hash-1", job_snapshots=jobs,
                policy_snapshots=[{"version": 1}, {"version": 1}], idempotency_key="key")
            assert created
            replayed, same = await reserve_campaign(
                db, owner_id="owner-a", request_hash="hash-1", job_snapshots=jobs,
                policy_snapshots=[{}, {}], idempotency_key="key")
            assert not replayed and same.id == campaign.id
            with pytest.raises(ValueError):
                await reserve_campaign(db, owner_id="owner-a", request_hash="different",
                                       job_snapshots=jobs, policy_snapshots=[{}, {}], idempotency_key="key")
            with pytest.raises(PermissionError):
                await get_campaign(db, campaign.id, "owner-b")
            for candidate in ("a", "b", "broken"):
                await reserve_cv(db, campaign_id=campaign.id, owner_id="owner-a",
                                 candidate_id=candidate, source_filename=f"{candidate}.pdf",
                                 content_hash="same-hash",
                                 pdf_bytes=b"%PDF" if candidate == "broken" else None)
            pending_broken = (await db.execute(sa.select(CampaignCVModel).where(
                CampaignCVModel.candidate_id == "broken"))).scalar_one()
            assert pending_broken.encrypted_pdf == b"encrypted:%PDF"
            retried, cv = await reserve_cv(db, campaign_id=campaign.id, owner_id="owner-a",
                                           candidate_id="a", source_filename="a.pdf", content_hash="same-hash")
            assert not retried and cv.candidate_id == "a"
            with pytest.raises(ValueError):
                await reserve_cv(db, campaign_id=campaign.id, owner_id="owner-a",
                                 candidate_id="a", source_filename="other.pdf", content_hash="same-hash")
            assert (await campaign_counts(db, campaign_id=campaign.id, owner_id="owner-a"))["pairs"] == {"PENDING": 6}
            for candidate in ("a", "b"):
                assert await finish_stage0(db, campaign_id=campaign.id, owner_id="owner-a",
                                           candidate_id=candidate, redacted_text="[NAME] worked",
                                           source_locations=[{"page": 1}])
            assert await finish_stage0(db, campaign_id=campaign.id, owner_id="owner-a",
                                       candidate_id="broken", error_code="INVALID_PDF")
            assert not await finish_stage0(db, campaign_id=campaign.id, owner_id="owner-a",
                                           candidate_id="broken", error_code="INVALID_PDF")
            assert await finish_pair(db, campaign_id=campaign.id, owner_id="owner-a",
                                     jd_key="engineering", candidate_id="a", status="SUCCESS",
                                     stage1_decision="REVIEW", stage3_status="SUCCESS", composite_score=80,
                                     tier="TIER_1", verification_reasons=[{"code": "AUTHORIZATION_UNKNOWN"}])
            assert await finish_pair(db, campaign_id=campaign.id, owner_id="owner-a",
                                     jd_key="operations", candidate_id="a", status="SUCCESS",
                                     stage1_decision="PASS", stage3_status="SUCCESS", composite_score=60,
                                     tier="TIER_2")
            assert await finish_pair(db, campaign_id=campaign.id, owner_id="owner-a",
                                     jd_key="engineering", candidate_id="b", status="SUCCESS",
                                     stage1_decision="PASS", stage3_status="SUCCESS", composite_score=90,
                                     tier="TIER_1")
            assert [candidate for _, candidate in await jd_ranked_successes(
                db, campaign_id=campaign.id, owner_id="owner-a", jd_key="engineering")] == ["b", "a"]
            records = await list_campaign_records(db, campaign_id=campaign.id, owner_id="owner-a")
            assert len(records["jds"]) == 2 and len(records["cvs"]) == 3 and len(records["pairs"]) == 6
            broken = (await db.execute(sa.select(CampaignCVModel).where(
                CampaignCVModel.candidate_id == "broken"))).scalar_one()
            assert all(pair.status == "EXTRACTION_FAILED" for pair in records["pairs"]
                       if pair.cv_id == broken.id)
            assert (await campaign_counts(db, campaign_id=campaign.id, owner_id="owner-a"))["terminal_pairs"] == 5
            assert (await jd_counts(db, campaign_id=campaign.id, owner_id="owner-a",
                                    jd_key="engineering"))["terminal_pairs"] == 3
            assert all(row.encrypted_pdf is None for row in (await db.execute(sa.select(CampaignCVModel))).scalars())
            with pytest.raises(PermissionError):
                await jd_ranked_successes(db, campaign_id=campaign.id, owner_id="owner-b", jd_key="engineering")
    finally:
        await engine.dispose()


def test_campaign_migration_adds_tables_without_touching_old_runs():
    engine = sa.create_engine("sqlite:///:memory:")
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "e52b7c9d0143_campaign_storage.py"
    migration = runpy.run_path(str(migration_path))
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        conn.execute(sa.text("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, payload TEXT)"))
        conn.execute(sa.text("INSERT INTO legacy_marker VALUES (1, 'retained')"))
        migration["upgrade"]()
        assert conn.execute(sa.text("SELECT payload FROM legacy_marker")).scalar_one() == "retained"
        assert {"campaigns", "campaign_jds", "campaign_cvs", "campaign_pairs"}.issubset(
            sa.inspect(conn).get_table_names())
    engine.dispose()
