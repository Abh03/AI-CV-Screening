"""Shared Stage 1 contracts for API requests and direct pipeline callers."""
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def numeric_years(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Experience must be a number, not a string or boolean")
    return value


Years = Annotated[float, BeforeValidator(numeric_years), Field(ge=0, allow_inf_nan=False)]
Term = Annotated[str, Field(strict=True, min_length=1)]


class AuthorizationStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


def normalize_authorization(value):
    # Only actual legacy booleans are accepted; strings such as "false" are invalid.
    if value is True:
        return AuthorizationStatus.ELIGIBLE
    if value is False:
        return AuthorizationStatus.INELIGIBLE
    if value is None:
        return AuthorizationStatus.UNKNOWN
    return value


Authorization = Annotated[AuthorizationStatus, BeforeValidator(normalize_authorization)]


class AttributeSource(str, Enum):
    RECRUITER_VERIFIED = "recruiter_verified"
    CV_EXTRACTED = "cv_extracted"
    UNKNOWN = "unknown"


DEGREE_HIERARCHY = {
    "NONE": 0,
    "SEE": 1, "SLC": 1, "SECONDARY": 1, "10TH": 1, "HIGH SCHOOL": 1,
    "+2": 2, "INTERMEDIATE": 2, "HIGHER SECONDARY": 2, "A-LEVELS": 2, "A LEVEL": 2, "12TH": 2,
    "DIPLOMA": 3,
    "BACHELOR": 4, "B.E": 4, "B.SC": 4, "BCA": 4, "BIT": 4, "BBA": 4, "BIM": 4,
    "B.TECH": 4, "B.S": 4, "BSC": 4, "BE": 4, "BTECH": 4, "BS": 4,
    "MASTER": 5, "M.E": 5, "M.SC": 5, "MCA": 5, "M.TECH": 5, "MBA": 5,
    "M.S": 5, "MSC": 5, "ME": 5, "MTECH": 5, "MS": 5,
    "PHD": 6, "DOCTORATE": 6,
}


def normalize_degree(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Degree level must be a string")
    value = value.strip().upper().rstrip(".")
    value = {"BACHELOR'S": "BACHELOR", "BACHELORS": "BACHELOR",
             "MASTER'S": "MASTER", "MASTERS": "MASTER", "PH.D": "PHD"}.get(value, value)
    if value not in DEGREE_HIERARCHY:
        raise ValueError("Unknown degree level")
    return value


class DegreeRequirement(StrictModel):
    level: str = "NONE"
    level_aliases: list[Term] = Field(default_factory=list)
    fields: list[Term] = Field(default_factory=list)
    field_aliases: list[Term] = Field(default_factory=list)

    _level = field_validator("level", mode="before")(normalize_degree)

    @field_validator("level_aliases", "fields", "field_aliases")
    @classmethod
    def nonblank_terms(cls, terms):
        if any(not term.strip() for term in terms):
            raise ValueError("Blank requirement terms are not allowed")
        return [term.strip() for term in terms]

    @model_validator(mode="after")
    def coherent_requirement(self):
        rank = DEGREE_HIERARCHY[self.level]
        if rank == 0 and (self.fields or self.field_aliases or self.level_aliases):
            raise ValueError("Degree fields/aliases require an active degree level")
        for alias in self.level_aliases:
            if DEGREE_HIERARCHY[normalize_degree(alias)] != rank:
                raise ValueError("Degree aliases must have the same level as the requirement")
        return self


class HardFilterRules(StrictModel):
    min_years_experience: Years = 0.0
    degree_requirement: DegreeRequirement | None = None
    require_work_authorization: StrictBool = True


def resolve_hard_filters(profile: dict[str, Any], explicit=None) -> HardFilterRules:
    """Accept legacy flattened profiles, but reject typos and conflicting rule sets."""
    metadata = {"job_id", "title", "job_title", "jd_category_queries", "must_have_skills",
                "nice_to_have_skills", "min_match_threshold", "hard_filter_rules"}
    root = {key: value for key, value in profile.items() if key not in metadata}
    root_rules = HardFilterRules.model_validate(root)
    merged = {key: getattr(root_rules, key) for key in root_rules.model_fields_set}
    for source in (profile.get("hard_filter_rules"), explicit):
        if source is None:
            continue
        parsed = HardFilterRules.model_validate(source)
        incoming = {key: getattr(parsed, key) for key in parsed.model_fields_set}
        for key, value in incoming.items():
            if key in merged and merged[key] != value:
                raise ValueError(f"Conflicting hard filter: {key}")
            merged[key] = value
    return HardFilterRules.model_validate(merged)


class CandidateAttributes(StrictModel):
    experience_years: Years | None = None
    experience_source: AttributeSource = AttributeSource.UNKNOWN
    work_authorized: Authorization = AuthorizationStatus.UNKNOWN
    authorization_source: AttributeSource = AttributeSource.UNKNOWN
    degree: str | None = Field(default=None, description="Legacy metadata only; education is evaluated from CV evidence")


class RecruiterOverrides(StrictModel):
    experience_years: Years | None = None
    work_authorized: Authorization | None = None


class CandidateInput(StrictModel):
    candidate_id: Term
    raw_cv_text: str
    work_authorized: Authorization = AuthorizationStatus.UNKNOWN
    authorization_source: AttributeSource = AttributeSource.UNKNOWN
    parsed_attributes: CandidateAttributes = Field(default_factory=CandidateAttributes)
    recruiter_overrides: RecruiterOverrides = Field(default_factory=RecruiterOverrides)

    @model_validator(mode="after")
    def consistent_authorization(self):
        nested = self.parsed_attributes.work_authorized
        if (self.work_authorized != AuthorizationStatus.UNKNOWN and nested != AuthorizationStatus.UNKNOWN
                and self.work_authorized != nested and self.recruiter_overrides.work_authorized is None):
            raise ValueError("Conflicting authorization inputs require an explicit recruiter override")
        return self

    def rule_facts(self):
        attrs = self.parsed_attributes
        auth = self.work_authorized
        auth_source = self.authorization_source
        if "work_authorized" not in self.model_fields_set:
            auth, auth_source = attrs.work_authorized, attrs.authorization_source
        experience, experience_source = attrs.experience_years, attrs.experience_source
        overrides = self.recruiter_overrides
        if overrides.experience_years is not None:
            experience, experience_source = overrides.experience_years, AttributeSource.RECRUITER_VERIFIED
        if overrides.work_authorized is not None:
            auth, auth_source = overrides.work_authorized, AttributeSource.RECRUITER_VERIFIED
        return CandidateFacts(experience_years=experience, experience_source=experience_source,
                              work_authorized=auth, authorization_source=auth_source)


class CandidateFacts(StrictModel):
    experience_years: Years | None = None
    experience_source: AttributeSource = AttributeSource.UNKNOWN
    work_authorized: Authorization = AuthorizationStatus.UNKNOWN
    authorization_source: AttributeSource = AttributeSource.UNKNOWN
