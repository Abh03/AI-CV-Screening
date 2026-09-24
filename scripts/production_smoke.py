"""Exercise authenticated API, queue, worker, and persistence with synthetic input."""
import asyncio
import base64
import json
from uuid import uuid4

import fitz
import httpx
from dotenv import dotenv_values


async def main():
    values = dotenv_values("docker/.env")
    token = json.loads(values["API_TOKENS_JSON"])[0]["token"]
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "EXPERIENCE")
    page.insert_text((40, 90), "Synthetic software engineering experience in Python and databases")
    pdf = document.tobytes()
    document.close()
    key = uuid4().hex
    payload = {
        "job_profile": {"job_id": "smoke-" + key, "title": "Synthetic Engineer",
                        "jd_category_queries": {}},
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
        headers = {"Authorization": "Bearer " + token}
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
