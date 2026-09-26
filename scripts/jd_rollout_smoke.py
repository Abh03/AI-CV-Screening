"""Run a one-JD, one-CV live rollout check using synthetic documents."""
import asyncio
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import fitz
import httpx
from dotenv import dotenv_values

from scripts.campaign_load_test import reconcile


def pdf(text):
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), text)
        return document.tobytes()


async def main():
    values = dotenv_values("docker/.env")
    token = json.loads(values["API_TOKENS_JSON"])[0]["token"]
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", headers=headers, timeout=180) as client:
        (await client.get("/ready")).raise_for_status()
        extracted = await client.post("/api/v1/jds/extract", content=pdf(
            "Synthetic Python Engineer\nPython is the only required skill.\n"
            "Build Python APIs and database services.\n"
            "Projects should demonstrate Python API development.\n"
            "No minimum experience, degree or work authorization requirement.\n"
            "Rollout reference " + uuid4().hex), headers={"Content-Type": "application/pdf"})
        extracted.raise_for_status()
        draft = extracted.json()
        assert draft["status"] == "REVIEW", draft.get("error_code")
        profile = draft["profile"]
        profile["hard_filter_rules"] = {"require_work_authorization": False}
        approved = await client.post(f"/api/v1/jds/drafts/{draft['draft_id']}/approve", json=profile)
        approved.raise_for_status()
        approved_id = approved.json()["approved_jd_id"]
        created = await client.post("/api/v1/campaigns", json={
            "approved_jd_ids": [approved_id], "idempotency_key": "jd-rollout-" + uuid4().hex})
        created.raise_for_status()
        campaign_id = created.json()["campaign_id"]
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("synthetic-candidate.pdf", pdf(
                "Synthetic Candidate\nEXPERIENCE\n"
                "Developed Python APIs and PostgreSQL database services for five years.\n"
                "SKILLS\nPython, APIs, PostgreSQL, testing, Git, Docker.\n"
                "PROJECTS\nBuilt and tested a Python API and database reporting service.\n"
                "EDUCATION\nBachelor of Science in Computer Science."))
        uploaded = await client.post(f"/api/v1/campaigns/{campaign_id}/archive",
            content=archive.getvalue(), headers={"Content-Type": "application/zip"})
        uploaded.raise_for_status()
        assert uploaded.json()["accepted_count"] == 1
        for _ in range(120):
            status_response = await client.get(f"/api/v1/campaigns/{campaign_id}")
            status_response.raise_for_status()
            status = status_response.json()
            if status["status"] in {"COMPLETED", "FAILED"}:
                break
            await asyncio.sleep(2)
        jds_response = await client.get(f"/api/v1/campaigns/{campaign_id}/jds")
        jds_response.raise_for_status()
        accounting = reconcile(status, jds_response.json())
        report = {"date": datetime.now(timezone.utc).date().isoformat(), "provider": values["LLM_PROVIDER"],
            "fixture": "synthetic text PDFs; one JD and one CV",
            "approved_jd_id": approved_id, "campaign_id": campaign_id,
            "status": status["status"], "accounting": accounting}
        Path("docs/jd-rollout-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        assert status["status"] == "COMPLETED"
        assert accounting["reconciled"] and accounting["terminal_pairs"] == 1
        assert sum(accounting["pair_statuses"].get(s, 0) for s in ("SUCCESS", "REVIEW_REQUIRED")) == 1


if __name__ == "__main__":
    asyncio.run(main())
