from fastapi import APIRouter, HTTPException, status
from app.api.schemas import ScreeningRequestSchema, ScreeningResponseSchema
from app.orchestrator import run_end_to_end_screening_pipeline

router = APIRouter(prefix="/api/v1/screening", tags=["CV Screening"])


@router.post("/run", response_model=ScreeningResponseSchema, status_code=status.HTTP_200_OK)
async def run_cv_screening_endpoint(payload: ScreeningRequestSchema):
    """
    Triggers the 4-Stage automated screening pipeline over a candidate batch.
    """
    if not payload.candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Candidate list cannot be empty."
        )

    job_dict = payload.job_profile.model_dump()
    raw_candidates_list = [c.model_dump() for c in payload.candidates]

    pipeline_result = await run_end_to_end_screening_pipeline(
        raw_candidates=raw_candidates_list,
        jd_profile=job_dict,
        hard_filter_rules=payload.job_profile.hard_filter_rules,
        top_n_stage2_cutoff=payload.top_n_stage2_cutoff
    )

    return ScreeningResponseSchema(
        job_id=payload.job_profile.job_id,
        metrics=pipeline_result["metrics"],
        leaderboard=pipeline_result["leaderboard"],
        rejected_candidates=pipeline_result["rejected_candidates"]
    )