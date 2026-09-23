import os
import pytest
from app.config import settings
from app.stage3_evaluation.evaluator import evaluate_candidate_stage3
from app.stage3_evaluation.schemas import FinalCandidateEvaluation, DecisionTier


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_gemini_stage3_evaluation():
    """
    Live integration test executing a real call to Google AI Studio Gemini API.
    Opt-in only via pytest marker: -m integration
    """
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")

    if settings.LLM_PROVIDER != "gemini" or not api_key or api_key == "mock_key_for_now":
        pytest.skip("Gemini provider or valid GEMINI_API_KEY not configured in .env")

    # Structured JD profile matching build_stage3_user_prompt expectations
    sample_jd_profile = {
        "title": "Senior Python Engineer",
        "jd_category_queries": {
            "SKILLS": "5+ years experience with Python, FastAPI, and PostgreSQL",
            "EXPERIENCE": "Experience building microservices and Docker containers",
            "PROJECTS": "Scalable API design and microservices architecture",
            "EDUCATION": "Degree in Computer Science or related field"
        }
    }

    # Structured evidence payload matching build_stage3_user_prompt expectations
    sample_evidence_payload = {
        "evidence_by_category": {
            "SKILLS": [
                {"text": "Proficient in Python, FastAPI, PostgreSQL, Redis, and Docker."}
            ],
            "EXPERIENCE": [
                {"text": "Backend Software Engineer (2019-2024): Built high-throughput microservices in FastAPI."}
            ],
            "PROJECTS": [
                {"text": "Designed and deployed a distributed microservices pipeline."}
            ],
            "EDUCATION": [
                {"text": "B.S. in Computer Science, University of Technology (2019)."}
            ]
        }
    }

    # Execute Stage 3 evaluation with live Gemini call using dict arguments
    result = await evaluate_candidate_stage3(
        candidate_id="cand_test_999",
        jd_profile=sample_jd_profile,
        evidence_payload=sample_evidence_payload
    )

    # Assertions on FinalCandidateEvaluation model
    assert isinstance(result, FinalCandidateEvaluation)
    assert result.candidate_id == "cand_test_999"
    assert result.tier in [DecisionTier.TIER_1, DecisionTier.TIER_2, DecisionTier.TIER_3]
    assert 0.0 <= result.composite_score <= 100.0
    assert "skills" in result.category_scores
    assert len(result.llm_raw_output.executive_summary) > 0

    print("\n--- Live Gemini Evaluation Result ---")
    print(f"Candidate ID: {result.candidate_id}")
    print(f"Tier: {result.tier}")
    print(f"Composite Score: {result.composite_score}")
    print(f"Category Scores: {result.category_scores}")
    print(f"Executive Summary: {result.llm_raw_output.executive_summary}")
    print("-------------------------------------")