import logging
from typing import Dict, List, Set, Any
from pydantic import ValidationError

from app.stage3_evaluation.schemas import (
    LLMEvaluationOutput,
    FinalCandidateEvaluation,
    DecisionTier,
    CategoryAssessment,
    Severity
)
from app.stage3_evaluation.prompts import build_stage3_user_prompt, SYSTEM_PROMPT_STAGE3
from app.stage3_evaluation.llm_client import llm_client

logger = logging.getLogger("cv_screening")


def calculate_decision_tier(composite_score: float, has_critical_flags: bool) -> DecisionTier:
    """
    Tier 1: Strong Fit (Composite >= 75, no Critical/High flags)
    Tier 2: Potential Fit (55 <= Composite < 75)
    Tier 3: Not Recommended (Composite < 55 or mandatory critical flag)
    """
    if has_critical_flags or composite_score < 55.0:
        return DecisionTier.TIER_3
    elif composite_score >= 75.0:
        return DecisionTier.TIER_1
    else:
        return DecisionTier.TIER_2


async def evaluate_candidate_stage3(
    candidate_id: str,
    jd_profile: Dict[str, Any],
    evidence_payload: Dict[str, Any]
) -> FinalCandidateEvaluation:

    # 1. Generate XML-formatted user prompt from JD profile and evidence payload
    user_prompt = build_stage3_user_prompt(
        candidate_id=candidate_id,
        jd_profile=jd_profile,
        evidence_payload=evidence_payload
    )

    try:
        # Combine system prompt and user prompt for LLM
        full_prompt = f"{SYSTEM_PROMPT_STAGE3}\n\n{user_prompt}"

        raw_response = await llm_client.generate_evaluation(
            prompt_text=full_prompt,
            candidate_id=candidate_id
        )

        # 2. Validate raw LLM JSON output against schema
        llm_output = LLMEvaluationOutput.model_validate(raw_response)

        return compute_deterministic_tier(
            candidate_id, llm_output, evidence_payload, user_prompt=user_prompt
        )

    except ValidationError as ve:
        logger.error(f"Schema Validation Guardrail failed for {candidate_id}: {ve}")
        return _fallback_evaluation(candidate_id, f"Schema validation error: {str(ve)}")
    except Exception as e:
        logger.error(f"Stage 3 evaluation failed for {candidate_id}: {e}")
        return _fallback_evaluation(candidate_id, f"Provider error: {str(e)}")


def compute_deterministic_tier(
    candidate_id: str,
    llm_output: LLMEvaluationOutput,
    evidence_payload: Dict[str, Any],
    *,
    user_prompt: str | None = None,
) -> FinalCandidateEvaluation:
    """Existing scoring behavior, extracted for the restored batch API.

    Policy and citation hardening belong to subsequent repair phases.
    """
    if user_prompt is None:
        user_prompt = build_stage3_user_prompt(candidate_id, {}, evidence_payload)
    # 3. Extract Category Scores & Apply Established Weights (40 / 25 / 20 / 15)
    category_scores = {
        "skills": llm_output.skills.score,
        "experience": llm_output.experience.score,
        "projects": llm_output.projects.score,
        "education": llm_output.education.score,
    }

    composite_score = round(
        (llm_output.skills.score * 0.40) +
        (llm_output.experience.score * 0.25) +
        (llm_output.projects.score * 0.20) +
        (llm_output.education.score * 0.15),
        2
    )

    # 4. Evaluate Candidate Flags for Critical / High Severity
    has_critical_flags = any(
        flag.severity in [Severity.CRITICAL, Severity.HIGH]
        for flag in llm_output.flags
    )

    # 5. Citation Verification Logic (Checked against generated user_prompt XML context)
    all_citations: List[str] = []
    for category in [llm_output.skills, llm_output.experience, llm_output.projects, llm_output.education]:
        all_citations.extend(category.citations)
    for flag in llm_output.flags:
        all_citations.extend(flag.citations)

    # Verify whether cited tags/snippets exist within the formatted user prompt XML
    verified_citations = [c for c in all_citations if c in user_prompt]
    invalid_citations = [c for c in all_citations if c not in user_prompt]

    # 6. Determine Decision Tier
    tier = calculate_decision_tier(composite_score, has_critical_flags)

    return FinalCandidateEvaluation(
        candidate_id=candidate_id,
        composite_score=composite_score,
        tier=tier,
        category_scores=category_scores,
        llm_raw_output=llm_output,
        verified_citations=list(set(verified_citations)),
        invalid_citations=list(set(invalid_citations)),
        has_critical_flags=has_critical_flags
    )


def _fallback_evaluation(candidate_id: str, error_msg: str) -> FinalCandidateEvaluation:
    empty_category = CategoryAssessment(score=0.0, rationale="Evaluation failed.", citations=[])
    fallback_llm = LLMEvaluationOutput(
        skills=empty_category,
        experience=empty_category,
        projects=empty_category,
        education=empty_category,
        flags=[],
        executive_summary=error_msg
    )
    return FinalCandidateEvaluation(
        candidate_id=candidate_id,
        composite_score=0.0,
        tier=DecisionTier.TIER_3,
        category_scores={"skills": 0.0, "experience": 0.0, "projects": 0.0, "education": 0.0},
        llm_raw_output=fallback_llm,
        verified_citations=[],
        invalid_citations=[],
        has_critical_flags=False  # System/API error is NOT a candidate red flag
    )
