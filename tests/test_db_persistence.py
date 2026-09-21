import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from app.main import app
from app.models.database import Base, get_db, JobProfileModel, EvaluationResultModel

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
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
async def test_screening_endpoint_persists_to_db():
    payload = {
        "job_profile": {
            "job_id": "job_db_test_01",
            "title": "Senior Python Backend Engineer",
            "jd_category_queries": {
                "SKILLS": "Python FastAPI PostgreSQL",
                "EXPERIENCE": "Backend Microservices",
                "PROJECTS": "High Throughput APIs",
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
                "candidate_id": "cand_db_001",
                "raw_cv_text": "John DB Test\nSkills: Python, FastAPI\nWORK EXPERIENCE\n3 years backend software engineer.\nEDUCATION\nBachelor of Science in Computer Science",
                "work_authorized": True,
                "parsed_attributes": {"experience_years": 3.0}
            }
        ],
        "top_n_stage2_cutoff": 10
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/screening/run", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["stage1_passed"] == 1

    # Verify Database Persistence
    async with TestingSessionLocal() as session:
        job_stmt = select(JobProfileModel).where(JobProfileModel.id == "job_db_test_01")
        saved_job = (await session.execute(job_stmt)).scalar_one_or_none()
        assert saved_job is not None
        assert saved_job.title == "Senior Python Backend Engineer"

        eval_stmt = select(EvaluationResultModel).where(
            EvaluationResultModel.job_id == "job_db_test_01",
            EvaluationResultModel.candidate_id == "cand_db_001"
        )
        saved_eval = (await session.execute(eval_stmt)).scalar_one_or_none()
        assert saved_eval is not None
        assert saved_eval.candidate_id == "cand_db_001"