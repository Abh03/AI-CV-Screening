from enum import Enum
from typing import List, Dict
from pydantic import BaseModel, Field, ConfigDict


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


class FlagDetail(StrictBaseModel):
    type: FlagType
    severity: Severity
    description: str
    citations: List[str]


class CategoryAssessment(StrictBaseModel):
    score: float = Field(ge=0.0, le=100.0)
    rationale: str
    citations: List[str]


class LLMEvaluationOutput(StrictBaseModel):
    skills: CategoryAssessment
    experience: CategoryAssessment
    projects: CategoryAssessment
    education: CategoryAssessment
    flags: List[FlagDetail]
    executive_summary: str


class FinalCandidateEvaluation(StrictBaseModel):
    candidate_id: str
    composite_score: float
    tier: DecisionTier
    category_scores: Dict[str, float]
    llm_raw_output: LLMEvaluationOutput
    verified_citations: List[str]
    invalid_citations: List[str]
    has_critical_flags: bool