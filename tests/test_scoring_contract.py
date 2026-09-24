import json

import pytest
from pydantic import ValidationError

from app.stage3_evaluation.schemas import (
    CategoryAssessment, DecisionTier, EvaluationStatus, FinalCandidateEvaluation,
    FlagDetail, FlagType, LLMEvaluationOutput, Severity,
)
from app.stage3_evaluation.scoring import (
    SCORING_POLICY_VERSION, calculate_decision_tier, evaluation_sort_key,
    failed_evaluation, score_evaluation as _score_evaluation, weighted_score,
)


def output(scores=(80, 80, 80, 80), flags=None):
    return LLMEvaluationOutput(
        **{key: CategoryAssessment(score=value, rationale="test", citations=[f"{key.upper()}:1"])
           for key, value in zip(("skills", "experience", "projects", "education"), scores)},
        flags=flags or [], executive_summary="test",
    )


def score_evaluation(candidate_id, assessment, *unused):
    from app.stage3_evaluation.evidence import build_evidence_registry, verify_evidence
    registry = build_evidence_registry(candidate_id, {"evidence_by_category": {
        category: [{"text": "Test evidence"}] for category in ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")
    }})
    return _score_evaluation(candidate_id, assessment, verify_evidence(assessment, registry, candidate_id))


def test_weights_and_rounding():
    assert weighted_score(dict(skills=90, experience=70, projects=50, education=30)) == 70
    assert weighted_score(dict(skills=74.995, experience=74.995, projects=74.995, education=74.995)) == 75
    result = score_evaluation("a", output((90, 70, 50, 30)), [], [])
    assert result.scoring_policy_version == SCORING_POLICY_VERSION
    assert result.tier == DecisionTier.TIER_2


@pytest.mark.parametrize("score,tier", [(0, "TIER_3"), (54.99, "TIER_3"),
    (55, "TIER_2"), (74.99, "TIER_2"), (75, "TIER_1"), (100, "TIER_1")])
def test_tier_boundaries(score, tier):
    assert calculate_decision_tier(score).value == tier


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"), -1, 101])
def test_invalid_scores_rejected(score):
    with pytest.raises(ValidationError):
        CategoryAssessment(score=score, rationale="", citations=[])
    with pytest.raises(ValidationError):
        weighted_score(dict(skills=score, experience=80, projects=80, education=80))


@pytest.mark.parametrize("kind", list(FlagType))
@pytest.mark.parametrize("severity", list(Severity))
def test_flag_matrix(kind, severity):
    flag = FlagDetail(type=kind, severity=severity, description="test", citations=["EXPERIENCE:1"])
    result = score_evaluation("a", output(flags=[flag]), [], [])
    review = kind == FlagType.MISSING_INFORMATION or severity in (Severity.HIGH, Severity.CRITICAL)
    assert result.composite_score == 80  # No extra deduction for flags.
    assert (result.evaluation_status == EvaluationStatus.REVIEW_REQUIRED) == review
    assert (result.tier is None) == review
    if kind == FlagType.MISSING_INFORMATION:
        assert not result.has_critical_flags


def test_failure_is_distinct_from_valid_zero_and_sort_is_stable():
    zero = score_evaluation("zero", output((0, 0, 0, 0)), [], [])
    error = failed_evaluation("error", "PROVIDER_ERROR", "Unavailable")
    assert zero.tier == DecisionTier.TIER_3
    assert error.tier is None and error.composite_score is None
    assert error.llm_raw_output is None
    a = score_evaluation("a", output(), [], [])
    b = score_evaluation("b", output(), [], [])
    results = [b, error, a, zero]
    assert [item.candidate_id for item in sorted(results, key=evaluation_sort_key)] == ["a", "b", "zero", "error"]
    assert sorted(results, key=evaluation_sort_key) == sorted(reversed(results), key=evaluation_sort_key)
    invalid = error.model_dump()
    invalid["composite_score"] = 0
    with pytest.raises(ValidationError):
        FinalCandidateEvaluation.model_validate(invalid)


@pytest.mark.asyncio
async def test_provider_failure_entry_points_agree(monkeypatch):
    from app.stage3_evaluation.llm_client import llm_client, evaluate_single_candidate_async
    from app.stage3_evaluation.evaluator import evaluate_candidate_stage3

    async def broken(*args, **kwargs):
        raise RuntimeError("private provider diagnostics")

    monkeypatch.setattr(llm_client, "generate_evaluation", broken)
    first = await evaluate_single_candidate_async({"candidate_id": "a"}, {})
    second = await evaluate_candidate_stage3("a", {}, {})
    assert first == second
    assert first.error_code == "PROVIDER_ERROR"
    assert "private" not in first.model_dump_json()


@pytest.mark.asyncio
async def test_stage1_review_is_preserved(monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", lambda text: text)
    result = await orchestrator.run_end_to_end_screening_pipeline(
        [{"candidate_id": "review", "raw_cv_text": "EDUCATION\nBachelor of Computer Science - pursuing university degree", "parsed_attributes": {"experience_years": 5}}],
        {"title": "Engineer"}, {"degree_requirement": {"level": "BACHELOR"}},
    )
    assert result["rejected_candidates"] == []
    assert result["leaderboard"] == []
    assert result["review_candidates"][0]["stage"] == "STAGE1"
    assert result["metrics"]["stage1_review_required"] == 1
    assert result["metrics"]["stage1_rejected"] == 0


@pytest.mark.asyncio
async def test_batch_ties_do_not_depend_on_input_order():
    from app.stage3_evaluation.llm_client import evaluate_candidate_batch_async

    class Provider:
        async def generate_structured_evaluation(self, **kwargs):
            return output().model_dump_json()

    payloads = [{"candidate_id": "b"}, {"candidate_id": "a"}]
    first = await evaluate_candidate_batch_async(payloads, {}, Provider())
    second = await evaluate_candidate_batch_async(list(reversed(payloads)), {}, Provider())
    assert [item.candidate_id for item in first] == ["a", "b"]
    assert first == second
