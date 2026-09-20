from typing import List, Dict, Any, Optional
from app.stage0_extraction.pii_masker import mask_pii_runtime_view
from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters
from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence,
    rank_and_filter_candidate_batch
)
from app.stage3_evaluation.llm_client import evaluate_candidate_batch_async
from app.stage3_evaluation.schemas import FinalCandidateEvaluation


async def run_end_to_end_screening_pipeline(
    raw_candidates: List[Dict[str, Any]],
    jd_profile: Dict[str, Any],
    hard_filter_rules: Optional[Dict[str, Any]] = None,
    top_n_stage2_cutoff: int = 30,
    llm_concurrency_limit: int = 5,
    llm_provider: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Executes the full automated screening pipeline across all candidates:
    - Stage 0: PII Masking & Security
    - Stage 1: Deterministic Hard Filters
    - Stage 2: Category-Aware Hybrid Retrieval + Cross-Encoder Ranking
    - Stage 3: Async LLM Multi-Attribute Evaluation & Tier Assembly
    """
    pipeline_metrics = {
        "total_input_candidates": len(raw_candidates),
        "stage0_processed": 0,
        "stage1_passed": 0,
        "stage1_rejected": 0,
        "stage2_shortlisted": 0,
        "stage3_evaluated": 0
    }

    if not raw_candidates or not jd_profile:
        return {
            "metrics": pipeline_metrics,
            "leaderboard": [],
            "rejected_candidates": []
        }

    # Merge explicit hard_filter_rules into a working copy of jd_profile
    effective_jd = dict(jd_profile)
    if hard_filter_rules:
        effective_jd.update(hard_filter_rules)

    jd_category_queries = effective_jd.get("jd_category_queries", {})
    stage1_survivors = []
    rejected_candidates = []

    # Stage 0 & Stage 1: PII Masking & Hard Filtering
    for cand in raw_candidates:
        cand_id = cand.get("candidate_id", "UNKNOWN")
        raw_text = cand.get("raw_cv_text", "")

        # Stage 0: PII Redaction (returns string directly)
        redacted_text = mask_pii_runtime_view(raw_text)
        pipeline_metrics["stage0_processed"] += 1

        # Stage 1: Deterministic Rules Filter
        cand_data_for_rules = cand.get("parsed_attributes", {})
        cand_yoe = float(cand_data_for_rules.get("experience_years", 0.0))
        work_auth = cand.get("work_authorized", cand_data_for_rules.get("work_authorized", True))

        filter_result = evaluate_stage1_hard_filters(
            candidate_yoe=cand_yoe,
            candidate_cv_text=redacted_text,
            work_authorized=work_auth,
            jd_profile=effective_jd
        )
        
        # Handle both boolean and dict return types from rules engine
        is_passed = (
            filter_result.get("status") == "PASS"
            if isinstance(filter_result, dict)
            else bool(filter_result)
        )
        
        if is_passed:
            pipeline_metrics["stage1_passed"] += 1
            stage1_survivors.append({
                "candidate_id": cand_id,
                "redacted_cv_text": redacted_text,
                "parsed_attributes": cand_data_for_rules
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
            "rejected_candidates": rejected_candidates
        }

    # Stage 2: Category-Aware Evidence Extraction & Candidate Batch Ranking
    stage2_payloads = []
    for survivor in stage1_survivors:
        evidence_payload = extract_candidate_category_evidence(
            candidate_id=survivor["candidate_id"],
            redacted_cv_text=survivor["redacted_cv_text"],
            jd_category_queries=jd_category_queries
        )
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

    # Format Final Leaderboard
    leaderboard = [eval_obj.model_dump() for eval_obj in final_evaluations]

    return {
        "metrics": pipeline_metrics,
        "leaderboard": leaderboard,
        "rejected_candidates": rejected_candidates
    }