"""Run reservation, immutable audit snapshots, and deterministic score replay."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.database import CandidateOutcomeModel, EvaluationResultModel, JobProfileModel, ScreeningRunModel
from app.stage3_evaluation.prompts import SYSTEM_PROMPT_STAGE3
from app.stage3_evaluation.scoring import CATEGORY_WEIGHTS, SCORING_POLICY_VERSION
from app.stage3_evaluation.schemas import LLMEvaluationOutput
from app.stage1_rules.rules_engine import STAGE1_POLICY_VERSION
from app.stage2_retrieval.embeddings import EMBEDDING_MODEL_NAME, EMBEDDING_MODEL_VERSION
from app.stage2_retrieval.repository import CHUNKING_VERSION, REDACTION_VERSION
from app.stage2_retrieval.evidence_extractor import DEFAULT_CATEGORY_WEIGHTS
from app.stage2_retrieval.reranker import _MODEL_NAME as RERANKER_MODEL_NAME, RERANKER_MODEL_VERSION

PROMPT_VERSION = "stage3-prompt-v1"
RUN_POLICY_VERSION = "orchestration-v1"


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def policy_snapshot(cutoff):
    provider = settings.LLM_PROVIDER.lower()
    model = {"gemini": "gemini-3.6-flash", "groq": settings.GROQ_MODEL,
             "openrouter": settings.OPENROUTER_MODEL, "mock": "mock"}.get(provider)
    return {"orchestration_version": RUN_POLICY_VERSION, "scoring_policy_version": SCORING_POLICY_VERSION,
            "stage1_policy_version": STAGE1_POLICY_VERSION, "category_policy_version": "category-v1",
            "redaction_version": REDACTION_VERSION, "chunking_version": CHUNKING_VERSION,
            "embedding_model": EMBEDDING_MODEL_NAME, "embedding_model_version": EMBEDDING_MODEL_VERSION,
            "reranker_model": RERANKER_MODEL_NAME, "reranker_model_version": RERANKER_MODEL_VERSION,
            "stage2_category_weights": DEFAULT_CATEGORY_WEIGHTS.copy(),
            "category_weights": {key: str(value) for key, value in CATEGORY_WEIGHTS.items()},
            "tier_thresholds": {"TIER_1": 75, "TIER_2": 55},
            "prompt_version": PROMPT_VERSION, "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT_STAGE3.encode()).hexdigest(),
            "output_schema_sha256": canonical_hash(LLMEvaluationOutput.model_json_schema()),
            "model_temperature": 0.1,
            "provider": provider, "model": model, "stage2_cutoff": cutoff,
            "stage2_backend": settings.STAGE2_BACKEND,
            "pdf_max_bytes": settings.PDF_MAX_BYTES, "pdf_max_pages": settings.PDF_MAX_PAGES,
            "pdf_ocr_max_pages": settings.PDF_OCR_MAX_PAGES,
            "pdf_ocr_language": settings.PDF_OCR_LANGUAGE}


async def reserve_run(db, *, key, request_hash, job_snapshot, policy, candidates, owner_id="local", admin=False):
    """Commit the reservation before any retrieval or provider call."""
    if key:
        existing = (await db.execute(select(ScreeningRunModel).where(
            ScreeningRunModel.idempotency_key == key))).scalar_one_or_none()
        if existing:
            if existing.owner_id != owner_id and not admin:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Run access denied")
            if existing.request_hash != request_hash:
                await db.commit()
                return "CONFLICT", existing
            if existing.status == "COMPLETED":
                await db.commit()
                return "REPLAY", existing
            if existing.status == "QUEUED":
                await db.commit()
                return "IN_PROGRESS", existing
            if existing.status == "RUNNING" and existing.request_snapshot is not None:
                lease = existing.lease_until
                if lease is None or (lease.replace(tzinfo=timezone.utc) if lease.tzinfo is None else lease) > datetime.now(timezone.utc):
                    await db.commit()
                    return "IN_PROGRESS", existing
            if existing.status == "RUNNING" and existing.created_at:
                started = existing.created_at.replace(tzinfo=timezone.utc) if existing.created_at.tzinfo is None else existing.created_at
                if datetime.now(timezone.utc) - started < timedelta(minutes=5):
                    await db.commit()
                    return "IN_PROGRESS", existing
            # A failed or abandoned reservation has no published results.
            existing.status = "RUNNING"
            existing.created_at = datetime.now(timezone.utc)
            await db.commit()
            return "RESERVED", existing
    job_id = job_snapshot["job_id"]
    job = await db.get(JobProfileModel, job_id)
    if job is None:
        db.add(JobProfileModel(id=job_id, owner_id=owner_id, title=job_snapshot["title"],
                               category_queries=job_snapshot["jd_category_queries"],
                               hard_filter_rules=job_snapshot["hard_filter_rules"]))
    else:
        if job.owner_id != owner_id and not admin:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Job access denied")
        # This compatibility table reflects the latest profile; decisions refer to the run snapshot.
        job.title = job_snapshot["title"]
        job.category_queries = job_snapshot["jd_category_queries"]
        job.hard_filter_rules = job_snapshot["hard_filter_rules"]
    run = ScreeningRunModel(id=str(uuid4()), owner_id=owner_id, job_id=job_id, status="RUNNING", metrics={},
                            idempotency_key=key, request_hash=request_hash,
                            job_snapshot=job_snapshot, policy_snapshot=policy)
    db.add(run)
    try:
        # These ORM objects have no mapped relationship; flush the parent before
        # inserting candidate outcomes so PostgreSQL enforces the FK in this order.
        await db.flush()
        for candidate in candidates:
            db.add(CandidateOutcomeModel(
                run_id=run.id, candidate_id=candidate["candidate_id"], outcome="PENDING", stage="RUN",
                input_snapshot={"candidate_id": candidate["candidate_id"],
                                "input_sha256": canonical_hash(candidate)},
                stage_history=[{"stage": "RUN", "status": "RESERVED"}],
                evidence_snapshot=None, citation_mapping=None, validated_output=None,
                result_snapshot={}, provider=policy["provider"], model=policy["model"],
                prompt_version=policy["prompt_version"],
                scoring_policy_version=policy["scoring_policy_version"]))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if key:
            existing = (await db.execute(select(ScreeningRunModel).where(
                ScreeningRunModel.idempotency_key == key))).scalar_one_or_none()
            if existing:
                await db.commit()
                if existing.request_hash != request_hash:
                    return "CONFLICT", existing
                return ("REPLAY" if existing.status == "COMPLETED" else "IN_PROGRESS"), existing
        raise
    return "RESERVED", run


def validate_accounting(result, candidate_ids):
    outcomes = result.get("outcomes", [])
    expected = set(candidate_ids)
    if len(expected) != len(candidate_ids) or len(outcomes) != len(expected):
        raise ValueError("Candidate outcomes do not match the input batch")
    if {item["candidate_id"] for item in outcomes} != expected:
        raise ValueError("Candidate outcomes do not match the input batch")
    public_items = (result["leaderboard"] + result["review_candidates"] +
                    result["rejected_candidates"] + result["failed_candidates"])
    if len(public_items) != len(expected) or {item["candidate_id"] for item in public_items} != expected:
        raise ValueError("Candidate response lists do not reconcile")
    for item in outcomes:
        if not item.get("outcome") or not item.get("stage"):
            raise ValueError("Candidate outcome is incomplete")
    metrics = result["metrics"]
    if metrics["total_input_candidates"] != len(expected):
        raise ValueError("Input metric does not match candidate outcomes")
    counts = {name: sum(item["outcome"] == name for item in outcomes) for name in
              ("SUCCESS", "REVIEW_REQUIRED", "EVALUATION_FAILED", "FILTER_REJECTED",
               "CUTOFF_EXCLUDED", "EXTRACTION_FAILED", "PROCESSING_FAILED")}
    if sum(counts.values()) != len(expected):
        raise ValueError("Outcome counts do not reconcile")
    if metrics["stage3_succeeded"] != counts["SUCCESS"] or metrics["stage3_failed"] != counts["EVALUATION_FAILED"]:
        raise ValueError("Stage 3 metrics do not reconcile")
    for stage, metric in (("STAGE0", "stage0_failed"), ("STAGE1", "stage1_failed"),
                          ("STAGE2", "stage2_failed")):
        stage_failures = sum(item["stage"] == stage and item["outcome"] in
                             {"EXTRACTION_FAILED", "PROCESSING_FAILED"} for item in outcomes)
        if stage_failures != metrics.get(metric, 0):
            raise ValueError(f"{stage} failure metric does not reconcile")
    if metrics["stage1_review_required"] != sum(item["stage"] == "STAGE1" and
                                                 item["outcome"] == "REVIEW_REQUIRED" for item in outcomes):
        raise ValueError("Stage 1 review metric does not reconcile")
    if metrics["stage3_review_required"] != sum(item["stage"] == "STAGE3" and
                                                 item["outcome"] == "REVIEW_REQUIRED" for item in outcomes):
        raise ValueError("Stage 3 review metric does not reconcile")
    if metrics["stage1_rejected"] != counts["FILTER_REJECTED"] or metrics["stage2_excluded"] != counts["CUTOFF_EXCLUDED"]:
        raise ValueError("Stage 1/2 metrics do not reconcile")
    if metrics["accounted_candidates"] != len(expected):
        raise ValueError("Accounted metric does not reconcile")
    if metrics["stage0_processed"] + metrics["stage0_failed"] + metrics.get("stage0_review_required", 0) != len(expected):
        raise ValueError("Stage 0 metrics do not reconcile")
    if metrics.get("stage0_review_required", 0) != sum(item["stage"] == "STAGE0" and
                                                        item["outcome"] == "REVIEW_REQUIRED" for item in outcomes):
        raise ValueError("Stage 0 review metric does not reconcile")
    if metrics["stage1_passed"] + metrics["stage1_rejected"] + metrics["stage1_review_required"] + metrics.get("stage1_failed", 0) != metrics["stage0_processed"]:
        raise ValueError("Stage 1 metrics do not reconcile")
    if metrics["stage2_shortlisted"] + metrics["stage2_excluded"] + metrics["stage2_failed"] != metrics["stage1_passed"]:
        raise ValueError("Stage 2 metrics do not reconcile")
    if metrics["stage3_succeeded"] + metrics["stage3_review_required"] + metrics["stage3_failed"] != metrics["stage3_evaluated"] or metrics["stage3_evaluated"] != metrics["stage2_shortlisted"]:
        raise ValueError("Stage 3 metrics do not reconcile")


async def complete_run(db, run, result, candidate_ids, response):
    """Publish all candidate records, evaluations, metrics and response in one short transaction."""
    validate_accounting(result, candidate_ids)
    policy = run.policy_snapshot
    existing = {row.candidate_id: row for row in (await db.execute(
        select(CandidateOutcomeModel).where(CandidateOutcomeModel.run_id == run.id))).scalars()}
    for item in result["outcomes"]:
        decision = item["result_snapshot"]
        verification = decision.get("evidence_verification") or {}
        row = existing[item["candidate_id"]]
        if row.outcome != "PENDING":
            raise ValueError("Candidate outcome has already been finalized")
        row.outcome = item["outcome"]
        row.stage = item["stage"]
        row.input_snapshot = item["input_snapshot"]
        row.stage_history = row.stage_history + item["stage_history"]
        row.evidence_snapshot = item.get("evidence_snapshot")
        row.citation_mapping = verification.get("registry")
        row.validated_output = decision.get("llm_raw_output")
        row.result_snapshot = decision
        row.scoring_policy_version = decision.get("scoring_policy_version", policy["scoring_policy_version"])
        if item["stage"] == "STAGE3":
            db.add(EvaluationResultModel(
                run_id=run.id, job_id=run.job_id, candidate_id=item["candidate_id"],
                composite_score=decision.get("composite_score"), tier=decision.get("tier"),
                evaluation_status=decision["evaluation_status"],
                scoring_policy_version=decision["scoring_policy_version"],
                category_scores=decision.get("category_scores", {}),
                verified_citations=decision.get("verified_citations", []),
                invalid_citations=decision.get("invalid_citations", []),
                has_critical_flags=decision.get("has_critical_flags", False), llm_raw_output=decision))
    run.metrics = result["metrics"]
    run.response_snapshot = response
    run.status = "COMPLETED"
    await db.commit()


def recompute_stored_decision(run: ScreeningRunModel, outcome: CandidateOutcomeModel):
    """Recompute score and tier from validated output and the stored policy only."""
    if outcome.stage != "STAGE3" or outcome.validated_output is None:
        return None
    scores = {name: Decimal(str(outcome.validated_output[name]["score"]))
              for name in run.policy_snapshot["category_weights"]}
    score = sum(scores[name] * Decimal(weight)
                for name, weight in run.policy_snapshot["category_weights"].items())
    score = float(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    thresholds = run.policy_snapshot["tier_thresholds"]
    tier = ("TIER_1" if score >= thresholds["TIER_1"] else
            "TIER_2" if score >= thresholds["TIER_2"] else "TIER_3")
    if outcome.outcome != "SUCCESS":
        tier = None
    return {"composite_score": score, "tier": tier,
            "matches_stored": score == outcome.result_snapshot.get("composite_score") and
            tier == outcome.result_snapshot.get("tier")}
