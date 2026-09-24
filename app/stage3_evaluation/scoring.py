"""Versioned Python policy; retrieval ranking weights are intentionally separate."""
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType

from app.stage3_evaluation.schemas import (
    CategoryAssessment, DecisionTier, EvaluationStatus, FinalCandidateEvaluation,
    FlagType, LLMEvaluationOutput, Severity, EvidenceVerification,
)

SCORING_POLICY_VERSION = "stage3-v1.1.0"
CATEGORY_WEIGHTS = MappingProxyType({
    "skills": Decimal("0.40"),
    "experience": Decimal("0.30"),
    "projects": Decimal("0.20"),
    "education": Decimal("0.10"),
})


def weighted_score(scores: dict[str, float]) -> float:
    if set(scores) != set(CATEGORY_WEIGHTS):
        raise ValueError("Exactly four category scores are required")
    # Validate even when called outside the LLM schema boundary.
    validated = {
        key: CategoryAssessment(score=value, rationale="", citations=[]).score
        for key, value in scores.items()
    }
    total = sum(Decimal(str(validated[key])) * weight for key, weight in CATEGORY_WEIGHTS.items())
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_decision_tier(composite_score: float) -> DecisionTier:
    """Apply thresholds to the final two-decimal score; no independent skill gate."""
    score = CategoryAssessment(score=composite_score, rationale="", citations=[]).score
    if score >= 75:
        return DecisionTier.TIER_1
    if score >= 55:
        return DecisionTier.TIER_2
    return DecisionTier.TIER_3


def score_evaluation(candidate_id: str, output: LLMEvaluationOutput,
                     verification: EvidenceVerification, *, is_mock: bool = False) -> FinalCandidateEvaluation:
    scores = {key: getattr(output, key).score for key in CATEGORY_WEIGHTS}
    composite = weighted_score(scores)
    reasons = set(verification.review_reasons)
    for flag in output.flags:
        if flag.type == FlagType.MISSING_INFORMATION:
            reasons.add("MISSING_INFORMATION")
        elif flag.severity in (Severity.HIGH, Severity.CRITICAL):
            reasons.add("HIGH_OR_CRITICAL_FLAG_REQUIRES_REVIEW")
    # Citation membership is not proof of a flag's semantic truth: require review.
    status = EvaluationStatus.REVIEW_REQUIRED if reasons else EvaluationStatus.SUCCESS
    return FinalCandidateEvaluation(
        candidate_id=candidate_id, evaluation_status=status,
        scoring_policy_version=SCORING_POLICY_VERSION,
        composite_score=composite,
        tier=calculate_decision_tier(composite) if not reasons else None,
        category_scores=scores, llm_raw_output=output,
        verified_citations=verification.verified_citations,
        invalid_citations=verification.invalid_citations,
        evidence_verification=verification, is_mock=is_mock,
        has_critical_flags=any(
            index in verification.verified_flag_indices and flag.type != FlagType.MISSING_INFORMATION and flag.severity in (Severity.HIGH, Severity.CRITICAL)
            for index, flag in enumerate(output.flags)
        ),
        review_reasons=sorted(reasons),
    )


def failed_evaluation(candidate_id: str, error_code: str, message: str, *, is_mock: bool = False) -> FinalCandidateEvaluation:
    return FinalCandidateEvaluation(
        candidate_id=candidate_id, evaluation_status=EvaluationStatus.EVALUATION_FAILED,
        scoring_policy_version=SCORING_POLICY_VERSION,
        composite_score=None, tier=None, category_scores={}, llm_raw_output=None,
        verified_citations=[], invalid_citations=[], has_critical_flags=False,
        error_code=error_code, error_message=message, is_mock=is_mock,
    )


def evaluation_sort_key(result: FinalCandidateEvaluation) -> tuple:
    """Successful decisions first; ties use case-sensitive candidate ID ascending."""
    order = {EvaluationStatus.SUCCESS: 0, EvaluationStatus.REVIEW_REQUIRED: 1,
             EvaluationStatus.EVALUATION_FAILED: 2}
    return (order[result.evaluation_status],
            -result.composite_score if result.composite_score is not None else 0,
            result.candidate_id)
