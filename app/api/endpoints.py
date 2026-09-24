import base64
import binascii
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScreeningRequestSchema, ScreeningResponseSchema, PDFScreeningRequestSchema
from app.config import settings
from app.stage0_extraction.pipeline import ingest_pdf
from app.models.database import get_db
from app.orchestrator import run_end_to_end_screening_pipeline
from app.run_audit import canonical_hash, complete_run, policy_snapshot, reserve_run

router = APIRouter(prefix="/api/v1/screening", tags=["CV Screening"])


@router.post("/ingest-pdf")
async def ingest_pdf_upload(request: Request):
    """Accept a bounded PDF byte stream and return its redacted view."""
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/pdf":
        return {"status": "failure", "code": "INVALID_CONTENT_TYPE"}
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > settings.PDF_MAX_BYTES:
            return {"status": "failure", "code": "PDF_TOO_LARGE"}
        data.extend(chunk)
    result = ingest_pdf(bytes(data))
    data.clear()
    return {"status": result.status, "code": result.code,
            "pages": result.pages if result.status == "success" else []}


@router.post("/run-pdf")
async def run_pdf_screening_endpoint(payload: PDFScreeningRequestSchema, db: AsyncSession = Depends(get_db)):
    """PDF-only production entry point; no unredacted document reaches screening."""
    if len(payload.pdf_base64) > ((settings.PDF_MAX_BYTES + 2) // 3) * 4:
        return await _record_pdf_failure(payload, db, "PDF_TOO_LARGE")
    try:
        pdf = base64.b64decode(payload.pdf_base64, validate=True)
    except binascii.Error:
        return await _record_pdf_failure(payload, db, "INVALID_PDF_ENCODING")
    result = ingest_pdf(pdf)
    del pdf
    if result.status != "success":
        return await _record_pdf_failure(payload, db, result.code, result.status)
    candidate = {
        "candidate_id": payload.candidate_id,
        "raw_cv_text": result.redacted_text,
        "work_authorized": payload.work_authorized,
        "authorization_source": payload.authorization_source,
        "parsed_attributes": payload.parsed_attributes,
        "recruiter_overrides": payload.recruiter_overrides,
    }
    screening = ScreeningRequestSchema(job_profile=payload.job_profile, candidates=[candidate],
                                       top_n_stage2_cutoff=payload.top_n_stage2_cutoff,
                                       idempotency_key=payload.idempotency_key)
    response = await _run_screening(screening, db, {payload.candidate_id: result})
    return {"status": "success", "code": "OK", "screening": response.model_dump(mode="json")}


async def _record_pdf_failure(payload, db, code, outcome_status="failure"):
    job = payload.job_profile.model_dump(mode="json")
    policy = policy_snapshot(payload.top_n_stage2_cutoff)
    pdf_hash = hashlib.sha256(payload.pdf_base64.encode()).hexdigest()
    request_hash = canonical_hash({"job": job, "candidate_id": payload.candidate_id,
                                   "pdf_sha256": pdf_hash, "policy": policy})
    state, run = await reserve_run(db, key=payload.idempotency_key or request_hash, request_hash=request_hash,
                                   job_snapshot=job, policy=policy,
                                   candidates=[{"candidate_id": payload.candidate_id,
                                                "pdf_sha256": pdf_hash}])
    if state == "CONFLICT":
        raise HTTPException(status_code=409, detail="Idempotency key belongs to a different request")
    if state == "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="Screening run is in progress")
    if state == "REPLAY":
        return run.response_snapshot
    review = outcome_status == "review"
    outcome = "REVIEW_REQUIRED" if review else "EXTRACTION_FAILED"
    item = {"candidate_id": payload.candidate_id, "stage": "STAGE0",
            "evaluation_status": outcome, "reason": code}
    metrics = {"total_input_candidates": 1, "stage0_processed": 0,
               "stage0_failed": 0 if review else 1, "stage0_review_required": 1 if review else 0,
               "stage1_passed": 0, "stage1_rejected": 0, "stage1_review_required": 0,
               "stage2_shortlisted": 0, "stage2_excluded": 0, "stage2_failed": 0,
               "stage3_evaluated": 0, "stage3_succeeded": 0, "stage3_review_required": 0,
               "stage3_failed": 0, "accounted_candidates": 1}
    outcome_record = {"candidate_id": payload.candidate_id, "outcome": outcome,
               "stage": "STAGE0", "input_snapshot": {"candidate_id": payload.candidate_id,
                                                   "pdf_sha256": pdf_hash},
               "stage_history": [{"stage": "STAGE0", "status": "FAILED", "code": code}],
               "result_snapshot": item}
    result = {"metrics": metrics, "outcomes": [outcome_record], "leaderboard": [],
              "rejected_candidates": [], "review_candidates": [item] if review else [],
              "failed_candidates": [] if review else [item]}
    response = {"status": outcome_status, "code": code, "candidate_id": payload.candidate_id,
                "run_id": run.id, "idempotency_key": run.idempotency_key}
    try:
        await complete_run(db, run, result, [payload.candidate_id], response)
    except Exception:
        await db.rollback()
        run.status = "FAILED"
        await db.commit()
        raise
    return response


@router.post("/run", response_model=ScreeningResponseSchema, status_code=status.HTTP_200_OK)
async def run_cv_screening_endpoint(
    payload: ScreeningRequestSchema,
    db: AsyncSession = Depends(get_db)
):
    """
    Triggers the 4-Stage automated screening pipeline over a candidate batch
    and persists execution results to the database.
    """
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=404, detail="Text screening is available only for internal use")
    return await _run_screening(payload, db)


async def _run_screening(payload: ScreeningRequestSchema, db: AsyncSession, stage0_views=None):
    if not payload.candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Candidate list cannot be empty."
        )

    job_dict = payload.job_profile.model_dump(mode="json")
    raw_candidates_list = [c.model_dump(mode="json", exclude_unset=True) for c in payload.candidates]
    policy = policy_snapshot(payload.top_n_stage2_cutoff)
    request_hash = canonical_hash({"job": job_dict, "candidates": raw_candidates_list, "policy": policy})
    state, run = await reserve_run(db, key=payload.idempotency_key or request_hash, request_hash=request_hash,
                                   job_snapshot=job_dict, policy=policy, candidates=raw_candidates_list)
    if state == "CONFLICT":
        raise HTTPException(status_code=409, detail="Idempotency key belongs to a different request")
    if state == "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="Screening run is in progress")
    if state == "REPLAY":
        return ScreeningResponseSchema.model_validate(run.response_snapshot)

    try:
        pipeline_result = await run_end_to_end_screening_pipeline(
            raw_candidates=raw_candidates_list, jd_profile=job_dict,
            hard_filter_rules=payload.job_profile.hard_filter_rules,
            top_n_stage2_cutoff=payload.top_n_stage2_cutoff, stage0_views=stage0_views)
        response = ScreeningResponseSchema(
            run_id=run.id, idempotency_key=run.idempotency_key, job_id=run.job_id,
            metrics=pipeline_result["metrics"],
            leaderboard=pipeline_result["leaderboard"],
            rejected_candidates=pipeline_result["rejected_candidates"],
            review_candidates=pipeline_result["review_candidates"],
            failed_candidates=pipeline_result["failed_candidates"])
        await complete_run(db, run, pipeline_result, [c.candidate_id for c in payload.candidates],
                           response.model_dump(mode="json"))
        return response
    except Exception:
        await db.rollback()
        run.status = "FAILED"
        await db.commit()
        raise
