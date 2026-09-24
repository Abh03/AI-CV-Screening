import pytest
from app.orchestrator import run_end_to_end_screening_pipeline
from app.stage3_evaluation.schemas import DecisionTier


@pytest.mark.asyncio
async def test_stage1_review_is_provisional_without_changing_stage3_status(monkeypatch):
    from app import orchestrator
    from app.stage3_evaluation.schemas import CategoryAssessment, LLMEvaluationOutput, EvidenceVerification
    from app.stage3_evaluation.scoring import score_evaluation, failed_evaluation

    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", lambda text: text)
    retrieved = []

    def extract(**kwargs):
        retrieved.append(kwargs["candidate_id"])
        return {"candidate_id": kwargs["candidate_id"], "composite_score": 1,
                "evidence_by_category": {}}

    async def evaluate(candidate_payloads, **kwargs):
        assert all("stage1_filter_details" in item for item in candidate_payloads)
        output = LLMEvaluationOutput(
            **{name: CategoryAssessment(score=80, rationale="test", citations=[])
               for name in ("skills", "experience", "projects", "education")},
            flags=[], executive_summary="test")
        return [failed_evaluation(item["candidate_id"], "PROVIDER_ERROR", "Unavailable")
                if item["candidate_id"] == "failed" else
                score_evaluation(item["candidate_id"], output,
                                 EvidenceVerification(registry={}, checks=[], review_reasons=["UNSUPPORTED_EVIDENCE"]
                                                      if item["candidate_id"] == "evidence_review" else [],
                                                      verified_flag_indices=[], injection_signals=[]))
                for item in candidate_payloads]

    monkeypatch.setattr(orchestrator, "extract_candidate_category_evidence", extract)
    monkeypatch.setattr(orchestrator, "evaluate_candidate_batch_async", evaluate)
    candidates = [{"candidate_id": name, "raw_cv_text": "Test"} for name in
                  ("provisional", "evidence_review", "failed")]
    candidates.append({"candidate_id": "rejected", "raw_cv_text": "Test",
                       "recruiter_overrides": {"work_authorized": "ineligible"}})
    result = await orchestrator.run_end_to_end_screening_pipeline(
        candidates, {"job_id": "job", "title": "Engineer", "jd_category_queries": {}})
    assert set(retrieved) == {"provisional", "evidence_review", "failed"}
    assert [item["candidate_id"] for item in result["leaderboard"]] == ["provisional"]
    success = result["leaderboard"][0]
    assert success["provisional"] and success["verification_required"]
    assert success["verification_reasons"][0]["code"] == "AUTHORIZATION_UNKNOWN"
    assert success["tier"] == "TIER_1" and success["review_reasons"] == []
    review = result["review_candidates"][0]
    assert review["evaluation_status"] == "REVIEW_REQUIRED" and review["tier"] is None
    assert review["verification_required"] and review["review_reasons"] == ["UNSUPPORTED_EVIDENCE"]
    assert result["failed_candidates"][0]["verification_required"]
    assert result["rejected_candidates"][0]["candidate_id"] == "rejected"
    assert result["metrics"]["accounted_candidates"] == 4


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end_execution():
    jd_profile = {
        "title": "Senior Python Backend Engineer",
        "jd_category_queries": {
            "SKILLS": "Python FastAPI PostgreSQL Redis Kubernetes",
            "EXPERIENCE": "Senior Backend Developer building microservices APIs",
            "PROJECTS": "High throughput distributed transaction processing",
            "EDUCATION": "Bachelor in Computer Science or Software Engineering"
        }
    }

    hard_filter_rules = {
        "min_years_experience": 3.0,
        "degree_requirement": {
            "level": "BACHELOR",
            "fields": [],
            "field_aliases": []
        }
    }

    raw_candidates = [
        # Candidate 1: Strong candidate (Passes Stage 1, High Stage 2/3 Score)
        {
            "candidate_id": "cand_001",
            "recruiter_overrides": {"work_authorized": "eligible"},
            "raw_cv_text": (
                "John Doe\nEmail: john@example.com\nPhone: +1-555-0199\n\n"
                "WORK EXPERIENCE\n"
                "Senior Backend Engineer at TechCorp (2020 - Present).\n"
                "Architected high-throughput FastAPI microservices backed by PostgreSQL and Redis.\n"
                "Managed Kubernetes clusters on AWS.\n\n"
                "TECHNICAL SKILLS\n"
                "Python, FastAPI, PostgreSQL, Redis, Kubernetes, AWS\n\n"
                "EDUCATION\n"
                "Bachelor of Science in Computer Engineering - TU, 2019\n"
            ),
            "parsed_attributes": {
                "experience_years": 5.0,
                "experience_source": "recruiter_verified",
                "degree": "BACHELOR"
            }
        },
        # Candidate 2: Underqualified (Fails Stage 1 Hard Filters)
        {
            "candidate_id": "cand_002",
            "recruiter_overrides": {"work_authorized": "eligible"},
            "raw_cv_text": (
                "Jane Smith\nEmail: jane@example.com\n\n"
                "WORK EXPERIENCE\n"
                "Junior Support Tech (2023 - Present).\n"
                "Fixed office hardware and basic desktop issues.\n"
            ),
            "parsed_attributes": {
                "experience_years": 1.0,
                "experience_source": "recruiter_verified",
                "degree": "HIGH_SCHOOL"
            }
        }
    ]

    result = await run_end_to_end_screening_pipeline(
        raw_candidates=raw_candidates,
        jd_profile=jd_profile,
        hard_filter_rules=hard_filter_rules,
        top_n_stage2_cutoff=10
    )

    metrics = result["metrics"]
    leaderboard = result["leaderboard"]
    rejected = result["rejected_candidates"]

    # Verify Pipeline Metrics
    assert metrics["total_input_candidates"] == 2
    assert metrics["stage0_processed"] == 2
    assert metrics["stage1_passed"] == 1
    assert metrics["stage1_rejected"] == 1
    assert metrics["stage2_shortlisted"] == 1
    assert metrics["stage3_evaluated"] == 1

    # Verify Leaderboard Content
    assert len(leaderboard) == 1
    top_cand = leaderboard[0]
    assert top_cand["candidate_id"] == "cand_001"
    assert top_cand["composite_score"] > 0.0
    assert top_cand["tier"] in [DecisionTier.TIER_1, DecisionTier.TIER_2]

    # Verify Stage 1 Rejections
    assert len(rejected) == 1
    assert rejected[0]["candidate_id"] == "cand_002"
    assert rejected[0]["reason"] == "FAILED_STAGE1_FILTERS"
