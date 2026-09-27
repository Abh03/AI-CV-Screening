"""Recruiter-reviewed, soft relevance targets. Never used by hard filters."""
from typing import Literal
from pydantic import Field, model_validator
from app.stage1_rules.contracts import StrictModel


class RelevanceTarget(StrictModel):
    target_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    category: Literal["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]
    kind: Literal["skill", "responsibility", "domain", "experience", "project", "education"]
    text: str = Field(min_length=1, max_length=600, pattern=r"\S")
    source_quote: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    importance: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    treatment: Literal["requirement", "preference"] = "requirement"
    # AND across groups, OR across identity equivalents within a group.
    evidence_terms: list[list[str]] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def terms(self):
        if any(not group or len(group) > 20 or any(not term.strip() or len(term) > 120
               for term in group) for group in self.evidence_terms):
            raise ValueError("Evidence groups must contain bounded, nonblank terms")
        if self.kind == "domain" and self.treatment != "preference":
            raise ValueError("Domain context must remain a soft preference")
        return self


class RelevanceContract(StrictModel):
    version: Literal["relevance-v1"] = "relevance-v1"
    targets: list[RelevanceTarget] = Field(default_factory=list, max_length=48)
    # Zero means supported evidence is required, without claiming calibration.
    minimum_coverage: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_targets(self):
        ids = [target.target_id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("Relevance target IDs must be unique")
        return self
