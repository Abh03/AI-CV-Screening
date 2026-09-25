from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator


from app.stage1_rules.contracts import CandidateInput, HardFilterRules, StrictModel
from app.stage1_rules.contracts import CandidateAttributes, RecruiterOverrides, Authorization, AuthorizationStatus, AttributeSource


class CandidateInputSchema(CandidateInput):
    pass


class JobProfileInputSchema(StrictModel):
    job_id: str
    title: str
    jd_category_queries: Dict[str, str]
    hard_filter_rules: HardFilterRules = Field(default_factory=HardFilterRules)

class CampaignCreateSchema(StrictModel):
    job_profiles: List[JobProfileInputSchema] = Field(min_length=1)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_jds(self):
        ids = [job.job_id for job in self.job_profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("JD IDs must be distinct")
        for job in self.job_profiles:
            if not job.job_id.strip() or len(job.job_id) > 64 or not job.title.strip() or len(job.title) > 255:
                raise ValueError("JD ID and title must be nonempty and within length limits")
            if not job.jd_category_queries or any(not key.strip() or not value.strip()
                                                   for key, value in job.jd_category_queries.items()):
                raise ValueError("At least one nonempty category query is required")
        return self


class ScreeningRequestSchema(StrictModel):
    job_profile: JobProfileInputSchema
    candidates: List[CandidateInputSchema]
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate IDs must be unique within a run")
        return self


class PDFScreeningRequestSchema(StrictModel):
    job_profile: JobProfileInputSchema
    candidate_id: str = Field(min_length=1)
    pdf_base64: str
    work_authorized: Authorization = AuthorizationStatus.UNKNOWN
    authorization_source: AttributeSource = AttributeSource.UNKNOWN
    parsed_attributes: CandidateAttributes = Field(default_factory=CandidateAttributes)
    recruiter_overrides: RecruiterOverrides = Field(default_factory=RecruiterOverrides)
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)


class PipelineMetricsSchema(BaseModel):
    total_input_candidates: int
    stage0_processed: int
    stage1_passed: int
    stage1_rejected: int
    stage1_review_required: int
    stage1_failed: int = 0
    stage2_shortlisted: int
    stage3_evaluated: int
    stage3_succeeded: int
    stage3_review_required: int
    stage3_failed: int
    stage0_failed: int = 0
    stage0_review_required: int = 0
    stage2_failed: int = 0
    stage2_excluded: int = 0
    accounted_candidates: int = 0


class ScreeningResponseSchema(BaseModel):
    run_id: str
    idempotency_key: str
    job_id: str
    metrics: PipelineMetricsSchema
    leaderboard: List[Dict[str, Any]]
    rejected_candidates: List[Dict[str, Any]]
    review_candidates: List[Dict[str, Any]]
    failed_candidates: List[Dict[str, Any]]
