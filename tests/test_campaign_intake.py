import io
import zipfile

import fitz
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.main import app
from app.models.database import Base, CampaignCVModel, CampaignPairModel, get_db
from app.workers import tasks

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
TestingSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def campaign_db(monkeypatch):
    monkeypatch.setattr(tasks.campaign_coordinate_task, "delay", lambda *args: None)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async def override():
        async with TestingSessionLocal() as db:
            yield db
    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


def pdf_bytes():
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Jane Doe\nEXPERIENCE\nFive years in operations and logistics")
    data = doc.tobytes()
    doc.close()
    return data


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in entries:
            zipped.writestr(name, data)
    return stream.getvalue()


JOBS = [{"job_id": "ops", "title": "Operations", "jd_category_queries": {"EXPERIENCE": "operations"}},
        {"job_id": "sales", "title": "Sales", "jd_category_queries": {"EXPERIENCE": "sales"}}]


@pytest.mark.asyncio
async def test_zip_intake_stage0_reuse_and_rejections(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(tasks, "AsyncSessionLocal", TestingSessionLocal)
    class NoDispose:
        async def dispose(self):
            pass
    monkeypatch.setattr(tasks, "engine", NoDispose())
    queued = []
    monkeypatch.setattr(tasks.campaign_stage0_task, "delay", lambda cv_id: queued.append(cv_id))
    pdf = pdf_bytes()
    content = archive([("one.pdf", pdf), ("nested/two.pdf", pdf), ("two.pdf", pdf),
                       ("../attack.pdf", pdf), ("note.txt", b"hello"), ("bad.pdf", b"wrong")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/campaigns", json={"job_profiles": JOBS, "idempotency_key": "intake"})
        assert created.status_code == 201
        campaign_id = created.json()["campaign_id"]
        uploaded = await client.post(created.json()["upload_url"], content=content,
                                     headers={"Content-Type": "application/zip"})
        assert uploaded.status_code == 202
        report = uploaded.json()
        assert report["accepted_count"] == 3
        assert [item["code"] for item in report["rejected"]] == ["DUPLICATE_NAME", "UNSAFE_PATH", "NOT_PDF"]
        assert len(queued) == 3
        replay = await client.post(created.json()["upload_url"], content=content,
                                   headers={"Content-Type": "application/zip"})
        assert replay.status_code == 202
        assert replay.json()["accepted_count"] == 3
        assert len(queued) == 3
        status = await client.get(f"/api/v1/campaigns/{campaign_id}")
        assert status.json()["counts"]["pairs"] == {"PENDING": 6}
    for cv_id in queued:
        await tasks.execute_campaign_stage0(cv_id)
        assert await tasks.execute_campaign_stage0(cv_id) == "ALREADY_COMPLETE"
    async with TestingSessionLocal() as db:
        cvs = (await db.execute(select(CampaignCVModel))).scalars().all()
        assert len(cvs) == 3
        assert len({cv.candidate_id for cv in cvs}) == 3
        assert len({cv.content_hash for cv in cvs}) == 2
        assert all(cv.encrypted_pdf is None for cv in cvs)
        pairs = (await db.execute(select(CampaignPairModel))).scalars().all()
        assert len(pairs) == 6
        assert sum(pair.status == "EXTRACTION_FAILED" for pair in pairs) == 2
        assert next(cv for cv in cvs if cv.source_filename == "bad.pdf").stage0_status == "FAILED"
        assert all("Jane Doe" not in (cv.redacted_text or "") for cv in cvs)


@pytest.mark.asyncio
async def test_campaign_jd_validation_and_archive_limits(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for jobs in ([], [JOBS[0], JOBS[0]], [{**JOBS[0], "jd_category_queries": {}}]):
            assert (await client.post("/api/v1/campaigns", json={"job_profiles": jobs})).status_code == 422
        created = (await client.post("/api/v1/campaigns", json={"job_profiles": JOBS})).json()
        monkeypatch.setattr(settings, "CAMPAIGN_ARCHIVE_MAX_BYTES", 10)
        assert (await client.post(created["upload_url"], content=b"x" * 11,
                                  headers={"Content-Type": "application/zip"})).status_code == 413


@pytest.mark.asyncio
async def test_campaign_owner_scope_and_queue_routes(monkeypatch):
    import json
    from app.workers.celery_app import celery_app
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "API_TOKENS_JSON", json.dumps([
        {"id": "alice", "role": "recruiter", "token": "a" * 32},
        {"id": "bob", "role": "recruiter", "token": "b" * 32}]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/campaigns", json={"job_profiles": JOBS},
                                    headers={"Authorization": "Bearer " + "a" * 32})
        assert created.status_code == 201
        url = "/api/v1/campaigns/" + created.json()["campaign_id"]
        assert (await client.get(url, headers={"Authorization": "Bearer " + "b" * 32})).status_code == 404
        assert (await client.post(url + "/archive", content=b"zip",
                                  headers={"Authorization": "Bearer " + "b" * 32,
                                           "Content-Type": "application/zip"})).status_code == 404
        alice = {"Authorization": "Bearer " + "a" * 32}
        bob = {"Authorization": "Bearer " + "b" * 32}
        listing = (await client.get("/api/v1/campaigns?limit=1&offset=0", headers=alice)).json()
        assert listing["total"] == 1
        assert listing["campaigns"][0]["campaign_id"] == created.json()["campaign_id"]
        assert listing["campaigns"][0]["jd_count"] == 2
        assert listing["campaigns"][0]["accepted_count"] == 0
        assert (await client.get("/api/v1/campaigns", headers=alice)).headers["Cache-Control"] == "no-store"
        assert (await client.get("/api/v1/campaigns", headers=bob)).json()["campaigns"] == []
        definition = (await client.get(url + "/jds/ops/definition", headers=alice)).json()
        assert definition["job_profile"]["jd_category_queries"]["EXPERIENCE"] == "operations"
        assert definition["stage3_cap"] == 30
        assert (await client.get(url + "/jds/ops/definition", headers=bob)).status_code == 404
        assert (await client.get(url + "/jds/missing/definition", headers=alice)).status_code == 404
    routes = celery_app.conf.task_routes
    assert routes["campaign.stage0"]["queue"] == "ocr"
    assert routes["campaign.recover_stage0"]["queue"] == "control"
    assert routes["screening.execute_run"]["queue"] == "screening"


@pytest.mark.asyncio
async def test_zero_accepted_report_survives_reload(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/campaigns", json={"job_profiles": JOBS})).json()
        campaign_id = created["campaign_id"]
        response = await client.post(created["upload_url"], content=archive([("note.txt", b"synthetic")]),
                                     headers={"Content-Type": "application/zip"})
        assert response.status_code == 202
        assert response.json()["accepted_count"] == 0
        status = (await client.get(f"/api/v1/campaigns/{campaign_id}")).json()
        assert status["status"] == "INTAKE"
        assert status["intake_report"]["rejected"][0]["code"] == "NOT_PDF"
