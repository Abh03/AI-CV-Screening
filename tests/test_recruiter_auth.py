import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests.jd_helpers import approved_jobs
from app.config import settings
from app.core.auth import AUDIENCE, ISSUER, hash_password
from app.main import app
from app.models.database import Base, RecruiterUserModel, get_db

JOBS = [{"job_id": "ops", "title": "Operations", "jd_category_queries": {"EXPERIENCE": "operations"}}]
PASSWORD = "long-test-password-123"


@pytest.mark.asyncio
async def test_recruiter_login_csrf_ownership_and_revocation(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        db.add_all([RecruiterUserModel(id=name, email=f"{name}@example.test", username=name,
                     password_hash=hash_password(PASSWORD), role="recruiter", is_active=True,
                     token_version=0, failed_attempts=0) for name in ("alice", "bob")])
        await db.commit()

    async def override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "test-only-jwt-signing-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "API_TOKENS_JSON", None)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as alice, \
                   AsyncClient(transport=transport, base_url="https://test") as bob:
            assert (await alice.get("/api/v1/auth/me")).status_code == 401
            bad = await alice.post("/api/v1/auth/login", json={"identifier": "alice", "password": "wrong"})
            assert bad.status_code == 401
            assert (await alice.post("/api/v1/auth/login", json={"identifier": "alice", "password": PASSWORD},
                                     headers={"Origin": "https://other.test"})).status_code == 403
            login = await alice.post("/api/v1/auth/login", json={"identifier": "alice@example.test", "password": PASSWORD})
            assert login.status_code == 200
            assert "access_token" not in login.json()
            assert "httponly" in login.headers["set-cookie"].lower()
            assert "secure" in login.headers["set-cookie"].lower()
            assert (await alice.get("/api/v1/auth/me")).json()["user"]["id"] == "alice"
            no_csrf = await alice.post("/api/v1/campaigns", json={"job_profiles": JOBS})
            assert no_csrf.status_code == 403
            csrf = alice.cookies["cv_csrf"]
            created = await alice.post("/api/v1/campaigns", json={"approved_jd_ids": await approved_jobs(sessions, JOBS, "alice")},
                                       headers={"X-CSRF-Token": csrf})
            assert created.status_code == 201
            campaign_id = created.json()["campaign_id"]
            assert (await bob.post("/api/v1/auth/login", json={"identifier": "bob", "password": PASSWORD})).status_code == 200
            assert (await bob.get("/api/v1/campaigns")).json()["total"] == 0
            assert (await bob.get(f"/api/v1/campaigns/{campaign_id}")).status_code == 404
            old_token = alice.cookies["cv_access"]
            signed_out = await alice.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
            assert signed_out.status_code == 200
            assert (await alice.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"})).status_code == 401
            assert (await bob.get("/api/v1/auth/me")).status_code == 200
            for _ in range(5):
                assert (await bob.post("/api/v1/auth/login", json={"identifier": "bob", "password": "wrong"})).status_code == 401
            assert (await bob.post("/api/v1/auth/login", json={"identifier": "bob", "password": PASSWORD})).status_code == 401
            async with sessions() as db:
                bob_user = await db.get(RecruiterUserModel, "bob")
                assert bob_user.locked_until is not None
                bob_user.is_active = False
                await db.commit()
            assert (await bob.get("/api/v1/auth/me")).status_code == 401
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_jwt_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    secret = "test-only-jwt-signing-key-with-at-least-32-bytes"
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", secret)
    monkeypatch.setattr(settings, "API_TOKENS_JSON", None)
    now = datetime.now(timezone.utc)
    expired = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "alice", "iat": now - timedelta(hours=1),
                          "nbf": now - timedelta(hours=1), "exp": now - timedelta(minutes=1),
                          "jti": str(uuid.uuid4()), "ver": 0, "csrf": "unused"}, secret, algorithm="HS256")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert response.status_code == 401
