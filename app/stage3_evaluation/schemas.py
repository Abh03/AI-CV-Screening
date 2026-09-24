from enum import Enum
from typing import List, Dict, Annotated
from pydantic import BaseModel, Field, ConfigDict, model_validator

Score = Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)]


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlagType(str, Enum):
    DOCUMENTED_INCONSISTENCY = "DOCUMENTED_INCONSISTENCY"
    EVIDENCED_CAREER_GAP = "EVIDENCED_CAREER_GAP"
    MISSING_INFORMATION = "MISSING_INFORMATION"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DecisionTier(str, Enum):
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"


class EvaluationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


class FlagDetail(StrictBaseModel):
    type: FlagType
    severity: Severity
    description: str
    citations: List[str]


class CategoryAssessment(StrictBaseModel):
    score: Score
    rationale: str
    citations: List[str]


class LLMEvaluationOutput(StrictBaseModel):
    skills: CategoryAssessment
    experience: CategoryAssessment
    projects: CategoryAssessment
    education: CategoryAssessment
    flags: List[FlagDetail]
    executive_summary: str


class SourceLocation(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    section: str | None = None
    chunk_index: int | None = Field(default=None, ge=0)
    page_number: int | None = Field(default=None, ge=1)
    block_index: int | None = Field(default=None, ge=0)
    bbox: tuple[float, float, float, float] | None = None


class EvidenceReference(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str
    category: str
    source_category: str
    chunk_id: str
    document_id: str | None = None
    source_location: SourceLocation
    text: str


class CitationCheck(StrictBaseModel):
    field: str
    citation: str
    valid: bool
    reason: str | None = None


class EvidenceVerification(StrictBaseModel):
    registry: Dict[str, EvidenceReference]
    checks: List[CitationCheck]
    review_reasons: List[str]
    verified_flag_indices: List[int]
    injection_signals: List[str]

    @property
    def verified_citations(self) -> List[str]:
        return sorted({check.citation for check in self.checks if check.valid})

    @property
    def invalid_citations(self) -> List[str]:
        return sorted({check.citation for check in self.checks if not check.valid})


class FinalCandidateEvaluation(StrictBaseModel):
    candidate_id: str
    evaluation_status: EvaluationStatus
    scoring_policy_version: str
    composite_score: Score | None
    tier: DecisionTier | None
    category_scores: Dict[str, Score]
    llm_raw_output: LLMEvaluationOutput | None
    verified_citations: List[str]
    invalid_citations: List[str]
    has_critical_flags: bool
    review_reasons: List[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    evidence_verification: EvidenceVerification | None = None
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.evaluation_status == EvaluationStatus.EVALUATION_FAILED:
            if self.composite_score is not None or self.tier is not None or self.category_scores:
                raise ValueError("Failed evaluations cannot have suitability scores or a tier")
            if self.llm_raw_output is not None or not self.error_code or not self.error_message:
                raise ValueError("Failed evaluations require error details, not fabricated assessments")
            if self.has_critical_flags or self.review_reasons or self.verified_citations or self.invalid_citations:
                raise ValueError("System failures cannot imply candidate flags or evidence judgments")
        else:
            if self.composite_score is None or self.llm_raw_output is None:
                raise ValueError("Completed assessments require scores and validated output")
            if self.evidence_verification is None:
                raise ValueError("Completed assessments require evidence verification")
            if any(ref.candidate_id != self.candidate_id for ref in self.evidence_verification.registry.values()):
                raise ValueError("Evidence registry belongs to another candidate")
            if set(self.category_scores) != {"skills", "experience", "projects", "education"}:
                raise ValueError("All four category scores are required")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("Completed assessments cannot contain execution errors")
            if self.evaluation_status == EvaluationStatus.SUCCESS:
                if self.tier is None or self.review_reasons or self.evidence_verification.review_reasons:
                    raise ValueError("Successful evaluations require a tier and no review reasons")
            elif self.tier is not None or not self.review_reasons:
                raise ValueError("Review outcomes require reasons and no final tier")
        return self
