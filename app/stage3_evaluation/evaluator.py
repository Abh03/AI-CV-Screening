from typing import Any

from app.stage3_evaluation.schemas import LLMEvaluationOutput, FinalCandidateEvaluation
from app.stage3_evaluation.scoring import score_evaluation


async def evaluate_candidate_stage3(
    candidate_id: str, jd_profile: dict[str, Any], evidence_payload: dict[str, Any]
) -> FinalCandidateEvaluation:
    # Both public entry points use the same validation, failure, and scoring path.
    from app.stage3_evaluation.llm_client import evaluate_single_candidate_async
    if evidence_payload.get("candidate_id", candidate_id) != candidate_id:
        from app.stage3_evaluation.scoring import failed_evaluation
        return failed_evaluation(candidate_id, "INVALID_EVIDENCE", "Evidence ownership mismatch.")
    payload = dict(evidence_payload, candidate_id=candidate_id)
    return await evaluate_single_candidate_async(payload, jd_profile)


def compute_deterministic_tier(
    candidate_id: str, llm_output: LLMEvaluationOutput, evidence_payload: dict[str, Any],
    *, user_prompt: str | None = None, registry=None, injection_signals=(), is_mock=False,
) -> FinalCandidateEvaluation:
    """Compatibility entry point; all scoring and outcome policy lives in scoring.py."""
    # A supplied prompt string is never an authority for citation membership.
    from app.stage3_evaluation.evidence import build_evidence_registry, verify_evidence
    if registry is None:
        registry = build_evidence_registry(candidate_id, evidence_payload)
    verification = verify_evidence(llm_output, registry, candidate_id, injection_signals)
    return score_evaluation(candidate_id, llm_output, verification, is_mock=is_mock)
