from typing import List, Dict, Any, Optional
import asyncio
import hashlib
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
from app.stage3_evaluation.scoring import failed_evaluation


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
        "stage1_failed": 0,
        "stage2_shortlisted": 0,
        "stage3_evaluated": 0,
        "stage3_succeeded": 0,
        "stage3_review_required": 0,
        "stage3_failed": 0,
        "stage0_failed": 0,
        "stage0_review_required": 0,
        "stage2_failed": 0,
        "stage2_excluded": 0,
        "accounted_candidates": 0,
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
    failed_candidates = []
    outcomes = {cand.candidate_id: {"candidate_id": cand.candidate_id, "stage_history": [],
                                     "input_snapshot": {"candidate_id": cand.candidate_id,
                                                        "input_sha256": hashlib.sha256(cand.raw_cv_text.encode()).hexdigest(),
                                                        "work_authorized": cand.work_authorized.value,
                                                        "authorization_source": cand.authorization_source.value,
                                                        "parsed_attributes": cand.parsed_attributes.model_dump(mode="json"),
                                                        "recruiter_overrides": cand.recruiter_overrides.model_dump(mode="json")},
                                     "evidence_snapshot": None, "result_snapshot": {}}
                for cand in candidates}

    def finish():
        pipeline_metrics["accounted_candidates"] = len([entry for entry in outcomes.values() if entry.get("outcome")])
        if pipeline_metrics["accounted_candidates"] != len(candidates):
            raise RuntimeError("Candidate accounting is incomplete")
        return {"metrics": pipeline_metrics, "leaderboard": leaderboard,
                "rejected_candidates": sorted(rejected_candidates, key=lambda item: item["candidate_id"]),
                "review_candidates": sorted(review_candidates, key=lambda item: item["candidate_id"]),
                "failed_candidates": sorted(failed_candidates, key=lambda item: item["candidate_id"]),
                "outcomes": list(outcomes.values())}

    leaderboard = []

    # Stage 0 & Stage 1: PII Masking & Hard Filtering
    for cand in candidates:
        cand_id = cand.candidate_id
        raw_text = cand.raw_cv_text

        # Stage 0: PII Redaction (returns string directly)
        view = (stage0_views or {}).get(cand_id)
        try:
            redacted_text = view.redacted_text if view is not None else mask_pii_runtime_view(raw_text)
        except Exception:
            item = {"candidate_id": cand_id, "stage": "STAGE0", "evaluation_status": "EXTRACTION_FAILED",
                    "reason": "EXTRACTION_FAILED"}
            failed_candidates.append(item)
            outcomes[cand_id].update(outcome="EXTRACTION_FAILED", stage="STAGE0", result_snapshot=item)
            outcomes[cand_id]["stage_history"].append({"stage": "STAGE0", "status": "FAILED"})
            pipeline_metrics["stage0_failed"] += 1
            continue
        outcomes[cand_id]["input_snapshot"]["redacted_cv_text"] = redacted_text
        outcomes[cand_id]["stage_history"].append({"stage": "STAGE0", "status": "PROCESSED"})
        pipeline_metrics["stage0_processed"] += 1

        # Stage 1: Deterministic Rules Filter
        try:
            facts = cand.rule_facts()
            filter_result = evaluate_stage1_hard_filters(
                candidate_yoe=facts.experience_years,
                candidate_cv_text=redacted_text,
                work_authorized=facts.work_authorized,
                jd_profile=rules,
                experience_source=facts.experience_source,
                authorization_source=facts.authorization_source,
            )
        except Exception:
            item = {"candidate_id": cand_id, "stage": "STAGE1", "evaluation_status": "PROCESSING_FAILED",
                    "reason": "RULE_EVALUATION_FAILED"}
            failed_candidates.append(item)
            outcomes[cand_id].update(outcome="PROCESSING_FAILED", stage="STAGE1", result_snapshot=item)
            outcomes[cand_id]["stage_history"].append({"stage": "STAGE1", "status": "FAILED"})
            pipeline_metrics["stage1_failed"] += 1
            continue
        filter_result["input_provenance"] = {
            "reported_experience_years": cand.parsed_attributes.experience_years,
            "reported_experience_source": cand.parsed_attributes.experience_source.value,
            "reported_work_authorized": cand.work_authorized.value,
            "reported_authorization_source": cand.authorization_source.value,
            "nested_work_authorized": cand.parsed_attributes.work_authorized.value,
            "nested_authorization_source": cand.parsed_attributes.authorization_source.value,
            "recruiter_overrides": cand.recruiter_overrides.model_dump(mode="json", exclude_none=True),
        }
        outcomes[cand_id]["stage_history"].append({"stage": "STAGE1", "status": filter_result["status"],
                                                    "details": filter_result})
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
            item = {
                "candidate_id": cand_id, "stage": "STAGE1",
                "evaluation_status": "REVIEW_REQUIRED",
                "reason": "STAGE1_REQUIRES_REVIEW", "filter_details": filter_result,
            }
            review_candidates.append(item)
            outcomes[cand_id].update(outcome="REVIEW_REQUIRED", stage="STAGE1", result_snapshot=item)
        else:
            pipeline_metrics["stage1_rejected"] += 1
            item = {
                "candidate_id": cand_id,
                "reason": "FAILED_STAGE1_FILTERS",
                "filter_details": filter_result
            }
            rejected_candidates.append(item)
            outcomes[cand_id].update(outcome="FILTER_REJECTED", stage="STAGE1", result_snapshot=item)

    if not stage1_survivors:
        return finish()

    # Stage 2: Category-Aware Evidence Extraction & Candidate Batch Ranking
    if settings.ENVIRONMENT.lower() == "production" and settings.STAGE2_BACKEND != "postgres":
        raise RuntimeError("Production Stage 2 requires STAGE2_BACKEND=postgres")
    stage2_payloads = []
    for survivor in stage1_survivors:
        extractor = (extract_candidate_category_evidence_postgres
                     if settings.STAGE2_BACKEND == "postgres" else extract_candidate_category_evidence)
        try:
            kwargs = dict(
                candidate_id=survivor["candidate_id"],
                redacted_cv_text=survivor["redacted_cv_text"],
                jd_category_queries=jd_category_queries,
                source_pages=(stage0_views or {}).get(survivor["candidate_id"]).pages
                if survivor["candidate_id"] in (stage0_views or {}) else None)
            if settings.STAGE2_BACKEND == "postgres":
                evidence_payload = await extractor(**kwargs, job_id=effective_jd["job_id"])
            else:
                evidence_payload = await asyncio.to_thread(extractor, **kwargs)
            if evidence_payload.get("candidate_id") != survivor["candidate_id"]:
                raise ValueError("Evidence ownership mismatch")
        except Exception:
            cand_id = survivor["candidate_id"]
            item = {"candidate_id": cand_id, "stage": "STAGE2", "evaluation_status": "EXTRACTION_FAILED",
                    "reason": "EVIDENCE_EXTRACTION_FAILED"}
            failed_candidates.append(item)
            outcomes[cand_id].update(outcome="EXTRACTION_FAILED", stage="STAGE2", result_snapshot=item)
            outcomes[cand_id]["stage_history"].append({"stage": "STAGE2", "status": "FAILED"})
            pipeline_metrics["stage2_failed"] += 1
            continue
        outcomes[survivor["candidate_id"]]["evidence_snapshot"] = evidence_payload
        outcomes[survivor["candidate_id"]]["stage_history"].append({"stage": "STAGE2", "status": "EXTRACTED"})
        stage2_payloads.append(evidence_payload)

    # Global Batch Cutoff: Rank all Stage 1 survivors by S_cand and slice top candidates
    try:
        shortlisted_candidates = rank_and_filter_candidate_batch(
            stage2_payloads, top_n_llm=top_n_stage2_cutoff)
        selected_ids = [item["candidate_id"] for item in shortlisted_candidates]
        if len(selected_ids) != len(set(selected_ids)) or not set(selected_ids).issubset(
                {item["candidate_id"] for item in stage2_payloads}):
            raise ValueError("Invalid shortlist")
    except Exception:
        shortlisted_candidates = []
        for evidence in stage2_payloads:
            cand_id = evidence["candidate_id"]
            item = {"candidate_id": cand_id, "stage": "STAGE2", "evaluation_status": "PROCESSING_FAILED",
                    "reason": "RANKING_FAILED"}
            failed_candidates.append(item)
            outcomes[cand_id].update(outcome="PROCESSING_FAILED", stage="STAGE2", result_snapshot=item)
            outcomes[cand_id]["stage_history"].append({"stage": "STAGE2", "status": "FAILED"})
            pipeline_metrics["stage2_failed"] += 1
        stage2_payloads = []
    pipeline_metrics["stage2_shortlisted"] = len(shortlisted_candidates)
    shortlisted_ids = {item["candidate_id"] for item in shortlisted_candidates}
    for evidence in stage2_payloads:
        cand_id = evidence["candidate_id"]
        if cand_id not in shortlisted_ids:
            item = {"candidate_id": cand_id, "stage": "STAGE2", "reason": "CUTOFF_EXCLUDED",
                    "retrieval_score": evidence.get("composite_score")}
            rejected_candidates.append(item)
            outcomes[cand_id].update(outcome="CUTOFF_EXCLUDED", stage="STAGE2", result_snapshot=item)
            outcomes[cand_id]["stage_history"].append({"stage": "STAGE2", "status": "CUTOFF_EXCLUDED"})
            pipeline_metrics["stage2_excluded"] += 1

    # Stage 3: Async LLM Multi-Attribute Evaluation
    try:
        final_evaluations: List[FinalCandidateEvaluation] = await evaluate_candidate_batch_async(
            candidate_payloads=shortlisted_candidates, jd_profile=effective_jd,
            llm_provider=llm_provider, concurrency_limit=llm_concurrency_limit)
    except Exception:
        final_evaluations = []
    returned = {result.candidate_id: result for result in final_evaluations
                if result.candidate_id in shortlisted_ids}
    final_evaluations = [returned.get(item["candidate_id"]) or
                         failed_evaluation(item["candidate_id"], "BATCH_EVALUATION_FAILED",
                                           "Evaluation did not return a result.") for item in shortlisted_candidates]
    pipeline_metrics["stage3_evaluated"] = len(final_evaluations)

    stage1_details = {candidate["candidate_id"]: candidate["filter_details"] for candidate in stage1_survivors}
    for result in sorted(final_evaluations, key=evaluation_sort_key):
        item = result.model_dump(mode="json")
        item["stage1_filter_details"] = stage1_details[result.candidate_id]
        outcomes[result.candidate_id].update(outcome=result.evaluation_status.value,
                                              stage="STAGE3", result_snapshot=item)
        outcomes[result.candidate_id]["stage_history"].append(
            {"stage": "STAGE3", "status": result.evaluation_status.value})
        if result.evaluation_status == EvaluationStatus.SUCCESS:
            pipeline_metrics["stage3_succeeded"] += 1
            leaderboard.append(item)
        elif result.evaluation_status == EvaluationStatus.REVIEW_REQUIRED:
            pipeline_metrics["stage3_review_required"] += 1
            review_candidates.append(dict(item, stage="STAGE3"))
        else:
            pipeline_metrics["stage3_failed"] += 1
            failed_candidates.append(item)
    return finish()
