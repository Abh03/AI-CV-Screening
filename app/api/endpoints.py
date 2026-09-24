import base64
import binascii
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScreeningRequestSchema, ScreeningResponseSchema, PDFScreeningRequestSchema
from app.config import settings
from app.stage0_extraction.pipeline import ingest_pdf
from app.models.database import get_db, JobProfileModel, EvaluationResultModel
from app.models.database import ScreeningRunModel
from uuid import uuid4
from app.orchestrator import run_end_to_end_screening_pipeline

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
        return {"status": "failure", "code": "PDF_TOO_LARGE", "candidate_id": payload.candidate_id}
    try:
        pdf = base64.b64decode(payload.pdf_base64, validate=True)
    except binascii.Error:
        return {"status": "failure", "code": "INVALID_PDF_ENCODING", "candidate_id": payload.candidate_id}
    result = ingest_pdf(pdf)
    del pdf
    if result.status != "success":
        return {"status": result.status, "code": result.code, "candidate_id": payload.candidate_id}
    candidate = {
        "candidate_id": payload.candidate_id,
        "raw_cv_text": result.redacted_text,
        "work_authorized": payload.work_authorized,
        "authorization_source": payload.authorization_source,
        "parsed_attributes": payload.parsed_attributes,
        "recruiter_overrides": payload.recruiter_overrides,
    }
    screening = ScreeningRequestSchema(job_profile=payload.job_profile, candidates=[candidate],
                                       top_n_stage2_cutoff=payload.top_n_stage2_cutoff)
    response = await _run_screening(screening, db, {payload.candidate_id: result})
    return {"status": "success", "code": "OK", "screening": response.model_dump(mode="json")}


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

    job_dict = payload.job_profile.model_dump()
    raw_candidates_list = [c.model_dump(mode="json", exclude_unset=True) for c in payload.candidates]

    # Pipeline execution
    pipeline_result = await run_end_to_end_screening_pipeline(
        raw_candidates=raw_candidates_list,
        jd_profile=job_dict,
        hard_filter_rules=payload.job_profile.hard_filter_rules,
        top_n_stage2_cutoff=payload.top_n_stage2_cutoff,
        stage0_views=stage0_views
    )

    # Persistence Step 1: Job Profile
    job_id = payload.job_profile.job_id
    existing_job = await db.get(JobProfileModel, job_id)
    if not existing_job:
        new_job = JobProfileModel(
            id=job_id,
            title=payload.job_profile.title,
            category_queries=payload.job_profile.jd_category_queries,
            hard_filter_rules=payload.job_profile.hard_filter_rules.model_dump(mode="json")
        )
        db.add(new_job)
        await db.flush()

    # Persistence Step 2: Evaluation Results
    evaluation_items = (pipeline_result["leaderboard"] + pipeline_result["failed_candidates"]
                        + [item for item in pipeline_result["review_candidates"] if item.get("stage") == "STAGE3"])
    for eval_item in evaluation_items:
        eval_record = EvaluationResultModel(
            job_id=job_id,
            candidate_id=eval_item["candidate_id"],
            composite_score=eval_item["composite_score"],
            tier=eval_item["tier"],
            evaluation_status=eval_item["evaluation_status"],
            scoring_policy_version=eval_item["scoring_policy_version"],
            category_scores=eval_item.get("category_scores", {}),
            verified_citations=eval_item.get("verified_citations", []),
            invalid_citations=eval_item.get("invalid_citations", []),
            has_critical_flags=eval_item.get("has_critical_flags", False),
            llm_raw_output=eval_item
        )
        db.add(eval_record)

    db.add(ScreeningRunModel(id=str(uuid4()), job_id=job_id,
                             status="COMPLETED", metrics=pipeline_result["metrics"]))

    await db.commit()

    return ScreeningResponseSchema(
        job_id=job_id,
        metrics=pipeline_result["metrics"],
        leaderboard=pipeline_result["leaderboard"],
        rejected_candidates=pipeline_result["rejected_candidates"],
        review_candidates=pipeline_result["review_candidates"],
        failed_candidates=pipeline_result["failed_candidates"]
    )
