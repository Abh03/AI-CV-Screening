import json
import logging

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core.logging import SafeJSONFormatter
from app.main import app
from app.models.database import Base, JobProfileModel, ScreeningRunModel, get_db
from app.run_audit import reserve_run

ADMIN = "a" * 40
ALICE = "b" * 40
BOB = "c" * 40


@pytest.mark.asyncio
async def test_run_ownership_and_authentication(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add(JobProfileModel(id="job-owner", owner_id="alice", title="Engineer",
                                    category_queries={}, hard_filter_rules={}))
        session.add(ScreeningRunModel(id="run-owner", owner_id="alice", job_id="job-owner",
                                      status="COMPLETED", metrics={}, request_hash="hash",
                                      job_snapshot={}, policy_snapshot={}))
        await session.commit()

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "API_TOKENS_JSON", json.dumps([
        {"id": "admin", "role": "admin", "token": ADMIN},
        {"id": "alice", "role": "recruiter", "token": ALICE},
        {"id": "bob", "role": "recruiter", "token": BOB},
    ]))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            route = "/api/v1/screening/runs/run-owner"
            assert (await client.get(route)).status_code == 401
            assert (await client.get(route, headers={"Authorization": "Bearer " + BOB})).status_code == 404
            assert (await client.get(route, headers={"Authorization": "Bearer " + ALICE})).status_code == 200
            assert (await client.get(route, headers={"Authorization": "Bearer " + ADMIN})).status_code == 200
            assert (await client.post("/api/v1/screening/ingest-pdf", content=b"bad")).status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_job_and_idempotency_ownership():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    job = {"job_id": "owned-job", "title": "Engineer", "jd_category_queries": {}, "hard_filter_rules": {}}
    policy = {"provider": "mock", "model": "mock", "prompt_version": "v1", "scoring_policy_version": "v1"}
    async with sessions() as session:
        await reserve_run(session, key="same-key", request_hash="hash", job_snapshot=job,
                          policy=policy, candidates=[], owner_id="alice")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as denied_run:
            await reserve_run(session, key="same-key", request_hash="hash", job_snapshot=job,
                              policy=policy, candidates=[], owner_id="bob")
        assert denied_run.value.status_code == 403
        with pytest.raises(HTTPException) as denied_job:
            await reserve_run(session, key="new-key", request_hash="hash", job_snapshot=job,
                              policy=policy, candidates=[], owner_id="bob")
        assert denied_job.value.status_code == 403
    await engine.dispose()


def test_production_rejects_mock_and_missing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://postgres:secret@db/cv")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(settings, "STAGE2_BACKEND", "postgres")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "API_TOKENS_JSON", json.dumps([{"id": "admin", "role": "admin", "token": ADMIN}]))
    with pytest.raises(ValueError, match="live LLM"):
        settings.validate_production()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    with pytest.raises(ValueError, match="key is missing"):
        settings.validate_production()
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    settings.validate_production()


def test_structured_log_excludes_untrusted_message():
    record = logging.LogRecord("cv_screening", logging.INFO, __file__, 1,
                               "candidate Jane Doe 555-1234", (), None)
    output = SafeJSONFormatter().format(record)
    assert "Jane Doe" not in output
    assert "555-1234" not in output


@pytest.mark.asyncio
async def test_liveness_remains_available_when_readiness_dependency_fails(monkeypatch):
    import importlib
    main_module = importlib.import_module("app.main")

    class UnavailableEngine:
        def connect(self):
            raise ConnectionError("database unavailable")

    monkeypatch.setattr(main_module, "engine", UnavailableEngine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 503
