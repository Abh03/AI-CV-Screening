import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from app.main import app
from app.models.database import Base, get_db, JobProfileModel, EvaluationResultModel, ScreeningRunModel, CandidateOutcomeModel

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
        "candidates": [{"candidate_id": name, "raw_cv_text": "Test",
                        **({} if name == "success" else {"recruiter_overrides": {"work_authorized": "eligible"}})}
                       for name in ("failed", "review", "success")],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert [item["candidate_id"] for item in data["leaderboard"]] == ["success"]
    assert data["leaderboard"][0]["provisional"] is True
    assert data["leaderboard"][0]["verification_reasons"][0]["code"] == "AUTHORIZATION_UNKNOWN"
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
        assert by_id["success"].llm_raw_output["verification_required"] is True
        assert by_id["success"].llm_raw_output["stage1_filter_details"]["checks"][0]["code"] == "AUTHORIZATION_UNKNOWN"
        assert by_id["review"].tier is None
        assert by_id["review"].scoring_policy_version == "stage3-v1.1.0"
        stored = by_id["review"].llm_raw_output
        assert stored["is_mock"] is True
        assert stored["evidence_verification"]["registry"]["SKILLS:1"]["text"] == "Test evidence"
        if review_kind == "invalid_citation":
            assert stored["evidence_verification"]["checks"][0]["reason"] == "WRONG_CATEGORY"


@pytest.mark.asyncio
async def test_run_replay_and_changed_job_snapshot(monkeypatch):
    from app import orchestrator
    from app.run_audit import recompute_stored_decision

    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", lambda text: text)
    monkeypatch.setattr(orchestrator, "extract_candidate_category_evidence", lambda **kwargs: {
        "candidate_id": kwargs["candidate_id"], "composite_score": 5,
        "evidence_by_category": {name: [{"text": "Documented evidence"}] for name in
                                 ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")}})
    payload = {"job_profile": {"job_id": "versioned", "title": "Engineer",
                               "jd_category_queries": {"SKILLS": "Python"}},
               "candidates": [{"candidate_id": "c", "raw_cv_text": "Python engineer",
                               "recruiter_overrides": {"work_authorized": "eligible"}}],
               "idempotency_key": "request-one"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/screening/run", json=payload)
        replay = await client.post("/api/v1/screening/run", json=payload)
        changed = {**payload, "job_profile": {**payload["job_profile"],
                                               "title": "Senior Engineer",
                                               "jd_category_queries": {"SKILLS": "FastAPI"}}}
        conflict = await client.post("/api/v1/screening/run", json=changed)
        changed["idempotency_key"] = "request-two"
        second = await client.post("/api/v1/screening/run", json=changed)
    assert first.status_code == replay.status_code == second.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert first.json()["run_id"] != second.json()["run_id"]
    async with TestingSessionLocal() as session:
        runs = (await session.execute(select(ScreeningRunModel).order_by(ScreeningRunModel.created_at))).scalars().all()
        assert len(runs) == 2
        assert [run.job_snapshot["title"] for run in runs] == ["Engineer", "Senior Engineer"]
        assert [run.job_snapshot["jd_category_queries"]["SKILLS"] for run in runs] == ["Python", "FastAPI"]
        assert runs[0].policy_snapshot["prompt_sha256"]
        outcomes = (await session.execute(select(CandidateOutcomeModel))).scalars().all()
        assert len(outcomes) == 2
        for run in runs:
            outcome = next(row for row in outcomes if row.run_id == run.id)
            assert outcome.evidence_snapshot["candidate_id"] == "c"
            assert outcome.citation_mapping["SKILLS:1"]["text"] == "Documented evidence"
            assert recompute_stored_decision(run, outcome)["matches_stored"]


@pytest.mark.asyncio
async def test_every_candidate_has_outcome_across_stages(monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", lambda text: text)

    def extract(**kwargs):
        name = kwargs["candidate_id"]
        if name == "broken":
            raise RuntimeError("extractor unavailable")
        return {"candidate_id": name, "composite_score": 10 if name == "top" else 1,
                "evidence_by_category": {category: [{"text": "Evidence"}] for category in
                                         ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")}}

    monkeypatch.setattr(orchestrator, "extract_candidate_category_evidence", extract)
    candidates = [{"candidate_id": name, "raw_cv_text": "Evidence",
                   "recruiter_overrides": {"work_authorized": "eligible"}} for name in
                  ("top", "other", "broken")]
    candidates += [{"candidate_id": "review", "raw_cv_text": "Evidence"},
                   {"candidate_id": "rejected", "raw_cv_text": "Evidence",
                    "recruiter_overrides": {"work_authorized": "ineligible"}}]
    payload = {"job_profile": {"job_id": "all-stages", "title": "Engineer", "jd_category_queries": {}},
               "candidates": candidates, "top_n_stage2_cutoff": 1}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["accounted_candidates"] == 5
    async with TestingSessionLocal() as session:
        rows = (await session.execute(select(CandidateOutcomeModel))).scalars().all()
        assert {row.candidate_id: row.outcome for row in rows} == {
            "top": "SUCCESS", "other": "CUTOFF_EXCLUDED", "broken": "EXTRACTION_FAILED",
            "review": "CUTOFF_EXCLUDED", "rejected": "FILTER_REJECTED"}
        assert next(row for row in rows if row.candidate_id == "review").result_snapshot["verification_required"]
        assert all(row.stage_history for row in rows)


@pytest.mark.asyncio
async def test_failed_publish_rolls_back_and_retry_reuses_run(monkeypatch):
    from app.api import endpoints
    actual = endpoints.complete_run
    calls = 0

    async def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("persistence interrupted")
        return await actual(*args, **kwargs)

    monkeypatch.setattr(endpoints, "complete_run", interrupted)
    payload = {"job_profile": {"job_id": "retry", "title": "Engineer", "jd_category_queries": {}},
               "candidates": [{"candidate_id": "review", "raw_cv_text": "Test"}],
               "idempotency_key": "retry-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="persistence interrupted"):
            await client.post("/api/v1/screening/run", json=payload)
        async with TestingSessionLocal() as session:
            pending = (await session.execute(select(CandidateOutcomeModel))).scalars().all()
            assert len(pending) == 1 and pending[0].outcome == "PENDING"
        response = await client.post("/api/v1/screening/run", json=payload)
    assert response.status_code == 200
    async with TestingSessionLocal() as session:
        runs = (await session.execute(select(ScreeningRunModel))).scalars().all()
        outcomes = (await session.execute(select(CandidateOutcomeModel))).scalars().all()
        assert len(runs) == len(outcomes) == 1
        assert runs[0].id == response.json()["run_id"]
        assert runs[0].status == "COMPLETED"


@pytest.mark.asyncio
async def test_identical_submission_without_supplied_key_replays():
    payload = {"job_profile": {"job_id": "automatic-key", "title": "Engineer",
                               "jd_category_queries": {}},
               "candidates": [{"candidate_id": "review", "raw_cv_text": "Test"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/screening/run", json=payload)
        second = await client.post("/api/v1/screening/run", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["idempotency_key"]
    async with TestingSessionLocal() as session:
        assert len((await session.execute(select(ScreeningRunModel))).scalars().all()) == 1
