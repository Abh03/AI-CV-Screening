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
                "work_authorized": "eligible",
                "authorization_source": "recruiter_verified",
                "parsed_attributes": {"experience_years": 3.0, "experience_source": "recruiter_verified"}
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


@pytest.mark.asyncio
@pytest.mark.parametrize("review_kind", ["missing_information", "invalid_citation"])
async def test_api_preserves_review_and_failure_outcomes(monkeypatch, review_kind):
    from app import orchestrator
    from app.stage3_evaluation.llm_client import llm_client, LLMClientWrapper

    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", lambda text: text)
    monkeypatch.setattr(orchestrator, "extract_candidate_category_evidence", lambda **kwargs: {
        "candidate_id": kwargs["candidate_id"], "composite_score": 1,
        "evidence_by_category": {category: [{"text": "Test evidence"}] for category in ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")},
    })

    async def generate(*, system_prompt, user_prompt, candidate_id):
        if candidate_id == "failed":
            raise RuntimeError("unavailable")
        response = LLMClientWrapper._call_mock(candidate_id)
        if candidate_id == "review":
            if review_kind == "missing_information":
                response["flags"] = [{"type": "MISSING_INFORMATION", "severity": "CRITICAL",
                                      "description": "Unknown detail", "citations": []}]
            else:
                response["skills"]["citations"] = ["EDUCATION:1"]
        return response

    monkeypatch.setattr(llm_client, "generate_evaluation", generate)
    payload = {
        "job_profile": {"job_id": "outcomes", "title": "Engineer", "jd_category_queries": {}},
        "candidates": [{"candidate_id": name, "raw_cv_text": "Test", "recruiter_overrides": {"work_authorized": "eligible"}} for name in ("failed", "review", "success")],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert [item["candidate_id"] for item in data["leaderboard"]] == ["success"]
    assert data["review_candidates"][0]["tier"] is None
    assert data["failed_candidates"][0]["composite_score"] is None
    assert data["metrics"]["stage3_evaluated"] == 3
    assert data["metrics"]["stage3_succeeded"] == 1
    assert data["metrics"]["stage3_review_required"] == 1
    assert data["metrics"]["stage3_failed"] == 1
    async with TestingSessionLocal() as session:
        rows = (await session.execute(select(EvaluationResultModel))).scalars().all()
        assert len(rows) == 3
        by_id = {row.candidate_id: row for row in rows}
        assert by_id["failed"].tier is None
        assert by_id["failed"].composite_score is None
        assert by_id["failed"].evaluation_status == "EVALUATION_FAILED"
        assert by_id["failed"].llm_raw_output["error_code"] == "PROVIDER_ERROR"
        assert by_id["review"].tier is None
        assert by_id["review"].scoring_policy_version == "stage3-v1.1.0"
        stored = by_id["review"].llm_raw_output
        assert stored["is_mock"] is True
        assert stored["evidence_verification"]["registry"]["SKILLS:1"]["text"] == "Test evidence"
        if review_kind == "invalid_citation":
            assert stored["evidence_verification"]["checks"][0]["reason"] == "WRONG_CATEGORY"
