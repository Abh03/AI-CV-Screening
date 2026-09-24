from typing import List, Dict, Any, Optional
from app.stage0_extraction.pii_masker import mask_pii_runtime_view
from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters
from app.stage1_rules.contracts import CandidateInput, resolve_hard_filters
from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence,
    extract_candidate_category_evidence_postgres,
    rank_and_filter_candidate_batch
)
from app.config import settings
from app.stage3_evaluation.llm_client import evaluate_candidate_batch_async
from app.stage3_evaluation.schemas import FinalCandidateEvaluation, EvaluationStatus
from app.stage3_evaluation.scoring import evaluation_sort_key


async def run_end_to_end_screening_pipeline(
    raw_candidates: List[Dict[str, Any]],
    jd_profile: Dict[str, Any],
    hard_filter_rules: Optional[Dict[str, Any]] = None,
    top_n_stage2_cutoff: int = 30,
    llm_concurrency_limit: int = 5,
    llm_provider: Optional[Any] = None,
    stage0_views: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Executes the full automated screening pipeline across all candidates:
    - Stage 0: PII Masking & Security
    - Stage 1: Deterministic Hard Filters
    - Stage 2: Category-Aware Hybrid Retrieval + Cross-Encoder Ranking
    - Stage 3: Async LLM Multi-Attribute Evaluation & Tier Assembly
    """
    rules = resolve_hard_filters(jd_profile, hard_filter_rules)
    candidates = [CandidateInput.model_validate(candidate) for candidate in raw_candidates]
    pipeline_metrics = {
        "total_input_candidates": len(raw_candidates),
        "stage0_processed": 0,
        "stage1_passed": 0,
        "stage1_rejected": 0,
        "stage1_review_required": 0,
        "stage2_shortlisted": 0,
        "stage3_evaluated": 0,
        "stage3_succeeded": 0,
        "stage3_review_required": 0,
        "stage3_failed": 0
    }

    if not raw_candidates or not jd_profile:
        return {
            "metrics": pipeline_metrics,
            "leaderboard": [],
            "rejected_candidates": [],
            "review_candidates": [],
            "failed_candidates": []
        }

    effective_jd = dict(jd_profile)
    effective_jd["hard_filter_rules"] = rules.model_dump(mode="json")

    jd_category_queries = effective_jd.get("jd_category_queries", {})
    stage1_survivors = []
    rejected_candidates = []
    review_candidates = []

    # Stage 0 & Stage 1: PII Masking & Hard Filtering
    for cand in candidates:
        cand_id = cand.candidate_id
        raw_text = cand.raw_cv_text

        # Stage 0: PII Redaction (returns string directly)
        view = (stage0_views or {}).get(cand_id)
        redacted_text = view.redacted_text if view is not None else mask_pii_runtime_view(raw_text)
        pipeline_metrics["stage0_processed"] += 1

        # Stage 1: Deterministic Rules Filter
        facts = cand.rule_facts()
        filter_result = evaluate_stage1_hard_filters(
            candidate_yoe=facts.experience_years,
            candidate_cv_text=redacted_text,
            work_authorized=facts.work_authorized,
            jd_profile=rules,
            experience_source=facts.experience_source,
            authorization_source=facts.authorization_source,
        )
        filter_result["input_provenance"] = {
            "reported_experience_years": cand.parsed_attributes.experience_years,
            "reported_experience_source": cand.parsed_attributes.experience_source.value,
            "reported_work_authorized": cand.work_authorized.value,
            "reported_authorization_source": cand.authorization_source.value,
            "nested_work_authorized": cand.parsed_attributes.work_authorized.value,
            "nested_authorization_source": cand.parsed_attributes.authorization_source.value,
            "recruiter_overrides": cand.recruiter_overrides.model_dump(mode="json", exclude_none=True),
        }
        is_passed = filter_result["status"] == "PASS"
        if is_passed:
            pipeline_metrics["stage1_passed"] += 1
            stage1_survivors.append({
                "candidate_id": cand_id,
                "redacted_cv_text": redacted_text,
                "parsed_attributes": cand.parsed_attributes.model_dump(mode="json"),
                "filter_details": filter_result
            })
        elif filter_result["status"] == "REVIEW":
            pipeline_metrics["stage1_review_required"] += 1
            review_candidates.append({
                "candidate_id": cand_id, "stage": "STAGE1",
                "evaluation_status": "REVIEW_REQUIRED",
                "reason": "STAGE1_REQUIRES_REVIEW", "filter_details": filter_result,
            })
        else:
            pipeline_metrics["stage1_rejected"] += 1
            rejected_candidates.append({
                "candidate_id": cand_id,
                "reason": "FAILED_STAGE1_FILTERS",
                "filter_details": filter_result
            })

    if not stage1_survivors:
        return {
            "metrics": pipeline_metrics,
            "leaderboard": [],
            "rejected_candidates": rejected_candidates,
            "review_candidates": sorted(review_candidates, key=lambda item: item["candidate_id"]),
            "failed_candidates": [],
        }

    # Stage 2: Category-Aware Evidence Extraction & Candidate Batch Ranking
    if settings.ENVIRONMENT.lower() == "production" and settings.STAGE2_BACKEND != "postgres":
        raise RuntimeError("Production Stage 2 requires STAGE2_BACKEND=postgres")
    stage2_payloads = []
    for survivor in stage1_survivors:
        extractor = (extract_candidate_category_evidence_postgres
                     if settings.STAGE2_BACKEND == "postgres" else extract_candidate_category_evidence)
        kwargs = dict(
            candidate_id=survivor["candidate_id"],
            redacted_cv_text=survivor["redacted_cv_text"],
            jd_category_queries=jd_category_queries,
            source_pages=(stage0_views or {}).get(survivor["candidate_id"]).pages
            if survivor["candidate_id"] in (stage0_views or {}) else None
        )
        if settings.STAGE2_BACKEND == "postgres":
            evidence_payload = await extractor(**kwargs, job_id=effective_jd["job_id"])
        else:
            evidence_payload = extractor(**kwargs)
        stage2_payloads.append(evidence_payload)

    # Global Batch Cutoff: Rank all Stage 1 survivors by S_cand and slice top candidates
    shortlisted_candidates = rank_and_filter_candidate_batch(
        stage2_payloads,
        top_n_llm=top_n_stage2_cutoff
    )
    pipeline_metrics["stage2_shortlisted"] = len(shortlisted_candidates)

    # Stage 3: Async LLM Multi-Attribute Evaluation
    final_evaluations: List[FinalCandidateEvaluation] = await evaluate_candidate_batch_async(
        candidate_payloads=shortlisted_candidates,
        jd_profile=effective_jd,
        llm_provider=llm_provider,
        concurrency_limit=llm_concurrency_limit
    )
    pipeline_metrics["stage3_evaluated"] = len(final_evaluations)

    stage1_details = {candidate["candidate_id"]: candidate["filter_details"] for candidate in stage1_survivors}
    leaderboard = []
    failed_candidates = []
    for result in sorted(final_evaluations, key=evaluation_sort_key):
        item = result.model_dump(mode="json")
        item["stage1_filter_details"] = stage1_details[result.candidate_id]
        if result.evaluation_status == EvaluationStatus.SUCCESS:
            pipeline_metrics["stage3_succeeded"] += 1
            leaderboard.append(item)
        elif result.evaluation_status == EvaluationStatus.REVIEW_REQUIRED:
            pipeline_metrics["stage3_review_required"] += 1
            review_candidates.append(dict(item, stage="STAGE3"))
        else:
            pipeline_metrics["stage3_failed"] += 1
            failed_candidates.append(item)
    return {
        "metrics": pipeline_metrics,
        "leaderboard": leaderboard,
        "rejected_candidates": rejected_candidates,
        "review_candidates": sorted(review_candidates, key=lambda item: item["candidate_id"]),
        "failed_candidates": failed_candidates,
    }
