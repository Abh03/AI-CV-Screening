from typing import Dict, Any, List, Set, Tuple
from app.stage3_evaluation.schemas import (
    LLMEvaluationOutput,
    FinalCandidateEvaluation,
    DecisionTier,
    Severity,
    FlagType
)

DEFAULT_CATEGORY_WEIGHTS: Dict[str, float] = {
    "EXPERIENCE": 0.40,
    "SKILLS": 0.30,
    "PROJECTS": 0.15,
    "EDUCATION": 0.15
}


def extract_valid_citation_tags(evidence_payload: Dict[str, Any]) -> Set[str]:
    """Extracts all valid citation tags (e.g., {'SKILLS:1', 'EXPERIENCE:1'}) present in prompt payload."""
    valid_tags: Set[str] = set()
    evidence_map = evidence_payload.get("evidence_by_category", {})

    for category, chunks in evidence_map.items():
        for idx in range(1, len(chunks) + 1):
            valid_tags.add(f"{category}:{idx}")

    return valid_tags


def verify_llm_citations(
    llm_output: LLMEvaluationOutput,
    valid_tags: Set[str]
) -> Tuple[List[str], List[str]]:
    """Verifies every citation returned by LLM against valid evidence tags."""
    all_citations: Set[str] = set()

    # Collect citations from category assessments
    all_citations.update(llm_output.skills.citations)
    all_citations.update(llm_output.experience.citations)
    all_citations.update(llm_output.projects.citations)
    all_citations.update(llm_output.education.citations)

    # Collect citations from flags
    for flag in llm_output.flags:
        all_citations.update(flag.citations)

    verified = [c for c in all_citations if c in valid_tags]
    invalid = [c for c in all_citations if c not in valid_tags]

    return verified, invalid


def compute_deterministic_tier(
    candidate_id: str,
    llm_output: LLMEvaluationOutput,
    evidence_payload: Dict[str, Any],
    weights: Dict[str, float] = DEFAULT_CATEGORY_WEIGHTS
) -> FinalCandidateEvaluation:
    """
    Calculates final score and tier deterministically:
    1. Verifies LLM citations.
    2. Calculates composite score S_final from component scores.
    3. Enforces deterministic tier rules and critical flag penalties in Python.
    """
    valid_tags = extract_valid_citation_tags(evidence_payload)
    verified_cits, invalid_cits = verify_llm_citations(llm_output, valid_tags)

    scores = {
        "SKILLS": llm_output.skills.score,
        "EXPERIENCE": llm_output.experience.score,
        "PROJECTS": llm_output.projects.score,
        "EDUCATION": llm_output.education.score
    }

    # Deterministic weighted composite calculation
    composite_score = sum(
        weights.get(cat, 0.25) * scores.get(cat, 0.0)
        for cat in ["EXPERIENCE", "SKILLS", "PROJECTS", "EDUCATION"]
    )
    composite_score = round(composite_score, 2)

    # Flag severity check
    has_critical_or_high = any(
        flag.severity in [Severity.CRITICAL, Severity.HIGH]
        and flag.type != FlagType.MISSING_INFORMATION
        for flag in llm_output.flags
    )

    # Hard Deterministic Tier Assignment Rules
    if scores["SKILLS"] < 40.0 or composite_score < 55.0:
        tier = DecisionTier.TIER_3
    elif has_critical_or_high:
        tier = DecisionTier.TIER_2 if composite_score >= 70.0 else DecisionTier.TIER_3
    elif composite_score >= 75.0:
        tier = DecisionTier.TIER_1
    elif composite_score >= 55.0:
        tier = DecisionTier.TIER_2
    else:
        tier = DecisionTier.TIER_3

    return FinalCandidateEvaluation(
        candidate_id=candidate_id,
        composite_score=composite_score,
        tier=tier,
        category_scores=scores,
        llm_raw_output=llm_output,
        verified_citations=verified_cits,
        invalid_citations=invalid_cits,
        has_critical_flags=has_critical_or_high
    )