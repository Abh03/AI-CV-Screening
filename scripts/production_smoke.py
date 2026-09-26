"""Exercise authenticated API, queue, worker, and persistence with synthetic input."""
import asyncio
import base64
import getpass
import json
import os
from uuid import uuid4

import fitz
import httpx
from dotenv import dotenv_values


async def main():
    values = dotenv_values("docker/.env")
    legacy = values.get("API_TOKENS_JSON")
    token = json.loads(legacy)[0]["token"] if legacy else None
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "EXPERIENCE")
    page.insert_text((40, 90), "Synthetic software engineering experience in Python and databases")
    pdf = document.tobytes()
    document.close()
    key = uuid4().hex
    payload = {
        "candidate_id": "synthetic-" + key,
        "pdf_base64": base64.b64encode(pdf).decode(),
        "work_authorized": "ineligible",
        "authorization_source": "recruiter_verified",
        "idempotency_key": key,
    }
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=15) as client:
        for _ in range(60):
            try:
                if ((await client.get("/health")).status_code == 200
                        and (await client.get("/ready")).status_code == 200):
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        else:
            raise TimeoutError("API did not become ready within 60 seconds")
        route = "/api/v1/screening/runs/unknown"
        assert (await client.get(route)).status_code == 401
        if token is None:
            identifier = os.environ.get("SMOKE_USER") or input("Recruiter email or username: ")
            password = getpass.getpass("Recruiter password: ")
            login = await client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
            assert login.status_code == 200, f"login status {login.status_code}"
            token = login.cookies["cv_access"]
        headers = {"Authorization": "Bearer " + token}
        jd_document = fitz.open()
        jd_document.new_page().insert_text((40, 40),
            "Synthetic Engineer\nThe software engineer will work on Python software projects.\nWork authorization is required for this software developer role.")
        jd_pdf = jd_document.tobytes()
        jd_document.close()
        extracted = await client.post("/api/v1/jds/extract", content=jd_pdf,
            headers={**headers, "Content-Type": "application/pdf"}, timeout=180)
        extracted.raise_for_status()
        draft = extracted.json()
        if draft["status"] != "REVIEW":
            raise RuntimeError(f"JD extraction failed: {draft['error_code']}")
        # Explicitly approve a synthetic requirement to exercise deterministic rejection.
        profile = draft["profile"]
        profile["hard_filter_rules"]["require_work_authorization"] = True
        approved = await client.post(f"/api/v1/jds/drafts/{draft['draft_id']}/approve", json=profile, headers=headers)
        approved.raise_for_status()
        payload["approved_jd_id"] = approved.json()["approved_jd_id"]
        response = await client.post("/api/v1/screening/submit-pdf", json=payload, headers=headers)
        assert response.status_code == 202, f"submission status {response.status_code}"
        run_id = response.json()["run_id"]
        for _ in range(60):
            await asyncio.sleep(1)
            response = await client.get(f"/api/v1/screening/runs/{run_id}", headers=headers)
            assert response.status_code == 200
            result = response.json()
            if result["status"] == "COMPLETED":
                assert result["result"]["metrics"]["stage1_rejected"] == 1
                print("PASS: authenticated submission, queue, worker, persistence, readiness")
                return
            if result["status"] == "FAILED":
                raise RuntimeError(f"worker failure: {result['failure_code']}")
        raise TimeoutError("worker did not finish within 60 seconds")


if __name__ == "__main__":
    asyncio.run(main())
