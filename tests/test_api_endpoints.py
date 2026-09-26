import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.main import app
from app.config import settings
from app.models.database import Base, get_db
from app.stage0_extraction import pipeline as pdf_pipeline
import base64
import fitz


@pytest.mark.asyncio
async def test_async_submission_completion_and_redelivery(monkeypatch):
    from cryptography.fernet import Fernet
    from app.workers import tasks
    from app.models.database import CandidateOutcomeModel

    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())

    queued = []
    monkeypatch.setattr(tasks.screening_task, "delay", lambda run_id: queued.append(run_id))
    monkeypatch.setattr(tasks, "AsyncSessionLocal", TestingSessionLocal)

    class NoDispose:
        async def dispose(self):
            pass

    monkeypatch.setattr(tasks, "engine", NoDispose())
    payload = {"job_profile": {"job_id": "async-job", "title": "Engineer",
                               "jd_category_queries": {}},
               "candidates": [{"candidate_id": "async-candidate", "raw_cv_text": "Experience unknown"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/screening/submit", json=payload)
        second = await client.post("/api/v1/screening/submit", json=payload)
        assert first.status_code == 202
        assert second.json()["run_id"] == first.json()["run_id"]
        assert len(queued) == 1
        pending = await client.get(first.json()["result_url"])
        assert pending.json()["status"] == "QUEUED"
        from app.models.database import ScreeningRunModel
        async with TestingSessionLocal() as session:
            reserved = await session.get(ScreeningRunModel, queued[0])
            assert "Experience unknown" not in str(reserved.request_snapshot)
        assert await tasks.execute_run(queued[0]) == "COMPLETED"
        assert await tasks.execute_run(queued[0]) == "ALREADY_COMPLETE"
        finished = await client.get(first.json()["result_url"])
        assert finished.json()["status"] == "COMPLETED"
        assert finished.json()["result"]["metrics"]["accounted_candidates"] == 1
    async with TestingSessionLocal() as session:
        outcomes = (await session.execute(select(CandidateOutcomeModel))).scalars().all()
        assert len(outcomes) == 1
        assert outcomes[0].outcome != "PENDING"


@pytest.mark.asyncio
async def test_async_pdf_submission_runs_ocr_in_worker(monkeypatch):
    from cryptography.fernet import Fernet
    from app.workers import tasks
    from app import config

    queued = []
    monkeypatch.setattr(tasks.screening_task, "delay", lambda run_id: queued.append(run_id))
    monkeypatch.setattr(tasks, "AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(config.settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())

    class NoDispose:
        async def dispose(self):
            pass

    monkeypatch.setattr(tasks, "engine", NoDispose())
    document = fitz.open()
    page = document.new_page()
    page.insert_text((70, 70), "Experience unknown. Skills unknown.")
    pdf = document.tobytes()
    document.close()
    payload = {"job_profile": {"job_id": "pdf-async", "title": "Engineer",
                               "jd_category_queries": {}},
               "candidate_id": "pdf-candidate",
               "pdf_base64": base64.b64encode(pdf).decode()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        submitted = await client.post("/api/v1/screening/submit-pdf", json=payload)
        assert submitted.status_code == 202
        assert len(queued) == 1
        assert await tasks.execute_run(queued[0]) == "COMPLETED"
        finished = await client.get(submitted.json()["result_url"])
        assert finished.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_expired_worker_lease_is_recovered(monkeypatch):
    from cryptography.fernet import Fernet
    from datetime import datetime, timedelta, timezone
    from app.workers import tasks
    from app.models.database import ScreeningRunModel

    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())

    monkeypatch.setattr(tasks.screening_task, "delay", lambda run_id: None)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", TestingSessionLocal)

    class NoDispose:
        async def dispose(self):
            pass

    monkeypatch.setattr(tasks, "engine", NoDispose())
    payload = {"job_profile": {"job_id": "restart-job", "title": "Engineer",
                               "jd_category_queries": {}},
               "candidates": [{"candidate_id": "restart-candidate", "raw_cv_text": "Unknown"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        run_id = (await client.post("/api/v1/screening/submit", json=payload)).json()["run_id"]
        async with TestingSessionLocal() as session:
            run = await session.get(ScreeningRunModel, run_id)
            run.status = "RUNNING"
            run.attempt_count = 1
            run.lease_owner = "interrupted-worker"
            run.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        assert run_id in await tasks.find_recoverable_runs()
        assert await tasks.execute_run(run_id) == "COMPLETED"
        status = (await client.get(f"/api/v1/screening/runs/{run_id}")).json()
        assert status["status"] == "COMPLETED"
        assert status["attempt_count"] == 2


@pytest.mark.asyncio
async def test_async_pdf_invalid_document_has_actionable_failure(monkeypatch):
    from cryptography.fernet import Fernet
    from app.workers import tasks
    from app import config

    monkeypatch.setattr(tasks.screening_task, "delay", lambda run_id: None)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(config.settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())

    class NoDispose:
        async def dispose(self):
            pass

    monkeypatch.setattr(tasks, "engine", NoDispose())
    payload = {"job_profile": {"job_id": "bad-pdf", "title": "Engineer",
                               "jd_category_queries": {}},
               "candidate_id": "bad-candidate",
               "pdf_base64": base64.b64encode(b"not a PDF").decode()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        run_id = (await client.post("/api/v1/screening/submit-pdf", json=payload)).json()["run_id"]
        with pytest.raises(tasks.NonRetryableRunError):
            await tasks.execute_run(run_id)
        status = (await client.get(f"/api/v1/screening/runs/{run_id}")).json()
        assert status["status"] == "FAILED"
        assert status["failure_code"] == "INVALID_PDF"

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
                "work_authorized": "eligible",
                "authorization_source": "recruiter_verified",
                "parsed_attributes": {"experience_years": 4.0, "experience_source": "recruiter_verified"}
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"rules": {"min_years_experince": 5}},
    {"rules": {"degree_requirement": {"level": "unknown-degree"}}},
    {"rules": {"require_work_authorization": "false"}},
    {"attrs": {"experience_years": -1}},
    {"attrs": {"experience_years": "5"}},
    {"attrs": {"experience_years": float("nan")}},
    {"attrs": {"experience_years": float("inf")}},
    {"candidate": {"work_authorized": "false"}},
    {"candidate": {"authorization_source": "self_certified"}},
])
async def test_invalid_stage1_inputs_return_422(mutation):
    import json
    payload = {"job_profile": {"job_id": "invalid", "title": "Engineer", "jd_category_queries": {}},
               "candidates": [{"candidate_id": "a", "raw_cv_text": "Test"}]}
    if "rules" in mutation:
        payload["job_profile"]["hard_filter_rules"] = mutation["rules"]
    if "attrs" in mutation:
        payload["candidates"][0]["parsed_attributes"] = mutation["attrs"]
    if "candidate" in mutation:
        payload["candidates"][0].update(mutation["candidate"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run", content=json.dumps(payload),
                                     headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


@pytest.mark.asyncio
async def test_unknown_authorization_returns_review_through_api():
    payload = {"job_profile": {"job_id": "review", "title": "Engineer", "jd_category_queries": {}},
               "candidates": [{"candidate_id": "a", "raw_cv_text": "Test"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["leaderboard"] == result["rejected_candidates"] == []
    assert result["metrics"]["stage1_review_required"] == 1
    outcome = (result["leaderboard"] + result["review_candidates"] + result["failed_candidates"])[0]
    assert outcome["stage1_filter_details"]["checks"][0]["code"] == "AUTHORIZATION_UNKNOWN"
    assert outcome["verification_required"] is True


@pytest.mark.asyncio
async def test_pdf_api_passes_only_redacted_provenance_to_screening(monkeypatch, caplog):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 40), "Jane Doe")
    page.insert_text((40, 80), "EXPERIENCE")
    page.insert_text((40, 110), "software engineer with experience in python and java systems")
    pdf = doc.tobytes()
    doc.close()
    monkeypatch.setattr(pdf_pipeline, "assess_readable_extraction_integrity", lambda text, **kwargs: {"requires_ocr": False, "passed": True})
    observed = {}

    async def fake_screening(**kwargs):
        observed.update(kwargs)
        return {"metrics": {"total_input_candidates": 1, "stage0_processed": 1,
                            "stage1_passed": 0, "stage1_rejected": 0, "stage1_review_required": 1,
                            "stage2_shortlisted": 0, "stage3_evaluated": 0, "stage3_succeeded": 0,
                            "stage3_review_required": 0, "stage3_failed": 0,
                            "stage0_failed": 0, "stage2_failed": 0, "stage2_excluded": 1,
                            "accounted_candidates": 1},
                "leaderboard": [], "rejected_candidates": [{"candidate_id": "pdf_candidate", "stage": "STAGE2",
                                       "reason": "CUTOFF_EXCLUDED"}],
                "review_candidates": [], "failed_candidates": [],
                "outcomes": [{"candidate_id": "pdf_candidate", "outcome": "CUTOFF_EXCLUDED",
                              "stage": "STAGE2", "input_snapshot": {"candidate_id": "pdf_candidate"},
                              "stage_history": [{"stage": "STAGE1", "status": "REVIEW"}],
                              "result_snapshot": {"candidate_id": "pdf_candidate",
                                                  "reason": "CUTOFF_EXCLUDED"}}]}

    monkeypatch.setattr("app.api.endpoints.run_end_to_end_screening_pipeline", fake_screening)
    payload = {"job_profile": {"job_id": "pdf_job", "title": "Engineer", "jd_category_queries": {}},
               "candidate_id": "pdf_candidate", "pdf_base64": base64.b64encode(pdf).decode()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run-pdf", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "Jane Doe" not in observed["raw_candidates"][0]["raw_cv_text"]
    assert "Jane Doe" not in caplog.text
    assert observed["stage0_views"]["pdf_candidate"].pages[0]["blocks"][0]["bbox"]


@pytest.mark.asyncio
async def test_pdf_api_rejects_malformed_document():
    payload = {"job_profile": {"job_id": "pdf_bad", "title": "Engineer", "jd_category_queries": {}},
               "candidate_id": "bad", "pdf_base64": base64.b64encode(b"invalid").decode()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run-pdf", json=payload)
    assert {key: response.json()[key] for key in ("status", "code", "candidate_id")} == {
        "status": "failure", "code": "INVALID_PDF", "candidate_id": "bad"}
    assert response.json()["run_id"]


@pytest.mark.asyncio
async def test_pdf_ocr_review_is_audited(monkeypatch):
    from app.api import endpoints
    from app.models.database import CandidateOutcomeModel
    from app.stage0_extraction.pipeline import PDFIngestionResult

    monkeypatch.setattr(endpoints, "ingest_pdf", lambda data: PDFIngestionResult("review", "OCR_TIMEOUT"))
    payload = {"job_profile": {"job_id": "pdf_review", "title": "Engineer", "jd_category_queries": {}},
               "candidate_id": "candidate", "pdf_base64": base64.b64encode(b"%PDF-test").decode()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/run-pdf", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "review"
    async with TestingSessionLocal() as session:
        row = (await session.execute(select(CandidateOutcomeModel))).scalar_one()
        assert row.outcome == "REVIEW_REQUIRED"
        assert row.result_snapshot["reason"] == "OCR_TIMEOUT"


@pytest.mark.asyncio
async def test_binary_pdf_upload_has_structured_outcomes(monkeypatch):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 40), "Jane Doe")
    page.insert_text((40, 80), "EXPERIENCE")
    page.insert_text((40, 110), "software engineer with experience in python and java systems")
    pdf = doc.tobytes()
    doc.close()
    monkeypatch.setattr(pdf_pipeline, "assess_readable_extraction_integrity", lambda text, **kwargs: {"requires_ocr": False, "passed": True})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        good = await client.post("/api/v1/screening/ingest-pdf", content=pdf,
                                 headers={"Content-Type": "application/pdf"})
        bad = await client.post("/api/v1/screening/ingest-pdf", content=b"bad",
                                headers={"Content-Type": "application/pdf"})
    assert good.json()["status"] == "success"
    assert "Jane Doe" not in str(good.json())
    assert bad.json()["code"] == "INVALID_PDF"
