import os
import pytest

from app.config import settings
from app.stage3_evaluation.evaluator import evaluate_candidate_stage3
from app.stage3_evaluation.schemas import FinalCandidateEvaluation, DecisionTier, EvaluationStatus

@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_stage3_evaluation():
    """
    Live integration test for the LLM provider configured in .env.
    Supported providers: gemini, groq, openrouter.
    Opt-in only via pytest marker: -m integration
    """


    provider = settings.LLM_PROVIDER.lower()

    provider_keys = {
        "gemini": settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY"),
        "groq": settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY"),
        "openrouter": settings.OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY"),
    }

    if provider not in provider_keys:
        pytest.skip(
            f"Live integration test not supported for provider '{provider}'. "
            "Use gemini, groq, or openrouter."
        )

    api_key = provider_keys[provider]

    if not api_key or api_key == "mock_key_for_now":
        pytest.skip(
            f"{provider.upper()} API key is not configured in .env"
        )

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
                {
                    "text": "Proficient in Python, FastAPI, PostgreSQL, Redis, and Docker."
                }
            ],
            "EXPERIENCE": [
                {
                    "text": "Backend Software Engineer (2019-2024): "
                            "Built high-throughput microservices in FastAPI."
                }
            ],
            "PROJECTS": [
                {
                    "text": "Designed and deployed a distributed microservices pipeline."
                }
            ],
            "EDUCATION": [
                {
                    "text": "B.S. in Computer Science, University of Technology (2019)."
                }
            ]
        }
    }

    # Execute Stage 3 evaluation using the configured provider
    result = await evaluate_candidate_stage3(
        candidate_id="cand_test_999",
        jd_profile=sample_jd_profile,
        evidence_payload=sample_evidence_payload
    )

# Assertions on FinalCandidateEvaluation model
    assert isinstance(result, FinalCandidateEvaluation)
    assert result.evaluation_status != EvaluationStatus.EVALUATION_FAILED
    assert result.llm_raw_output is not None
    
    assert result.candidate_id == "cand_test_999"
    assert result.tier in [DecisionTier.TIER_1, DecisionTier.TIER_2, DecisionTier.TIER_3, None]
    assert 0.0 <= result.composite_score <= 100.0
    assert "skills" in result.category_scores
    assert len(result.llm_raw_output.executive_summary) > 0

    print(f"\n--- Live {provider.upper()} Stage 3 Evaluation Result ---")
    print(f"Provider: {provider}")
    print(f"Candidate ID: {result.candidate_id}")
    print(f"Tier: {result.tier}")
    print(f"Composite Score: {result.composite_score}")
    print(f"Category Scores: {result.category_scores}")
    print(f"Executive Summary: {result.llm_raw_output.executive_summary}")
    print("-------------------------------------")
