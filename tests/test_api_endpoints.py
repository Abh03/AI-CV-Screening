import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.config import settings
from app.models.database import Base, get_db

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# StaticPool keeps the in-memory database alive across all sessions/connections
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False
)
TestingSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function", autouse=True)
async def prepare_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_db] = override_get_db

    yield

    app.dependency_overrides.clear()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session



@pytest.mark.asyncio
async def test_health_check_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy", "service": settings.PROJECT_NAME,
        "version": settings.VERSION, "environment": settings.ENVIRONMENT,
    }


@pytest.mark.asyncio
async def test_run_screening_endpoint_valid_payload():
    payload = {
        "job_profile": {
            "job_id": "job_101",
            "title": "Backend Python Developer",
            "jd_category_queries": {
                "SKILLS": "Python FastAPI PostgreSQL",
                "EXPERIENCE": "Backend API design",
                "PROJECTS": "Distributed systems",
                "EDUCATION": "BS Computer Science"
            },
            "hard_filter_rules": {
                "min_years_experience": 2.0,
                "degree_requirement": {
                    "level": "BACHELOR",
                    "fields": [],
                    "field_aliases": []
                }
            }
        },
        "candidates": [
            {
                "candidate_id": "cand_api_001",
                "raw_cv_text": "Alex Dev\nSkills: Python, FastAPI, PostgreSQL\nWORK EXPERIENCE\n4 years backend development.\nEDUCATION\nBachelor of Science in Computer Science, 2020",
                "work_authorized": True,
                "parsed_attributes": {"experience_years": 4.0}
            }
        ],
        "top_n_stage2_cutoff": 10
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/screening/run", json=payload)

    assert response.status_code == 200

    data = response.json()
    assert data["job_id"] == "job_101"
    assert data["metrics"]["total_input_candidates"] == 1
    assert data["metrics"]["stage1_passed"] == 1
    assert len(data["leaderboard"]) == 1
    assert data["leaderboard"][0]["candidate_id"] == "cand_api_001"