from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class CandidateInputSchema(BaseModel):
    candidate_id: str
    raw_cv_text: str
    work_authorized: bool = True
    parsed_attributes: Dict[str, Any] = Field(default_factory=dict)


class JobProfileInputSchema(BaseModel):
    job_id: str
    title: str
    jd_category_queries: Dict[str, str]
    hard_filter_rules: Optional[Dict[str, Any]] = None


class ScreeningRequestSchema(BaseModel):
    job_profile: JobProfileInputSchema
    candidates: List[CandidateInputSchema]
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)


class PipelineMetricsSchema(BaseModel):
    total_input_candidates: int
    stage0_processed: int
    stage1_passed: int
    stage1_rejected: int
    stage2_shortlisted: int
    stage3_evaluated: int


class ScreeningResponseSchema(BaseModel):
    job_id: str
    metrics: PipelineMetricsSchema
    leaderboard: List[Dict[str, Any]]
    rejected_candidates: List[Dict[str, Any]]