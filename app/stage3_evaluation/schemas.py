from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


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
    TIER_1 = "TIER_1"  # Strong Fit (Composite >= 75, no Critical/High flags)
    TIER_2 = "TIER_2"  # Potential Fit (55 <= Composite < 75)
    TIER_3 = "TIER_3"  # Not Recommended (Composite < 55 or mandatory skill fail)


class FlagDetail(BaseModel):
    type: FlagType
    severity: Severity
    description: str
    citations: List[str] = Field(default_factory=list)


class CategoryAssessment(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    rationale: str
    citations: List[str] = Field(default_factory=list)


class LLMEvaluationOutput(BaseModel):
    skills: CategoryAssessment
    experience: CategoryAssessment
    projects: CategoryAssessment
    education: CategoryAssessment
    flags: List[FlagDetail] = Field(default_factory=list)
    executive_summary: str


class FinalCandidateEvaluation(BaseModel):
    candidate_id: str
    composite_score: float
    tier: DecisionTier
    category_scores: Dict[str, float]
    llm_raw_output: LLMEvaluationOutput
    verified_citations: List[str]
    invalid_citations: List[str]
    has_critical_flags: bool