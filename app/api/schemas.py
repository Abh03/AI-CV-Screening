from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator


from app.stage1_rules.contracts import CandidateInput, HardFilterRules, StrictModel
from app.stage1_rules.contracts import CandidateAttributes, RecruiterOverrides, Authorization, AuthorizationStatus, AttributeSource
from app.stage1_rules.jd_profiler import SkillCluster


class CandidateInputSchema(CandidateInput):
    pass


class JobProfileInputSchema(StrictModel):
    job_id: str
    title: str
    jd_category_queries: Dict[str, str]
    hard_filter_rules: HardFilterRules = Field(default_factory=HardFilterRules)
    must_have_skills: List[SkillCluster] = Field(default_factory=list, max_length=100)
    nice_to_have_skills: List[SkillCluster] = Field(default_factory=list, max_length=100)

class CampaignCreateSchema(StrictModel):
    job_profiles: List[JobProfileInputSchema] = Field(default_factory=list)
    approved_jd_ids: List[str] = Field(default_factory=list, max_length=100)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_jds(self):
        if not self.job_profiles and not self.approved_jd_ids:
            raise ValueError("Approved JD references are required")
        if self.job_profiles and self.approved_jd_ids:
            raise ValueError("Do not mix JD references and inline profiles")
        if len(set(self.approved_jd_ids)) != len(self.approved_jd_ids):
            raise ValueError("Approved JD references must be distinct")
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
    job_profile: Optional[JobProfileInputSchema] = None
    approved_jd_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    candidates: List[CandidateInputSchema]
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_candidates(self):
        if (self.job_profile is None) == (self.approved_jd_id is None):
            raise ValueError("Supply exactly one approved JD reference or internal job profile")
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate IDs must be unique within a run")
        return self


class PDFScreeningRequestSchema(StrictModel):
    job_profile: Optional[JobProfileInputSchema] = None
    approved_jd_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1)
    pdf_base64: str
    work_authorized: Authorization = AuthorizationStatus.UNKNOWN
    authorization_source: AttributeSource = AttributeSource.UNKNOWN
    parsed_attributes: CandidateAttributes = Field(default_factory=CandidateAttributes)
    recruiter_overrides: RecruiterOverrides = Field(default_factory=RecruiterOverrides)
    top_n_stage2_cutoff: int = Field(default=30, ge=1, le=100)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def profile_reference(self):
        if (self.job_profile is None) == (self.approved_jd_id is None):
            raise ValueError("Supply exactly one approved JD reference or internal job profile")
        return self


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
