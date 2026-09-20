import pytest
import json
import asyncio
from app.stage3_evaluation.llm_client import (
    evaluate_single_candidate_async,
    evaluate_candidate_batch_async,
    MockLLMProvider
)
from app.stage3_evaluation.schemas import DecisionTier


class FailingLLMProvider:
    """Mock provider simulating malformed JSON response to test fallback handling."""
    async def generate_structured_evaluation(self, system_prompt: str, user_prompt: str) -> str:
        return "INVALID_NON_JSON_RESPONSE"


@pytest.mark.asyncio
async def test_single_candidate_async_evaluation():
    jd_profile = {
        "title": "Senior Backend Engineer",
        "jd_category_queries": {
            "SKILLS": "Python FastAPI PostgreSQL",
            "EXPERIENCE": "Backend API development",
            "PROJECTS": "Microservices",
            "EDUCATION": "BS Computer Science"
        }
    }

    mock_payload = {
        "candidate_id": "cand_301",
        "evidence_by_category": {
            "SKILLS": [{"text": "Python FastAPI PostgreSQL"}],
            "EXPERIENCE": [{"text": "5 years backend development"}],
            "PROJECTS": [{"text": "Payment gateway project"}],
            "EDUCATION": [{"text": "BS Computer Science"}]
        }
    }

    result = await evaluate_single_candidate_async(mock_payload, jd_profile)

    assert result.candidate_id == "cand_301"
    assert result.composite_score > 0.0
    print(f"Composite Score: {result.composite_score}, Tier: {result.tier}")
    assert result.tier in [DecisionTier.TIER_1, DecisionTier.TIER_2, DecisionTier.TIER_3]
    assert len(result.verified_citations) > 0


@pytest.mark.asyncio
async def test_failing_llm_parsing_fallback():
    jd_profile = {"title": "DevOps Engineer", "jd_category_queries": {}}
    mock_payload = {"candidate_id": "cand_fail", "evidence_by_category": {}}

    failing_provider = FailingLLMProvider()
    result = await evaluate_single_candidate_async(
        mock_payload,
        jd_profile,
        llm_provider=failing_provider,
        max_retries=1
    )

    assert result.candidate_id == "cand_fail"
    assert result.composite_score == 0.0
    assert result.tier == DecisionTier.TIER_3
    assert "Evaluation failed" in result.llm_raw_output.executive_summary


@pytest.mark.asyncio
async def test_batch_candidate_concurrency():
    jd_profile = {"title": "Data Engineer", "jd_category_queries": {}}

    batch_payloads = [
        {
            "candidate_id": f"cand_{i}",
            "evidence_by_category": {
                "SKILLS": [{"text": "Python SQL"}],
                "EXPERIENCE": [{"text": "3 years experience"}],
                "PROJECTS": [],
                "EDUCATION": []
            }
        }
        for i in range(10)
    ]

    results = await evaluate_candidate_batch_async(
        candidate_payloads=batch_payloads,
        jd_profile=jd_profile,
        concurrency_limit=3
    )

    assert len(results) == 10
    # Output must be sorted descending by composite score
    for i in range(len(results) - 1):
        assert results[i].composite_score >= results[i + 1].composite_score