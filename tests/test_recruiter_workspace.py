"""Recruiter workflows: complete-pool filtering, protected CVs and durable decisions."""
import hashlib
import json
import runpy
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import Principal, current_principal
from app.main import app
from app.models.database import (Base, CampaignModel, CampaignCVModel, CampaignJDModel,
    CampaignPairModel, CandidateReviewModel, CandidateReviewEventModel, RecruiterUserModel, get_db)

PDF = b"%PDF-1.4\noriginal candidate document"


@pytest_asyncio.fixture
async def workspace(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = [Principal("alice", "recruiter")]
    async def database():
        async with sessions() as db:
            yield db
    async def identity():
        return principal[0]
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_principal] = identity
    monkeypatch.setattr("app.api.recruiter.decrypt_bytes", lambda value: value.removeprefix(b"encrypted:"))
    monkeypatch.setattr("app.api.recruiter.encrypt_payload", lambda value: b"encrypted:" + value)
    async with sessions() as db:
        for owner in ("alice", "bob"):
            db.add(CampaignModel(id=owner, owner_id=owner, name=f"{owner} hiring", request_hash=owner))
            db.add(RecruiterUserModel(id=owner, username=owner, email=f"{owner}@test.example", password_hash="unused"))
        await db.flush()
        target = {"target_id": "python", "text": "Python", "treatment": "requirement", "category": "SKILLS"}
        db.add(CampaignJDModel(id="role", campaign_id="alice", jd_key="engineering",
            job_snapshot={"title": "Engineer", "relevance_contract": {"targets": [target]}}, policy_snapshot={"category_weights": {"skills": "0.4"}}))
        db.add(CampaignJDModel(id="other-role", campaign_id="alice", jd_key="operations", job_snapshot={"title": "Operations"}, policy_snapshot={}))
        db.add(CampaignJDModel(id="bob-role", campaign_id="bob", jd_key="private", job_snapshot={"title": "Private"}, policy_snapshot={}))
        await db.flush()
        for index in range(36):
            cv = CampaignCVModel(id=f"cv-{index}", campaign_id="alice", candidate_id=f"candidate-{index}",
                source_filename=f"Person {index:02d}.pdf", content_hash=hashlib.sha256(PDF).hexdigest(), stage0_status="SUCCEEDED",
                redacted_text="Built Python APIs" if index == 35 else "Other experience",
                source_locations=[{"page_number": 1, "blocks": [{"block_number": 0, "text": "Built Python APIs", "bbox": [0, 0, 100, 20]}]}],
                encrypted_original_pdf=b"encrypted:" + PDF if index == 35 else None)
            db.add(cv)
            await db.flush()
            failed = index == 0
            snapshot = {} if failed else {"stage2_evidence": {"target_assessments": [{"target_id": "python", "target_text": "Python", "category": "SKILLS", "coverage": 1 if index == 35 else 0, "status": "DIRECT" if index == 35 else "MISSING_INFORMATION", "supporting_text": "Built Python APIs" if index == 35 else ""}]},
                "stage3_evaluation": {"evaluation_status": "SUCCESS", "scoring_policy_version": "v1", "category_scores": {"skills": 95, "experience": 65, "projects": 70, "education": 50},
                    "llm_raw_output": {"executive_summary": "Documented API delivery", "skills": {"score": 95, "rationale": "Python delivery", "citations": ["e1"]}, "flags": []},
                    "verified_citations": ["e1"], "evidence_verification": {"registry": {"e1": {"text": "Built Python APIs", "source_location": {"page_number": 1}}}}}}
            db.add(CampaignPairModel(id=f"pair-{index}", campaign_id="alice", cv_id=cv.id, jd_id="role",
                status="FILTER_REJECTED" if failed else "SUCCESS", composite_score=None if failed else index,
                stage1_decision="FAIL" if failed else "PASS", stage1_details={"checks": [{"rule": "experience", "status": "FAIL" if failed else "PASS", "code": "INSUFFICIENT_EXPERIENCE" if failed else "EXPERIENCE_MET", "message": "Below minimum" if failed else "Meets experience"}],
                "metrics": {"candidate_yoe": 0 if failed else 3}}, result_snapshot=snapshot))
        db.add(CampaignPairModel(id="cross-role", campaign_id="alice", cv_id="cv-35", jd_id="other-role", status="CUTOFF_EXCLUDED"))
        db.add(CampaignCVModel(id="bob-cv", campaign_id="bob", candidate_id="private-id", source_filename="Private.pdf", content_hash="secret"))
        await db.flush()
        db.add(CampaignPairModel(id="private-pair", campaign_id="bob", cv_id="bob-cv", jd_id="bob-role"))
        await db.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions, principal
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_principal, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pool_filters_full_pool_before_pagination_and_separates_unknown(workspace):
    client, _, _ = workspace
    url = "/api/v1/recruiter/campaigns/alice/roles/engineering/pool"
    result = (await client.get(url, params={"limit": 1, "filters": json.dumps({"search": "Python", "sort": "name", "direction": "asc"})})).json()
    assert result["total"] == 1 and result["results"][0]["candidate_id"] == "candidate-35"
    assert result["analytics"]["total"] == 36 and result["analytics"]["assessed"] == 35
    assert result["filtered_analytics"]["total"] == 1
    states = result["analytics"]["requirements"][0]["counts"]
    assert states == {"UNASSESSED": 1, "MISSING": 34, "DIRECT": 1}
    for state, count in (("DIRECT", 1), ("MISSING", 34), ("UNASSESSED", 1)):
        body = (await client.get(url, params={"filters": json.dumps({"requirement_ids": ["python"], "requirement_status": state})})).json()
        assert body["total"] == count
    assert (await client.get(url, params={"filters": '{"min_score": 70, "max_score": 10}'})).status_code == 422
    assert (await client.get(url, params={"filters": '{"invented": true}'})).status_code == 422
    rejected = (await client.get(url, params={"filters": '{"eligibility_rule":"experience","eligibility_status":"FAIL"}'})).json()
    assert rejected["total"] == 1 and rejected["results"][0]["score"] is None


@pytest.mark.asyncio
async def test_candidate_search_history_evidence_and_document_access(workspace):
    client, _, principal = workspace
    found = (await client.get("/api/v1/recruiter/candidates", params={"search": "Person 00"})).json()
    assert found["total"] == 1
    row = found["results"][0]
    assert row["status"] == "FILTER_REJECTED" and row["stage_history"][1]["reason"] == "Below minimum"
    assert row["stage_history"][3]["state"] == "NOT_REACHED"
    detailed = (await client.get("/api/v1/recruiter/pairs/pair-35")).json()
    assert detailed["assessments"]["skills"]["rationale"] == "Python delivery"
    assert detailed["evidence"][0]["text"] == "Built Python APIs"
    assert detailed["other_roles"][0]["status"] == "CUTOFF_EXCLUDED"
    document = "/api/v1/recruiter/campaigns/alice/candidates/candidate-35/document"
    pdf = await client.get(document)
    assert pdf.content == PDF and pdf.headers["cache-control"] == "no-store"
    assert "application/pdf" in pdf.headers["content-type"]
    assert (await client.get("/api/v1/recruiter/campaigns/alice/candidates/candidate-0/document")).status_code == 404
    restored = await client.put("/api/v1/recruiter/campaigns/alice/candidates/candidate-0/document", content=PDF, headers={"Content-Type": "application/pdf"})
    assert restored.status_code == 200
    assert (await client.put(document, content=b"%PDF-different", headers={"Content-Type": "application/pdf"})).status_code == 422
    principal[0] = Principal("bob", "recruiter")
    assert (await client.get(document)).status_code == 404
    assert (await client.get("/api/v1/recruiter/pairs/pair-35")).status_code == 404
    assert (await client.get("/api/v1/recruiter/candidates", params={"search": "Person"})).json()["total"] == 0


@pytest.mark.asyncio
async def test_candidate_search_includes_intake_rejections_without_fabricating_candidate_records(workspace):
    client, sessions, principal = workspace
    async with sessions() as db:
        campaign = await db.get(CampaignModel, "alice")
        campaign.intake_report = {"rejected": [{"name": "Person Bad.pdf", "code": "CORRUPT_MEMBER"}]}
        await db.commit()
    response = (await client.get("/api/v1/recruiter/candidates", params={"search": "Person Bad"})).json()
    assert response["total"] == 0 and response["results"] == []
    assert response["intake_rejection_total"] == 1 and response["intake_rejections"][0]["reason"] == "CORRUPT_MEMBER"
    principal[0] = Principal("bob", "recruiter")
    assert (await client.get("/api/v1/recruiter/candidates", params={"search": "Person Bad"})).json()["intake_rejections"] == []


@pytest.mark.asyncio
async def test_durable_reviews_audit_conflicts_atomic_bulk_and_exports(workspace):
    client, sessions, principal = workspace
    body = {"pair_versions": {"pair-35": 0}, "decision": "SHORTLIST", "reason": "Strong Python delivery", "notes": "=unsafe formula", "tags": ["Interview"]}
    assert (await client.patch("/api/v1/recruiter/reviews", json=body)).status_code == 200
    assert (await client.patch("/api/v1/recruiter/reviews", json=body)).status_code == 409
    detail = (await client.get("/api/v1/recruiter/pairs/pair-35")).json()
    assert detail["review"]["version"] == 1 and detail["history"][0]["actor_id"] == "alice"
    assert detail["score"] == 35 and detail["status"] == "SUCCESS"
    invalid = {"pair_versions": {"pair-00": 0, "pair-35": 0}, "decision": "HOLD"}
    # A real earlier row is modified before the later conflicting row; rollback must undo it.
    invalid["pair_versions"] = {"pair-0": 0, "pair-35": 0}
    assert (await client.patch("/api/v1/recruiter/reviews", json=invalid)).status_code == 409
    async with sessions() as db:
        assert await db.get(CandidateReviewModel, "pair-0") is None
        assert len((await db.execute(sa.select(CandidateReviewEventModel))).scalars().all()) == 1
    export = await client.get("/api/v1/recruiter/campaigns/alice/roles/engineering/export", params={"filters": '{"decision":"SHORTLIST"}'})
    assert "'=unsafe formula" in export.text and "Person 35.pdf" in export.text and "Person 34.pdf" not in export.text
    assert (await client.patch("/api/v1/recruiter/reviews", json={"pair_versions": {"pair-0": 0}, "decision": "NOT_PROCEEDING"})).status_code == 422
    principal[0] = Principal("bob", "recruiter")
    assert (await client.patch("/api/v1/recruiter/reviews", json={"pair_versions": {"pair-35": 1}, "decision": "HOLD"})).status_code == 404


@pytest.mark.asyncio
async def test_saved_views_private_and_campaign_reviewers_revocable(workspace):
    client, _, principal = workspace
    saved = (await client.post("/api/v1/recruiter/views", json={"name": "Python prospects", "filters": {"requirement_ids": ["python"]}, "columns": ["requirements", "strengths"]})).json()
    assert saved["columns"] == ["requirements", "strengths"]
    principal[0] = Principal("bob", "recruiter")
    assert (await client.get("/api/v1/recruiter/views")).json() == []
    assert (await client.delete(f'/api/v1/recruiter/views/{saved["id"]}')).status_code == 404
    principal[0] = Principal("alice", "recruiter")
    assert (await client.post("/api/v1/recruiter/campaigns/alice/members", json={"username": "bob"})).status_code == 200
    principal[0] = Principal("bob", "recruiter")
    assert (await client.get("/api/v1/recruiter/pairs/pair-35")).status_code == 200
    assert (await client.get("/api/v1/campaigns")).json()["total"] == 2
    assert (await client.delete("/api/v1/recruiter/campaigns/alice/members/bob")).status_code == 403
    principal[0] = Principal("alice", "recruiter")
    assert (await client.delete("/api/v1/recruiter/campaigns/alice/members/bob")).status_code == 204
    principal[0] = Principal("bob", "recruiter")
    assert (await client.get("/api/v1/recruiter/pairs/pair-35")).status_code == 404


@pytest.mark.asyncio
async def test_verified_facts_update_filters_without_rewriting_screening(workspace):
    client, sessions, _ = workspace
    async with sessions() as db:
        jd = await db.get(CampaignJDModel, "role")
        jd.job_snapshot = {**jd.job_snapshot, "hard_filter_rules": {"min_years_experience": 2, "require_work_authorization": True}}
        pair = await db.get(CampaignPairModel, "pair-35")
        pair.verification_required = True
        pair.verification_reasons = [{"rule": "authorization", "status": "REVIEW", "message": "Authorization unknown"}]
        pair.stage1_details = {**pair.stage1_details, "checks": pair.stage1_details["checks"] + [{"rule": "authorization", "status": "REVIEW", "code": "AUTHORIZATION_UNKNOWN", "message": "Authorization unknown"}]}
        await db.commit()
    update = {"pair_versions": {"pair-35": 0}, "decision": "HOLD", "verified_facts": {"experience_years": 8, "work_authorized": "eligible"}}
    assert (await client.patch("/api/v1/recruiter/reviews", json=update)).status_code == 200
    row = (await client.get("/api/v1/recruiter/pairs/pair-35")).json()
    assert row["experience_years"] == 3 and row["effective_experience_years"] == 8
    assert row["stage1_checks"][-1]["status"] == "REVIEW" and row["effective_eligibility_checks"][-1]["status"] == "PASS"
    assert row["original_verification_reasons"] and not row["verification_reasons"]
    assert row["score"] == 35 and not row["provisional"]
    assert row["history"][0]["snapshot"]["verified_facts"]["experience_years"] == 8
    filtered = (await client.get("/api/v1/recruiter/campaigns/alice/roles/engineering/pool", params={"filters": '{"min_years":7,"eligibility_rule":"authorization","eligibility_status":"PASS"}'})).json()
    assert filtered["total"] == 1
    update["pair_versions"] = {"pair-35": 1}
    update["verified_facts"]["experience_years"] = True
    assert (await client.patch("/api/v1/recruiter/reviews", json=update)).status_code == 422


def test_workspace_migration_preserves_pending_documents_and_existing_data():
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/f04d8c912e63_recruiter_workspace.py"))
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        conn.execute(sa.text("CREATE TABLE campaigns (id VARCHAR(64) PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE campaign_pairs (id VARCHAR(64) PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE campaign_cvs (id VARCHAR(64) PRIMARY KEY, encrypted_pdf BLOB)"))
        conn.execute(sa.text("INSERT INTO campaign_cvs VALUES ('pending', X'0102'), ('purged', NULL)"))
        migration["upgrade"]()
        assert conn.execute(sa.text("SELECT encrypted_original_pdf FROM campaign_cvs WHERE id='pending'")).scalar() == b"\x01\x02"
        assert conn.execute(sa.text("SELECT encrypted_original_pdf FROM campaign_cvs WHERE id='purged'")).scalar() is None
        migration["downgrade"]()
        assert conn.execute(sa.text("SELECT COUNT(*) FROM campaign_cvs")).scalar() == 2
    engine.dispose()
