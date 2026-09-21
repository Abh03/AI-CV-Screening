from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScreeningRequestSchema, ScreeningResponseSchema
from app.models.database import get_db, JobProfileModel, EvaluationResultModel
from app.orchestrator import run_end_to_end_screening_pipeline

router = APIRouter(prefix="/api/v1/screening", tags=["CV Screening"])


@router.post("/run", response_model=ScreeningResponseSchema, status_code=status.HTTP_200_OK)
async def run_cv_screening_endpoint(
    payload: ScreeningRequestSchema,
    db: AsyncSession = Depends(get_db)
):
    """
    Triggers the 4-Stage automated screening pipeline over a candidate batch
    and persists execution results to the database.
    """
    if not payload.candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Candidate list cannot be empty."
        )

    job_dict = payload.job_profile.model_dump()
    raw_candidates_list = [c.model_dump() for c in payload.candidates]

    # Pipeline execution
    pipeline_result = await run_end_to_end_screening_pipeline(
        raw_candidates=raw_candidates_list,
        jd_profile=job_dict,
        hard_filter_rules=payload.job_profile.hard_filter_rules,
        top_n_stage2_cutoff=payload.top_n_stage2_cutoff
    )

    # Persistence Step 1: Job Profile
    job_id = payload.job_profile.job_id
    existing_job = await db.get(JobProfileModel, job_id)
    if not existing_job:
        new_job = JobProfileModel(
            id=job_id,
            title=payload.job_profile.title,
            category_queries=payload.job_profile.jd_category_queries,
            hard_filter_rules=payload.job_profile.hard_filter_rules or {}
        )
        db.add(new_job)
        await db.flush()

    # Persistence Step 2: Evaluation Results
    for eval_item in pipeline_result.get("leaderboard", []):
        eval_record = EvaluationResultModel(
            job_id=job_id,
            candidate_id=eval_item["candidate_id"],
            composite_score=eval_item.get("composite_score", 0.0),
            tier=eval_item.get("tier", "TIER_3"),
            category_scores=eval_item.get("category_scores", {}),
            verified_citations=eval_item.get("verified_citations", []),
            invalid_citations=eval_item.get("invalid_citations", []),
            has_critical_flags=eval_item.get("has_critical_flags", False),
            llm_raw_output=eval_item
        )
        db.add(eval_record)

    await db.commit()

    return ScreeningResponseSchema(
        job_id=job_id,
        metrics=pipeline_result["metrics"],
        leaderboard=pipeline_result["leaderboard"],
        rejected_candidates=pipeline_result["rejected_candidates"]
    )