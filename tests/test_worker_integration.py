"""Exercises PostgreSQL reservation, Redis delivery, and a live Celery worker."""
import asyncio
import base64
from uuid import uuid4

import pytest
import fitz
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.infrastructure
@pytest.mark.worker
@pytest.mark.asyncio
async def test_queued_pdf_reaches_worker_and_records_failure():
    run_key = uuid4().hex
    payload = {
        "job_profile": {"job_id": "ci-" + run_key, "title": "Engineer", "jd_category_queries": {}},
        "candidate_id": "ci-candidate-" + run_key,
        "pdf_base64": base64.b64encode(b"not a PDF").decode(),
        "idempotency_key": run_key,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/submit-pdf", json=payload)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(60):
            await asyncio.sleep(1)
            status = await client.get(f"/api/v1/screening/runs/{run_id}")
            if status.json()["status"] == "FAILED":
                assert status.json()["failure_code"] == "INVALID_PDF"
                return
    pytest.fail("Worker did not finish the queued run within 60 seconds")


@pytest.mark.infrastructure
@pytest.mark.worker
@pytest.mark.asyncio
async def test_queued_pdf_completes_and_persists_filter_result():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "EXPERIENCE")
    page.insert_text((40, 90), "Software engineer with Python, databases and deployment experience")
    pdf = document.tobytes()
    document.close()
    run_key = uuid4().hex
    payload = {
        "job_profile": {"job_id": "ci-" + run_key, "title": "Engineer", "jd_category_queries": {}},
        "candidate_id": "ci-candidate-" + run_key,
        "pdf_base64": base64.b64encode(pdf).decode(),
        "work_authorized": "ineligible",
        "authorization_source": "recruiter_verified",
        "idempotency_key": run_key,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/screening/submit-pdf", json=payload)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(60):
            await asyncio.sleep(1)
            status = (await client.get(f"/api/v1/screening/runs/{run_id}")).json()
            if status["status"] == "COMPLETED":
                assert status["result"]["metrics"]["stage1_rejected"] == 1
                return
            if status["status"] == "FAILED":
                pytest.fail(f"Worker failed: {status['failure_code']}")
    pytest.fail("Worker did not complete the queued run within 60 seconds")
