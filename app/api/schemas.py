from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


from app.stage1_rules.contracts import CandidateInput, HardFilterRules, StrictModel
from app.stage1_rules.contracts import CandidateAttributes, RecruiterOverrides, Authorization, AuthorizationStatus, AttributeSource


class CandidateInputSchema(CandidateInput):
    pass


class JobProfileInputSchema(StrictModel):
    job_id: str
    title: str
    jd_category_queries: Dict[str, str]
    hard_filter_rules: HardFilterRules = Field(default_factory=HardFilterRules)


class ScreeningRequestSchema(StrictModel):
    job_profile: JobProfileInputSchema
    candidates: List[CandidateInputSchema]
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)


class PDFScreeningRequestSchema(StrictModel):
    job_profile: JobProfileInputSchema
    candidate_id: str = Field(min_length=1)
    pdf_base64: str
    work_authorized: Authorization = AuthorizationStatus.UNKNOWN
    authorization_source: AttributeSource = AttributeSource.UNKNOWN
    parsed_attributes: CandidateAttributes = Field(default_factory=CandidateAttributes)
    recruiter_overrides: RecruiterOverrides = Field(default_factory=RecruiterOverrides)
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)


class PipelineMetricsSchema(BaseModel):
    total_input_candidates: int
    stage0_processed: int
    stage1_passed: int
    stage1_rejected: int
    stage1_review_required: int
    stage2_shortlisted: int
    stage3_evaluated: int
    stage3_succeeded: int
    stage3_review_required: int
    stage3_failed: int


class ScreeningResponseSchema(BaseModel):
    job_id: str
    metrics: PipelineMetricsSchema
    leaderboard: List[Dict[str, Any]]
    rejected_candidates: List[Dict[str, Any]]
    review_candidates: List[Dict[str, Any]]
    failed_candidates: List[Dict[str, Any]]
